.PHONY: install lint format typecheck unit test infra-up infra-down migrate seed demo

install:
	uv sync --frozen

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy packages apps scripts

unit:
	uv run pytest tests/unit

test:
	uv run pytest

infra-up:
	docker compose up -d postgres redis

infra-down:
	docker compose down

migrate:
	uv run alembic -c migrations/relaypay/alembic.ini upgrade head
	uv run alembic -c migrations/provider/alembic.ini upgrade head

seed:
	uv run python -m scripts.seed

demo:
	uv run python -m scripts.lost_response_demo

