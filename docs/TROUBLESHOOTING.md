# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Stays on "LEYENDO" for minutes | The model is still processing the prompt (normal on CPU). Watch the console line `[prefill 73s]`; reduce `SECTION_CHARS`, use a smaller fast model, check `ollama ps` (CPU vs GPU) |
| `(thinking)` / `THINK` warning | Reasoning model. Use an instruct model (`qwen2.5:*-instruct`) |
| `Connection error` / `ERROR ... is the local server running` | Start Ollama / your server; check `LLM_BASE_URL` |
| Output is cut or JSON invalid | Context too small or model too weak: raise `--ctx` / `OLLAMA_CONTEXT_LENGTH`, use a bigger model, `--resume` |
| `pdflatex not found` | Install MiKTeX / TeX Live. Without it you get `.tex` but no PDF views |
| Redline PDF missing | `ulem`/`xcolor` unavailable or a patch produced un-compilable markup; check `build_redline/redline.log` |
| PDF mode finds no sections / odd order | Two-column or scanned PDFs: scanned PDFs need OCR; for LaTeX papers pass the source instead |
| Windows saved `file (1).py` | Downloads never overwrite; use `git pull` instead of downloading files by hand |
| `ModuleNotFoundError: utils/methods` | Run the CLIs from the repository root |
| Port in use | `--port 8766` (the server also tries the next 20 ports) |
