#!/usr/bin/env python
"""Cross-platform dry run: starts the fake model server and runs the whole writing pipeline against it.
No model, no internet. Verifies that your installation is wired correctly.   python scripts/dry_run.py [--refine]"""
import os
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import fake_local_llm  # noqa: E402

srv = ThreadingHTTPServer(("127.0.0.1", 0), fake_local_llm.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
args = [sys.executable, str(ROOT / "write_paper_local.py"), "--model", "fake-local", "--ctx", "8192"]
if "--refine" in sys.argv:
    args += ["--refine", "--refine-mock", "--headless"]
print(f"[dry-run] fake model at {url}")
code = subprocess.call(args, cwd=ROOT, env=dict(os.environ, LLM_BASE_URL=url, MOCK_SPEED="200"))
print("[dry-run] OK -> outputs/dummy_paper/draft_paper.pdf (placeholder text: plumbing test only)" if code == 0 else "[dry-run] FAILED")
sys.exit(code)
