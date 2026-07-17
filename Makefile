.PHONY: install lint format typecheck unit test infra-up infra-down migrate seed reset demo console-install console-check console-e2e check

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
	uv run alembic -c migrations/receiver/alembic.ini upgrade head

seed:
	uv run python -m scripts.seed

reset:
	uv run python -m scripts.reset_sandbox

demo:
	uv run python -m scripts.lost_response_demo

console-install:
	cd apps/console && npm ci

console-check:
	cd apps/console && npm run lint && npm run typecheck && npm run build && npm audit --omit=dev

console-e2e:
	cd apps/console && npm run test:e2e

check: lint typecheck test console-check
