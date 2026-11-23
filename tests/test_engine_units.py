"""Deterministic building blocks of the review engine: no model, no network."""
import copy

from plocal import review_engine as E
from plocal.review_basics import apply_patches


def ctx(tmp_path):
    mon = E.Monitor(tmp_path)
    llm = E.LLM(mon, tmp_path / "cache", mock=True)
    mon.chunks["S1.1"] = {"title": "t", "risk": 0.5}
    return mon, llm


def new_issue(mon, severity="high", typ="overclaim"):
    return mon.add_issue(chunk="S1.1", title="t", severity=severity, type=typ, text="p", quote="q", source="screener",
                         state="candidate", grounded=True, cmp_rel=None, cmp_relation="")


def patch(old, new, severity="high", typ="overclaim"):
    return {"fix_old": old, "fix_new": new, "severity": severity, "type": typ, "problem": "x", "needs_author": False}


# ---------- grounding ----------
def test_ground_exact_fuzzy_and_hallucination():
    text = "We train the model. This clearly demonstrates the effect of pacing. Done."
    assert E.ground("This clearly demonstrates the effect of pacing.", text)
    assert E.ground("This clearly demonstrates the effect of pacing", text)           # missing final period
    assert E.ground("this sentence is nowhere in the paper at all really", text) is None
    assert E.ground("short", text) is None


# ---------- safety guards of the rewrite engine ----------
def test_necessary_rewrite_is_applied(tmp_path, mock_fast):
    mon, llm = ctx(tmp_path)
    work = "Intro text here. This clearly demonstrates the effect. Outro text here."
    iid = new_issue(mon)
    out, ok = E.try_patch(llm, mon, work, patch("This clearly demonstrates the effect.", "This suggests the effect."),
                          "S1.1", iid, {"deep": True})
    assert ok and "suggests the effect" in out and "clearly" not in out
    assert mon.issues[iid]["state"] == "applied" and len(mon.changes) == 1
    assert mon.changes[0]["tournament"] == ["new", "new"]          # won in both orders


def test_rewrite_that_changes_numbers_becomes_suggestion(tmp_path, mock_fast):
    mon, llm = ctx(tmp_path)
    work = "Accuracy reached 93.5 percent in our experiments."
    iid = new_issue(mon)
    out, ok = E.try_patch(llm, mon, work, patch("Accuracy reached 93.5 percent", "Accuracy reached 94.0 percent"), "S1.1", iid, {"deep": True})
    assert not ok and out == work
    assert mon.issues[iid]["state"] == "suggestion" and "numbers" in mon.issues[iid]["reason"]
    assert mon.suggestions and not mon.changes


def test_low_severity_is_only_a_suggestion(tmp_path, mock_fast):
    mon, llm = ctx(tmp_path)
    work = "The method is simple and the results are good."
    iid = new_issue(mon, "low", "clarity")
    out, ok = E.try_patch(llm, mon, work, patch("The method is simple", "This method is simple", "low", "clarity"), "S1.1", iid, {"deep": True})
    assert not ok and out == work and mon.issues[iid]["state"] == "suggestion"


def test_tables_are_never_edited(tmp_path, mock_fast):
    mon, llm = ctx(tmp_path)
    work = "a & b \\\\ c & d \\\\"
    iid = new_issue(mon)
    out, ok = E.try_patch(llm, mon, work, patch("a & b", "a & e"), "S1.1", iid, {"deep": True})
    assert not ok and out == work


def test_patch_that_does_not_win_the_tournament_is_not_applied(tmp_path, mock_fast):
    mon, llm = ctx(tmp_path)
    work = "This suggests the effect."
    iid = new_issue(mon)
    out, ok = E.try_patch(llm, mon, work, patch("This suggests the effect.", "This clearly proves the effect."), "S1.1", iid, {"deep": True})
    assert not ok and out == work and "tournament" in mon.issues[iid]["reason"]


def test_suggest_only_mode_never_rewrites(tmp_path, mock_fast):
    mon, llm = ctx(tmp_path)
    mon.suggest_only = True
    work = "This clearly demonstrates the effect."
    iid = new_issue(mon)
    out, ok = E.try_patch(llm, mon, work, patch("This clearly demonstrates the effect.", "This suggests the effect."), "S1.1", iid, {"deep": True})
    assert not ok and out == work and mon.suggestions


def test_apply_patches_blocks_new_citations_and_duplicates():
    text = "Claim one. Claim one."
    _, applied, rejected = apply_patches(text, [patch("Claim one.", "Claim 1.")])
    assert not applied and "found 2 times" in rejected[0]["reason"]
    _, applied, rejected = apply_patches("A sentence.", [patch("A sentence.", "A sentence \\cite{new}.")])
    assert not applied and "citation" in rejected[0]["reason"]


def test_latex_balance_and_necessity_rules():
    assert E.latex_ok("see \\textbf{x}", "see \\emph{x}")
    assert not E.latex_ok("see \\textbf{x}", "see \\textbf{x")
    assert E.necessary({"severity": "high", "fix_old": "x"}, {"deep": True})
    assert not E.necessary({"severity": "low", "fix_old": "x"}, {"deep": True})
    assert not E.necessary({"severity": "high", "fix_old": "plain words"}, {"screener": True})   # single weak signal


# ---------- text utilities ----------
def test_plainify_strips_latex_but_keeps_percent():
    assert E.plainify(r"\textbf{Hello}~world \cite{a} 93.5\% % a comment") == "Hello world 93.5%"


def test_risk_score_ranks_overclaims_higher():
    over = "We prove this clearly. It demonstrates a breakthrough and always works. Robust and novel."
    hedged = "Results may suggest an effect. This is preliminary and consistent with prior work."
    assert E.risk_score(over, 0, False) > E.risk_score(hedged, 0, False)
    assert E.risk_score(hedged, 3, True) > E.risk_score(hedged, 0, False)


def test_retriever_links_shared_numbers_and_the_log():
    chunks = [
        {"id": "S1.1", "title": "Intro", "text": "The death rate of the primary condition was 0.950 in our experiments with agents across seeds."},
        {"id": "S2.1", "title": "Results", "text": "We report a death rate of 0.950 for the primary condition across all seeds in the main table."},
    ]
    ret = E.Retriever(chunks, "Primary condition death rate 0.950 over ten seeds.")
    rels = ret.relate("S1.1")
    assert rels and any(r["kind"] == "value" and "0.950" in r["label"] for r in rels)
    assert {r["b_chunk"] for r in rels} <= {"S2.1", "LOG"}
