.PHONY: install lint format format-check typecheck unit test handwritten proof chaos chaos-full evaluations portfolio-demo api-contract sdk-drift sdk-example m7-evidence edge-install edge-check compose-check infra-up infra-down migrate seed reset demo reconciliation-demo merchant-balance-demo connector-demo dependency-audit console-install console-check console-e2e check

install:
	uv sync --frozen

lint:
	uv run ruff check .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

typecheck:
	uv run mypy packages apps scripts

unit:
	uv run pytest tests/unit -m "not live_psp"

# CI narrows this to "not handwritten and not proof and not chaos"; the proof-gate job runs the rest.
PYTEST_MARK ?= not handwritten and not live_psp

test:
	uv run pytest -m "($(PYTEST_MARK)) and not live_psp"

handwritten:
	uv run pytest -m "handwritten and not live_psp"

proof:
	uv run pytest -m "proof and not live_psp"

chaos:
	uv run python -m scripts.chaos.run --mode ci --seeds 20 --ops 1000

chaos-full:
	uv run python -m scripts.chaos.run --mode full --seeds 200 --ops 10000

evaluations:
	uv run python -m scripts.run_evaluations

portfolio-demo:
	uv run python -m scripts.portfolio_demo

api-contract:
	uv run python -m scripts.generate_openapi --baseline --check
	uv run python -m scripts.generate_openapi --check
	uv run python -m scripts.check_api_compatibility

sdk-drift:
	uv run python -m scripts.check_sdk_generation

sdk-example:
	uv run python examples/python/verify_webhook.py

m7-evidence:
	uv run python -m scripts.capture_m7_query_plans --output output/m7/query-plans.json
	uv run python -m scripts.measure_m7_performance --output output/m7/performance.json
	uv run python -m scripts.verify_m7_evidence output/m7/performance.json output/m7/query-plans.json

edge-install:
	npm ci --ignore-scripts --prefix apps/edge

edge-check:
	npm run check --prefix apps/edge

compose-check:
	docker compose --env-file .env.example config --quiet
	docker compose --env-file .env.example --profile observability config --quiet
	docker compose --env-file .env.production.example -f compose.yaml -f compose.production.yaml config --quiet
	docker compose --env-file .env.production.example -f compose.yaml -f compose.production.yaml --profile observability config --quiet

infra-up:
	docker compose up -d postgres

infra-down:
	docker compose down

migrate:
	uv run alembic -c migrations/relaypay/alembic.ini upgrade head
	uv run alembic -c migrations/provider/alembic.ini upgrade head
	uv run alembic -c migrations/receiver/alembic.ini upgrade head
	uv run alembic -c migrations/bank/alembic.ini upgrade head
	uv run alembic -c migrations/commerce/alembic.ini upgrade head

seed:
	uv run python -m scripts.seed

reset:
	uv run python -m scripts.reset_sandbox

demo:
	uv run python -m scripts.lost_response_demo

reconciliation-demo:
	uv run python -m scripts.reconciliation_demo

merchant-balance-demo:
	uv run python -m scripts.merchant_balance_demo

connector-demo:
	uv run python -m scripts.connector_demo

console-install:
	cd apps/console && npm ci --ignore-scripts

console-check:
	cd apps/console && npm run lint && npm run typecheck && npm run build && npm audit --package-lock-only --ignore-scripts --audit-level=high

console-e2e:
	cd apps/console && npm run test:e2e

check: lint format-check typecheck api-contract sdk-drift sdk-example test console-check edge-check compose-check

.PHONY: load load-check reconcile-check reconcile-proof

load:
	uv run python -m scripts.run_loadtest

load-check:
	node --experimental-vm-modules --test loadtest/k6/common.test.mjs
	uv run pytest tests/unit/test_loadtest_credentials.py -q

reconcile-check:
	uv run pytest tests/unit/reconcile -m "not proof" -q

reconcile-proof:
	uv run pytest tests/unit/reconcile/test_reconcile_memory_proof.py -m proof -q

# Inspect committed locks without running dependency installation scripts.
dependency-audit:
	uv audit --frozen
	npm audit --prefix apps/console --package-lock-only --ignore-scripts --audit-level=high
	npm audit --prefix apps/edge --package-lock-only --ignore-scripts --audit-level=high
