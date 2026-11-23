"""Offline literature agent for PaperOrchestra.

The original HybridLiteratureAgent needs Google Search (Gemini tool) + Semantic Scholar. This one reads a
curated local library (raw_materials/local_literature.json), assigns each reference to the outline slots
where it fits (keyword overlap, many-to-many), then reuses the ORIGINAL BibTeX/citation-map/synthesis code.
It NEVER invents references: only entries you put in the library can be cited.
"""
import json
import math
import os
import re

from methods.agents.literature_review_agent import HybridLiteratureAgent, PaperData

STOP = set("the of and to in a is that for with on as by we this are be it from an at or which our can not have "
           "has was were their these those also more than using used use paper results find identify search "
           "papers research introducing establishing highly cited".split())


def toks(s):
    return {w for w in re.findall(r"[a-z0-9]{3,}", (s or "").lower()) if w not in STOP}


def key_of(p):
    if p.get("citation_key"):
        return p["citation_key"]
    last = re.sub(r"[^A-Za-z]", "", p["authors"][0].split()[-1]).capitalize() if p.get("authors") else "Unknown"
    words = [w.capitalize() for w in re.findall(r"[A-Za-z0-9]+", p["title"]) if w.lower() not in STOP][:2]
    return f"{last}{p.get('year', '')}{''.join(words)}"


class LocalLiteratureAgent(HybridLiteratureAgent):
    def __init__(self, *a, library_path=None, **kw):
        super().__init__(*a, **kw)
        self.library_path = library_path

    def _load_library(self):
        with open(self.library_path, "r", encoding="utf-8") as f:
            lib = json.load(f)
        papers = []
        for p in lib:
            papers.append(PaperData(
                citation_key=key_of(p), title=p["title"], authors=p.get("authors", []), venue=p.get("venue", ""),
                year=int(p.get("year", 2024)), abstract=p.get("abstract", ""), citation_count=int(p.get("citation_count", 0)),
                found_in_section="local library", reason=p.get("note", "curated local library"),
                journal=p.get("journal"), volume=p.get("volume"), pages=p.get("pages")))
        tags = {key_of(p): " ".join(p.get("tags", [])) for p in lib}
        return papers, tags

    def _score(self, slot_text, paper, tags):
        a = toks(slot_text)
        b = toks(paper.title + " " + paper.abstract + " " + tags.get(paper.citation_key, ""))
        return len(a & b) / (math.sqrt(len(a)) + 1.0)

    def _pick(self, slot_text, papers, tags, n):
        sc = sorted(((self._score(slot_text, p, tags), p) for p in papers), key=lambda x: -x[0])
        return [p.citation_key for s, p in sc[:n] if s > 0]

    def _assign(self, outline, papers, tags):
        plan = outline.get("intro_related_work_plan", {})
        used = set()
        intro = plan.get("introduction_strategy", {})
        itext = " ".join([intro.get("hook_hypothesis", ""), intro.get("problem_gap_hypothesis", "")]
                         + list(intro.get("search_directions", [])))
        intro["citation_candidates"] = self._pick(itext, papers, tags, 6)
        used.update(intro["citation_candidates"])
        rw = plan.get("related_work_strategy", {})
        subs = rw.get("subsections", [])
        for sub in subs:
            text = " ".join([sub.get("subsection_title", ""), sub.get("methodology_cluster", ""), sub.get("sota_investigation_mission", ""),
                             sub.get("limitation_hypothesis", ""), sub.get("bridge_to_our_method", "")]
                            + list(sub.get("limitation_search_queries", [])))
            sub["citation_candidates"] = self._pick(text, papers, tags, 4)
            used.update(sub["citation_candidates"])
        for section in outline.get("section_plan", []):
            for sub in section.get("subsections", []):
                hints = sub.get("citation_hints", [])
                text = " ".join([sub.get("subsection_title", "")] + list(sub.get("content_bullets", [])) + list(hints))
                sub["citation_candidates"] = self._pick(text, papers, tags, 3) if hints else []
                used.update(sub["citation_candidates"])
                sub.pop("citation_hints", None)
        leftovers = [p.citation_key for p in papers if p.citation_key not in used]
        if leftovers:  # every library paper must be citable somewhere
            target = subs[0] if subs else intro
            target["citation_candidates"] = list(dict.fromkeys(target.get("citation_candidates", []) + leftovers))
        return outline

    def run(self, outline_path, cutoff_date=None):
        self._safe_print("📚 Offline literature stage: using the curated local library (no web search).")
        with open(outline_path, "r", encoding="utf-8") as f:
            outline = json.load(f)
        papers, tags = self._load_library()
        self._safe_print(f"    -> {len(papers)} references in the local library")
        updated = self._assign(outline, papers, tags)
        bibtex = self._generate_bibtex(papers)
        cmap = self._generate_citation_map(papers)
        self._safe_print("✍️  Writing Introduction & Related Work with the local model...")
        latex = self._synthesize_content(updated, papers, cutoff_date or "")
        self._save_outputs(latex, bibtex, cmap, updated, f"{self.output_dir}/outline_v1.json")
        self._safe_print(f"✅ Literature outputs saved to {self.output_dir}/")
