#!/usr/bin/env python
"""Write a paper from ideas + results with a LOCAL model (no Gemini/OpenAI key).

Stages (PaperOrchestra's own agents, redirected to your local server):
  1 outline            OutlineAgent
  2 literature         LocalLiteratureAgent  (offline, curated library, never invents references)
  3 section writing    SectionWritingAgent   (full LaTeX draft, tables from the log, figures)
  4 compile            pdflatex + bibtex (preamble auto-fixed if the model forgot packages)
  5 refine (optional)  Cascade Reviewer (rewrites with justification, shows a live GUI)  (--refine)

Resumable: --resume skips stages whose output already exists.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:  # optional .env with LLM_BASE_URL, FAST_MODEL, ...
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass


def banner(n, total, title):
    print("\n" + "=" * 78 + f"\n[{n}/{total}] {title}\n" + "=" * 78, flush=True)


def fix_preamble(tex):
    """Local models often forget packages. Add the usual ones only if the body uses them."""
    need = []
    if "\\includegraphics" in tex and "{graphicx}" not in tex:
        need.append("\\usepackage{graphicx}")
    if re.search(r"\\(toprule|midrule|bottomrule)", tex) and "{booktabs}" not in tex:
        need.append("\\usepackage{booktabs}")
    if re.search(r"\\begin\{(align|equation\*|gather)", tex) and "{amsmath}" not in tex:
        need.append("\\usepackage{amsmath}")
    if need and "\\begin{document}" in tex:
        tex = tex.replace("\\begin{document}", "\n".join(need) + "\n\\begin{document}", 1)
        print("  [fix] added missing packages:", ", ".join(n[12:-1] for n in need))
    return tex


def compile_tex(workdir, name):
    if not shutil.which("pdflatex"):
        print("  [compile] pdflatex not found (install MiKTeX/TeX Live). The .tex is still saved.")
        return None
    stem = name[:-4]
    cmds = [["pdflatex", "-interaction=nonstopmode", name], ["bibtex", stem],
            ["pdflatex", "-interaction=nonstopmode", name], ["pdflatex", "-interaction=nonstopmode", name]]
    for c in cmds:
        try:
            subprocess.run(c, cwd=workdir, capture_output=True, timeout=300)
        except Exception as e:
            print("  [compile] ", c[0], "failed:", e)
    pdf = Path(workdir) / f"{stem}.pdf"
    if not pdf.exists():
        log = Path(workdir) / f"{stem}.log"
        if log.exists():
            errs = [l for l in log.read_text("utf-8", errors="ignore").splitlines() if l.startswith("!")][:5]
            print("  [compile] FAILED. First LaTeX errors:\n    " + "\n    ".join(errs))
        return None
    return pdf


def stage(fn, name, out_file, resume, tries=2):
    if resume and out_file and Path(out_file).exists():
        print(f"  (resume) '{name}' already done -> skipping")
        return True
    for k in range(tries):
        t0 = time.time()
        try:
            fn()
            print(f"  ✓ {name} finished in {time.time() - t0:.0f}s")
            return True
        except Exception:
            print(f"  ✗ {name} failed (attempt {k + 1}/{tries}):\n" + traceback.format_exc()[-1500:])
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw_materials_dir", default="examples/dummy_paper/raw_materials")
    ap.add_argument("--latex_template_dir", default="templates/iclr2025")
    ap.add_argument("--output_dir", default="outputs/dummy_paper")
    ap.add_argument("--idea_filename", default="idea_sparse.md")
    ap.add_argument("--experimental_log_filename", default="experimental_log.md")
    ap.add_argument("--library", default="local_literature.json", help="curated references, inside raw_materials")
    ap.add_argument("--model", default=os.getenv("LOCAL_LLM_MODEL", "qwen3:8b"))
    ap.add_argument("--base_url", default=os.getenv("LLM_BASE_URL") or os.getenv("LOCAL_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "http://localhost:11434/v1")
    ap.add_argument("--ctx", type=int, default=int(os.getenv("LOCAL_LLM_CTX", "16384")))
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--refine", action="store_true", help="run the Cascade Reviewer (cascade + GUI) on the draft")
    ap.add_argument("--refine-mock", action="store_true", help="refinement with the fake model (GUI demo)")
    ap.add_argument("--headless", action="store_true", help="refinement without GUI/browser (exits when done)")
    args = ap.parse_args()

    os.environ["LOCAL_LLM_MODEL"], os.environ["LOCAL_LLM_BASE_URL"], os.environ["LOCAL_LLM_CTX"] = args.model, args.base_url, str(args.ctx)
    from plocal import llm_shim as local_llm_shim
    local_llm_shim.install(args.model, args.base_url, args.ctx)
    from methods.agents.outline_agent import OutlineAgent
    from methods.agents.section_writing_agent import SectionWritingAgent
    from plocal.literature_local import LocalLiteratureAgent

    base = Path(args.output_dir)
    base.mkdir(parents=True, exist_ok=True)
    raw = base / "raw_materials"
    if raw.resolve() != Path(args.raw_materials_dir).resolve():
        shutil.copytree(args.raw_materials_dir, raw, dirs_exist_ok=True)
    work = base / "latex_writeup"
    if work.exists() and not args.resume:
        shutil.rmtree(work)
    shutil.copytree(args.latex_template_dir, work, dirs_exist_ok=True)
    shutil.copytree(raw / "figures", work / "figures", dirs_exist_ok=True)
    guidelines, template = work / "guidelines.md", work / "template.tex"
    idea, elog = raw / args.idea_filename, raw / args.experimental_log_filename
    for f in (guidelines, template, idea, elog, raw / args.library, work / "figures" / "info.json"):
        if not Path(f).exists():
            sys.exit(f"Missing required file: {f}")

    total = 5 if args.refine else 4
    t_all = time.time()
    outline = base / "outline.json"
    lit_dir = base / "literature_agent_output"

    banner(1, total, f"OUTLINE  (local model: {args.model})")
    ok = stage(lambda: OutlineAgent(model_name=args.model, cutoff_date="2026-11").run(
        idea_file=str(idea), experimental_log_file=str(elog), latex_template_file=str(template),
        conference_guidelines_file=str(guidelines), output_filepath=str(outline)), "outline", outline, args.resume)
    if not ok:
        sys.exit("Outline failed. Try a larger model or --ctx 32768.")

    banner(2, total, "LITERATURE  (offline, curated library)")

    def lit():
        agent = LocalLiteratureAgent(idea_path=str(idea), experimental_log_path=str(elog), latex_template_path=str(template),
                                     conference_guidelines_path=str(guidelines), output_dir=str(lit_dir), model_name=args.model,
                                     max_workers=1, library_path=str(raw / args.library))
        agent.run(outline_path=str(outline), cutoff_date="2026-11")
        shutil.copy(lit_dir / "outline_v1.json", outline)
        shutil.copy(lit_dir / "updated_template.tex", template)
        shutil.copy(lit_dir / "references.bib", work / "references.bib")

    if not stage(lit, "literature", lit_dir / "citation_map.json", args.resume):
        sys.exit("Literature stage failed.")
    if args.resume and (lit_dir / "updated_template.tex").exists():
        shutil.copy(lit_dir / "updated_template.tex", template)
        shutil.copy(lit_dir / "references.bib", work / "references.bib")

    banner(3, total, "SECTION WRITING  (full LaTeX draft)")
    draft = work / "raw_draft_paper.tex"

    def write():
        SectionWritingAgent(outline_path=str(outline), template_path=str(template), idea_path=str(idea),
                            experimental_log_path=str(elog), citation_map_path=str(lit_dir / "citation_map.json"),
                            figures_info_path=str(work / "figures" / "info.json"), guidelines_path=str(guidelines),
                            model_name=args.model).run(output_path=str(draft))

    if not stage(write, "section writing", draft, args.resume):
        sys.exit("Section writing failed.")
    draft.write_text(fix_preamble(draft.read_text("utf-8")), "utf-8")

    banner(4, total, "COMPILE")
    pdf = compile_tex(work, "raw_draft_paper.tex")
    if pdf:
        shutil.copy(pdf, base / "draft_paper.pdf")
        print(f"  ✓ PDF: {base / 'draft_paper.pdf'}")
    print(f"  LaTeX draft: {draft}")

    if args.refine:
        banner(5, total, "REFINE  (Cascade Reviewer: cascade, rewriting, GUI)")
        from plocal import review_engine
        ns = argparse.Namespace(project=str(work), main="raw_draft_paper.tex", out=str(base / "refine"),
                                guidelines=str(guidelines), elog=str(elog), venue="a top-tier ML conference (ICLR-style)",
                                pdf=str(pdf) if pdf else None, compile=True, suggest_only=False, gui=not args.headless, no_browser=False,
                                no_hold=args.headless, port=8765, mock=args.refine_mock, diagnostic=False, pilot=0)
        review_engine.run(ns)

    print(f"\nAll done in {(time.time() - t_all) / 60:.1f} min · LLM calls: {local_llm_shim.STATS['calls']} · "
          f"tokens: {local_llm_shim.STATS['tokens']}")


if __name__ == "__main__":
    main()
