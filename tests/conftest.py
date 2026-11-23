import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


@pytest.fixture(scope="session")
def fake_llm():
    """A fake OpenAI-compatible server (canned answers) so the pipelines can be tested without a model."""
    import fake_local_llm
    srv = ThreadingHTTPServer(("127.0.0.1", 0), fake_local_llm.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


@pytest.fixture
def mock_fast(monkeypatch):
    monkeypatch.setenv("MOCK_SPEED", "2000")
