import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_local_chat_roundtrip(fake_llm):
    from plocal import llm_shim
    llm_shim.BASE_URL, llm_shim._client = fake_llm, None
    assert llm_shim.local_chat("hello there") == "OK"


def test_shim_parses_json_like_gemini_parsers(fake_llm):
    from plocal import llm_shim
    llm_shim.BASE_URL, llm_shim._client = fake_llm, None
    res = llm_shim.shim_contents(["Please produce the plotting_plan and the rest of the outline."])
    assert "plotting_plan" in res["parsed_response"] and "section_plan" in res["parsed_response"]


def test_offline_literature_assigns_every_reference_and_never_invents_any():
    import fake_local_llm
    from plocal.literature_local import LocalLiteratureAgent
    agent = object.__new__(LocalLiteratureAgent)
    agent.library_path = str(ROOT / "examples/dummy_paper/raw_materials/local_literature.json")
    papers, tags = agent._load_library()
    known = {p.citation_key for p in papers}
    outline = agent._assign(copy.deepcopy(fake_local_llm.OUTLINE), papers, tags)
    assigned = set(outline["intro_related_work_plan"]["introduction_strategy"]["citation_candidates"])
    for sub in outline["intro_related_work_plan"]["related_work_strategy"]["subsections"]:
        assigned |= set(sub["citation_candidates"])
    for sec in outline["section_plan"]:
        for sub in sec["subsections"]:
            assigned |= set(sub["citation_candidates"])
    assert assigned <= known                 # nothing outside the curated library
    assert known <= assigned                 # every library reference is citable somewhere


def test_library_is_valid_json_with_required_fields():
    lib = json.load(open(ROOT / "examples/dummy_paper/raw_materials/local_literature.json", encoding="utf-8"))
    assert len(lib) >= 5
    for p in lib:
        assert p["title"] and p["authors"] and p["year"] and p["abstract"]
