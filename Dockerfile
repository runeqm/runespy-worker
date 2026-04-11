FROM python:3.12-slim

ARG VCS_REF=unknown

COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1
ENV RUNESPY_WORKER_COMMIT=${VCS_REF}
LABEL org.opencontainers.image.revision=${VCS_REF}

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN uv sync --no-dev

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

# Default: web UI. Override with --entrypoint /entrypoint.sh for headless mode.
CMD ["uv", "run", "python", "-m", "runespy_worker.webui"]
