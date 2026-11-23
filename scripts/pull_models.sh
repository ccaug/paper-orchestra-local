#!/usr/bin/env bash
# Pull two small non-"thinking" models that fit a 16 GB machine (starting points, not benchmarked).
set -e
ollama pull qwen2.5:3b-instruct   # fast screener
ollama pull qwen2.5:7b-instruct   # deep adjudicator / writer
echo "Done. Set OLLAMA_CONTEXT_LENGTH=16384 and restart Ollama before running."
