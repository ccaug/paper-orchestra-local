#!/usr/bin/env python
"""
Basic building blocks of the review engine (parsing, chunking, static checks, guarded patches).

Goal: review/refine a long LaTeX paper with a small LOCAL model (Ollama,
colibri, llama.cpp server...) without ever truncating the paper, while keeping
the result coherent as ONE paper.

Idea (map -> ledger -> contextual review -> cross-check -> global pass -> guarded patch):

  0. Parse   : flatten \\input, split into sections, sub-split big sections at
               paragraph boundaries (never mid-paragraph).
  1. MAP     : per chunk, extract a compact record: summary, claims
               (observation/interpretation/hypothesis), numbers + meaning.
  2. LEDGER  : deterministic code (no LLM): outline, refs/labels/cites/bib
               checks, numbers that appear in the abstract but nowhere else,
               fraction registry (34/40 ...). Cheap and 100% reliable.
  3. REVIEW  : per chunk, with GLOBAL SKELETON + claims of other sections in
               the prompt, so each local review "knows" the whole paper.
               Output = exact find/replace patches, not rewritten text.
  4. CROSS   : LLM sees only the compact ledger (claims + numbers of ALL
               sections, a few thousand tokens) -> contradictions/denominator
               mismatches across sections.
  5. GLOBAL  : abstract + contributions + conclusion + skeleton -> coherence
               (does the story promised = the story delivered?).
  6. PATCH   : guarded application. A patch is rejected if the old text is not
               found exactly once, if it introduces ANY new number, or adds a
               citation/ref not already in the old snippet. Anything flagged
               needs_author goes to the report, not to the paper.

Everything is cached on disk (re-runs are free/resumable) and stages 1 and 3
are independent per chunk. Stable prompt prefix first -> prefix/KV-cache reuse
in Ollama and llama.cpp.

Env: OPENAI_BASE_URL (default Ollama), LLM_MODEL, LLM_CTX, SECTION_CHARS,
     WORKERS, NO_THINK (default 1, appends /no_think for Qwen3), OLLAMA_OPTS.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MODEL = os.getenv("LLM_MODEL", "gpt-local-qwen3")
CTX = int(os.getenv("LLM_CTX", "32768"))
BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
SECTION_CHARS = int(os.getenv("SECTION_CHARS", "24000"))  # ~8-9k tokens of LaTeX
WORKERS = int(os.getenv("WORKERS", "1"))
NO_THINK = os.getenv("NO_THINK", "1") == "1"
OLLAMA_OPTS = os.getenv("OLLAMA_OPTS", "1") == "1"

CACHE = Path(".llm_cache")
_client = None

NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?:/\d+)?%?")
CITE = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}")
REF = re.compile(r"\\(?:auto|c|C|eq|v|page|name)?ref\{([^}]+)\}")
LABEL = re.compile(r"\\label\{([^}]+)\}")


# --------------------------------------------------------------------------
# LLM access (OpenAI-compatible, local) with disk cache + JSON repair
# --------------------------------------------------------------------------
def client():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(
            base_url=BASE_URL,
            api_key=os.getenv("OPENAI_API_KEY", "local"),
            timeout=3600,
        )
    return _client


def parse_json(txt):
    txt = re.sub(r"```(?:json)?", "", txt)
    a, b = txt.find("{"), txt.rfind("}")
    if a == -1 or b <= a:
        return None
    s = txt[a : b + 1]
    for cand in (s, re.sub(r",\s*([\]}])", r"\1", re.sub(r"[\x00-\x1f]", " ", s))):
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return None


def llm(system, user, max_tokens=3000, as_json=True, tag=""):
    key = hashlib.sha256(
        json.dumps([MODEL, system, user, max_tokens, as_json]).encode()
    ).hexdigest()[:24]
    f = CACHE / f"{key}.json"
    if f.exists():
        return json.loads(f.read_text("utf-8"))["out"]
    out = None
    u = user
    for _ in range(3):
        kw = {}
        if OLLAMA_OPTS:
            kw["extra_body"] = {"options": {"num_ctx": CTX}}
        r = client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": u + ("\n/no_think" if NO_THINK else "")},
            ],
            temperature=0.1,
            max_tokens=max_tokens,
            **kw,
        )
        txt = re.sub(
            r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S
        ).strip()
        if not as_json:
            out = txt
            break
        out = parse_json(txt)
        if out is not None:
            break
        u = user + "\n\nYour previous reply was not valid JSON. Reply with ONLY one JSON object."
    if out is not None:
        CACHE.mkdir(exist_ok=True)
        f.write_text(json.dumps({"out": out}), "utf-8")
    return out


# --------------------------------------------------------------------------
# 0. Parse
# --------------------------------------------------------------------------
def flatten(path: Path, seen=None):
    seen = seen or set()
    txt = path.read_text("utf-8", errors="ignore")

    def rep(m):
        p = path.parent / m.group(1)
        if p.suffix == "":
            p = p.with_suffix(".tex")
        if p.exists() and p not in seen:
            seen.add(p)
            return flatten(p, seen)
        return m.group(0)

    return re.sub(r"\\(?:input|include)\{([^}]+)\}", rep, txt)


def split_sections(tex):
    """Returns (head, [section dict], tail) so that head+sections+tail == tex."""
    b = tex.find(r"\begin{document}")
    b = 0 if b == -1 else b
    e = tex.rfind(r"\end{document}")
    e = len(tex) if e == -1 else e
    head, body, tail = tex[:b], tex[b:e], tex[e:]
    marks = [
        (m.start(), m.group(1))
        for m in re.finditer(r"\\section\*?\{([^}]*)\}", body)
    ]
    ab = body.find(r"\begin{abstract}")
    if ab != -1:
        marks.append((ab, "Abstract"))
    marks = sorted(set(marks))
    secs = []
    first = marks[0][0] if marks else len(body)
    head += body[:first]
    for i, (s, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        secs.append({"title": title, "text": body[s:end]})
    return head, secs, tail


def chunk_section(sec, sid):
    text, out = sec["text"], []
    paras = re.split(r"(\n\s*\n)", text)
    cur = ""
    for p in paras:
        if cur and len(cur) + len(p) > SECTION_CHARS:
            out.append(cur)
            cur = ""
        cur += p
    if cur:
        out.append(cur)
    return [
        {"id": f"S{sid}.{i+1}", "title": sec["title"], "text": t, "part": i + 1, "parts": len(out)}
        for i, t in enumerate(out)
    ]


# --------------------------------------------------------------------------
# 2. Deterministic ledger
# --------------------------------------------------------------------------
def static_checks(secs, bibtext):
    full = "\n".join(s["text"] for s in secs)
    labels = set(LABEL.findall(full))
    refs = {k.strip() for r in REF.findall(full) for k in r.split(",")}
    cites = {k.strip() for c in CITE.findall(full) for k in c.split(",")}
    bib = set(re.findall(r"@\w+\{([^,\s]+),", bibtext))
    w = []
    for k in sorted(refs - labels):
        w.append(f"\\ref to undefined label: {k}")
    for k in sorted(l for l in labels - refs if l.split(":")[0] in ("fig", "tab", "table", "figure")):
        w.append(f"label never referenced in text: {k}")
    if bib:
        for k in sorted(cites - bib):
            w.append(f"cite key not in .bib: {k}")
        for k in sorted(bib - cites):
            w.append(f"bib entry never cited: {k}")
    ab = next((s for s in secs if s["title"] == "Abstract"), None)
    if ab:
        rest = "\n".join(s["text"] for s in secs if s is not ab)
        miss = [n for n in set(NUM.findall(ab["text"])) - set(NUM.findall(rest)) if len(n) > 1]
        for n in sorted(miss):
            w.append(f"number in Abstract not found anywhere else: {n}")
    return w


def numbers_digest(chunk, cap=30):
    t, seen, out = chunk["text"], set(), []
    for m in NUM.finditer(t):
        n = m.group(0)
        if len(n) < 2 or n in seen:
            continue
        seen.add(n)
        ctx = re.sub(r"\s+", " ", t[max(0, m.start() - 50) : m.end() + 40])
        out.append(f"{n} :: {ctx}")
        if len(out) >= cap:
            break
    return out


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------
def system_prefix(guidelines, elog):
    return (
        "You are a rigorous scientific-paper refinement agent. Follow these rules "
        "strictly.\n\n=== GUIDELINES ===\n"
        + guidelines[:6000]
        + "\n\n=== EXPERIMENTAL LOG (authoritative evidence) ===\n"
        + elog[:5000]
        + "\n\nHard rules: never invent or change numbers; never invent citations; "
        "quote text VERBATIM when giving fix_old; output JSON only."
    )


MAP_PROMPT = """Extract a compact record of this chunk of a paper (section: {title}, part {part}/{parts}).
Return JSON: {{"summary": "<=60 words",
 "claims": [{{"text": "<=25 words", "kind": "observation|interpretation|hypothesis", "strength": "weak|medium|strong"}}],
 "numbers": [{{"value": "...", "meaning": "<=15 words", "denominator_or_n": "..."}}],
 "terms": ["key defined terms"]}}
Max 12 claims. Only what the text states.

TEXT:
{text}"""

REVIEW_PROMPT = """GLOBAL SKELETON OF THE WHOLE PAPER (so you review this part in context):
{skeleton}

CLAIMS MADE ELSEWHERE IN THE PAPER (for consistency):
{other_claims}

AUTOMATIC WARNINGS TO CONSIDER:
{warnings}

Now review ONLY this chunk (section: {title}, part {part}/{parts}) against the guidelines:
scientific accuracy, over-claiming (anthropomorphic/causal language beyond evidence), consistency with the skeleton and claims above, clarity, LaTeX correctness.
Return JSON: {{"issues": [{{"severity": "high|medium|low", "type": "inconsistency|overclaim|clarity|latex|unsupported|other",
 "problem": "...", "fix_old": "EXACT verbatim substring of the chunk (<=300 chars) or empty",
 "fix_new": "replacement; must NOT add numbers or citations", "needs_author": true|false}}]}}
Use needs_author=true for anything touching results, numbers, or interpretation you cannot verify. Max 10 issues, most important first.

CHUNK:
{text}"""

CROSS_PROMPT = """Below is the compact ledger of the WHOLE paper (claims + numbers per section, plus a fraction/number registry).
Find CONTRADICTIONS and mismatches between sections: different values for the same quantity, inconsistent denominators (e.g. x/n vs x/m for the same event), claims in the abstract/intro not supported by results, results claimed stronger in one place than another, terminology drift.
Return JSON: {{"contradictions": [{{"severity": "high|medium|low", "sections": ["..."], "description": "...", "evidence": ["quoted ledger lines"]}}]}}
Do NOT resolve them; report them.

LEDGER:
{ledger}"""

GLOBAL_PROMPT = """Check global coherence: does the paper deliver what Abstract/Introduction promise, and does the Conclusion claim no more than the results show?
SKELETON:
{skeleton}

ABSTRACT + INTRODUCTION (head):
{head}

CONCLUSION/DISCUSSION:
{tail}

Return JSON: {{"coherence": [{{"severity": "high|medium|low", "description": "...", "suggestion": "..."}}],
 "contribution_claims": ["each contribution as promised"], "unmet_promises": ["..."], "overclaims": ["..."]}}"""


# --------------------------------------------------------------------------
# Patch application with scientific-integrity guards
# --------------------------------------------------------------------------
def apply_patches(text, issues):
    applied, rejected = [], []
    order = {"high": 0, "medium": 1, "low": 2}
    for it in sorted(issues, key=lambda i: order.get(i.get("severity"), 3)):
        old, new = it.get("fix_old") or "", it.get("fix_new")
        why = None
        if it.get("needs_author"):
            why = "needs author approval"
        elif not old or new is None or old == new:
            why = "no concrete patch"
        elif text.count(old) != 1:
            why = f"fix_old found {text.count(old)} times (need exactly 1)"
        elif not set(NUM.findall(new)) <= set(NUM.findall(old)):
            why = "introduces new numbers"
        elif not {k for c in CITE.findall(new) for k in c.split(",")} <= {
            k for c in CITE.findall(old) for k in c.split(",")
        }:
            why = "introduces new citation keys"
        elif not set(REF.findall(new)) <= set(REF.findall(old)) or not set(LABEL.findall(new)) <= set(LABEL.findall(old)):
            why = "introduces new refs/labels"
        if why:
            rejected.append({**it, "reason": why})
        else:
            text = text.replace(old, new, 1)
            applied.append(it)
    return text, applied, rejected


# --------------------------------------------------------------------------
# Main pipeline
# --------------------------------------------------------------------------
def run(project, main_tex, out, guidelines_f, elog_f, apply, compile_):
    global CACHE
    project, out = Path(project), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    CACHE = out / ".llm_cache"

    tex = flatten(project / main_tex)
    bibtext = "\n".join(p.read_text("utf-8", errors="ignore") for p in project.glob("*.bib"))
    guidelines = Path(guidelines_f).read_text("utf-8") if Path(guidelines_f).exists() else ""
    elog = Path(elog_f).read_text("utf-8") if Path(elog_f).exists() else ""
    sysmsg = system_prefix(guidelines, elog)

    head, secs, tail = split_sections(tex)
    reviewable = [s for s in secs if "checklist" not in s["title"].lower()]
    chunks = [c for i, s in enumerate(reviewable) for c in chunk_section(s, i + 1)]
    print(f"[0] {len(secs)} sections -> {len(chunks)} chunks (<= {SECTION_CHARS} chars)")

    # 1. MAP
    def do_map(c):
        r = llm(sysmsg, MAP_PROMPT.format(**c), 1800, tag="map") or {}
        c["rec"] = r
        return c

    with ThreadPoolExecutor(WORKERS) as ex:
        chunks = list(ex.map(do_map, chunks))
    print("[1] map done")

    # 2. LEDGER (deterministic)
    warnings = static_checks(secs, bibtext)
    skeleton = "\n".join(
        f"- {c['id']} {c['title']} (part {c['part']}/{c['parts']}): {c['rec'].get('summary', '')}"
        for c in chunks
    )
    claims_by = {
        c["id"]: [f"[{k['kind']}/{k['strength']}] {k['text']}" for k in c["rec"].get("claims", []) if isinstance(k, dict)]
        for c in chunks
    }
    print(f"[2] ledger: {len(warnings)} static warnings")

    # 3. REVIEW each chunk with global context
    def do_review(c):
        others = "\n".join(
            f"{cid}: " + " | ".join(cl[:6]) for cid, cl in claims_by.items() if cid != c["id"]
        )[:9000]
        w = "\n".join(f"- {x}" for x in warnings[:40]) or "none"
        r = llm(
            sysmsg,
            REVIEW_PROMPT.format(skeleton=skeleton, other_claims=others, warnings=w, **{k: c[k] for k in ("title", "part", "parts", "text")}),
            3500,
            tag="review",
        ) or {"issues": []}
        iss = [i for i in r.get("issues", []) if isinstance(i, dict)]
        for i in iss:
            i["chunk"] = c["id"]
        return iss

    with ThreadPoolExecutor(WORKERS) as ex:
        issues = [i for lst in ex.map(do_review, chunks) for i in lst]
    print(f"[3] review done: {len(issues)} issues")

    # 4. CROSS-SECTION consistency over the compact ledger
    ledger_lines = []
    for c in chunks:
        ledger_lines.append(f"## {c['id']} {c['title']}")
        ledger_lines += [f"claim: {x}" for x in claims_by[c["id"]]]
        ledger_lines += [f"number: {n}" for n in numbers_digest(c)]
    ledger = "\n".join(ledger_lines)[:28000]
    cross = llm(sysmsg, CROSS_PROMPT.format(ledger=ledger), 3000, tag="cross") or {}
    print("[4] cross-section check done")

    # 5. GLOBAL coherence
    def pick(names, n):
        return "\n".join(s["text"] for s in secs if any(k in s["title"].lower() for k in names))[:n]

    glob = llm(
        sysmsg,
        GLOBAL_PROMPT.format(
            skeleton=skeleton,
            head=pick(("abstract", "introduction"), 9000),
            tail=pick(("conclusion", "discussion", "limitation"), 9000),
        ),
        2500,
        tag="global",
    ) or {}
    print("[5] global coherence done")

    # 6. PATCH
    full = head + "".join(s["text"] for s in secs) + tail
    refined, applied, rejected = (full, [], issues)
    if apply:
        refined, applied, rejected = apply_patches(full, issues)
    (out / "refined_flat.tex").write_text(refined, "utf-8")

    # Report
    exec_summary = llm(
        sysmsg,
        "Write a concise executive review (max 300 words) of this paper for its authors. "
        "Prioritize scientific problems over style. Data:\n"
        + json.dumps({"cross": cross, "global": glob, "high_issues": [i for i in issues if i.get("severity") == "high"][:15]})[:12000],
        900,
        as_json=False,
        tag="exec",
    )
    rep = ["# Cascade local refinement report", "", "## Executive summary", exec_summary or "", "",
           "## Static warnings (deterministic)"] + [f"- {w}" for w in warnings]
    rep += ["", "## Cross-section contradictions", json.dumps(cross, indent=2, ensure_ascii=False),
            "", "## Global coherence", json.dumps(glob, indent=2, ensure_ascii=False),
            "", f"## Applied patches ({len(applied)})"]
    for a in applied:
        rep.append(f"- [{a['chunk']}] {a['problem']}\n  - OLD: `{a['fix_old'][:200]}`\n  - NEW: `{a['fix_new'][:200]}`")
    rep.append(f"\n## Not applied / needs author ({len(rejected)})")
    for a in rejected:
        rep.append(f"- [{a.get('chunk')}] ({a.get('severity')}) {a.get('problem')} -> {a.get('reason', 'review only')}")
    (out / "review_report.md").write_text("\n".join(rep), "utf-8")
    (out / "ledger.json").write_text(
        json.dumps({"skeleton": skeleton, "claims": claims_by, "warnings": warnings}, indent=2, ensure_ascii=False), "utf-8"
    )
    print(f"[6] applied {len(applied)}, left for author {len(rejected)}")

    if compile_ and shutil.which("pdflatex"):
        build = out / "build"
        shutil.copytree(project, build, dirs_exist_ok=True)
        (build / "refined.tex").write_text(refined, "utf-8")
        for cmd in (["pdflatex", "-interaction=nonstopmode", "refined.tex"], ["bibtex", "refined"],
                    ["pdflatex", "-interaction=nonstopmode", "refined.tex"], ["pdflatex", "-interaction=nonstopmode", "refined.tex"]):
            subprocess.run(cmd, cwd=build, capture_output=True)
        print("[compile] done:", build / "refined.pdf")
    print("Outputs in", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="Cascade_PROJECT")
    ap.add_argument("--main", default="neurips_2026.tex")
    ap.add_argument("--out", default="Cascade_OUTPUT/local")
    ap.add_argument("--guidelines", default="Cascade_PROJECT/guidelines.md")
    ap.add_argument("--elog", default="Cascade_PROJECT/experimental_log.md")
    ap.add_argument("--no-apply", action="store_true", help="review only, do not patch")
    ap.add_argument("--compile", action="store_true")
    a = ap.parse_args()
    run(a.project, a.main, a.out, a.guidelines, a.elog, not a.no_apply, a.compile)
