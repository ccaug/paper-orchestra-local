#!/usr/bin/env python
"""Review / refine ANY paper with local models (Cascade Reviewer engine + control-room GUI).

    python review_paper.py PAPER [options]

PAPER can be:
  * a .tex file                     (project folder = its folder)
  * a folder                        (main .tex is auto-detected)
  * a .zip (e.g. an Overleaf export, extracted automatically)
  * a .pdf                          (no LaTeX source: review-only, every fix becomes a suggestion)

Optional extras (auto-detected next to the paper, or pass them explicitly):
  --elog FILE          experimental results (md/txt/csv/json): numbers in the paper are checked against it
  --guidelines FILE    venue / style guidelines
  --instructions TEXT  your own review focus, or a path to a text file, e.g. "check the statistics and baselines"

Everything is optional: with only the paper it still runs.
"""
import argparse
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:  # optional .env with LLM_BASE_URL, FAST_MODEL, DEEP_MODEL, ...
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

BAD_MAIN = ("supp", "appendix", "response", "rebuttal", "cover", "poster", "slides", "checklist")
EXTRA_NAMES = {"elog": ("experimental_log.md", "results.md", "experiments.md", "experimental_log.txt", "results.txt"),
               "guidelines": ("guidelines.md", "guidelines.txt")}


def find_main(root: Path):
    cands = []
    for p in root.rglob("*.tex"):
        try:
            t = p.read_text("utf-8", errors="ignore")
        except Exception:
            continue
        if "\\documentclass" in t and "\\begin{document}" in t:
            cands.append((p, t))
    if not cands:
        return None
    pref = [c for c in cands if not any(b in c[0].name.lower() for b in BAD_MAIN)] or cands
    for name in ("main.tex", "paper.tex", "ms.tex", "manuscript.tex"):
        for p, _ in pref:
            if p.name.lower() == name:
                return p
    return max(pref, key=lambda c: len(c[1]))[0]


def find_extra(kind, dirs):
    for d in dirs:
        for n in EXTRA_NAMES[kind]:
            if (d / n).exists():
                return d / n
    return None


def as_text(v):
    if not v:
        return ""
    p = Path(v)
    return p.read_text("utf-8", errors="ignore") if p.exists() and p.is_file() else v


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paper", help=".tex file, folder, .zip or .pdf")
    ap.add_argument("--main", help="main .tex file name if auto-detection picks the wrong one")
    ap.add_argument("--elog", help="experimental results file (md/txt/csv/json)")
    ap.add_argument("--guidelines", help="venue/style guidelines file")
    ap.add_argument("--instructions", default="", help="your review focus (text or path to a text file)")
    ap.add_argument("--venue", default="a top-tier conference or journal in the paper's field")
    ap.add_argument("--out", help="output folder (default: REVIEWS/<paper name>)")
    ap.add_argument("--pdf", help="existing compiled PDF to visualise (LaTeX mode)")
    ap.add_argument("--no-compile", action="store_true", help="do not compile PDFs with pdflatex")
    ap.add_argument("--suggest-only", action="store_true", help="never rewrite; everything becomes a suggestion")
    ap.add_argument("--fast-model", help="fast screener model (default qwen3:4b)")
    ap.add_argument("--deep-model", help="deep adjudicator model (default qwen3:8b)")
    ap.add_argument("--ctx", type=int, help="context window sent to Ollama (default 16384)")
    ap.add_argument("--pilot", type=int, default=0, help="only the first N chunks (quick test)")
    ap.add_argument("--diagnostic", action="store_true", help="no model: parse, retrieval, PDF map, ETA + visual tour")
    ap.add_argument("--mock", action="store_true", help="fake model, to see the dashboard")
    ap.add_argument("--no-gui", dest="gui", action="store_false")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-hold", action="store_true", help="exit when finished instead of keeping the GUI open")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()

    if a.fast_model:
        os.environ["FAST_MODEL"] = a.fast_model
    if a.deep_model:
        os.environ["DEEP_MODEL"] = a.deep_model
    if a.ctx:
        os.environ["LLM_CTX"] = str(a.ctx)

    src = Path(a.paper).expanduser()
    if not src.exists():
        sys.exit(f"Not found: {src}")
    stem = src.stem if src.is_file() else src.name
    out = Path(a.out) if a.out else Path("REVIEWS") / stem
    out.mkdir(parents=True, exist_ok=True)

    from plocal import review_engine
    ns = dict(project=".", main="main.tex", out=str(out), guidelines="", elog="", instructions=as_text(a.instructions),
              venue=a.venue, pdf=a.pdf, pdf_source=None, compile=not a.no_compile, suggest_only=a.suggest_only, gui=a.gui,
              no_browser=a.no_browser, no_hold=a.no_hold, port=a.port, mock=a.mock, diagnostic=a.diagnostic, pilot=a.pilot)

    if src.is_file() and src.suffix.lower() == ".pdf":
        print(f"[input] PDF only -> review mode (no source to rewrite; fixes become suggestions): {src.name}")
        ns.update(pdf_source=str(src), compile=False, suggest_only=True)
        extra_dirs = [src.parent]
    else:
        if src.is_file() and src.suffix.lower() == ".zip":
            dest = out / "_source"
            if dest.exists():
                shutil.rmtree(dest)
            with zipfile.ZipFile(src) as z:
                z.extractall(dest)
            root = dest
        elif src.is_file() and src.suffix.lower() == ".tex":
            root = src.parent
        elif src.is_dir():
            root = src
        else:
            sys.exit("PAPER must be a .tex, a folder, a .zip or a .pdf")
        if src.is_file() and src.suffix.lower() == ".tex":
            main_tex = src
        elif a.main:
            cand = list(root.rglob(a.main))
            main_tex = cand[0] if cand else None
        else:
            main_tex = find_main(root)
        if not main_tex:
            sys.exit("No main .tex (with \\documentclass and \\begin{document}) found. Use --main NAME.tex")
        print(f"[input] LaTeX project: {main_tex.parent} · main file: {main_tex.name}")
        ns.update(project=str(main_tex.parent), main=main_tex.name)
        extra_dirs = [main_tex.parent, main_tex.parent.parent, root]

    elog = a.elog or find_extra("elog", extra_dirs)
    guide = a.guidelines or find_extra("guidelines", extra_dirs)
    ns.update(elog=str(elog) if elog else "", guidelines=str(guide) if guide else "")
    print(f"[input] results log: {elog or 'none (number checking disabled)'} · guidelines: {guide or 'none (general standards)'}"
          + (" · custom instructions: yes" if a.instructions else ""))
    print(f"[output] {out}")
    review_engine.run(argparse.Namespace(**ns))


if __name__ == "__main__":
    main()
