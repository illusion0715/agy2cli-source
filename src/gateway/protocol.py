"""Text-only OpenAI compatibility; native Gemini responses are never rewritten."""
import json
import math
import re
from fastapi import HTTPException

MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def bad(message):
    raise HTTPException(422, message)


def model_id(value):
    if not isinstance(value, str):
        bad("model must be a string")
    value = value.removeprefix("models/")
    if not MODEL.fullmatch(value):
        bad("Use a published Gemini model ID, without URL or path components")
    return value


def native_body(body):
    allowed = {"contents", "systemInstruction", "generationConfig", "safetySettings"}
    if set(body) - allowed:
        bad("Unsupported native request fields; this gateway supports text generation")
    contents = body.get("contents")
    if not isinstance(contents, list) or not contents or len(contents) > 256:
        bad("contents must contain between 1 and 256 text messages")
    for content in contents + ([body["systemInstruction"]] if "systemInstruction" in body else []):
        if not isinstance(content, dict) or not isinstance(content.get("parts"), list) or not content["parts"]:
            bad("Each content requires text parts")
        if content.get("role", "user") not in ("user", "model", "system"):
            bad("Invalid content role")
        for part in content["parts"]:
            if not isinstance(part, dict) or set(part) != {"text"} or not isinstance(part["text"], str):
                bad("Only ordinary text parts are supported; tools and files are not supported")
    if "generationConfig" in body and not isinstance(body["generationConfig"], dict):
        bad("generationConfig must be an object")
    if "safetySettings" in body and not isinstance(body["safetySettings"], list):
        bad("safetySettings must be an array")
    return body


def chat_body(body):
    allowed = {"model", "messages", "temperature", "top_p", "top_k", "max_tokens",
               "max_completion_tokens", "stop", "stream", "stream_options", "n", "response_format"}
    if set(body) - allowed:
        bad("Unsupported OpenAI fields; only ordinary text chat is supported")
    model = model_id(body.get("model"))
    if body.get("n", 1) != 1:
        bad("Only n=1 is supported")
    if not isinstance(body.get("stream", False), bool):
        bad("stream must be a boolean")
    options = body.get("stream_options", {})
    if not isinstance(options, dict) or set(options) - {"include_usage"} or (
        "include_usage" in options and not isinstance(options["include_usage"], bool)
    ):
        bad("Unsupported stream_options")
    messages = body.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 256:
        bad("messages must contain between 1 and 256 entries")
    contents, system = [], []
    for msg in messages:
        if not isinstance(msg, dict) or set(msg) - {"role", "content", "name"}:
            bad("Unsupported message fields; tool/function history is not supported")
        role, content = msg.get("role"), msg.get("content")
        if not isinstance(content, str):
            bad("Message content must be text")
        if role in ("system", "developer"):
            system.append(content)
        elif role in ("user", "assistant"):
            contents.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": content}]})
        else:
            bad("Unsupported message role")
    if not contents:
        bad("At least one user or assistant message is required")
    result = {"contents": contents}
    if system:
        result["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
    generation = {}
    for old, new in [("temperature", "temperature"), ("top_p", "topP"), ("top_k", "topK")]:
        if old in body:
            value = body[old]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                bad(old + " must be a finite nonnegative number")
            generation[new] = value
    if "max_tokens" in body and "max_completion_tokens" in body:
        bad("Specify only one output token limit")
    limit = body.get("max_completion_tokens", body.get("max_tokens"))
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            bad("Output token limit must be a positive integer")
        generation["maxOutputTokens"] = limit
    if "stop" in body:
        stop = body["stop"]
        stop = [stop] if isinstance(stop, str) else stop
        if not isinstance(stop, list) or len(stop) > 5 or any(not isinstance(s, str) for s in stop):
            bad("stop must be text or an array of up to five strings")
        generation["stopSequences"] = stop
    if "response_format" in body:
        fmt = body["response_format"]
        if fmt not in ({"type": "text"}, {"type": "json_object"}):
            bad("Only text and json_object response formats are supported")
        if fmt["type"] == "json_object":
            generation["responseMimeType"] = "application/json"
    if generation:
        result["generationConfig"] = generation
    return model, result


def usage(data):
    m = data.get("usageMetadata", {})
    def number(k):
        v = m.get(k, 0)
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0
    prompt = number("promptTokenCount")
    completion = number("candidatesTokenCount") + number("thoughtsTokenCount")
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": number("totalTokenCount") or prompt + completion}


def finish_reason(reason):
    if reason in (None, "STOP"):
        return "stop"
    if reason == "MAX_TOKENS":
        return "length"
    return "content_filter"


def candidate(data):
    candidates = data.get("candidates", [])
    if not candidates:
        if data.get("promptFeedback", {}).get("blockReason"):
            return "", "content_filter"
        return "", None
    first = candidates[0]
    parts = first.get("content", {}).get("parts", [])
    if any(not isinstance(p, dict) or any(k not in ("text", "thought") for k in p) for p in parts):
        raise ValueError("Non-text upstream response")
    text = "".join(p.get("text", "") for p in parts if not p.get("thought", False))
    return text, finish_reason(first["finishReason"]) if "finishReason" in first else None


def sse(data):
    return "data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n\n"


async def events(response):
    """SSE parser handles comments, multi-line data, CRLF, and final unterminated events."""
    lines, size = [], 0
    async for line in response.aiter_lines():
        if not line:
            if lines:
                yield parse_event(lines)
                lines, size = [], 0
        elif line.startswith("data:"):
            value = line[5:].lstrip(" ")
            size += len(value)
            if size > 1_048_576:
                raise ValueError("Upstream event too large")
            lines.append(value)
    if lines:
        yield parse_event(lines)


def parse_event(lines):
    value = json.loads("\n".join(lines))
    if not isinstance(value, dict) or "error" in value:
        raise ValueError("Invalid or failed upstream event")
    return value
