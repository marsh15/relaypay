import json
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "contracts/openapi/api-v1-v0.6.0.json"
CURRENT = ROOT / "contracts/openapi/v0.8.0.json"
METHODS = {"get", "post", "put", "patch", "delete"}


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text()))


def main() -> None:
    baseline = _load(BASELINE)
    current = _load(CURRENT)
    failures: list[str] = []
    for path, baseline_path in baseline["paths"].items():
        if not path.startswith("/api/v1/"):
            continue
        current_path = current["paths"].get(path)
        if current_path is None:
            failures.append(f"removed path: {path}")
            continue
        for method, operation in baseline_path.items():
            if method not in METHODS:
                continue
            if current_path.get(method) != operation:
                failures.append(f"changed operation: {method.upper()} {path}")
    if failures:
        raise SystemExit("Incompatible /api/v1 change:\n" + "\n".join(failures))
    print("Frozen /api/v1 compatibility: PASS")


if __name__ == "__main__":
    main()
