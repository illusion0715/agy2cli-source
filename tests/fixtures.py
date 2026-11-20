import json
import asyncio
import httpx

GATEWAY_KEY = "TEST_ONLY_GATEWAY_KEY_NOT_REAL_123456789"
OFFICIAL_KEY = "TEST_ONLY_OFFICIAL_KEY_NOT_REAL"
MODEL = "gemini-test"
TEXT = "Synthetic hello"
RESULT = {"candidates": [{"content": {"role": "model", "parts": [{"text": TEXT}]}, "finishReason": "STOP"}],
          "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2, "totalTokenCount": 5}}


class Bytes(httpx.AsyncByteStream):
    async def __aiter__(self):
        for event in [
            {"candidates": [{"content": {"parts": [{"text": "Synthetic "}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "hello"}]}, "finishReason": "STOP"}],
             "usageMetadata": RESULT["usageMetadata"]},
        ]:
            frame = (": official comment\r\ndata: " + json.dumps(event) + "\r\n\r\n").encode()
            yield frame[:9]
            yield frame[9:]
            await asyncio.sleep(0.1)


def official(request):
    assert request.url.scheme == "https"
    assert request.url.host == "generativelanguage.googleapis.com"
    assert request.headers.get("x-goog-api-key") == OFFICIAL_KEY
    assert "authorization" not in request.headers
    assert "key" not in request.url.params
    if request.url.path == "/v1beta/models":
        return httpx.Response(200, json={"models": [{"name": "models/" + MODEL, "supportedGenerationMethods": ["generateContent"]}]})
    if request.url.path.endswith(":streamGenerateContent"):
        assert request.url.params.get("alt") == "sse"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Bytes())
    if request.url.path.endswith(":generateContent"):
        return httpx.Response(200, json=RESULT)
    return httpx.Response(404, json={"error": {"message": "Synthetic unsupported route"}})
