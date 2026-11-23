# Local models

Any server exposing the OpenAI chat API works (`LLM_BASE_URL`): Ollama (`http://localhost:11434/v1`), llama.cpp server, LM Studio, vLLM, colibri (`coli serve`) ...

## Ollama setup
1. Install Ollama, then `ollama pull qwen2.5:3b-instruct` and `ollama pull qwen2.5:7b-instruct` (or `scripts/pull_models.*`).
2. **Context window**: Ollama defaults to 4096 tokens, which silently truncates a paper. Set `OLLAMA_CONTEXT_LENGTH=16384` and restart Ollama (the apps also send `num_ctx`).
3. One model resident at a time on small machines: `OLLAMA_MAX_LOADED_MODELS=1`.

## Choosing models (starting points, NOT benchmarked here)
| RAM | Fast screener | Deep / writer |
|---|---|---|
| 16 GB, CPU | 3B-4B instruct | 7B-8B instruct (Q4) |
| 32 GB | 7B-8B | 14B |
| GPU / more RAM | 8B-14B | 30B+ |

Prefer **non-"thinking"** instruct models. Reasoning models (e.g. `qwen3`) may spend minutes thinking before answering; the apps send `reasoning_effort=none` and `/no_think` (`NO_THINK=1`), and the dashboard shows a `(thinking)` phase and a `THINK` warning if the model ignores it.

## What to expect on CPU
Reading a 4k-token prompt and generating a few thousand tokens takes minutes per call on a 16 GB CPU. The dashboard shows `LEYENDO` (prompt processing), `PENSANDO`, `GENERANDO`, a waiting counter and a measured ETA. To go faster: smaller `SECTION_CHARS` (6000), shorter guidelines, a smaller fast model, `--pilot 1` first.
