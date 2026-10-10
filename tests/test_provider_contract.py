import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


def test_openai_compatible_provider_contract():
    pytest.importorskip("langchain_openai")
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        from langchain_openai import ChatOpenAI
        response = ChatOpenAI(model="contract", base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="test").invoke("hello")
        assert response.content == "ok"
    finally:
        server.shutdown()


def test_openrouter_provider_contract():
    pytest.importorskip("langchain_openai")
    captured_headers = {}
    captured_body = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            captured_headers["http-referer"] = self.headers.get("HTTP-Referer")
            captured_headers["x-title"] = self.headers.get("X-Title")
            captured_body.update(json.loads(raw.decode("utf-8")))
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "free-router-ok"}, "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        from devx.config import Settings
        from devx.llm import create_llm

        settings = Settings(
            provider="openrouter",
            model="openrouter/free",
            api_key="sk-or-v1-test",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
        )
        model = create_llm(settings)
        response = model.invoke("test openrouter free")
        assert response.content == "free-router-ok"
        assert captured_headers.get("http-referer") == "https://github.com/devx-coding-agent"
        assert captured_headers.get("x-title") == "devx"
        assert captured_body.get("model") == "openrouter/free"
    finally:
        server.shutdown()


