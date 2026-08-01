import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "packages/relaypay_sdk/_generated"


def _digests(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="relaypay-sdk-") as temporary:
        generated = Path(temporary) / "generated"
        subprocess.run(  # noqa: S603 - fixed local module and repository-owned inputs
            [
                sys.executable,
                "-m",
                "openapi_python_client",
                "generate",
                "--path",
                str(ROOT / "contracts/openapi/v0.9.0.json"),
                "--config",
                str(ROOT / "openapi-python-client.yml"),
                "--output-path",
                str(generated),
            ],
            cwd=ROOT,
            check=True,
        )
        committed_files = _digests(COMMITTED)
        generated_files = _digests(generated)
        if committed_files != generated_files:
            differences = sorted(
                path
                for path in committed_files.keys() | generated_files.keys()
                if committed_files.get(path) != generated_files.get(path)
            )
            raise SystemExit(f"Generated SDK drift detected: {differences}")
    print("Generated SDK drift: PASS")


if __name__ == "__main__":
    main()
