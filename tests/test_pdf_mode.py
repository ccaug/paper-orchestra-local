"""PDF-only review: any paper can be reviewed without its LaTeX source."""
import os
import subprocess
import sys
from pathlib import Path

import fitz  # PyMuPDF

from plocal import review_engine as E

ROOT = Path(__file__).resolve().parents[1]


def make_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    y = 72

    def block(txt, size=11, h=60):
        nonlocal y
        page.insert_textbox(fitz.Rect(72, y, 520, y + h), txt, fontsize=size)
        y += h + 10

    block("Abstract", 12, 30)
    block("We study curriculum learning for small networks. This clearly proves our method always works on every dataset.", 11, 50)
    block("1 Introduction", 12, 30)
    block("Models are trained on data. Curriculum ordering helps early training. The accuracy was 93.5% in our setting.", 11, 50)
    block("Conclusion", 12, 30)
    block("We conclude that the approach is robust and novel for all tasks.", 11, 30)
    doc.save(str(path))


def test_pdf_to_sections_and_word_location(tmp_path):
    pdf = tmp_path / "p.pdf"
    make_pdf(pdf)
    E.PLAIN = True
    try:
        _, secs, _ = E.pdf_to_secs(pdf)
        titles = [s["title"] for s in secs]
        assert "Abstract" in titles and "1 Introduction" in titles and "Conclusion" in titles
        loc = E.PdfLocator(pdf)
        assert loc.span_of("Curriculum ordering helps early training.")
        assert loc.span_of("this sentence is not in the pdf at all")is None
    finally:
        E.PLAIN = False


def test_pdf_review_cli_produces_suggestions_only(tmp_path):
    pdf = tmp_path / "p.pdf"
    make_pdf(pdf)
    out = tmp_path / "out"
    env = dict(os.environ, MOCK_SPEED="2000")
    r = subprocess.run([sys.executable, str(ROOT / "review_paper.py"), str(pdf), "--mock", "--no-gui", "--no-hold", "--out", str(out)],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    for name in ("suggestions.md", "review_report.md", "changes.md", "issues.json"):
        assert (out / name).exists(), name
    assert not (out / "refined_flat.tex").exists()          # no source -> nothing rewritten


def test_heading_glued_to_its_paragraph_is_still_detected(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(72, 72, 520, 170), "1 Introduction\nModels are trained on data and curriculum helps.\nA second sentence follows here.", fontsize=11)
    page.insert_textbox(fitz.Rect(72, 200, 520, 290), "2 Method\nWe order examples by the trend of their loss over time.", fontsize=11)
    pdf = tmp_path / "g.pdf"
    doc.save(str(pdf))
    E.PLAIN = True
    try:
        titles = [s["title"] for s in E.pdf_to_secs(pdf)[1]]
    finally:
        E.PLAIN = False
    assert "1 Introduction" in titles and "2 Method" in titles
