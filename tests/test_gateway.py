import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx
from src.gateway.app import create_app
from src.gateway.settings import Settings
from src.gateway.store import Store
from tests.fixtures import GATEWAY_KEY, OFFICIAL_KEY, MODEL, TEXT, RESULT, official

AUTH = {"Authorization": "Bearer " + GATEWAY_KEY}
NATIVE = {"contents": [{"role": "user", "parts": [{"text": "Synthetic question"}]}]}
CHAT = {"model": MODEL, "messages": [{"role": "user", "content": "Synthetic question"}]}


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(gateway_key=GATEWAY_KEY, gemini_key=OFFICIAL_KEY, data_dir=Path(self.tmp.name))
        self.calls = []

    async def asyncTearDown(self):
        self.tmp.cleanup()

    @asynccontextmanager
    async def client(self, handler=official, settings=None):
        def capture(request):
            self.calls.append(request)
            return handler(request)
        app = create_app(settings or self.settings, transport=httpx.MockTransport(capture))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                yield client

    async def test_health_readiness_and_missing_configuration(self):
        empty = Settings(data_dir=Path(self.tmp.name))
        async with self.client(settings=empty) as c:
            self.assertEqual((await c.get("/healthz")).json(), {"status": "ok"})
            self.assertEqual((await c.get("/readyz")).status_code, 503)
            self.assertEqual((await c.get("/v1/models", headers=AUTH)).status_code, 503)
        self.assertEqual(len(self.calls), 0)

    async def test_invalid_key_is_rejected_before_upstream(self):
        async with self.client() as c:
            for headers in [{}, {"Authorization": "Bearer wrong"}, {"x-api-key": OFFICIAL_KEY}]:
                r = await c.get("/v1/models", headers=headers)
                self.assertEqual(r.status_code, 401)
            self.assertEqual((await c.get("/usage")).status_code, 401)
        self.assertEqual(self.calls, [])

    async def test_model_lists_and_header_separation(self):
        async with self.client() as c:
            r = await c.get("/v1/models", headers=AUTH)
            self.assertEqual(r.json()["data"][0]["id"], MODEL)
            r = await c.get("/v1beta/models", headers={"x-goog-api-key": GATEWAY_KEY})
            self.assertEqual(r.json()["models"][0]["name"], "models/" + MODEL)
        self.assertEqual(len(self.calls), 2)

    async def test_model_pagination(self):
        def pages(request):
            if request.url.params.get("pageToken") == "second":
                return httpx.Response(200, json={"models": [{"name": "models/second"}]})
            return httpx.Response(200, json={"models": [{"name": "models/first"}, {"name": "models/embedding", "supportedGenerationMethods": ["embedContent"]}], "nextPageToken": "second"})
        async with self.client(pages) as c:
            r = await c.get("/v1/models", headers=AUTH)
            self.assertEqual([m["id"] for m in r.json()["data"]], ["first", "second"])

    async def test_native_generate_preserves_payload_and_response(self):
        async with self.client() as c:
            r = await c.post(f"/v1beta/models/{MODEL}:generateContent", headers=AUTH, json=NATIVE)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json(), RESULT)
        self.assertEqual(json.loads(self.calls[0].content), NATIVE)

    async def test_openai_text_conversion_and_usage(self):
        body = {**CHAT, "messages": [{"role": "system", "content": "System"},
                                   {"role": "assistant", "content": "Prior"},
                                   {"role": "user", "content": "Question"}],
                "temperature": 0.3, "max_tokens": 40, "response_format": {"type": "json_object"}}
        async with self.client() as c:
            r = await c.post("/v1/chat/completions", headers=AUTH, json=body)
            self.assertEqual(r.status_code, 200)
            data = r.json()
            self.assertEqual(data["choices"][0]["message"]["content"], TEXT)
            self.assertEqual(data["usage"]["total_tokens"], 5)
        sent = json.loads(self.calls[0].content)
        self.assertEqual(sent["systemInstruction"]["parts"][0]["text"], "System")
        self.assertEqual(sent["contents"][0]["role"], "model")
        self.assertEqual(sent["generationConfig"]["maxOutputTokens"], 40)
        self.assertEqual(sent["generationConfig"]["responseMimeType"], "application/json")

    async def test_openai_sse_contract_and_native_sse(self):
        async with self.client() as c:
            r = await c.post("/v1/chat/completions", headers=AUTH,
                             json={**CHAT, "stream": True, "stream_options": {"include_usage": True}})
            self.assertIn("text/event-stream", r.headers["content-type"])
            frames = [line[6:] for line in r.text.splitlines() if line.startswith("data: ")]
            self.assertEqual(frames[-1], "[DONE]")
            chunks = [json.loads(f) for f in frames[:-1]]
            content = "".join(ch["choices"][0]["delta"].get("content", "") for ch in chunks if ch["choices"])
            self.assertEqual(content, TEXT)
            self.assertEqual(chunks[-2]["choices"][0]["finish_reason"], "stop")
            self.assertEqual(chunks[-1]["usage"]["total_tokens"], 5)
            native = await c.post(f"/v1beta/models/{MODEL}:streamGenerateContent", headers=AUTH, json=NATIVE)
            self.assertEqual(native.status_code, 200)
            self.assertNotIn("[DONE]", native.text)
            self.assertIn('"finishReason":"STOP"', native.text)

    async def test_safety_block_is_not_replaced_with_fake_success(self):
        blocked = {"promptFeedback": {"blockReason": "SAFETY"}}
        async with self.client(lambda r: httpx.Response(200, json=blocked)) as c:
            r = await c.post("/v1/chat/completions", headers=AUTH, json=CHAT)
            self.assertEqual(r.json()["choices"][0]["finish_reason"], "content_filter")
            self.assertEqual(r.json()["choices"][0]["message"]["content"], "")
            r = await c.post(f"/v1beta/models/{MODEL}:generateContent", headers=AUTH, json=NATIVE)
            self.assertEqual(r.json(), blocked)

    async def test_provider_errors_are_sanitized_and_not_retried(self):
        for status, expected in [(429, 429), (401, 502), (403, 502), (302, 502), (500, 500)]:
            self.calls.clear()
            def error(request):
                return httpx.Response(status, json={"error": {"message": "SYNTHETIC_DO_NOT_REFLECT"}},
                                      headers={"location": "http://127.0.0.1/never-follow"})
            async with self.client(error) as c:
                r = await c.post("/v1/chat/completions", headers=AUTH, json=CHAT)
                self.assertEqual(r.status_code, expected)
                self.assertNotIn("SYNTHETIC_DO_NOT_REFLECT", r.text)
                self.assertNotIn(OFFICIAL_KEY, r.text)
            self.assertEqual(len(self.calls), 1)

    async def test_invalid_json_unsupported_inputs_and_body_limit(self):
        async with self.client() as c:
            for body in [{**CHAT, "tools": []}, {**CHAT, "n": 2}, {**CHAT, "model": "../bad"},
                         {**CHAT, "messages": [{"role": "tool", "content": "x"}]},
                         {**CHAT, "stream": "true"}]:
                r = await c.post("/v1/chat/completions", headers=AUTH, json=body)
                self.assertEqual(r.status_code, 422)
            self.assertEqual((await c.post("/v1/chat/completions", headers=AUTH, content="{")).status_code, 400)
            self.assertEqual((await c.post("/v1/chat/completions", headers=AUTH, content='{"temperature":NaN}')).status_code, 400)
            r = await c.post(f"/v1beta/models/{MODEL}:generateContent", headers=AUTH,
                             json={**NATIVE, "upstream_url": "http://127.0.0.1"})
            self.assertEqual(r.status_code, 422)
            r = await c.post("/v1/chat/completions", headers=AUTH, content=b"x" * 1_048_577)
            self.assertEqual(r.status_code, 413)
        self.assertEqual(self.calls, [])

    async def test_timeout_and_invalid_upstream_json(self):
        def timeout(request):
            raise httpx.ReadTimeout("synthetic detail must not leak", request=request)
        async with self.client(timeout) as c:
            r = await c.get("/v1/models", headers=AUTH)
            self.assertEqual(r.status_code, 504)
            self.assertNotIn("synthetic detail", r.text)
        async with self.client(lambda r: httpx.Response(200, content=b"not json")) as c:
            self.assertEqual((await c.get("/v1/models", headers=AUTH)).status_code, 502)

    async def test_malformed_sse_is_an_error_frame(self):
        def malformed(request):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b"data: broken\n\n")
        async with self.client(malformed) as c:
            r = await c.post("/v1/chat/completions", headers=AUTH, json={**CHAT, "stream": True})
            self.assertIn('"error"', r.text)
            self.assertIn('"code":502', r.text)
            self.assertNotIn('"finish_reason":"stop"', r.text)

    async def test_incomplete_stream_and_nontext_response_fail_safely(self):
        incomplete = {"candidates": [{"content": {"parts": [{"text": "Partial"}]}}]}
        def handler(request):
            if request.url.path.endswith(":streamGenerateContent"):
                return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                      content=("data: " + json.dumps(incomplete) + "\n\n").encode())
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"functionCall": {}}]}}]})
        async with self.client(handler) as c:
            r = await c.post("/v1/chat/completions", headers=AUTH, json={**CHAT, "stream": True})
            self.assertIn('"code":502', r.text)
            self.assertNotIn('"finish_reason":"stop"', r.text)
            r = await c.post("/v1/chat/completions", headers=AUTH, json=CHAT)
            self.assertEqual(r.status_code, 502)
            rows = (await c.get("/usage", headers=AUTH)).json()["rows"]
            self.assertTrue(all(row["status"] == 502 for row in rows))

    async def test_storage_survives_reopen_without_contents_or_keys(self):
        async with self.client() as c:
            await c.post("/v1/chat/completions", headers=AUTH, json=CHAT)
            rows = (await c.get("/usage", headers=AUTH)).json()["rows"]
            self.assertEqual(rows[0]["requests"], 1)
        reopened = Store(Path(self.tmp.name))
        self.assertEqual(reopened.summary()[0]["input_tokens"], 3)
        data = reopened.path.read_bytes()
        for value in [TEXT, "Synthetic question", GATEWAY_KEY, OFFICIAL_KEY]:
            self.assertNotIn(value.encode(), data)

    async def test_production_url_cannot_be_changed_by_environment(self):
        with patch.dict(os.environ, {"UPSTREAM_BASE_URL": "http://127.0.0.1/forbidden"}):
            async with self.client() as c:
                self.assertEqual((await c.get("/v1/models", headers=AUTH)).status_code, 200)
        self.assertEqual(self.calls[0].url.host, "generativelanguage.googleapis.com")

    def test_identical_keys_and_invalid_limits_are_refused(self):
        with self.assertRaises(ValueError):
            Settings(gateway_key=GATEWAY_KEY, gemini_key=GATEWAY_KEY)
        with self.assertRaises(ValueError):
            Settings(max_concurrent=0)


if __name__ == "__main__":
    unittest.main()
