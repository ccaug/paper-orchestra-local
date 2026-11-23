# Usage reference

## `review_paper.py PAPER` - review / refine any paper
`PAPER`: a `.tex` file, a folder, an Overleaf `.zip`, or a `.pdf` (PDF-only = review mode: nothing is rewritten, every fix is a suggestion).
The main `.tex` is auto-detected (the file with `\documentclass` + `\begin{document}`, preferring `main/paper/ms/manuscript.tex` and skipping supplementary/appendix/response files).

| Option | Meaning |
|---|---|
| `--main NAME.tex` | override main-file detection |
| `--elog FILE` | results log (md/txt/csv/json). Numbers in the paper are checked against it; also used as evidence in comparisons. Auto-detected: `experimental_log.md`, `results.md`, `experiments.md` next to the paper |
| `--guidelines FILE` | venue/style guidelines (auto-detected: `guidelines.md`) |
| `--instructions TEXT\|FILE` | your own review focus, e.g. `"check statistics and baselines"` |
| `--venue TEXT` | venue persona used by the reviewers |
| `--out DIR` | output folder (default `REVIEWS/<paper name>`) |
| `--pdf FILE` | existing compiled PDF to visualise (LaTeX mode) |
| `--no-compile` | do not run pdflatex (no PDF views) |
| `--suggest-only` | never rewrite; everything becomes a suggestion |
| `--fast-model / --deep-model / --ctx` | screener model, adjudicator model, context window sent to Ollama |
| `--pilot N` | only the first N chunks (quick test) |
| `--diagnostic` | no model: parse, retrieval, PDF word map, ETA plan and a visual tour in the GUI |
| `--mock` | fake model (see the dashboard working without a model) |
| `--no-gui`, `--no-browser`, `--no-hold`, `--port N` | GUI controls (`--no-hold` exits when finished) |

Recommended first steps on a new machine: `--diagnostic`, then `--pilot 1`, then the full run.

## `write_paper_local.py` - write a paper
Stages: 1 outline · 2 offline literature · 3 section writing · 4 compile · 5 refine (`--refine`).

| Option | Meaning |
|---|---|
| `--raw_materials_dir` | folder with `idea_sparse.md`, `experimental_log.md`, `figures/` (+`info.json`), `local_literature.json` |
| `--latex_template_dir` | default `templates/iclr2025` (a folder with `template.tex`, `guidelines.md`, style files) |
| `--output_dir` | default `outputs/dummy_paper` |
| `--model`, `--base_url`, `--ctx` | local model name, OpenAI-compatible URL, context window |
| `--idea_filename`, `--experimental_log_filename`, `--library` | override input file names |
| `--resume` | skip stages whose output exists |
| `--refine`, `--refine-mock`, `--headless` | chain the review engine; mock model; no GUI |

### Input formats
* `figures/info.json`: `[{"name": "fig.png", "caption": "..."}]`
* `local_literature.json`: list of `{citation_key?, title, authors[], year, venue, abstract (your own words), tags[], pages?, note?}`. Only these can be cited.

## Environment variables (`.env` is loaded automatically)
`LLM_BASE_URL` (default `http://localhost:11434/v1`) · `FAST_MODEL` · `DEEP_MODEL` · `LLM_CTX` · `LOCAL_LLM_MODEL` · `LOCAL_LLM_CTX` · `LOCAL_MAX_TOKENS` · `NO_THINK`.
Tuning: `SECTION_CHARS` (chunk size, default 12000) · `MAX_ROUNDS` (adversarial rounds, 3) · `SIM_MIN` (retrieval threshold) · `SWAP_SECONDS` · `DEFAULT_PRE_TPS` / `DEFAULT_DEC_TPS` (initial speed guesses for the ETA) · `SYS_GUIDE_CHARS` / `SYS_LOG_CHARS` (system-prompt budget) · `ECHO_MIN` (harmonisation threshold). Testing: `MOCK_SPEED`.
