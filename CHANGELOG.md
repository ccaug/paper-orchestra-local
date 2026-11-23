# Changelog

## 0.1.0 - initial release
### Write a paper with local models (`write_paper_local.py`)
- Redirects every Gemini call of PaperOrchestra's outline / literature / refinement agents to any OpenAI-compatible local server (`plocal/llm_shim.py`), with streaming heartbeat and parser retries.
- Offline literature stage (`plocal/literature_local.py`): cites only a curated local library, never invents references.
- Stage banners, `--resume`, automatic LaTeX preamble repair, pdflatex/bibtex compilation.
### Review any paper (`review_paper.py`)
- Accepts `.tex`, folder, Overleaf `.zip` or a bare `.pdf` (review-only mode).
- Cascade engine: fast screener -> one model swap -> deep adjudicator; risk-weighted compute; grounded-critique filter; adversarial Reviewer-2 rounds; position-swapped patch tournament; evidence binding against the results log.
- Rewrites necessary passages with justification, keeps the rest as suggestions; harmonises the same claim across sections; numbers/citations are never changed.
- Live control-room GUI: word-level reading sweep over the PDF, comparison arcs, attention minimap, cascade timeline, diffs, one-click suggestions, redline PDF.
- ETA model with measured prefill/decode speeds.
### Project
- Test suite (unit + end-to-end dry runs with a fake model server), CI workflow, docs.
