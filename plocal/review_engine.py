#!/usr/bin/env python
"""
Cascade Reviewer -- asymmetric cascade that REWRITES the paper (with proof) and shows its work.

What changed vs v2.0
--------------------
* ACTS, not only advises. A finding that is necessary (severity high/medium, confirmed by the
  deep model / adversarial round / deterministic evidence) and wins a position-swapped patch
  tournament is APPLIED to the paper. Everything else stays as a SUGGESTION the user can accept
  with one click in the GUI.
* HARD SAFETY for the science: a rewrite must keep exactly the same numbers, add no citations /
  refs, keep LaTeX balanced, and never touch tables or math environments.
* JUSTIFIABLE: every change records why, what it was compared with (and how), which signals
  confirmed it (screener / deep / attack / harmonize / tournament) and is flagged for review
  when it touches interpretation. Outputs: changes.md, suggestions.md, a real red/blue redline PDF.
* COHERENT: after rewriting a claim, a HARMONIZE stage hunts the same claim elsewhere in the paper
  (abstract/intro/conclusion echoes) and rewrites them consistently; a GLOBAL REWRITE stage turns
  cross-section contradictions and abstract-vs-conclusion overclaims into grounded patches.
* VISIBLE: word-level tracking in the PDF, the passages retrieved as context and what each
  finding was compared with (and how) are streamed to the GUI.
"""
import argparse
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from plocal import review_basics as V1
from plocal.review_basics import (CITE, LABEL, NUM, REF, apply_patches, chunk_section,
                                   flatten, parse_json, split_sections, static_checks,
                                   system_prefix)

FAST = os.getenv("FAST_MODEL", "qwen3:4b")
DEEP = os.getenv("DEEP_MODEL", "qwen3:8b")
CTX = int(os.getenv("LLM_CTX", "16384"))
BASE_URL = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "http://localhost:11434/v1"
NO_THINK = os.getenv("NO_THINK", "1") == "1"
MAX_ROUNDS = int(os.getenv("MAX_ROUNDS", "3"))
SWAP_S = float(os.getenv("SWAP_SECONDS", "25"))
OLLAMA_OPTS = os.getenv("OLLAMA_OPTS", "1") == "1"
SIM_MIN = float(os.getenv("SIM_MIN", "0.22"))
SECTION_CHARS = int(os.getenv("SECTION_CHARS", "12000"))
V1.SECTION_CHARS = SECTION_CHARS

NUM2 = re.compile(r"(?<![\w.])\d+\.\d+|(?<![\w.])\d+/\d+|(?<![\w.])\d+(?:\.\d+)?%")
OVER = re.compile(r"\b(prove[sd]?|proof|demonstrat\w+|clearly|obviously|significant(?:ly)?|novel|"
                  r"state-of-the-art|first|always|never|guarantee\w*|conscious\w*|feel\w*|fear|"
                  r"emotion\w*|suffer\w*|understand\w*|human-like|breakthrough|dramatic\w*|"
                  r"unprecedented|robust)\b", re.I)
HEDGE = re.compile(r"\b(may|might|suggest\w*|appears?|consistent with|hypothesi[sz]e\w*|"
                   r"indicate\w*|preliminary|exploratory|we observe)\b", re.I)
STOP = set("the of and to in a is that for with on as by we this are be it from an at or which our can "
           "not have has was were their these those also more than between using used use paper results "
           "show shows showed into over under such each both while when then thus".split())
WORD = re.compile(r"[a-zA-Z][a-zA-Z\-]{2,}|\d+(?:[.,/]\d+)?%?")

C = dict(r="\033[0m", dim="\033[2m", b="\033[1m", c="\033[96m", m="\033[95m", g="\033[92m",
         y="\033[93m", red="\033[91m")


def tpl(s, **kw):
    for k, v in kw.items():
        s = s.replace("{{" + k + "}}", str(v))
    return s


def fmt(sec):
    sec = max(0, int(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def bar(frac, w=26):
    n = int(max(0, min(1, frac)) * w)
    return "█" * n + "░" * (w - n)


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def isnum(w):
    return bool(w) and w[0].isdigit() and bool(re.search(r"[./%]", w))


# ==========================================================================
# Monitor
# ==========================================================================
class Monitor:
    STAGES = ["PARSE", "MAP", "SCAN", "ADJUDICATE", "ATTACK", "HARMONIZE", "CROSS", "GLOBAL", "PATCH", "VERIFY"]
    QRE = re.compile(r'"quote"\s*:\s*"((?:[^"\\]|\\.)*)"')
    CRE = re.compile(r'"quote"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"compared_with"\s*:\s*"([^"]*)"')

    def __init__(self, out):
        self.out = Path(out)
        self.t0 = time.time()
        self.lock = threading.RLock()
        self.tasks, self.byid = [], {}
        self.rates = {}
        self.stage_state = {}
        self.now = None
        self.live_text = ""
        self.issues, self.patches, self.tourn, self.log = [], [], [], []
        self.changes, self.suggestions = [], []
        self.panel = {}
        self.cnt = dict(cache=0, filtered=0, escalated=0, calls=0, tok_out=0, unbound=0, swaps=0, rewritten=0)
        self.pdf = {"before": False, "after": False, "redline": False}
        self.chunks, self.pages = {}, []
        self.title = "Cascade Reviewer"
        self.finished = False
        self.tty = sys.stdout.isatty()
        self.drawn = 0
        self._stop = False
        self.locator = None
        self.geo, self.geo_ver, self.geo_v = {}, 0, {}
        self.relations, self.chunk_rels, self.chunk_text = [], {}, {}
        self.cur_text, self._qmap, self.live_n = "", {}, 0
        self.refined = ""
        self.suggest_only = False
        self.pdf_mode = False
        if os.name == "nt":
            os.system("")

    # ---- geometry -----------------------------------------------------
    def set_geo(self, key, rects):
        with self.lock:
            self.geo_ver += 1
            self.geo[key] = rects
            self.geo_v[key] = self.geo_ver

    def geo_since(self, since):
        with self.lock:
            return {"ver": self.geo_ver, "items": {k: v for k, v in self.geo.items() if self.geo_v.get(k, 0) > since}}

    def locate(self, key, plain, words=False):
        if not self.locator:
            return False
        sp = self.locator.span_of(plain)
        if not sp:
            rs = self.locator.rects_fallback(plain)
            if rs:
                self.set_geo(key, rs)
                return True
            return False
        self.set_geo(key, self.locator.word_rects(*sp) if words else self.locator.line_rects(*sp))
        return True

    # ---- tasks / ETA --------------------------------------------------
    def add_task(self, stage, model, tin=0, tout=0, optional=False, sec=None, label=""):
        with self.lock:
            t = dict(id=len(self.tasks), stage=stage, model=model, tin=tin, tout=tout, optional=optional,
                     sec=sec, state="pending", label=label, started=None, ended=None)
            self.tasks.append(t)
            self.byid[t["id"]] = t
            return t["id"]

    def rate(self, model):
        r = self.rates.get(model) or {}
        pre = float(os.getenv("DEFAULT_PRE_TPS", "45"))
        dec = float(os.getenv("DEFAULT_DEC_TPS", "5"))
        others = [v for k, v in self.rates.items() if k != model and v.get("pre") and v.get("dec")]
        if others:
            scale = 0.5 if model == DEEP and FAST != DEEP else 1.0
            pre, dec = others[0]["pre"] * scale, others[0]["dec"] * scale
        return (r.get("pre") or pre), (r.get("dec") or dec)

    def est(self, t):
        if t["sec"] is not None:
            return t["sec"]
        pre, dec = self.rate(t["model"])
        return t["tin"] / pre + t["tout"] / dec

    def observe(self, model, tin, ttft, tout, tdec):
        with self.lock:
            r = self.rates.setdefault(model, {"pre": None, "dec": None})
            if ttft and ttft > 0.02 and tin > 50:
                p = tin / ttft
                r["pre"] = p if not r["pre"] else 0.6 * r["pre"] + 0.4 * p
            if tout >= 8 and tdec > 0.05:
                d = tout / tdec
                r["dec"] = d if not r["dec"] else 0.6 * r["dec"] + 0.4 * d
            self.cnt["tok_out"] += tout
            self.cnt["calls"] += 1

    def totals(self):
        done = likely = worst = credit = 0.0
        for t in self.tasks:
            e = self.est(t)
            if t["state"] == "done":
                done += e
            elif t["state"] in ("pending", "running"):
                likely += e * (0.4 if t["optional"] and t["state"] == "pending" else 1.0)
                worst += e
                if t["state"] == "running" and t["started"]:
                    credit += min(e, time.time() - t["started"])
        return done, max(0.0, likely - credit), max(0.0, worst - credit)

    def begin(self, tid, agent, model, label, chunk=None):
        with self.lock:
            t = self.byid.get(tid)
            if t:
                t["state"], t["started"] = "running", time.time()
            c = self.chunks.get(chunk, {}) if chunk else {}
            self.now = dict(tid=tid, agent=agent, model=model, label=label, chunk=chunk, start=c.get("start"),
                            end=c.get("end"), since=time.time(), t_phase=time.time(), phase="prefill", pre_eta=1.0, cites=[],
                            rels=[r["id"] for r in self.chunk_rels.get(chunk, [])] if chunk else [])
            self.live_text = ""
            self.cur_text = self.chunk_text.get(chunk, "") if chunk else ""
            self._qmap, self.live_n = {}, 0

    def end(self, tid, state="done"):
        with self.lock:
            t = self.byid.get(tid)
            if t:
                t["state"], t["ended"] = state, time.time()
            self.now = None

    def phase(self, name, tin=None):
        with self.lock:
            if not self.now:
                return
            if tin is not None:
                pre = self.rate(self.now["model"])[0]
                self.now["pre_eta"] = max(0.8, tin / pre)
                self.now["tin"] = int(tin)
            self.now["phase"] = name
            self.now["t_phase"] = time.time()

    def live(self, tail, full=None):
        self.live_text = tail
        if full is not None:
            try:
                self.parse_live(full)
            except Exception:
                pass

    def parse_live(self, text):
        """Quotes the model has just emitted -> red word boxes; compared_with -> comparison arc."""
        if not self.now or not self.locator or not self.cur_text:
            return
        for m in self.QRE.finditer(text):
            raw = m.group(1)
            if raw in self._qmap:
                continue
            try:
                q = json.loads('"' + raw + '"')
            except Exception:
                q = raw
            g = ground(q, self.cur_text)
            cite = {"k": None, "c": None}
            if g:
                key = f"L{self.live_n}"
                self.live_n += 1
                if self.locate(key, plainify(g), words=True):
                    cite["k"] = key
            self._qmap[raw] = cite
            if cite["k"]:
                self.now["cites"].append(cite)
        for m in self.CRE.finditer(text):
            cite = self._qmap.get(m.group(1))
            mm = re.match(r"R(\d+)", m.group(2) or "")
            if cite and mm and cite["c"] is None:
                i = int(mm.group(1)) - 1
                if 0 <= i < len(self.now["rels"]):
                    cite["c"] = self.now["rels"][i]

    def stage(self, name, state):
        with self.lock:
            self.stage_state[name] = state

    # ---- issues -------------------------------------------------------
    def add_issue(self, **kw):
        with self.lock:
            kw["id"] = len(self.issues)
            kw.setdefault("state", "open")
            self.issues.append(kw)
            return kw["id"]

    def set_issue(self, iid, **kw):
        with self.lock:
            self.issues[iid].update(kw)

    def set_chunk(self, cid, **kw):
        with self.lock:
            if cid in self.chunks:
                self.chunks[cid].update(kw)

    def accept(self, sid):
        with self.lock:
            if self.pdf_mode:
                return {"ok": False, "msg": "PDF-only review: apply this edit in your own source file"}
            s = next((x for x in self.suggestions if x["id"] == sid), None)
            if not s or s["state"] != "open":
                return {"ok": False, "msg": "suggestion not open"}
            nw, ap, rej = apply_patches(self.refined, [{"fix_old": s["old_full"], "fix_new": s["new_full"],
                                                         "severity": "high", "needs_author": False}])
            if not ap:
                return {"ok": False, "msg": rej[0].get("reason", "cannot apply")}
            self.refined = nw
            (self.out / "refined_flat.tex").write_text(nw, "utf-8")
            s["state"] = "accepted"
            self.set_issue(s["issue"], state="accepted-by-user")
            self.changes.append(dict(id=len(self.changes), issue=s["issue"], chunk=s["chunk"], type=s["type"],
                                     severity=s["severity"], why=s["why"], old=s["old"], new=s["new"],
                                     status="accepted-by-user", signals={"user": True}, tournament=[], review=False,
                                     compared=None, old_full=s["old_full"], new_full=s["new_full"]))
            return {"ok": True, "msg": "applied to refined_flat.tex (recompile to refresh the PDF)"}

    # ---- CLI ----------------------------------------------------------
    def event(self, kind, msg, color="c"):
        line = f"{C['dim']}[{time.strftime('%H:%M:%S')}]{C['r']} {C[color]}{kind:<9}{C['r']} {msg}"
        with self.lock:
            self.log.append(dict(t=time.strftime("%H:%M:%S"), kind=kind, msg=msg))
            self.log = self.log[-200:]
            try:
                with open(self.out / "events.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(self.log[-1], ensure_ascii=False) + "\n")
            except Exception:
                pass
            if self.tty and self.drawn:
                sys.stdout.write(f"\033[{self.drawn}F\033[J")
                self.drawn = 0
            print(line, flush=True)
            if self.tty:
                self._draw()

    def _ram(self):
        try:
            import psutil
            v = psutil.virtual_memory()
            return v.used / 2**30, v.total / 2**30
        except Exception:
            return None

    def lines(self):
        done, likely, worst = self.totals()
        frac = done / (done + likely) if (done + likely) > 0 else 0
        st = next((n for n in self.STAGES if self.stage_state.get(n) == "running"), "-")
        nd = sum(t["state"] == "done" for t in self.tasks)
        nt = sum(t["state"] != "skipped" for t in self.tasks)
        pre, dec = self.rate(self.now["model"]) if self.now else self.rate(FAST)
        ram = self._ram()
        l1 = (f"{C['b']}Cascade Reviewer{C['r']} {st:<10}[{C['g']}{bar(frac)}{C['r']}] {frac*100:3.0f}%  "
              f"{nd}/{nt} tasks  elapsed {fmt(time.time()-self.t0)}  "
              f"ETA {C['y']}{fmt(likely)}{C['r']} (worst {fmt(worst)})")
        l2 = (f"{C['dim']}fast={FAST} deep={DEEP}{C['r']} | {dec:4.1f} tok/s | rewritten {self.cnt['rewritten']} | "
              f"suggestions {sum(s['state']=='open' for s in self.suggestions)} | filtered {self.cnt['filtered']} fake critiques"
              + (f" | RAM {ram[0]:.1f}/{ram[1]:.0f} GB" if ram else ""))
        if self.now:
            col = "c" if self.now["model"] == FAST else "m"
            l3 = (f"{C[col]}▶ {self.now['agent']}{C['r']} [{self.now.get('phase','')} {int(time.time()-self.now.get('t_phase', self.now['since']))}s] {self.now['label']}  "
                  f"{C['dim']}{re.sub(chr(10), ' ', self.live_text[-60:])}{C['r']}")
        else:
            l3 = f"{C['dim']}▶ idle / deterministic step{C['r']}"
        return [l1, l2, l3]

    def _draw(self):
        ls = self.lines()
        sys.stdout.write("\n".join(l + "\033[K" for l in ls) + "\n")
        sys.stdout.flush()
        self.drawn = len(ls)

    def _loop(self):
        while not self._stop:
            try:
                with self.lock:
                    if self.tty:
                        if self.drawn:
                            sys.stdout.write(f"\033[{self.drawn}F")
                        self._draw()
                    self.write_status()
            except Exception:
                pass
            try:
                n = self.now
                if n and n.get("phase") in ("prefill", "think"):
                    waited = time.time() - n.get("t_phase", n["since"])
                    if waited > 45 and time.time() - getattr(self, "_hb", 0) > 60:
                        self._hb = time.time()
                        self.event("WAIT", f"{n['model']} still {'reading the prompt' if n['phase']=='prefill' else 'thinking'} "
                                   f"({int(waited)}s, no token yet). Normal on CPU for long prompts; check `ollama ps`.", "y")
            except Exception:
                pass
            time.sleep(0.5)

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop = True
        time.sleep(0.6)

    # ---- status for GUI ----------------------------------------------
    def snapshot(self):
        with self.lock:
            done, likely, worst = self.totals()
            stages = []
            for n in self.STAGES:
                ts = [t for t in self.tasks if t["stage"] == n and t["state"] != "skipped"]
                stages.append(dict(name=n, total=len(ts), done=sum(t["state"] == "done" for t in ts),
                                   state=self.stage_state.get(n, "waiting")))
            pre, dec = self.rate(self.now["model"]) if self.now else self.rate(FAST)
            now = time.time()
            timeline = [dict(s=t["started"] - self.t0, e=(t["ended"] - self.t0) if t["ended"] else None,
                             model=t["model"], stage=t["stage"], label=t["label"])
                        for t in self.tasks if t["started"]][-140:]
            return dict(mode="pdf" if self.pdf_mode else "latex", title=self.title, elapsed=now - self.t0, server_time=now, eta_likely=likely, eta_worst=worst,
                        progress=done / (done + likely) if (done + likely) > 0 else 0, stages=stages,
                        now=self.now, live=self.live_text[-700:], issues=self.issues[-400:], tourn=self.tourn[-60:],
                        log=self.log[-40:], cnt=self.cnt, panel=self.panel, pdf=self.pdf, fast=FAST, deep=DEEP,
                        tps=dec, pre_tps=pre, ram=self._ram(), finished=self.finished, chunks=self.chunks,
                        relations=self.relations, timeline=timeline, geo_ver=self.geo_ver,
                        changes=[{k: v for k, v in c.items() if not k.endswith("_full")} for c in self.changes],
                        suggestions=[{k: v for k, v in s.items() if not k.endswith("_full")} for s in self.suggestions])

    def write_status(self):
        try:
            tmp = self.out / "status.json.tmp"
            tmp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, default=str), "utf-8")
            os.replace(tmp, self.out / "status.json")
        except Exception:
            pass


# ==========================================================================
# LLM wrapper
# ==========================================================================
class LLM:
    def __init__(self, mon, cache, mock=False):
        self.mon, self.cache, self.mock = mon, Path(cache), mock
        self.cache.mkdir(parents=True, exist_ok=True)
        self._client = None

    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=BASE_URL, api_key=os.getenv("OPENAI_API_KEY", "local"), timeout=3600)
        return self._client

    def call(self, model, system, user, max_tokens=2500, as_json=True, label="", temperature=0.1, mock_ctx=None):
        key = hashlib.sha256(json.dumps([model, system, user, max_tokens, as_json, temperature]).encode()).hexdigest()[:24]
        f = self.cache / f"{key}.json"
        if f.exists() and not self.mock:
            self.mon.cnt["cache"] += 1
            return json.loads(f.read_text("utf-8"))["out"]
        out, u = None, user
        for _ in range(3):
            txt = self._gen(model, system, u, max_tokens, temperature, label, mock_ctx)
            txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
            if not as_json:
                out = txt
                break
            out = parse_json(txt)
            if out is not None:
                break
            u = user + "\n\nYour previous reply was not valid JSON. Reply with ONLY one JSON object."
        if out is not None and not self.mock:
            f.write_text(json.dumps({"out": out}), "utf-8")
        return out

    def _gen(self, model, system, user, max_tokens, temperature, label, mock_ctx):
        tin = (len(system) + len(user)) / 3.0
        t0 = time.time()
        self.mon.phase("prefill", tin)
        if self.mock:
            return self._mock(model, label, user, mock_ctx, tin, t0)
        eb = {"reasoning_effort": "none"} if NO_THINK else {}
        if OLLAMA_OPTS:
            eb["options"] = {"num_ctx": CTX}
        kw = {"extra_body": eb} if eb else {}
        parts, ttft, last, nthink = [], None, 0, 0
        try:
            stream = self.client().chat.completions.create(
                model=model, temperature=temperature, max_tokens=max_tokens, stream=True,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user + ("\n/no_think" if NO_THINK else "")}], **kw)
            for ev in stream:
                dl = ev.choices[0].delta if ev.choices else None
                think = (getattr(dl, "reasoning", None) or getattr(dl, "reasoning_content", None)) if dl else None
                if think:
                    if nthink == 0:
                        self.mon.phase("think")
                    nthink += 1
                    if time.time() - last > 0.25:
                        self.mon.live("(thinking) " + str(think)[-200:])
                        last = time.time()
                d = dl.content if dl else None
                if d:
                    if ttft is None:
                        ttft = time.time() - t0
                        self.mon.phase("generate")
                    parts.append(d)
                    if time.time() - last > 0.25:
                        full = "".join(parts)
                        self.mon.live(full[-700:], full)
                        last = time.time()
        except Exception as e:
            self.mon.event("ERROR", f"{type(e).__name__}: {str(e)[:120]} -- is the local server running at {BASE_URL}?", "red")
            raise
        total = time.time() - t0
        if nthink:
            self.mon.event("THINK", f"{model} spent {nthink} tokens thinking; use a non-thinking model/NO_THINK=1", "y")
        self.mon.observe(model, tin, ttft or total, len(parts) + nthink, max(0.01, total - (ttft or 0)))
        return "".join(parts)

    def _mock(self, model, label, user, ctx, tin, t0):
        speed = float(os.getenv("MOCK_SPEED", "1"))
        time.sleep(1.6 / speed)  # prefill: the reading sweep plays here
        self.mon.phase("generate")
        resp = mock_response(label, user, ctx)
        txt = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False)
        cps = (14 if model == FAST else 7) * 4 * speed
        for i in range(0, len(txt), 12):
            time.sleep(12 / cps)
            self.mon.live(txt[: i + 12][-700:], txt[: i + 12])
        total = time.time() - t0
        self.mon.observe(model, tin, 1.6 / speed, len(txt) / 4, max(0.05, total - 1.6 / speed))
        return txt


def mock_response(label, user, ctx):
    chunk = (ctx or {}).get("chunk", "")
    nrel = (ctx or {}).get("nrel", 0)
    cw = "R1" if nrel else ""
    rel = "exceeds the evidence of" if nrel else ""
    body = re.sub(r"^\s*(?:\\(?:section|subsection)\*?\{[^}]*\}|\\begin\{[^}]*\})\s*", "", chunk)
    sents = [s for s in re.split(r"(?<=[.!?])\s+", body) if 25 < len(s) < 260 and not s.lstrip().startswith("\\")]
    bad = [s for s in sents if OVER.search(s)]

    def soften(s):
        s = re.sub(r"\bfeels\b", "appears to exhibit", s)
        s = re.sub(r"\bprove[sd]?\b", "suggest", s)
        s = re.sub(r"\bdemonstrates?\b", "suggests", s)
        s = re.sub(r"\bclearly\b ?", "", s)
        s = re.sub(r"\balways\b", "often", s)
        s = re.sub(r"\bis a breakthrough\b", "may be a useful step", s)
        return s

    def issue(s, sev="high", typ="overclaim", prob="Claim stronger than the evidence supports.", new=None):
        return {"severity": sev, "type": typ, "problem": prob, "quote": s, "compared_with": cw, "relation": rel,
                "fix_old": s, "fix_new": new if new is not None else soften(s), "needs_author": False}

    if label == "map":
        return {"summary": "Mock summary of this chunk.", "claims": [{"text": "mock claim", "kind": "observation", "strength": "weak"}]}
    if label in ("scan", "adj", "attack", "harm", "grw"):
        iss = [issue(s) for s in bad[: (2 if label == "scan" else 1)]]
        if label == "scan":
            others = [s for s in sents if s not in bad and s.startswith("The ")]
            if others:
                iss.append(issue(others[0], "low", "clarity", "Could be more direct.", others[0].replace("The ", "This ", 1)))
            iss.append({"severity": "medium", "type": "unsupported", "problem": "Hallucinated critique with no real quote.",
                        "quote": "this sentence does not exist anywhere in the paper at all", "compared_with": "", "relation": "",
                        "fix_old": "", "fix_new": "", "needs_author": False})
        if label == "adj":
            return {"verdicts": [{"id": c.get("id"), "verdict": "confirm", "severity": c.get("severity"), "problem": c.get("problem"),
                                  "fix_old": c.get("fix_old"), "fix_new": c.get("fix_new"), "needs_author": False}
                                 for c in (ctx or {}).get("cands", [])], "new_issues": []}
        return {"issues": iss}
    if label == "judge":
        m = re.search(r"### A\n(.*?)\n### B\n(.*)", user, re.S)
        a, b = (m.group(1), m.group(2)) if m else ("", "")
        return "B" if len(OVER.findall(b)) < len(OVER.findall(a)) else "A"
    if label == "cross":
        return {"contradictions": [{"severity": "medium", "sections": ["Abstract", "Conclusion"],
                                    "description": "Abstract and conclusion claim more than the results report.", "evidence": []}]}
    if label == "global":
        return {"coherence": [{"severity": "medium", "description": "Conclusion stronger than results.", "suggestion": "Hedge."}],
                "overclaims": ["agent always feels fear"], "unmet_promises": []}
    if label == "panel":
        import random
        return {"scores": {d: {"score": random.randint(4, 7), "why": "mock"} for d in
                           ["soundness", "contribution", "clarity", "evidence_support", "overclaim_risk"]}}
    if label == "exec":
        return "Mock executive summary: claims outran the evidence in several places; those were rewritten and logged."
    return "OK"


# ==========================================================================
# PDF locator with word-level index (PyMuPDF)
# ==========================================================================
PLAIN = False
HEAD_RE = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+)?(abstract|introduction|related work|background|preliminaries|method|methods|"
                     r"methodology|approach|model|experiments?|experimental setup|results|evaluation|analysis|discussion|"
                     r"limitations?|conclusions?|future work|acknowledg\w+|references|bibliography|appendix\w*)\b", re.I)


def pdf_to_secs(pdf):
    """Reading order text of a PDF -> (head, sections, tail) so ANY paper can be reviewed without its LaTeX source."""
    import fitz
    from collections import Counter
    doc = fitz.open(str(pdf))
    def is_heading(t):
        m = HEAD_RE.match(t) if len(t) < 80 else None
        return bool(m) and ((re.match(r"\d", t) and len(t.split()) <= 8) or bool(re.fullmatch(re.escape(m.group(1)) + r"[\s.:]*", t, re.I)))

    texts = []
    for pno, page in enumerate(doc):
        for b in page.get_text("blocks"):
            if b[6] != 0:
                continue
            lines = [l.strip() for l in b[4].split("\n") if l.strip()]
            if len(lines) > 1 and is_heading(lines[0]):          # heading glued to its paragraph
                texts.append((pno, lines[0]))
                lines = lines[1:]
            t = re.sub(r"-\n(?=[a-z])", "", "\n".join(lines))
            t = re.sub(r"\s*\n\s*", " ", t).strip()
            if t:
                texts.append((pno, t))
    cnt = Counter(t for _, t in texts)
    keep = [t for _, t in texts if not re.fullmatch(r"\d+", t) and not (len(t) < 80 and cnt[t] >= 3 and cnt[t] > 0.4 * len(doc))]
    secs, cur = [], {"title": "Front matter", "text": ""}
    for t in keep:
        if is_heading(t):
            secs.append(cur)
            cur = {"title": t.strip(), "text": t.strip() + "\n\n"}
            continue
        cur["text"] += t + "\n\n"
    secs.append(cur)
    return "", [x for x in secs if x["text"].strip()], ""


def plainify(t):
    if PLAIN:
        return re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"(?<!\\)%.*", "", t)
    t = re.sub(r"\\(?:cite|ref|eqref|autoref|cref|label)[a-zA-Z]*\*?(?:\[[^\]]*\])*\{[^}]*\}", "", t)
    for _ in range(3):
        t = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"\\[a-zA-Z]+\*?", " ", t)
    t = t.replace("~", " ").replace("\\%", "%").replace("$", "")
    t = re.sub(r"[{}\\]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def ntok(w):
    return re.sub(r"[^a-z0-9]", "", w.lower())


class PdfLocator:
    def __init__(self, pdf):
        import fitz
        self.doc = fitz.open(str(pdf))
        self.pages = [(p.rect.width, p.rect.height) for p in self.doc]
        self.W, self.T = [], []
        for pno, page in enumerate(self.doc):
            for w in page.get_text("words"):
                n = ntok(w[4])
                if n:
                    self.W.append((pno, w[0], w[1], w[2], w[3]))
                    self.T.append(n)
        self.idx = {}
        for i in range(len(self.T) - 2):
            self.idx.setdefault((self.T[i], self.T[i + 1], self.T[i + 2]), []).append(i)

    def toks(self, plain):
        return [n for n in (ntok(w) for w in plain.split()) if n]

    def find_span(self, toks):
        if len(toks) < 3:
            return None
        for off in range(0, min(len(toks) - 2, 10)):
            cands = self.idx.get(tuple(toks[off: off + 3]))
            if not cands:
                continue
            best = max(cands, key=lambda p: sum(1 for k in range(min(len(toks) - off, 40))
                                                if p + k < len(self.T) and self.T[p + k] == toks[off + k]))
            start = max(0, best - off)
            exp_end = start + len(toks)
            end = min(len(self.T), exp_end)
            tail = self.idx.get(tuple(toks[-3:]), [])
            near = [p + 3 for p in tail if start + len(toks) * 0.5 <= p <= start + len(toks) * 1.7 + 8]
            if near:
                end = min(near, key=lambda e: abs(e - exp_end))
            return (start, max(end, start + 1))
        return None

    def span_of(self, plain):
        return self.find_span(self.toks(plain))

    @staticmethod
    def _r(w):
        return [w[0], round(w[1], 1), round(w[2], 1), round(w[3], 1), round(w[4], 1)]

    def word_rects(self, i, j, cap=1500):
        return [self._r(w) for w in self.W[i:j][:cap]]

    def line_rects(self, i, j):
        out = []
        for w in self.W[i:j]:
            if out and out[-1][0] == w[0] and abs(out[-1][2] - w[2]) < 3 and w[1] >= out[-1][1] - 1:
                out[-1][3] = max(out[-1][3], w[3])
                out[-1][4] = max(out[-1][4], w[4])
            else:
                out.append([w[0], w[1], w[2], w[3], w[4]])
        return [[r[0], round(r[1], 1), round(r[2], 1), round(r[3], 1), round(r[4], 1)] for r in out][:14]

    def rects_fallback(self, plain):
        words = plain.split()
        for n in (10, 6, 4):
            if len(words) < n:
                continue
            probe = " ".join(words[:n])
            for pno, page in enumerate(self.doc):
                rs = page.search_for(probe)
                if rs:
                    return [[pno, round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)] for r in rs[:2]]
        return []

    def endpoint(self, i, which):
        w = self.W[i if which == "start" else max(i, 0)]
        return {"page": w[0], "x0": w[1], "y0": w[2], "x1": w[3], "y1": w[4]}


def compile_pdf(project, tex_name, out_dir, tex_text=None, jobname="paper"):
    if not shutil.which("pdflatex"):
        return None
    build = Path(out_dir) / ("build_" + jobname)
    shutil.copytree(project, build, dirs_exist_ok=True)
    src = f"{jobname}.tex"
    if tex_text is not None:
        (build / src).write_text(tex_text, "utf-8")
    else:
        shutil.copy(build / tex_name, build / src)
    for cmd in (["pdflatex", "-interaction=nonstopmode", src], ["bibtex", jobname],
                ["pdflatex", "-interaction=nonstopmode", src], ["pdflatex", "-interaction=nonstopmode", src]):
        try:
            subprocess.run(cmd, cwd=build, capture_output=True, timeout=300)
        except Exception:
            pass
    pdf = build / f"{jobname}.pdf"
    return pdf if pdf.exists() else None


# ==========================================================================
# Retrieval: what is compared with what
# ==========================================================================
def sentences(plain):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", plain) if 40 <= len(s.strip()) <= 420]


class Retriever:
    def __init__(self, chunks, elog):
        self.units = []
        for c in chunks:
            for s in sentences(plainify(c["text"])):
                self.units.append({"chunk": c["id"], "title": c["title"], "text": s})
        for ln in re.split(r"[\n]+", elog or ""):
            ln = ln.strip(" -*#>\t")
            if len(ln) >= 14:
                self.units.append({"chunk": "LOG", "title": "experimental log", "text": ln})
        df = {}
        toks = []
        for u in self.units:
            ws = {w for w in WORD.findall(u["text"].lower()) if w not in STOP}
            toks.append(ws)
            for w in ws:
                df[w] = df.get(w, 0) + 1
        n = len(self.units) + 1
        self.idf = {w: math.log((n + 1) / (d + 1)) + 1.0 for w, d in df.items()}
        for u, ws in zip(self.units, toks):
            u["vec"] = self.vec(u["text"])
            u["nums"] = {w for w in ws if isnum(w)}

    def vec(self, s):
        cnt = {}
        for w in WORD.findall(s.lower()):
            if w not in STOP:
                cnt[w] = cnt.get(w, 0) + 1
        v = {w: (1 + math.log(c)) * self.idf.get(w, 1.0) * (2.5 if isnum(w) else 1.0) for w, c in cnt.items()}
        nr = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / nr for w, x in v.items()}

    @staticmethod
    def cos(a, b):
        if len(a) > len(b):
            a, b = b, a
        return sum(x * b.get(w, 0.0) for w, x in a.items())

    def relate(self, cid, top=6):
        A = [u for u in self.units if u["chunk"] == cid]
        B = [u for u in self.units if u["chunk"] != cid]
        found = []
        for a in A:
            best_log = best_paper = None
            for b in B:
                sh = a["nums"] & b["nums"]
                sc = self.cos(a["vec"], b["vec"]) + (0.3 if sh else 0.0)
                if not (sc >= SIM_MIN or sh):
                    continue
                if b["chunk"] == "LOG":
                    if best_log is None or sc > best_log[0]:
                        best_log = (sc, b, sh)
                elif best_paper is None or sc > best_paper[0]:
                    best_paper = (sc, b, sh)
            for best in (best_paper, best_log):
                if best:
                    found.append((best[0], a, best[1], best[2]))
        found.sort(key=lambda x: (bool(x[3]), x[0]), reverse=True)
        out, used = [], set()
        for sc, a, b, sh in found:
            if b["text"] in used:
                continue
            used.add(b["text"])
            shared = sorted(set(WORD.findall(a["text"].lower())) & set(WORD.findall(b["text"].lower())) - STOP, key=lambda w: -self.idf.get(w, 1))
            kind = "evidence" if b["chunk"] == "LOG" else ("value" if sh else "concept")
            label = ", ".join(sorted(sh)[:2]) if sh else ", ".join(shared[:2])
            out.append(dict(kind=kind, label=label or "-", score=round(sc, 2), a_text=a["text"], b_text=b["text"],
                            b_chunk=b["chunk"], b_title=b["title"]))
            if len(out) >= top:
                break
        return out

    def echoes(self, text, exclude, k=2):
        v = self.vec(plainify(text))
        sc = [(self.cos(v, u["vec"]), u) for u in self.units if u["chunk"] not in (exclude, "LOG")]
        sc = [x for x in sc if x[0] >= float(os.getenv("ECHO_MIN", "0.30"))]
        sc.sort(key=lambda x: -x[0])
        return [u for _, u in sc[:k]]


def related_block(mon, cid):
    rels = mon.chunk_rels.get(cid, [])
    if not rels:
        return "(none)"
    lines = []
    for k, r in enumerate(rels, 1):
        src = "experimental log" if r["b_chunk"] == "LOG" else f"{r['b_title']} [{r['b_chunk']}]"
        lines.append(f'[R{k}] ({src}) "{r["b_text"][:300]}"  <- related to this chunk\'s: "{r["a_text"][:140]}" ({r["kind"]}: {r["label"]})')
    return "\n".join(lines)


# ==========================================================================
# Grounding / risk
# ==========================================================================
def ground(quote, text):
    q = norm(quote)
    if len(q) < 12:
        return None
    T = norm(text)
    if q in T:
        return q
    best, br = "", 0.0
    for s in re.split(r"(?<=[.!?])\s+", T):
        r = difflib.SequenceMatcher(None, q.lower(), s.lower()).ratio()
        if r > br:
            best, br = s, r
    return best if br >= 0.85 and len(best) < 500 else None


def risk_score(text, n_unbound, has_warn):
    n = max(len(text), 1)
    over, hedge = len(OVER.findall(text)), len(HEDGE.findall(text))
    nd = len(NUM2.findall(text)) / (n / 1000)
    dens = over / (n / 1000)
    r = 0.35 * min(1, dens / 3) + 0.25 * min(1, nd / 6) + 0.2 * min(1, n_unbound / 3) + 0.2 * (1 if has_warn else 0)
    r *= 1 - 0.3 * min(1, hedge / (over + 1))
    return round(min(1.0, r), 3)


# ==========================================================================
# Prompts
# ==========================================================================
SCHEMA = ('{"issues":[{"severity":"high|medium|low","type":"overclaim|unsupported|inconsistency|clarity|latex|other",'
          '"problem":"...","quote":"exact verbatim span <=200 chars","compared_with":"R1..Rn or empty",'
          '"relation":"contradicts|exceeds the evidence of|duplicates|depends on|is undefined in|is consistent with|",'
          '"fix_old":"exact verbatim substring (<=300 chars) or empty","fix_new":"minimal rewrite: SAME numbers, no new citations",'
          '"needs_author":false}]}')

RULES = ("Rules: every issue needs an exact verbatim quote. compared_with = the [Rk] related passage you compared the quote with "
         "(empty if none) and relation = how they relate. A rewrite (fix_old -> fix_new) must keep every number identical, add no "
         "citation/ref/label, keep LaTeX balanced and keep the author's meaning except for removing unsupported strength. "
         "Never rewrite tables, math or numeric results; for those leave fix_old empty.")

SCAN_P = """GLOBAL SKELETON OF THE WHOLE PAPER:
{{SKELETON}}

RELATED PASSAGES (retrieved from other parts of the paper and from the experimental log):
{{RELATED}}

TARGET VENUE: {{VENUE}}. Review ONLY the chunk below (section "{{TITLE}}", part {{PART}}/{{PARTS}}).
Find REAL problems: over-claiming (causal / anthropomorphic / "prove"), claims unsupported by the related evidence, inconsistency with related passages, ambiguity, LaTeX errors.
{{RULES}}
Return JSON {{SCHEMA}}
Max 8 issues, most important first. If the chunk is fine return {"issues":[]}.

CHUNK:
{{TEXT}}"""

ADJ_P = """GLOBAL SKELETON OF THE WHOLE PAPER:
{{SKELETON}}

RELATED PASSAGES:
{{RELATED}}

TARGET VENUE: {{VENUE}}. A fast screener proposed CANDIDATE ISSUES for the chunk below (section "{{TITLE}}").
VERIFY each against the text and the related passages: "confirm" only if the quote really shows the problem, otherwise "refute". Improve the minimal fix. Then add up to 3 important issues the screener missed.
{{RULES}}
CANDIDATES:
{{CANDS}}

Return JSON {"verdicts":[{"id":0,"verdict":"confirm|refute","severity":"high|medium|low","problem":"...","fix_old":"...","fix_new":"...","needs_author":false}],"new_issues":<list of issues in this format: {{ITEM}}>}

CHUNK:
{{TEXT}}"""

ATTACK_P = """GLOBAL SKELETON OF THE WHOLE PAPER:
{{SKELETON}}

RELATED PASSAGES:
{{RELATED}}

You are Reviewer 2 at {{VENUE}}: harsh but fair. Using ONLY the section below (round {{ROUND}}), state the strongest reasons to reject or lower the score of THIS text: overclaiming, weak evidence, ambiguity, undefined terms, claims stronger than the related evidence allows.
{{RULES}}
Top 3 only. If there is no serious problem return {"issues":[]}.
Return JSON {{SCHEMA}}

SECTION:
{{TEXT}}"""

HARM_P = """A claim in this paper was rewritten for scientific accuracy.
OLD: "{{OLD}}"
NEW: "{{NEW}}"
REASON: {{WHY}}
Below is a paragraph from section "{{TITLE}}". Does it repeat, rely on, or contradict the OLD stronger claim? If yes, rewrite the minimum needed so the whole paper stays consistent with the NEW wording. If it is already consistent return {"issues":[]}.
{{RULES}}
Return JSON {{SCHEMA}}

PARAGRAPH:
{{TEXT}}"""

GRW_P = """You are fixing a global coherence problem of a scientific paper.
PROBLEM: {{PROBLEM}}
Below are sections of the paper (abstract / introduction / conclusion and others, current wording). Find the sentences responsible and rewrite them so the paper does not claim more than its results show.
{{RULES}}
Return JSON {{SCHEMA}}

SKELETON:
{{SKELETON}}

SECTIONS:
{{TEXT}}"""

JUDGE_P = """Two versions of a passage from a scientific paper. Choose the better one by: scientific accuracy, no over-claiming, clarity, and preservation of every number and citation.
### A
{{A}}
### B
{{B}}
Answer with exactly one letter: A or B."""

PANEL_P = """You are a {{VENUE}} reviewer. From the excerpt below score the paper 1-10 on: soundness, contribution, clarity, evidence_support, overclaim_risk (1 = none, 10 = severe). Heuristic self-assessment to prioritise revisions, NOT an acceptance prediction.
Return JSON {"scores":{"soundness":{"score":0,"why":"one sentence"},"contribution":{...},"clarity":{...},"evidence_support":{...},"overclaim_risk":{...}}}

SKELETON:
{{SKELETON}}

EXCERPT:
{{TEXT}}"""

ITEM = ('{"severity":"..","type":"..","problem":"..","quote":"..","compared_with":"","relation":"",'
        '"fix_old":"..","fix_new":"..","needs_author":false}')


# ==========================================================================
# Patch decision engine
# ==========================================================================
def latex_ok(old, new):
    pairs = [("{", "}"), ("\\begin", "\\end")]
    d = lambda s, a, b: s.count(a) - s.count(b)
    return all(d(old, a, b) == d(new, a, b) for a, b in pairs) and old.count("$") % 2 == new.count("$") % 2


def necessary(it, sig):
    sev = it.get("severity", "medium")
    det = bool(OVER.search(it.get("fix_old") or "")) or it.get("type") == "latex"
    strong = sig.get("deep") or sig.get("attack") or sig.get("harmonize") or det
    return sev in ("high", "medium") and bool(strong)


def judge(llm, a, b):
    r = llm.call(DEEP, "You are a precise scientific editor.", tpl(JUDGE_P, A=a, B=b), max_tokens=4,
                 as_json=False, label="judge", temperature=0.0, mock_ctx={"chunk": a})
    m = re.search(r"\b([AB])\b", (r or "").upper())
    return m.group(1) if m else "A"


def suggest(mon, iid, cid, it, why_not, state="suggestion"):
    old, new = it.get("fix_old") or "", it.get("fix_new") or ""
    mon.set_issue(iid, state=state, reason=why_not)
    if old and new and old != new:
        mon.suggestions.append(dict(id=len(mon.suggestions), issue=iid, chunk=cid, type=it.get("type"),
                                    severity=it.get("severity"), why=it.get("problem", ""), reason=why_not,
                                    old=old[:400], new=new[:400], old_full=old, new_full=new, state="open"))


def try_patch(llm, mon, work, it, cid, iid, sig):
    """Decide: apply (necessary + safe + wins tournament) or leave as suggestion. Returns (work, applied)."""
    old, new = it.get("fix_old") or "", it.get("fix_new")
    if not old or new is None or old == new:
        mon.set_issue(iid, state="note", reason="observation without a concrete rewrite")
        return work, False
    if it.get("type") == "evidence":
        mon.set_issue(iid, state="note", reason="numbers/evidence are never rewritten automatically")
        return work, False
    if "&" in old or "\\begin" in old or "\\end" in old or "tabular" in old:
        suggest(mon, iid, cid, it, "touches a table/environment: never auto-edited")
        return work, False
    if set(NUM.findall(new)) != set(NUM.findall(old)):
        suggest(mon, iid, cid, it, "rewrite alters numbers")
        return work, False
    if not latex_ok(old, new) or len(new) > 2.2 * len(old) + 80:
        suggest(mon, iid, cid, it, "rewrite breaks LaTeX balance or bloats the text")
        return work, False
    nw, applied, rej = apply_patches(work, [dict(it, needs_author=False)])
    if not applied:
        suggest(mon, iid, cid, it, "guard: " + rej[0].get("reason", ""))
        return work, False
    if mon.suggest_only:
        suggest(mon, iid, cid, it, "suggest-only mode")
        return work, False
    if not necessary(it, sig):
        suggest(mon, iid, cid, it, "not strictly necessary (low severity or single weak signal)")
        return work, False
    idx = work.find(old)
    a = work[max(0, idx - 200): idx + len(old) + 200]
    b = a.replace(old, new, 1)
    r1, r2 = judge(llm, a, b), judge(llm, b, a)
    won = (r1 == "B") and (r2 == "A")
    mon.tourn.append(dict(issue=iid, order1="new" if r1 == "B" else "old", order2="new" if r2 == "A" else "old", won=won))
    if not won:
        suggest(mon, iid, cid, it, "tournament inconclusive (new version did not win in both orders)")
        return work, False
    iss = mon.issues[iid]
    mon.set_issue(iid, state="applied")
    review = bool(it.get("needs_author")) or it.get("type") in ("inconsistency", "unsupported")
    cmp_ = None
    if iss.get("cmp_rel") is not None:
        rr = mon.relations[iss["cmp_rel"]]
        cmp_ = dict(text=rr["b_text"][:160], where=rr["b_title"], relation=iss.get("cmp_relation", ""), kind=rr["kind"])
    mon.changes.append(dict(id=len(mon.changes), issue=iid, chunk=cid, type=it.get("type"), severity=it.get("severity"),
                            why=it.get("problem", ""), old=old[:400], new=new[:400], status="applied", signals=dict(sig),
                            tournament=[mon.tourn[-1]["order1"], mon.tourn[-1]["order2"]], review=review, compared=cmp_,
                            old_full=old, new_full=new))
    mon.cnt["rewritten"] += 1
    ch = mon.chunks.get(cid, {})
    mon.set_chunk(cid, state="rewritten", changes=ch.get("changes", 0) + 1)
    return nw, True


def register(mon, c, text, it, source, state, seen):
    """Ground an issue against `text`; register it with its comparison; returns issue id or None."""
    if not isinstance(it, dict):
        return None
    q = ground(it.get("quote"), text)
    if not q:
        mon.cnt["filtered"] += 1
        return None
    key = norm(q)[:60].lower()
    if key in seen:
        return None
    seen.add(key)
    cmp_rel, cmp_relation = None, ""
    mm = re.match(r"R(\d+)", str(it.get("compared_with") or ""))
    rels = mon.chunk_rels.get(c["id"], [])
    if mm and 0 < int(mm.group(1)) <= len(rels):
        cmp_rel, cmp_relation = rels[int(mm.group(1)) - 1]["id"], str(it.get("relation") or "")
    iid = mon.add_issue(chunk=c["id"], title=c["title"], severity=it.get("severity", "medium"), type=it.get("type", "other"),
                        text=it.get("problem", ""), quote=q[:200], source=source, state=state, grounded=True,
                        cmp_rel=cmp_rel, cmp_relation=cmp_relation)
    mon.locate(f"i{iid}", plainify(q))
    it["quote"] = q
    return iid


def absorb(llm, mon, issues, pool, work, seen, source, sig):
    """Register issues on whichever chunk of `pool` contains their quote, then try to rewrite."""
    n_high = 0
    for it in issues or []:
        if not isinstance(it, dict):
            continue
        host = next((c for c in pool if ground(it.get("quote"), work[c["id"]])), None)
        if host is None:
            mon.cnt["filtered"] += 1
            continue
        iid = register(mon, host, work[host["id"]], it, source, "open", seen[host["id"]])
        if iid is None:
            continue
        if it.get("severity") == "high":
            n_high += 1
        work[host["id"]], _ = try_patch(llm, mon, work[host["id"]], it, host["id"], iid, sig)
    return n_high


# ==========================================================================
# Main
# ==========================================================================
def find_paragraph(raw, plain_sentence):
    probe = norm(plain_sentence)[:40].lower()
    for p in re.split(r"\n\s*\n", raw):
        if probe and probe in norm(plainify(p)).lower():
            return p
    return None


def make_redline(full, changes):
    txt = full
    for ch in changes:
        o, n = ch["old_full"], ch["new_full"]
        if txt.count(o) == 1:
            txt = txt.replace(o, "{\\color{red}\\sout{" + o + "}}{\\color{blue}" + n + "}", 1)
    pre = ("\\usepackage{xcolor}\n\\IfFileExists{ulem.sty}{\\usepackage[normalem]{ulem}}{\\newcommand{\\sout}[1]{[#1]}}\n")
    return txt.replace("\\begin{document}", pre + "\\begin{document}", 1)


def run(a):
    global PLAIN
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "events.jsonl").write_text("", "utf-8")
    mon = Monitor(out)
    mon.suggest_only = a.suggest_only
    llm = LLM(mon, out / ".cache", mock=a.mock)
    pdf_src = getattr(a, "pdf_source", None)
    pdf_mode = bool(pdf_src)
    PLAIN = pdf_mode
    mon.title = f"REVIEW · {Path(pdf_src).name if pdf_mode else Path(a.main).name}" + (" · MOCK" if a.mock else "")

    if a.gui:
        from plocal import review_gui
        port = review_gui.serve(mon, out, a.port)
        mon.event("GUI", f"control room at http://127.0.0.1:{port}", "g")
        if not a.no_browser:
            webbrowser.open(f"http://127.0.0.1:{port}")
    mon.start()

    # ---------------- PARSE ----------------
    mon.stage("PARSE", "running")
    def read_opt(p):
        try:
            return Path(p).read_text("utf-8", errors="ignore") if p and Path(p).exists() else ""
        except Exception:
            return ""

    if pdf_mode:
        mon.pdf_mode = mon.suggest_only = True
        project, tex, bibtext = Path(out), "", ""
    else:
        project = Path(a.project)
        tex = flatten(project / a.main)
        bibtext = "\n".join(p.read_text("utf-8", errors="ignore") for p in project.glob("*.bib"))
    guidelines, elog = read_opt(getattr(a, "guidelines", None)), read_opt(getattr(a, "elog", None))
    instr = (getattr(a, "instructions", "") or "").strip()
    sysmsg = ("You are a rigorous scientific-paper refinement agent.\n=== GUIDELINES ===\n"
              + (guidelines[:int(os.getenv("SYS_GUIDE_CHARS", "2500"))] or "(none provided: use general scientific-writing standards)")
              + ("\n=== EXPERIMENTAL LOG (authoritative) ===\n" + elog[:int(os.getenv("SYS_LOG_CHARS", "1800"))] if elog else "")
              + ("\n=== AUTHOR'S REVIEW INSTRUCTIONS (follow them) ===\n" + instr[:1500] if instr else "")
              + "\nHard rules: never invent or change numbers; never invent citations; quote text VERBATIM; output JSON only.")
    head, secs, tail = pdf_to_secs(pdf_src) if pdf_mode else split_sections(tex)
    reviewable = [s for s in secs if not any(k in s["title"].lower() for k in ("checklist", "references", "bibliography"))]
    chunks = [c for i, s in enumerate(reviewable) for c in chunk_section(s, i + 1)]
    if a.pilot:
        chunks = chunks[: a.pilot]
    mon.event("PARSE", f"{len(secs)} sections -> {len(chunks)} chunks", "g")

    pdf_before = Path(pdf_src) if pdf_mode else (Path(a.pdf) if a.pdf else None)
    if not pdf_before and a.compile and not pdf_mode:
        mon.event("COMPILE", "building baseline PDF (pdflatex)...", "y")
        pdf_before = compile_pdf(project, a.main, out, None, "before")
    if pdf_before and Path(pdf_before).exists():
        (out / "view").mkdir(exist_ok=True)
        shutil.copy(pdf_before, out / "view" / "before.pdf")
        mon.pdf["before"] = True
        try:
            mon.locator = PdfLocator(out / "view" / "before.pdf")
            mon.pages = mon.locator.pages
            mon.event("PDF", f"{len(mon.pages)} pages, {len(mon.locator.W)} words indexed for visual tracking", "g")
        except Exception as e:
            mon.event("PDF", f"PyMuPDF unavailable ({e}); GUI will show status only", "y")
    else:
        mon.event("PDF", "no PDF (use --compile or --pdf file.pdf)", "y")

    undefined = set(k.strip() for r in REF.findall(tex) for k in r.split(",")) - set(LABEL.findall(tex))
    elog_nums = set(NUM2.findall(elog)) if elog else set()
    for c in chunks:
        t = c["text"]
        mon.chunk_text[c["id"]] = t
        unb = [m for m in NUM2.finditer(t) if elog and m.group(0) not in elog_nums and m.group(0) not in elog]
        c["unbound"] = unb
        c["risk"] = risk_score(t, len(unb), any(k in t for k in undefined))
        c["rounds"] = 0 if c["risk"] < 0.35 else min(MAX_ROUNDS, 1 + (c["risk"] > 0.6) + (c["risk"] > 0.8))
        info = dict(title=c["title"], part=c["part"], parts=c["parts"], risk=c["risk"], rounds=c["rounds"],
                    state="pending", changes=0)
        if mon.locator:
            sp = None
            pl = plainify(t)
            toks = mon.locator.toks(pl)
            s1 = mon.locator.find_span(toks[:14]) if len(toks) >= 6 else None
            s2 = mon.locator.find_span(toks[-14:]) if len(toks) >= 6 else None
            if s1 and s2 and s2[1] > s1[0]:
                sp = (s1[0], s2[1])
                info["start"] = mon.locator.endpoint(sp[0], "start")
                info["end"] = mon.locator.endpoint(sp[1] - 1, "end")
                mon.set_geo(f"W:{c['id']}", mon.locator.word_rects(*sp))
        mon.chunks[c["id"]] = info

    # retrieval: what is compared with what
    ret = Retriever(chunks, elog)
    for c in chunks:
        rels = ret.relate(c["id"])
        recs = []
        for r in rels:
            r["id"] = len(mon.relations)
            r["chunk"] = c["id"]
            r["log"] = r["b_chunk"] == "LOG"
            mon.relations.append(r)
            recs.append(r)
            mon.locate(f"ra{r['id']}", r["a_text"])
            if not r["log"]:
                mon.locate(f"rb{r['id']}", r["b_text"])
        mon.chunk_rels[c["id"]] = recs
    mon.event("RETRIEVE", f"{len(mon.relations)} cross-references built (value / concept / evidence links)", "g")

    for c in chunks:
        for m in c["unbound"][:6]:
            ctx = plainify(c["text"][max(0, m.start() - 50): m.end() + 40]).split()
            idx = next((i for i, w in enumerate(ctx) if m.group(0) in w), 0)
            probe = " ".join(ctx[max(0, idx - 3): idx + 4])
            mon.cnt["unbound"] += 1
            iid = mon.add_issue(chunk=c["id"], title=c["title"], severity="medium", type="evidence",
                                text=f"Number {m.group(0)} does not appear in the experimental log. Verify its source.",
                                quote=probe, source="evidence-binding", state="note", grounded=True, cmp_rel=None, cmp_relation="")
            mon.locate(f"i{iid}", probe)
    warnings = [] if pdf_mode else static_checks(secs, bibtext)
    for w in warnings:
        mon.add_issue(chunk="-", title="static", severity="low", type="static", text=w, quote="", source="static",
                      state="note", grounded=True, cmp_rel=None, cmp_relation="")
    mon.event("ANALYZE", f"risk scores done · {mon.cnt['unbound']} unbound numbers · {len(warnings)} static warnings", "g")
    mon.stage("PARSE", "done")

    # ---------------- PLAN ----------------
    for c in chunks:
        c["tid_map"] = mon.add_task("MAP", FAST, len(c["text"]) / 3 + 300, 500, label=f"{c['id']} map")
    for c in chunks:
        c["tid_scan"] = mon.add_task("SCAN", FAST, len(c["text"]) / 3 + 2000, 800, label=f"{c['id']} scan")
    swap_id = mon.add_task("ADJUDICATE", DEEP, sec=SWAP_S, label="model swap") if FAST != DEEP else None
    pb = mon.add_task("GLOBAL", DEEP, 3500, 300, label="panel before")
    for c in chunks:
        c["tid_adj"] = mon.add_task("ADJUDICATE", DEEP, len(c["text"]) / 3 + 3000, 1000, optional=True, label=f"{c['id']} adjudicate")
        c["tid_att"] = [mon.add_task("ATTACK", DEEP, len(c["text"]) / 3 + 2300, 700, optional=True, label=f"{c['id']} attack r{r+1}")
                        for r in range(c["rounds"])]
    harm_pool = [mon.add_task("HARMONIZE", DEEP, 2500, 500, optional=True, label=f"harmonize slot {k+1}")
                 for k in range(max(2, len(chunks) // 2))]
    cross_id = mon.add_task("CROSS", DEEP, 4000, 600, label="cross-section")
    glob_id = mon.add_task("GLOBAL", DEEP, 6000, 600, label="global coherence")
    grw_pool = [mon.add_task("GLOBAL", DEEP, 5000, 700, optional=True, label=f"global rewrite {k+1}") for k in range(3)]
    pa = mon.add_task("GLOBAL", DEEP, 3500, 300, label="panel after")
    patch_id = mon.add_task("PATCH", "-", sec=1, label="apply patches")
    ver_id = mon.add_task("VERIFY", "-", sec=60 if a.compile else 2, label="verify")
    d, lk, wr = mon.totals()
    mon.event("PLAN", f"{len(mon.tasks)} tasks planned · initial ETA ~{fmt(lk)} (worst {fmt(wr)}) · refines as measured", "y")

    def claim(pool, stage, label, tin, tout):
        if pool:
            tid = pool.pop(0)
            mon.byid[tid].update(label=label, tin=tin, tout=tout, optional=False)
            return tid
        return mon.add_task(stage, DEEP, tin, tout, label=label)

    if a.diagnostic:
        mon.event("DIAG", "diagnostic: no LLM calls. Playing a visual tour of the reading/comparison animation...", "g")
        tour(mon, chunks)
        mon.finished = True
        return finish(a, mon, hold=True)

    # ---------------- MAP (FAST) ----------------
    mon.stage("MAP", "running")
    claims_by, summaries = {}, {}
    for c in chunks:
        mon.begin(c["tid_map"], "mapper", FAST, f"{c['id']} {c['title'][:28]}", c["id"])
        r = llm.call(FAST, sysmsg, V1.MAP_PROMPT.format(title=c["title"], part=c["part"], parts=c["parts"], text=c["text"]),
                     1500, label="map", mock_ctx={"chunk": c["text"]}) or {}
        summaries[c["id"]] = r.get("summary", "")
        claims_by[c["id"]] = [f"[{k.get('kind')}] {k.get('text')}" for k in r.get("claims", []) if isinstance(k, dict)]
        mon.set_chunk(c["id"], state="mapped")
        mon.end(c["tid_map"])
    mon.stage("MAP", "done")
    skeleton = "\n".join(f"- {c['id']} {c['title']}: {summaries.get(c['id'], '')}" for c in chunks)

    # ---------------- SCAN (FAST) ----------------
    mon.stage("SCAN", "running")
    seen = {c["id"]: set() for c in chunks}
    work = {c["id"]: c["text"] for c in chunks}
    escalate, cands_of = {}, {}
    for c in chunks:
        mon.begin(c["tid_scan"], "screener", FAST, f"{c['id']} {c['title'][:28]} (risk {c['risk']:.2f})", c["id"])
        r = llm.call(FAST, sysmsg, tpl(SCAN_P, SKELETON=skeleton, RELATED=related_block(mon, c["id"]), VENUE=a.venue,
                                       TITLE=c["title"], PART=c["part"], PARTS=c["parts"], TEXT=c["text"], RULES=RULES, SCHEMA=SCHEMA),
                     2400, label="scan", mock_ctx={"chunk": c["text"], "nrel": len(mon.chunk_rels.get(c["id"], []))})
        cands = []
        for it in ((r or {}).get("issues", []) if r else []):
            iid = register(mon, c, c["text"], it, "screener", "candidate", seen[c["id"]])
            if iid is not None:
                it["_iid"] = iid
                cands.append(it)
        cands_of[c["id"]] = cands
        escalate[c["id"]] = bool(r is None or c["risk"] >= 0.35 or any(i.get("severity") in ("high", "medium") for i in cands)
                                 or (c["risk"] >= 0.2 and not cands))
        if escalate[c["id"]]:
            mon.byid[c["tid_adj"]]["optional"] = False
            mon.cnt["escalated"] += 1
        else:
            mon.byid[c["tid_adj"]]["state"] = "skipped"
        mon.set_chunk(c["id"], state="scanned")
        mon.end(c["tid_scan"])
    mon.stage("SCAN", "done")
    mon.event("SCAN", f"{sum(escalate.values())}/{len(chunks)} chunks escalated to the deep model", "c")

    # ---------------- DEEP phase ----------------
    mon.stage("ADJUDICATE", "running")
    if swap_id is not None:
        mon.begin(swap_id, "loader", DEEP, f"loading {DEEP} (one-time swap)")
        t0 = time.time()
        llm.call(DEEP, "You are a helper.", "Reply OK.", 4, as_json=False, label="warm")
        mon.byid[swap_id]["sec"] = time.time() - t0
        mon.cnt["swaps"] += 1
        mon.end(swap_id)

    def panel(tid, tag, secs_now):
        txt = "\n".join(s["text"] for s in secs_now if any(k in s["title"].lower() for k in
                        ("abstract", "introduction", "conclusion", "discussion")))[:9000]
        mon.begin(tid, "area-chair", DEEP, f"panel {tag}")
        r = llm.call(DEEP, sysmsg, tpl(PANEL_P, VENUE=a.venue, SKELETON=skeleton, TEXT=txt), 900, label="panel")
        mon.end(tid)
        if r and isinstance(r.get("scores"), dict):
            mon.panel[tag] = {k: (v.get("score") if isinstance(v, dict) else v) for k, v in r["scores"].items()}

    panel(pb, "before", secs)

    for c in chunks:
        if not escalate[c["id"]]:
            continue
        cid, cands = c["id"], cands_of[c["id"]]
        mon.begin(c["tid_adj"], "adjudicator", DEEP, f"{cid} {c['title'][:28]} ({len(cands)} candidates)", cid)
        cj = json.dumps([{"id": i["_iid"], "severity": i.get("severity"), "problem": i.get("problem"), "quote": i.get("quote"),
                          "fix_old": i.get("fix_old"), "fix_new": i.get("fix_new")} for i in cands], ensure_ascii=False)
        r = llm.call(DEEP, sysmsg, tpl(ADJ_P, SKELETON=skeleton, RELATED=related_block(mon, cid), VENUE=a.venue, TITLE=c["title"],
                                       CANDS=cj, TEXT=work[cid], RULES=RULES, ITEM=ITEM), 2800, label="adj",
                     mock_ctx={"chunk": work[cid], "cands": [{"id": i["_iid"], **i} for i in cands],
                               "nrel": len(mon.chunk_rels.get(cid, []))}) or {}
        by_id = {i["_iid"]: i for i in cands}
        for v in r.get("verdicts", []):
            if not isinstance(v, dict) or not isinstance(v.get("id"), int) or v["id"] not in by_id:
                continue
            iid, base = v["id"], by_id[v["id"]]
            if v.get("verdict") == "refute":
                mon.set_issue(iid, state="refuted")
                continue
            it = dict(base)
            for k in ("severity", "problem", "fix_old", "fix_new"):
                if v.get(k):
                    it[k] = v[k]
            mon.set_issue(iid, severity=it.get("severity"), text=it.get("problem"), source="adjudicated", state="confirmed")
            work[cid], _ = try_patch(llm, mon, work[cid], it, cid, iid, {"screener": True, "deep": True})
        absorb(llm, mon, r.get("new_issues", []), [c], work, seen, "adjudicator", {"deep": True})
        mon.end(c["tid_adj"])
    mon.stage("ADJUDICATE", "done")

    mon.stage("ATTACK", "running")
    for c in chunks:
        cid = c["id"]
        for r_i, tid in enumerate(c["tid_att"]):
            mon.begin(tid, "reviewer-2", DEEP, f"{cid} {c['title'][:24]} round {r_i+1}/{c['rounds']} (risk {c['risk']:.2f})", cid)
            r = llm.call(DEEP, sysmsg, tpl(ATTACK_P, SKELETON=skeleton, RELATED=related_block(mon, cid), VENUE=a.venue,
                                           ROUND=r_i + 1, TEXT=work[cid], RULES=RULES, SCHEMA=SCHEMA), 2200, label="attack",
                         mock_ctx={"chunk": work[cid], "nrel": len(mon.chunk_rels.get(cid, []))}) or {}
            n_high = absorb(llm, mon, r.get("issues", []), [c], work, seen, f"attack-r{r_i+1}", {"deep": True, "attack": True})
            mon.end(tid)
            mon.set_chunk(cid, state="rewritten" if mon.chunks[cid].get("changes") else "attacked")
            if n_high == 0:
                for rest in c["tid_att"][r_i + 1:]:
                    mon.byid[rest]["state"] = "skipped"
                mon.event("ATTACK", f"{cid}: attack found nothing serious in round {r_i+1} -> converged", "g")
                break
    mon.stage("ATTACK", "done")

    # ---------------- HARMONIZE: ripple every rewritten claim through the paper ----------------
    mon.stage("HARMONIZE", "running")
    ripple = [ch for ch in list(mon.changes) if ch["type"] in ("overclaim", "unsupported", "inconsistency")]
    done_pairs = set()
    for ch in ripple:
        for u in ret.echoes(ch["old_full"], ch["chunk"], k=2):
            host = next((c for c in chunks if c["id"] == u["chunk"]), None)
            if not host or (ch["id"], host["id"], u["text"][:30]) in done_pairs:
                continue
            par = find_paragraph(work[host["id"]], u["text"])
            if not par:
                continue
            done_pairs.add((ch["id"], host["id"], u["text"][:30]))
            tid = claim(harm_pool, "HARMONIZE", f"{host['id']} echo of change #{ch['id']}", len(par) / 3 + 1800, 500)
            mon.begin(tid, "harmonizer", DEEP, f"{host['id']} {host['title'][:22]} · echo of change #{ch['id']}", host["id"])
            r = llm.call(DEEP, sysmsg, tpl(HARM_P, OLD=ch["old_full"][:400], NEW=ch["new_full"][:400], WHY=ch["why"],
                                           TITLE=host["title"], TEXT=par[:2500], RULES=RULES, SCHEMA=SCHEMA), 1400, label="harm",
                         mock_ctx={"chunk": par, "nrel": len(mon.chunk_rels.get(host["id"], []))}) or {}
            absorb(llm, mon, r.get("issues", []), [host], work, seen, f"harmonize<-#{ch['id']}", {"deep": True, "harmonize": True})
            mon.end(tid)
    for tid in harm_pool:
        mon.byid[tid]["state"] = "skipped"
    mon.stage("HARMONIZE", "done")

    # ---------------- CROSS + GLOBAL ----------------
    mon.stage("CROSS", "running")
    led = []
    for c in chunks:
        led.append(f"## {c['id']} {c['title']}")
        led += [f"claim: {x}" for x in claims_by[c["id"]]]
        led += [f"number: {n}" for n in V1.numbers_digest(c)]
    mon.begin(cross_id, "cross-checker", DEEP, "contradictions across sections")
    cross = llm.call(DEEP, sysmsg, V1.CROSS_PROMPT.format(ledger="\n".join(led)[:24000]), 2200, label="cross") or {}
    mon.end(cross_id)
    mon.stage("CROSS", "done")

    mon.stage("GLOBAL", "running")

    def cur(names, n):
        return "\n".join(f"### [{c['id']}] {c['title']}\n{work[c['id']]}" for c in chunks
                         if any(k in c["title"].lower() for k in names))[:n]

    mon.begin(glob_id, "coherence", DEEP, "abstract vs conclusion")
    glob = llm.call(DEEP, sysmsg, V1.GLOBAL_PROMPT.format(skeleton=skeleton, head=cur(("abstract", "introduction"), 8000),
                                                         tail=cur(("conclusion", "discussion", "limitation"), 8000)),
                    1800, label="global") or {}
    mon.end(glob_id)

    problems = []
    for x in cross.get("contradictions", []) or []:
        if isinstance(x, dict):
            problems.append(("cross-section", x.get("severity", "medium"), x.get("description", "")))
    for x in glob.get("coherence", []) or []:
        if isinstance(x, dict):
            problems.append(("coherence", x.get("severity", "medium"), (x.get("description", "") + " -> " + x.get("suggestion", ""))))
    for s in (glob.get("overclaims", []) or [])[:3]:
        problems.append(("overclaim", "high", str(s)))
    for s in (glob.get("unmet_promises", []) or [])[:2]:
        problems.append(("unmet-promise", "medium", str(s)))
    wide = [c for c in chunks if any(k in c["title"].lower() for k in ("abstract", "introduction", "conclusion", "discussion", "limitation"))]
    for kind, sev, desc in problems:
        iid = mon.add_issue(chunk="-", title="global", severity=sev, type=kind, text=desc, quote="", source="global",
                            state="open", grounded=False, cmp_rel=None, cmp_relation="")
        if not wide or not desc:
            mon.set_issue(iid, state="note")
            continue
        tid = claim(grw_pool, "GLOBAL", f"rewrite for: {desc[:40]}", 4500, 700)
        mon.begin(tid, "rewriter", DEEP, f"global rewrite: {desc[:50]}")
        r = llm.call(DEEP, sysmsg, tpl(GRW_P, PROBLEM=desc, SKELETON=skeleton, TEXT=cur(("abstract", "introduction", "conclusion",
                     "discussion", "limitation"), 7000), RULES=RULES, SCHEMA=SCHEMA), 1800, label="grw",
                     mock_ctx={"chunk": "\n".join(work[c["id"]] for c in wide), "nrel": 0}) or {}
        n0 = len(mon.changes)
        absorb(llm, mon, r.get("issues", []), wide, work, seen, f"global-{kind}", {"deep": True, "harmonize": True})
        mon.set_issue(iid, state="addressed" if len(mon.changes) > n0 else "note")
        mon.end(tid)
    for tid in grw_pool:
        mon.byid[tid]["state"] = "skipped"

    # ---------------- PATCH (deterministic, ordered) ----------------
    mon.stage("PATCH", "running")
    mon.begin(patch_id, "patcher", "-", "applying verified rewrites in order")
    full = head + "".join(s["text"] for s in secs) + tail
    refined, final = full, []
    for ch in mon.changes:
        if ch["status"] != "applied":
            final.append(ch)
            continue
        r2, ap, _ = apply_patches(refined, [{"fix_old": ch["old_full"], "fix_new": ch["new_full"], "severity": "high", "needs_author": False}])
        if ap:
            refined = r2
            final.append(ch)
        else:
            ch["status"] = "not-applicable"
            mon.cnt["rewritten"] -= 1
            mon.set_issue(ch["issue"], state="suggestion")
    mon.refined = refined
    if not pdf_mode:
        (out / "refined_flat.tex").write_text(refined, "utf-8")
    mon.end(patch_id)
    mon.stage("PATCH", "done")
    n_ok = sum(1 for c in mon.changes if c["status"] == "applied")
    mon.event("PATCH", f"{n_ok} rewrites applied to the paper (numbers/citations untouched, each won its tournament)", "g")

    # ---------------- VERIFY ----------------
    mon.stage("VERIFY", "running")
    mon.begin(ver_id, "verifier", "-", "recompiling + re-checking")
    if pdf_mode:
        s2, warn_after = secs, []
    else:
        h2, s2, t2 = split_sections(refined)
        warn_after = static_checks(s2, bibtext)
    if a.compile and not pdf_mode:
        pdf_after = compile_pdf(project, a.main, out, refined, "after")
        if pdf_after:
            shutil.copy(pdf_after, out / "view" / "after.pdf")
            mon.pdf["after"] = True
            try:
                la = PdfLocator(out / "view" / "after.pdf")
                for ch in mon.changes:
                    if ch["status"] == "applied":
                        sp = la.span_of(plainify(ch["new_full"]))
                        if sp:
                            mon.set_geo(f"pa{ch['id']}", la.line_rects(*sp))
            except Exception:
                pass
            mon.event("COMPILE", "rewritten PDF built", "g")
        applied_changes = [c for c in mon.changes if c["status"] == "applied"]
        if applied_changes:
            pdf_red = compile_pdf(project, a.main, out, make_redline(full, applied_changes), "redline")
            if pdf_red:
                shutil.copy(pdf_red, out / "view" / "redline.pdf")
                mon.pdf["redline"] = True
                mon.event("COMPILE", "redline PDF (red = removed, blue = added) built", "g")
    mon.end(ver_id)
    panel(pa, "after", s2)

    report(out, mon, warnings, warn_after, cross, glob, llm, sysmsg)
    mon.stage("VERIFY", "done")
    mon.finished = True
    return finish(a, mon, hold=True)


def tour(mon, chunks):
    """Diagnostic visual tour: no LLM, plays the reading sweep + comparison arcs over each chunk."""
    for c in chunks:
        cid = c["id"]
        mon.begin(None, "demo", FAST, f"{cid} {c['title'][:30]} (demo)", cid)
        mon.phase("prefill")
        mon.now["pre_eta"] = 4.0
        time.sleep(4.2)
        mon.phase("generate")
        mon.live("{\"issues\": [{\"severity\": \"high\", \"quote\": \"...\"}]}")
        for r in mon.chunk_rels.get(cid, [])[:3]:
            key = f"L{mon.live_n}"
            mon.live_n += 1
            mon.set_geo(key, mon.geo.get(f"ra{r['id']}", []))
            mon.now["cites"].append({"k": key, "c": r["id"]})
            time.sleep(1.2)
        time.sleep(0.8)
        mon.set_chunk(cid, state="scanned")
        mon.end(None)


def report(out, mon, w_before, w_after, cross, glob, llm, sysmsg):
    applied = [c for c in mon.changes if c["status"] in ("applied", "accepted-by-user")]
    ex = llm.call(DEEP, sysmsg, "Write a concise executive review (max 250 words) for the authors: what was rewritten and why, and "
                  "what still needs their judgement. Data:\n" + json.dumps({"cross": cross, "global": glob, "panel": mon.panel,
                  "changes": [{k: c[k] for k in ("chunk", "type", "why")} for c in applied][:20]}, ensure_ascii=False)[:9000],
                  700, as_json=False, label="exec") or ""
    L = ["# Cascade Reviewer report", "", "## Executive summary", ex, "",
         "## Scores (heuristic self-assessment for prioritisation, NOT an acceptance prediction)", json.dumps(mon.panel, indent=2), "",
         f"## Static warnings: {len(w_before)} before -> {len(w_after)} after", ""] + [f"- {w}" for w in w_after]
    L += ["", "## Run statistics", f"- rewrites applied: {len(applied)}",
          f"- fake critiques filtered by quote grounding: {mon.cnt['filtered']}",
          f"- unbound numbers (not in experimental log): {mon.cnt['unbound']}",
          f"- patch tournaments: {len(mon.tourn)} (won in both orders: {sum(t['won'] for t in mon.tourn)})",
          f"- escalations to deep model: {mon.cnt['escalated']} · swaps: {mon.cnt['swaps']} · tokens: {mon.cnt['tok_out']}"]
    (out / "review_report.md").write_text("\n".join(L), "utf-8")

    C_ = ["# Changes made to the paper (with justification)", ""]
    for c in applied:
        C_ += [f"## Change #{c['id']} · {c['chunk']} · {c['type']} · {c['severity']}" + ("  ⚠ review recommended" if c["review"] else ""),
               f"- **Why:** {c['why']}", f"- **Before:** {c['old_full']}", f"- **After:** {c['new_full']}"]
        if c.get("compared"):
            C_.append(f"- **Compared with ({c['compared']['kind']}, {c['compared']['relation']}):** [{c['compared']['where']}] {c['compared']['text']}")
        C_.append(f"- **Confirmed by:** {', '.join(k for k, v in c['signals'].items() if v)} · tournament: {c['tournament']}")
        C_.append("")
    (out / "changes.md").write_text("\n".join(C_), "utf-8")

    S_ = ["# Suggestions left for the author", ""]
    for s in mon.suggestions:
        S_ += [f"## Suggestion #{s['id']} · {s['chunk']} · {s['type']} · {s['severity']} · {s['state']}",
               f"- **Problem:** {s['why']}", f"- **Not applied because:** {s['reason']}",
               f"- **Before:** {s['old_full']}", f"- **Proposed:** {s['new_full']}", ""]
    for i in mon.issues:
        if i["state"] in ("note", "addressed") and i["source"] != "static":
            S_ += [f"- ({i['severity']}, {i['type']}, {i['source']}) [{i['chunk']}] {i['text']}"]
    (out / "suggestions.md").write_text("\n".join(S_), "utf-8")
    (out / "issues.json").write_text(json.dumps(mon.issues, indent=2, ensure_ascii=False), "utf-8")


def finish(a, mon, hold):
    mon.write_status()
    files = "changes.md, suggestions.md, review_report.md" if getattr(a, "pdf_source", None) else "refined_flat.tex, changes.md, suggestions.md, review_report.md"
    mon.event("DONE", f"outputs in {a.out}: {files}", "g")
    if a.gui and hold and not a.no_hold:
        mon.event("GUI", "dashboard stays up (you can accept suggestions there). Press Ctrl+C to exit.", "dim")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    mon.stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Cascade Reviewer asymmetric-cascade refinement that rewrites the paper")
    ap.add_argument("--project", default=".")
    ap.add_argument("--main", default="main.tex")
    ap.add_argument("--out", default="REVIEW_OUTPUT")
    ap.add_argument("--guidelines", default="")
    ap.add_argument("--instructions", default="", help="your own review instructions (text)")
    ap.add_argument("--pdf-source", dest="pdf_source", default=None, help="review a PDF only (no LaTeX source): suggestions only")
    ap.add_argument("--elog", default="")
    ap.add_argument("--venue", default="a top-tier ML conference (NeurIPS-style)")
    ap.add_argument("--pdf", help="existing compiled PDF to visualise (skips baseline compile)")
    ap.add_argument("--compile", action="store_true", help="compile baseline, rewritten and redline PDFs with pdflatex")
    ap.add_argument("--suggest-only", action="store_true", help="never rewrite; everything becomes a suggestion")
    ap.add_argument("--no-gui", dest="gui", action="store_false")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-hold", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--mock", action="store_true", help="fake LLM, to see the dashboard without a model")
    ap.add_argument("--diagnostic", action="store_true", help="no LLM: parse, retrieval, PDF map, ETA plan + visual tour")
    ap.add_argument("--pilot", type=int, default=0, help="only the first N chunks")
    run(ap.parse_args())
