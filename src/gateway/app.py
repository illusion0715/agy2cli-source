"""Self-contained official Gemini gateway. No production/session integration."""
from contextlib import asynccontextmanager
import asyncio
import hmac
import json
import time
import uuid

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from src.gateway import protocol
from src.gateway.settings import OFFICIAL_ORIGIN, Settings
from src.gateway.store import Store


def reject_json_constant(value):
    raise ValueError("Non-standard JSON constant")


def create_app(settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None):
    """transport is dependency injection for tests, never an environment-configurable URL."""
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(settings.data_dir)
        app.state.slots = asyncio.Semaphore(settings.max_concurrent)
        async with httpx.AsyncClient(
            base_url=OFFICIAL_ORIGIN + "/v1beta/", transport=transport,
            follow_redirects=False, verify=True, trust_env=False,
            timeout=httpx.Timeout(settings.timeout_seconds, connect=10, write=30, pool=10),
        ) as client:
            app.state.client = client
            yield

    app = FastAPI(title="AGY2API Official Gemini Gateway", version="1.0.0", lifespan=lifespan)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": {"message": exc.detail, "type": "gateway_error", "code": exc.status_code}},
                            status_code=exc.status_code, headers=exc.headers)

    async def authorize(request: Request):
        if not settings.configured:
            raise HTTPException(503, "Operator must configure separate gateway and official Gemini keys")
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else (
            request.headers.get("x-api-key") or request.headers.get("x-goog-api-key") or ""
        )
        if not supplied or not hmac.compare_digest(supplied.encode(), settings.gateway_key.encode()):
            raise HTTPException(401, "Invalid gateway API key", headers={"WWW-Authenticate": "Bearer"})

    async def body_json(request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > settings.max_body_bytes:
                raise HTTPException(413, "Request body too large")
        try:
            body = json.loads(raw, parse_constant=reject_json_constant)
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Request must contain valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(422, "JSON body must be an object")
        return body

    def record(route, status, started, values=None):
        app.state.store.record(route, status, int((time.monotonic() - started) * 1000), values or {})

    async def send(path, *, body=None, params=None, stream=False):
        # Every path is constructed internally from a validated model identifier.
        url = httpx.URL(OFFICIAL_ORIGIN + "/v1beta/" + path)
        if url.scheme != "https" or url.host != "generativelanguage.googleapis.com" or url.port not in (None, 443):
            raise HTTPException(500, "Invalid internal upstream configuration")
        try:
            await asyncio.wait_for(app.state.slots.acquire(), timeout=5)
        except TimeoutError:
            raise HTTPException(503, "Gateway concurrency limit reached")
        try:
            request = app.state.client.build_request(
                "POST" if body is not None else "GET", url,
                json=body, params=params, headers={"x-goog-api-key": settings.gemini_key},
            )
            response = await app.state.client.send(request, stream=stream)
            if response.status_code != 200:
                code = response.status_code
                await response.aclose()
                # Never reflect raw provider messages, URLs, headers or response bodies.
                if code in (401, 403):
                    raise HTTPException(502, "Official upstream authentication or permission failure")
                if 300 <= code < 400:
                    raise HTTPException(502, "Official upstream redirect refused")
                raise HTTPException(code if code in (400, 404, 408, 413, 422, 429, 500, 502, 503, 504) else 502,
                                    "Official upstream rejected the request")
            return response
        except HTTPException:
            app.state.slots.release()
            raise
        except httpx.TimeoutException:
            app.state.slots.release()
            raise HTTPException(504, "Official upstream timed out")
        except httpx.HTTPError:
            app.state.slots.release()
            raise HTTPException(502, "Official upstream connection failed")
        except BaseException:
            app.state.slots.release()
            raise

    async def json_request(path, route, body=None, params=None, validate=None):
        started = time.monotonic()
        try:
            response = await send(path, body=body, params=params)
        except HTTPException as exc:
            record(route, exc.status_code, started)
            raise
        try:
            data = response.json()
            if not isinstance(data, dict) or "error" in data:
                raise ValueError("Invalid upstream JSON")
            if validate is not None:
                validate(data)
            values = protocol.usage(data)
            record(route, 200, started, values)
            return data
        except (ValueError, TypeError, AttributeError):
            record(route, 502, started)
            raise HTTPException(502, "Invalid official upstream response")
        finally:
            await response.aclose()
            app.state.slots.release()

    async def streaming(path, body, route, *, openai_model=None, include_usage=False):
        started = time.monotonic()
        try:
            response = await send(path, body=body, params={"alt": "sse"}, stream=True)
        except HTTPException as exc:
            record(route, exc.status_code, started)
            raise
        if "text/event-stream" not in response.headers.get("content-type", ""):
            await response.aclose()
            app.state.slots.release()
            record(route, 502, started)
            raise HTTPException(502, "Official upstream did not return SSE")

        async def generate():
            values, status, seen = {}, 200, False
            response_id, created = "chatcmpl-" + uuid.uuid4().hex, int(time.time())
            reason = None
            def chunk(delta, finish=None):
                return {"id": response_id, "object": "chat.completion.chunk", "created": created,
                        "model": openai_model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            try:
                if openai_model:
                    yield protocol.sse(chunk({"role": "assistant"}))
                async for data in protocol.events(response):
                    if "usageMetadata" in data:
                        values = protocol.usage(data)
                    if not openai_model:
                        seen = True
                        yield protocol.sse(data)
                        continue
                    text, finish = protocol.candidate(data)
                    if data.get("candidates") or data.get("promptFeedback", {}).get("blockReason"):
                        seen = True
                    if text:
                        yield protocol.sse(chunk({"content": text}))
                    if finish:
                        reason = finish
                if not seen:
                    raise ValueError("Empty upstream stream")
                if openai_model:
                    if reason is None:
                        raise ValueError("Upstream stream ended without a finish reason")
                    yield protocol.sse(chunk({}, reason))
                    if include_usage:
                        yield protocol.sse({"id": response_id, "object": "chat.completion.chunk",
                                            "created": created, "model": openai_model,
                                            "choices": [], "usage": values or protocol.usage({})})
                    yield "data: [DONE]\n\n"
            except asyncio.CancelledError:
                status = 499
                raise
            except (ValueError, TypeError, KeyError, AttributeError, httpx.HTTPError):
                status = 502
                yield protocol.sse({"error": {"message": "Official upstream stream failed", "code": 502}})
                if openai_model:
                    yield "data: [DONE]\n\n"
            finally:
                try:
                    await asyncio.shield(response.aclose())
                finally:
                    app.state.slots.release()
                    record(route, status, started, values)
        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready():
        return JSONResponse({"ready": settings.configured}, status_code=200 if settings.configured else 503)

    @app.get("/", response_class=HTMLResponse)
    async def home():
        return """<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>AGY2API 官方网关</title>
        <h1>AGY2API 官方 Gemini 网关</h1><p>仅使用部署者提供的官方 API Key。接口需要独立网关 Key。</p>
        <p><a href='/healthz'>存活检查</a> · <a href='/readyz'>配置就绪检查</a> · <a href='/docs'>接口路径</a></p>
        <p>OpenAI：/v1/models、/v1/chat/completions；Gemini：/v1beta/models、generateContent、streamGenerateContent。</p>
        </html>"""

    @app.get("/usage", dependencies=[Depends(authorize)])
    async def summary():
        return {"retention_days": 90, "rows": app.state.store.summary()}

    @app.get("/v1beta/models", dependencies=[Depends(authorize)])
    async def native_models(request: Request):
        page_size = request.query_params.get("pageSize", "1000")
        page_token = request.query_params.get("pageToken", "")
        if not page_size.isdecimal() or not 1 <= int(page_size) <= 1000 or len(page_token) > 2048:
            raise HTTPException(422, "Invalid model pagination")
        params = {"pageSize": page_size}
        if page_token:
            params["pageToken"] = page_token
        return await json_request("models", "models", params=params)

    @app.get("/v1/models", dependencies=[Depends(authorize)])
    async def models():
        result, token, seen = [], None, set()
        for _ in range(20):
            params = {"pageSize": "1000"}
            if token:
                params["pageToken"] = token
            data = await json_request("models", "models", params=params)
            items = data.get("models", [])
            if not isinstance(items, list):
                raise HTTPException(502, "Invalid official model list")
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    methods = item.get("supportedGenerationMethods")
                    if isinstance(methods, list) and "generateContent" not in methods:
                        continue
                    result.append({"id": item["name"].removeprefix("models/"), "object": "model",
                                   "created": 0, "owned_by": "google"})
            token = data.get("nextPageToken")
            if not token:
                return {"object": "list", "data": result}
            if not isinstance(token, str) or token in seen:
                raise HTTPException(502, "Invalid official pagination")
            seen.add(token)
        raise HTTPException(502, "Official model pagination exceeded limit")

    @app.post("/v1beta/models/{model}:generateContent", dependencies=[Depends(authorize)])
    @app.post("/v1/models/{model}:generateContent", dependencies=[Depends(authorize)])
    async def native_generate(model: str, request: Request):
        model = protocol.model_id(model)
        body = protocol.native_body(await body_json(request))
        return await json_request("models/" + model + ":generateContent", "native", body=body)

    @app.post("/v1beta/models/{model}:streamGenerateContent", dependencies=[Depends(authorize)])
    @app.post("/v1/models/{model}:streamGenerateContent", dependencies=[Depends(authorize)])
    async def native_stream(model: str, request: Request):
        model = protocol.model_id(model)
        body = protocol.native_body(await body_json(request))
        return await streaming("models/" + model + ":streamGenerateContent", body, "native_stream")

    @app.post("/v1/chat/completions", dependencies=[Depends(authorize)])
    async def chat(request: Request):
        body = await body_json(request)
        model, native = protocol.chat_body(body)
        if body.get("stream", False):
            return await streaming("models/" + model + ":streamGenerateContent", native, "chat_stream",
                                   openai_model=model, include_usage=body.get("stream_options", {}).get("include_usage", False))
        def validate(data):
            if not data.get("candidates") and not data.get("promptFeedback", {}).get("blockReason"):
                raise ValueError("Missing candidates")
            protocol.candidate(data)
        data = await json_request("models/" + model + ":generateContent", "chat", body=native, validate=validate)
        text, reason = protocol.candidate(data)
        return {"id": "chatcmpl-" + uuid.uuid4().hex, "object": "chat.completion", "created": int(time.time()),
                "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                               "finish_reason": reason or "stop"}], "usage": protocol.usage(data)}

    return app
