"""Outils de test : base de données temporaire, client HTTP, faux serveur llama.cpp."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import config, db

FIXTURES = Path(__file__).parent / "fixtures"
APP_HEADERS = {"X-Requested-With": "InternshipApplier"}


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "DOCUMENTS_DIR", tmp_path / "documents")
    db.init_db()
    return tmp_path


@pytest.fixture
def client(data_dir):
    from app.main import create_app

    with TestClient(create_app(), base_url="http://127.0.0.1:8000", headers=APP_HEADERS) as test_client:
        yield test_client


@pytest.fixture
def no_sleep(monkeypatch):
    """Supprime les pauses (politesse envers les sites, nouvelles tentatives)."""

    async def instant(*_args, **_kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


# --------------------------------------------------------------------------- faux llama-server


def sse_chunks(text: str, *, size: int = 7, finish: str = "stop", reasoning: str = "") -> list[dict[str, Any]]:
    """Découpe ``text`` en morceaux au format de streaming de llama-server."""
    chunks: list[dict[str, Any]] = [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": None}, "finish_reason": None}]}]
    for i in range(0, len(reasoning), size):
        chunks.append({"choices": [{"index": 0, "delta": {"reasoning_content": reasoning[i:i + size]}, "finish_reason": None}]})
    for i in range(0, len(text), size):
        chunks.append({"choices": [{"index": 0, "delta": {"content": text[i:i + size]}, "finish_reason": None}]})
    chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    return chunks


class MockLLM:
    """Serveur HTTP qui imite llama-server (/v1/models, /props, /v1/chat/completions en SSE).

    ``responses`` : liste consommée à chaque appel de /v1/chat/completions, chaque élément
    étant ``("sse", [chunks])`` ou ``("error", status, payload)``.
    """

    def __init__(self, responses: list[tuple], n_ctx: int | None = 4096):
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.n_ctx = n_ctx
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def _json(self, status: int, payload: Any) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/v1/models":
                    self._json(200, {"object": "list", "data": [{"id": "mock-model", "object": "model"}]})
                elif self.path == "/props" and mock.n_ctx:
                    self._json(200, {"default_generation_settings": {"n_ctx": mock.n_ctx}})
                else:
                    self._json(404, {"error": {"code": 404, "message": "not found"}})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                mock.requests.append(json.loads(self.rfile.read(length) or b"{}"))
                if self.path != "/v1/chat/completions" or not mock.responses:
                    self._json(500, {"error": {"code": 500, "message": "no scripted response"}})
                    return
                kind, *rest = mock.responses.pop(0)
                if kind == "error":
                    self._json(rest[0], rest[1])
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in rest[0]:
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "MockLLM":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()


def read_ndjson(response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]
