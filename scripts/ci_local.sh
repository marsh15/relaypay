#!/usr/bin/env bash
# Reproduce relaypay CI (release-gate + proof-gate essentials) locally, in CI order.
set -u
cd "$(dirname "$0")/.."
fail=0
step() { echo "== $1"; shift; if ! "$@"; then echo "!! FAILED: $*"; fail=1; fi; }
step lint make lint
step format make format-check
step types make typecheck
step contract make api-contract
step sdk make sdk-drift
step sdk-example make sdk-example
step verify-m7 uv run python -m scripts.verify_m7_upgrade
step migrate-check bash -c 'make migrate >/dev/null && for t in relaypay provider receiver bank commerce; do uv run alembic -c migrations/$t/alembic.ini check || exit 1; done'
for m in 15 16 17; do step "verify-m$m" uv run python -m "scripts.verify_m${m}_upgrade"; done
step release-tests make test PYTEST_MARK="not handwritten and not proof and not chaos and not live_psp"
step demo make merchant-balance-demo
uv run python - <<'PY' > /tmp/ci_isolated.sh
import re, yaml
wf = yaml.safe_load(open(".github/workflows/ci.yml"))
for st in wf["jobs"]["release-gate"]["steps"]:
    for line in re.sub(r"\\\n\s*", " ", st.get("run", "")).splitlines():
        line = line.strip()
        if line.startswith("uv run pytest"):
            print(re.sub(r"--junitxml=\S+", "", line) + " -q -p no:cacheprovider")
PY
while read -r cmd; do step "isolated: ${cmd:0:70}" bash -c "$cmd"; done < /tmp/ci_isolated.sh
step evaluations uv run python -m scripts.run_evaluations
step proof-tests uv run pytest -q -p no:cacheprovider -m "(proof or chaos) and not handwritten and not live_psp"
step chaos uv run python -m scripts.chaos.run --mode ci --seeds 6 --ops 1000
echo "== RESULT: $([ $fail = 0 ] && echo ALL PASS || echo FAILURES)"
exit $fail
