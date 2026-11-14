"""Gemma inference on Modal. Only model weights are persisted in the Volume.
Deploy from this directory with: uv run --with 'modal>=1.0,<2' modal deploy gemma_service.py
"""

import modal

MODEL = "google/gemma-3-12b-it"
REVISION = "96b6f1eccf38110c56df3a15bffe176da04bfd80"
app = modal.App("quiet-curator-gemma")
weights = modal.Volume.from_name("quiet-curator-model-weights", create_if_missing=True)
image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .pip_install("vllm==0.21.0", "transformers==5.8.1", "fastapi==0.162.7", "httpx==0.28.1")
    .env({"HF_HOME": "/weights", "VLLM_LOGGING_LEVEL": "WARNING", "HF_HUB_DISABLE_TELEMETRY": "1"})
)


@app.cls(
    image=image,
    gpu="L40S",
    volumes={"/weights": weights},
    secrets=[modal.Secret.from_name("quiet-curator-gemma")],
    max_containers=1,
    scaledown_window=300,
    timeout=300,
    startup_timeout=1200,
)
@modal.concurrent(max_inputs=2)
class Gemma:
    @modal.enter()
    def start(self):
        import os
        import subprocess
        import time

        import httpx

        self.token = os.environ["GEMMA_TOKEN"]
        if len(self.token) < 32:
            raise RuntimeError("Use a high-entropy GEMMA_TOKEN")
        # Never put credentials in the subprocess argv or print them.
        env = os.environ.copy()
        env["VLLM_API_KEY"] = self.token
        self.process = subprocess.Popen(
            [
                "vllm",
                "serve",
                MODEL,
                "--revision",
                REVISION,
                "--served-model-name",
                MODEL,
                "--host",
                "127.0.0.1",
                "--port",
                "8001",
                "--dtype",
                "bfloat16",
                "--max-model-len",
                "32768",
                "--max-num-seqs",
                "2",
                "--gpu-memory-utilization",
                "0.9",
                "--enforce-eager",
                "--disable-log-stats",
                "--uvicorn-log-level",
                "warning",
                "--limit-mm-per-prompt",
                '{"image":0,"video":0}',
            ],
            env=env,
        )
        deadline = time.monotonic() + 1000
        with httpx.Client(timeout=5) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("Gemma failed to start")
                try:
                    if client.get("http://127.0.0.1:8001/health").status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(2)
        raise RuntimeError("Gemma startup timed out")

    @modal.exit()
    def stop(self):
        if hasattr(self, "process"):
            self.process.terminate()

    @modal.asgi_app()
    def api(self):
        import hmac
        import json

        import httpx
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import JSONResponse

        api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @api.post("/v1/chat/completions")
        async def completions(request: Request):
            if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + self.token):
                raise HTTPException(401, "Unauthorized")
            body = bytearray()
            async for part in request.stream():
                body.extend(part)
                if len(body) > 200000:
                    raise HTTPException(413, "Input too large")
            try:
                data = json.loads(body)
                if not isinstance(data, dict) or data.get("model") != MODEL or data.get("stream"):
                    raise ValueError()
                if set(data) - {"model", "temperature", "max_tokens", "messages", "response_format"}:
                    raise ValueError()
                if not isinstance(data.get("max_tokens"), int) or not 1 <= data["max_tokens"] <= 8192:
                    raise ValueError()
            except (ValueError, TypeError):
                raise HTTPException(400, "Invalid request") from None
            async with httpx.AsyncClient(timeout=240) as client:
                response = await client.post(
                    "http://127.0.0.1:8001/v1/chat/completions",
                    headers={"Authorization": "Bearer " + self.token},
                    json=data,
                )
            if response.status_code != 200:
                # Never forward raw vLLM errors, which can echo source text.
                raise HTTPException(502, "Inference failed")
            return JSONResponse(response.json(), headers={"Cache-Control": "no-store"})

        return api
