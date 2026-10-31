"""Package constraints: stdlib only, no network, and no import of the RelayPay packages."""

import ast
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "packages" / "relaypay_reconcile"
PACKAGE_NAME = "relaypay_reconcile"
NETWORK_MODULES = frozenset(
    {
        "asyncio",
        "ftplib",
        "http",
        "imaplib",
        "poplib",
        "selectors",
        "smtplib",
        "socket",
        "ssl",
        "urllib",
        "xmlrpc",
    }
)


def _imported_top_level_names(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_package_sources_exist() -> None:
    assert sorted(path.name for path in PACKAGE_ROOT.joinpath(PACKAGE_NAME).glob("*.py"))


def test_every_import_is_standard_library_or_the_package_itself() -> None:
    package_sources = sorted(PACKAGE_ROOT.joinpath(PACKAGE_NAME).glob("*.py"))
    for source in package_sources:
        imported = _imported_top_level_names(source)
        outside = {
            name
            for name in imported
            if name not in sys.stdlib_module_names and name != PACKAGE_NAME
        }
        assert not outside, f"{source.name} imports non-stdlib modules: {sorted(outside)}"


def test_package_imports_no_network_module_and_no_relaypay_package() -> None:
    for source in sorted(PACKAGE_ROOT.joinpath(PACKAGE_NAME).glob("*.py")):
        imported = _imported_top_level_names(source)
        assert not imported & NETWORK_MODULES, f"{source.name} imports a network module"
        assert "relaypay" not in imported, f"{source.name} imports the relaypay package"
        assert "relaypay_sdk" not in imported, f"{source.name} imports the relaypay SDK"
