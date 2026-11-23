"""End-to-end dry runs: the real CLIs against a fake model server. They check the plumbing, not the writing quality."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.e2e


def run(args, env_extra, timeout=300):
    env = dict(os.environ, MOCK_SPEED="2000", **env_extra)
    return subprocess.run([sys.executable] + args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)


def test_write_paper_with_fake_local_model(fake_llm, tmp_path):
    out = tmp_path / "out"
    r = run(["write_paper_local.py", "--model", "fake-local", "--ctx", "8192", "--output_dir", str(out)], {"LOCAL_LLM_BASE_URL": fake_llm})
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    tex = (out / "latex_writeup" / "raw_draft_paper.tex").read_text("utf-8")
    assert "\\citep{" in tex and "\\includegraphics" in tex
    assert (out / "outline.json").exists() and (out / "literature_agent_output" / "citation_map.json").exists()
    if shutil.which("pdflatex"):
        assert (out / "draft_paper.pdf").exists()


def test_write_then_review_chain_headless(fake_llm, tmp_path):
    out = tmp_path / "out"
    r = run(["write_paper_local.py", "--model", "fake-local", "--ctx", "8192", "--output_dir", str(out),
             "--refine", "--refine-mock", "--headless"], {"LOCAL_LLM_BASE_URL": fake_llm}, timeout=420)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    for name in ("changes.md", "suggestions.md", "review_report.md", "issues.json"):
        assert (out / "refine" / name).exists(), name


def test_review_a_latex_project_without_any_optional_files(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n\\begin{abstract}\nWe show our method clearly demonstrates a novel effect with 93.5\\% accuracy.\n"
        "\\end{abstract}\n\\section{Introduction}\nThis proves the approach always works. We then evaluate it carefully on one benchmark dataset.\n"
        "\\section{Conclusion}\nWe conclude that the method is a breakthrough.\n\\end{document}\n")
    out = tmp_path / "out"
    r = run(["review_paper.py", str(proj), "--mock", "--no-gui", "--no-hold", "--no-compile", "--out", str(out)], {})
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert (out / "changes.md").exists() and (out / "refined_flat.tex").exists()
    refined = (out / "refined_flat.tex").read_text("utf-8")
    assert "93.5" in refined                                  # numbers are never changed
