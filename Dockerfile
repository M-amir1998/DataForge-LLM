# syntax=docker/dockerfile:1.7
# Lightweight CPU-only images:
#   runtime - the `dataforge` CLI for pipeline jobs (non-root, no build tools)
#   ci      - a Jenkins agent image with git, curl, and uv (non-root `jenkins` user)
# Build:  docker build --target runtime -t dataforge-llm .
ARG PYTHON_VERSION=3.12

FROM ghcr.io/astral-sh/uv:0.11.21 AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first so they cache independently of source changes.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
LABEL org.opencontainers.image.title="DataForge-LLM" \
      org.opencontainers.image.description="Reproducible pretraining-data engineering CLI (CPU only)" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/M-amir1998/DataForge-LLM"
RUN groupadd --system --gid 10001 dataforge \
 && useradd --system --uid 10001 --gid dataforge --create-home --home-dir /home/dataforge dataforge
COPY --from=builder --chown=dataforge:dataforge /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /work
USER dataforge
ENTRYPOINT ["dataforge"]
CMD ["--help"]

FROM python:${PYTHON_VERSION}-slim-bookworm AS ci
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /usr/local/bin/
RUN groupadd --gid 1000 jenkins \
 && useradd --uid 1000 --gid jenkins --create-home --home-dir /home/jenkins jenkins
ENV UV_CACHE_DIR=/home/jenkins/.cache/uv \
    UV_LINK_MODE=copy \
    PYTHONUTF8=1
USER jenkins
WORKDIR /home/jenkins/agent
