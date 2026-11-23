@echo off
REM Pull two small non-"thinking" models that fit a 16 GB machine (starting points, not benchmarked).
ollama pull qwen2.5:3b-instruct
ollama pull qwen2.5:7b-instruct
echo Done. Run: setx OLLAMA_CONTEXT_LENGTH 16384   and restart Ollama.
