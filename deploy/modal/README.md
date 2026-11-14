# Private Gemma inference on Modal

The application and SQLite stay on Hostinger. Only bounded inference payloads go to this GPU endpoint. The Volume holds Hugging Face model files, never application notes or the SQLite database. Gemini public scoring is called by the application coordinator.

## Provision

1. Sign in to Modal and install its CLI in a separate deployment environment. The application packages do not import Modal.
2. Accept the Gemma model license on Hugging Face for `google/gemma-3-12b-it`; create a read token with access to that checkpoint.
3. Create a Modal Secret named `quiet-curator-gemma` containing `HF_TOKEN` and `GEMMA_TOKEN` (a random token at least 32 characters long). Use the Modal dashboard/secret manager, never put actual values in commands committed to the repository.
4. Deploy from the repository root:

```sh
uv run --with 'modal>=1.0,<2' modal deploy deploy/modal/gemma_service.py
```

5. Set the application host's `GEMMA_URL` to the returned endpoint URL plus `/v1`, and `GEMMA_TOKEN` to the same service token. Set `GEMMA_MODEL=google/gemma-3-12b-it`.
6. Set `GEMINI_API_KEY` and `GEMINI_MODEL` to a model available in the event account. Restart the Hostinger curation/web processes through the existing deployment procedure.

Model weights are pinned to revision `96b6f1eccf38110c56df3a15bffe176da04bfd80`. vLLM/Transformers are pinned in the image. A single L40S is the initial target, not a benchmarked throughput guarantee. The service scales to zero after 5 idle minutes and is capped at one container/two concurrent inputs. Measure actual startup, memory use and latency before a demo; a cold download can exceed the application's inference timeout. Failed feedback jobs retry with a bounded attempt count, and no private data is sent to Gemini on failure.

Run a synthetic profile update first, then a synthetic chunk match, before importing real private notes. Confirm the endpoint rejects an absent/wrong token, source text is absent from logs, and removing a note snapshot removes its displayed matches. This repository's tests mock inference; they do not prove GPU fit, model quality or live endpoint compatibility.

The endpoint exposes only authenticated chat completions. Application schema validation and ID checks remain mandatory. It returns model output only to the backend; the reader never receives it directly. The Gemini API key is not needed in Modal.

References: Modal Web Functions (`https://modal.com/docs/guide/webhooks`), Gemma/vLLM example (`https://modal.com/docs/examples/vllm_inference`), vLLM CLI (`https://docs.vllm.ai/en/latest/cli/serve/`).
