# Dummy paper (for testing only)

Everything here is **synthetic**: the idea ("TRACE", trajectory-aware curriculum learning), the results in
`experimental_log.md` and the two figures. The 8 references in `local_literature.json` are real, well-known
papers (summaries written from scratch); verify their metadata before using them in a real submission.

Use it to test the pipeline without exposing your own work:

    python scripts/dry_run.py                       # no model: plumbing test
    python write_paper_local.py --model qwen2.5:7b-instruct --ctx 16384   # real local model

To write YOUR paper, copy this folder, replace the four items, and pass `--raw_materials_dir` and `--output_dir`.
