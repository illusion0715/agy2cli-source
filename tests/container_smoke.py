"""Real loopback Docker smoke; only the test image injects a simulated official transport.
Run after building runtime and test targets: python -m tests.container_smoke
"""
import json
from contextlib import closing
import os
import subprocess
import time
import uuid

import httpx
from tests.fixtures import GATEWAY_KEY, MODEL, TEXT

DOCKER = ["docker", "--host=unix:///var/run/docker.sock"]


def command(*args):
    return subprocess.check_output(DOCKER + list(args), text=True).strip()


def main():
    suffix = uuid.uuid4().hex[:10]
    volume = "agy-official-test-" + suffix
    active = []
    results = {}
    command("volume", "create", "--label", "purpose=isolated-official-gateway-test", volume)

    def start(image, mock=False):
        args = ["run", "-d", "--read-only", "--cap-drop=ALL", "--security-opt", "no-new-privileges:true",
                "--tmpfs", "/tmp:rw,nosuid,size=16m", "-p", "127.0.0.1::8080", "-v", volume + ":/data", image]
        if mock:
            args += ["uvicorn", "tests.serve_mock:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
        container = command(*args)
        active.append(container)
        port = command("port", container, "8080/tcp").rsplit(":", 1)[1]
        client = httpx.Client(base_url="http://127.0.0.1:" + port, trust_env=False, timeout=10)
        for _ in range(80):
            try:
                if client.get("/healthz").status_code == 200:
                    return container, client
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise AssertionError("Container did not become healthy")

    try:
        container, client = start("agy2api-official:local")
        with closing(client):
            assert client.get("/healthz").json() == {"status": "ok"}
            assert client.get("/readyz").status_code == 503
            assert client.get("/v1/models").status_code == 503
            assert command("exec", container, "id", "-u") == "10001"
            command("exec", container, "sh", "-c", "test ! -e /app/tests")
            results["runtime_boot_nonroot_no_test_code"] = True
            results["missing_configuration_fails_closed"] = True
        command("rm", "-f", container)
        active.remove(container)

        container, client = start("agy2api-official:test", mock=True)
        auth = {"Authorization": "Bearer " + GATEWAY_KEY}
        with closing(client):
            assert client.get("/readyz").status_code == 200
            assert client.get("/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
            assert client.get("/v1/models", headers=auth).json()["data"][0]["id"] == MODEL
            body = {"model": MODEL, "messages": [{"role": "user", "content": "Synthetic question"}]}
            data = client.post("/v1/chat/completions", headers=auth, json=body).json()
            assert data["choices"][0]["message"]["content"] == TEXT
            received = []
            with client.stream("POST", "/v1/chat/completions", headers=auth, json={**body, "stream": True}) as stream:
                assert stream.status_code == 200
                for line in stream.iter_lines():
                    if line.startswith("data: "):
                        received.append((time.monotonic(), line[6:]))
            assert received[-1][1] == "[DONE]"
            text_frames = [(t, json.loads(value)["choices"][0]["delta"].get("content"))
                           for t, value in received if value != "[DONE]" and json.loads(value).get("choices")]
            first = next(t for t, content in text_frames if content == "Synthetic ")
            second = next(t for t, content in text_frames if content == "hello")
            assert second - first >= 0.05
            results["sse_text_frames_arrive_incrementally_over_tcp"] = True
            native = client.post(f"/v1beta/models/{MODEL}:generateContent", headers=auth,
                                 json={"contents": [{"parts": [{"text": "Synthetic question"}]}]})
            assert native.json()["candidates"][0]["content"]["parts"][0]["text"] == TEXT
            before = client.get("/usage", headers=auth).json()["rows"]
            assert sum(row["requests"] for row in before) >= 4
            results["real_http_models_chat_native_and_sse_with_mock"] = True
        command("rm", "-f", container)
        active.remove(container)
        container, client = start("agy2api-official:test", mock=True)
        with closing(client):
            after = client.get("/usage", headers=auth).json()["rows"]
            assert before == after
            results["named_volume_persists_across_container_recreation"] = True
        results["real_provider_calls"] = 0
        print(json.dumps(results, indent=2))
    finally:
        for container in active:
            subprocess.run(DOCKER + ["rm", "-f", container], check=False, capture_output=True)
        subprocess.run(DOCKER + ["volume", "rm", volume], check=False, capture_output=True)


if __name__ == "__main__":
    main()
