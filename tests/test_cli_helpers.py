import review_paper
import write_paper_local


def test_find_main_prefers_the_real_paper(tmp_path):
    (tmp_path / "supplementary.tex").write_text("\\documentclass{article}\\begin{document}S\\end{document}")
    (tmp_path / "paper.tex").write_text("\\documentclass{article}\\begin{document}" + "text " * 50 + "\\end{document}")
    (tmp_path / "sec.tex").write_text("just a section file")
    assert review_paper.find_main(tmp_path).name == "paper.tex"


def test_find_main_returns_none_without_a_document(tmp_path):
    (tmp_path / "a.tex").write_text("no document class")
    assert review_paper.find_main(tmp_path) is None


def test_fix_preamble_adds_only_missing_packages():
    tex = "\\documentclass{article}\n\\begin{document}\n\\includegraphics{x}\n\\toprule\n\\end{document}"
    fixed = write_paper_local.fix_preamble(tex)
    assert "\\usepackage{graphicx}" in fixed and "\\usepackage{booktabs}" in fixed
    already = "\\usepackage{graphicx}\n" + tex
    assert write_paper_local.fix_preamble(already).count("graphicx") == 1
