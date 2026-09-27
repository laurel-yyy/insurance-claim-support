# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# The app (prompts, templates and the UI are package data), then the data it reads at runtime.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable
COPY apps/insurance_claims/fixtures ./apps/insurance_claims/fixtures
COPY data/kb ./data/kb
COPY evals/scenarios ./evals/scenarios

RUN useradd --create-home --uid 10001 app && mkdir -p /app/var && chown app /app/var
USER app

ENV PATH="/app/.venv/bin:$PATH" \
    DEMO_TODAY=2026-03-10
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"]
CMD ["uvicorn", "sop_agent.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
