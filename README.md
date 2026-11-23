# PaperOrchestra Local

**Write and review scientific papers with local LLMs: no API keys, no cloud.**

> Unofficial add-on built on [google-research/paper-orchestra](https://github.com/google-research/paper-orchestra) (Apache-2.0).
> Not affiliated with or endorsed by Google. See [NOTICE](NOTICE).
> *Spanish version: [README.es.md](README.es.md)*

Two apps share one local-model backend (any OpenAI-compatible server: Ollama, llama.cpp, LM Studio, vLLM, colibri...):

| App | Command | What it does |
|---|---|---|
| **Write** | `python write_paper_local.py` | You give an idea + results (+ figures + a reference library). It writes the paper: outline → offline literature → full LaTeX draft → PDF. |
| **Review** | `python review_paper.py PAPER` | Works on **any** paper (`.tex`, folder, Overleaf `.zip` or a bare `.pdf`). Finds problems, **rewrites what is necessary with a justification**, keeps the rest as suggestions, and shows everything live in a GUI on top of your PDF. |

```
python review_paper.py my_paper.zip      # or .tex / folder / .pdf
python write_paper_local.py --model qwen2.5:7b-instruct
python write_paper_local.py --refine     # write, then review the draft
```

## Why it is different

Small local models are weak reviewers if you just paste the paper. The review engine (**Cascade Reviewer**) compensates with systems tricks:

- **Propose cheap, verify expensive**: a small fast model screens every chunk; a bigger one only *adjudicates* contested findings. All fast work runs first, then **one** model swap (matters on 16 GB RAM).
- **Grounded critiques only**: every finding must quote the paper verbatim, verified by code. Hallucinated critiques are dropped (and counted).
- **Risk-weighted compute**: deterministic risk scores (over-claim words vs hedging, numbers not found in your results log, broken refs) decide how many adversarial "Reviewer 2" rounds a section deserves.
- **Position-swapped patch tournament**: a rewrite must beat the original in *both* A/B and B/A order.
- **Evidence binding**: numbers in the paper that appear nowhere in your results log are flagged instantly (no LLM).
- **Coherence**: after a claim is softened, the same claim is hunted in other sections (abstract / intro / conclusion) and rewritten consistently.
- **Honest ETA**: prefill/decode speeds are measured live; the dashboard shows a likely and a worst-case finish time.

### Scientific-safety guarantees (covered by tests)
A rewrite is applied only if it is *necessary* (high/medium severity, confirmed by the deep model / adversarial round / deterministic evidence) **and** keeps **exactly the same numbers**, adds **no new citations/refs**, keeps LaTeX balanced, never touches tables/math environments, and wins the tournament. Everything else stays a suggestion you can apply with one click. In PDF-only mode nothing is rewritten.

## The control-room GUI

Opens in your browser while the review runs (`--no-gui` to disable):
red word-level boxes sweeping over the text being read and over the exact words the model cites; animated arcs from each passage to **what it is compared with and how** (other sections, your results log); an attention minimap of the whole paper; a fast-vs-deep model timeline; diffs with justification; one-click suggestions; before / rewritten / redline (red-blue) PDF views. See [docs/GUI.md](docs/GUI.md).

## Quick start

Requirements: Python 3.10+, a local OpenAI-compatible server (we assume [Ollama](https://ollama.com)), and `pdflatex` (MiKTeX / TeX Live) for PDFs.

```bash
git clone https://github.com/<you>/paperorchestra-local.git
cd paperorchestra-local
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # optional settings

python scripts/dry_run.py                              # 1) no model needed: checks the wiring
```

Then with a real model (Windows: `setx OLLAMA_CONTEXT_LENGTH 16384` and restart Ollama; Linux/macOS: `export OLLAMA_CONTEXT_LENGTH=16384`):

```bash
ollama pull qwen2.5:3b-instruct && ollama pull qwen2.5:7b-instruct    # or scripts/pull_models.*
python write_paper_local.py --model qwen2.5:7b-instruct --ctx 16384    # writes the example paper
python review_paper.py outputs/dummy_paper/draft_paper.pdf --fast-model qwen2.5:3b-instruct --deep-model qwen2.5:7b-instruct
```

Quick no-model preview of the GUI animations over any paper: `python review_paper.py paper.pdf --diagnostic` (or `--mock`).

## Write your own paper

Put four things in a folder (see [`examples/dummy_paper/`](examples/dummy_paper/)): `idea_sparse.md`, `experimental_log.md`, `figures/` (+ `info.json` captions) and `local_literature.json` (the references the paper may cite; it never invents references). Then:

```bash
python write_paper_local.py --raw_materials_dir MY_FOLDER --output_dir outputs/my_paper --model qwen2.5:7b-instruct
```

Add `--resume` to continue an interrupted run and `--refine` to chain the review. Templates: `templates/iclr2025` (default), `cvpr2025`, `NeurIPS_2026.zip` (`--latex_template_dir`).

## Outputs

| App | Files |
|---|---|
| Write | `outline.json`, `literature_agent_output/` (bib, citation map), `latex_writeup/raw_draft_paper.tex`, `draft_paper.pdf` |
| Review | `REVIEWS/<paper>/`: `changes.md` (every rewrite + why + what it was compared with), `suggestions.md`, `review_report.md`, `refined_flat.tex`, `issues.json`, `view/{before,after,redline}.pdf` |

## Status and honest limits

Alpha (v0.1.0). **Tested**: 25 automated tests (unit + end-to-end dry runs against a fake model server, real `pdflatex`). **Not benchmarked**: the quality of writing/review with real 3B-8B models, and run times on your hardware: expect minutes per section on a 16 GB CPU. Small models can return invalid JSON/LaTeX; use a bigger model, larger `--ctx`, or `--resume`.
Text models do not see figures (only captions). Scanned PDFs need OCR (not included). Model self-scores in the dashboard are for prioritising work, **not** a prediction of acceptance. Nothing here replaces your own scientific judgement.

## Repository layout

```
write_paper_local.py   review_paper.py      CLIs
plocal/                our code: llm_shim, literature_local, review_engine, review_gui, review_basics
methods/ utils/ templates/ autoraters/ frontend/ assets/   upstream PaperOrchestra (Apache-2.0, see NOTICE)
examples/dummy_paper/  synthetic idea + results + figures + real reference library
tools/fake_local_llm.py   fake model server for dry runs/tests
scripts/               dry_run.py, pull_models.*
tests/                 pytest suite        docs/   documentation
```

Docs: [Usage](docs/USAGE.md) · [Local models](docs/LOCAL_MODELS.md) · [Architecture](docs/ARCHITECTURE.md) · [GUI](docs/GUI.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md)

## Security / privacy
Everything runs on your machine; no text is sent to a cloud model by these apps. **Never commit `.env`, API keys or unpublished manuscripts** (`.gitignore` excludes `.env`, `outputs/`, `REVIEWS/`).

## License
Apache-2.0 (see [LICENSE](LICENSE) and [NOTICE](NOTICE)).
