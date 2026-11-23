# Contributing

Thanks for helping! Quick rules:

1. **Setup**: `python -m venv .venv && pip install -r requirements-dev.txt`, then `pytest -q` (the e2e tests need no real model; PDF checks need `pdflatex`).
2. **Never commit** `.env`, API keys, unpublished manuscripts or generated `outputs/` / `REVIEWS/`. `.gitignore` covers them, but double-check `git status`.
3. **Upstream code** (`methods/`, `utils/`, `templates/`, `autoraters/`, `frontend/`, `assets/`) comes from google-research/paper-orchestra. Prefer extending through `plocal/` (monkey-patch / subclass) instead of editing it. If you must edit it, add a notice in the file and an entry in `NOTICE`.
4. **Scientific safety is a feature**: any change to the rewrite engine must keep the guarantees tested in `tests/test_engine_units.py` (numbers unchanged, no new citations, tables untouched, tournament required).
5. **Be honest in docs**: say what was measured and what was not.
6. Style: `ruff check .`; small, documented functions; tests for new behaviour.

Good first issues: more journal/conference templates, better PDF section detection for 2-column layouts, OCR for scanned PDFs, a benchmark of local models on the example paper.
