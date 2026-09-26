.PHONY: install dev test live eval lint typecheck check docker-build docker-run

install:
	uv sync

dev:
	uv run uvicorn sop_agent.main:create_app --factory --reload --port 8000

test:
	uv run pytest -m "not live"

live:
	uv run pytest -m live

eval:
	uv run python -m evals.run

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy src

check: lint typecheck test

docker-build:
	docker build -t insurance-sop-agent .

docker-run:
	docker run --rm -p 8000:8000 --env-file .env insurance-sop-agent
