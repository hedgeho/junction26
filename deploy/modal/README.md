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
6. Set `GEMINI_API_KEY` for the default `gemini-decision-1` scorer. For the previous generative scorer, set `SCORING_BACKEND=generative` and `GEMINI_MODEL=gemini-3.1-flash-lite`. Restart the Hostinger curation/web processes through the existing deployment procedure.

Model weights are pinned to revision `96b6f1eccf38110c56df3a15bffe176da04bfd80`. vLLM/Transformers are pinned in the image. The 12B model runs in bfloat16 on an L40S (48 GB), text only. The 1B model edited profiles unreliably. One container is kept warm (`min_containers=1`), because cold starts exceeded the application's inference timeout. That bills the GPU around the clock. The service is capped at one container/two concurrent inputs. Failed feedback jobs retry with a bounded attempt count, and no private data is sent to Gemini on failure.

Run a synthetic profile update first, then a synthetic chunk match, before importing real private notes. Confirm the endpoint rejects an absent/wrong token, source text is absent from logs, and removing a note snapshot removes its displayed matches. This repository's tests mock inference; they do not prove GPU fit, model quality or live endpoint compatibility.

The endpoint exposes only authenticated chat completions. Application schema validation and ID checks remain mandatory. It returns model output only to the backend; the reader never receives it directly. The Gemini API key is not needed in Modal.

References: Modal Web Functions (`https://modal.com/docs/guide/webhooks`), Gemma/vLLM example (`https://modal.com/docs/examples/vllm_inference`), vLLM CLI (`https://docs.vllm.ai/en/latest/cli/serve/`).

## Notes-aware feed scoring

Set `SCORING_BACKEND=gemma` on the application host and restart both web and curation processes. Gemma then scores feed candidates as well as processing feedback and note matches. The worker queues note-derived feed-profile updates when this backend is enabled. Existing Gemini installations keep their current backend until explicitly changed. If a feed profile has incorporated notes, switching back to Gemini is blocked rather than sending those interests to Gemini.

A Drive folder requires a Drive API service-account JSON at `GOOGLE_APPLICATION_CREDENTIALS`, accessible inside both application containers. Share the folder with the service-account email as Viewer; access granted to a separate Google account does not grant this service account access. Keep the JSON outside Git. Model and Drive access must be checked against the live deployment; tests use mocked services.


## Temporary demo keep-alive

With the normal application credentials configured, run `uv run python scripts/keep_gemma_warm.py --hours 4` from the repository root. It sends one synthetic one-token request per minute, prepares the fixed JSON output formats, and stops after the requested window. Status, process ID and stop time are written to ignored `data/gemma-keepwarm.json`; credentials and source text are never written there. The host must remain awake and connected. This supplements the deployed container configuration; it does not change Modal deployment settings or guarantee latency while other inference requests are queued. The worker also performs a non-blocking synthetic format warm-up at startup (`GEMMA_WARMUP=true`). Initial schema preparation and GPU startup are separate costs, so a warm GPU alone does not prove every first structured request will be fast.
