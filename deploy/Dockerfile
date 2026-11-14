# Build from the repo root: docker build -f deploy/Dockerfile .
# One image runs all three processes (pick with the compose `command`).
# --build-arg PACKAGE=curator-ingest gives a slim image with only that package's deps.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG PACKAGE=""
WORKDIR /app
ENV UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" DATA_DIR=/app/data

COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/
COPY packages/ingest/pyproject.toml packages/ingest/
COPY packages/curation/pyproject.toml packages/curation/
COPY packages/web/pyproject.toml packages/web/
RUN uv sync --frozen --no-dev --no-install-workspace $([ -n "$PACKAGE" ] && echo "--package $PACKAGE" || echo --all-packages)

COPY packages/ packages/
COPY scripts/ scripts/
RUN uv sync --frozen --no-dev $([ -n "$PACKAGE" ] && echo "--package $PACKAGE" || echo --all-packages)

EXPOSE 8000
CMD ["curator-web"]
