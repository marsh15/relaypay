import json
import stat
from pathlib import Path

import pytest

from scripts.loadtest_seed import write_credentials


def test_credentials_are_private_and_never_overwrite_existing_files(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    write_credentials(path, {"apiKey": "synthetic-secret"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        write_credentials(path, {"apiKey": "replacement"})
    assert json.loads(path.read_text()) == {"apiKey": "synthetic-secret"}


def test_credentials_refuse_symlink_targets(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("preserved")
    link = tmp_path / "credentials.json"
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        write_credentials(link, {"apiKey": "synthetic-secret"})
    assert target.read_text() == "preserved"
