import io
import zipfile

import pytest
from relaypay.disputes.package import PackageFile, freeze_package


def _freeze() -> bytes:
    return freeze_package(
        dispute_id="dpc_proof",
        draft_id="dpd_proof",
        response_html=b"<p>synthetic evidence</p>",
        attachments=(
            PackageFile("invoice.json", "application/json", b'{"invoice":"synthetic"}'),
            PackageFile("delivery.txt", "text/plain", b"delivered"),
        ),
        signing_secret=b"release-pinned-synthetic-signing-key",
    ).content


def test_frozen_package_is_byte_stable_and_manifest_is_first() -> None:
    first, second = _freeze(), _freeze()
    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == [
            "manifest.json",
            "response.html",
            "delivery.txt",
            "invoice.json",
        ]
        assert b'"signature"' in archive.read("manifest.json")


def test_package_rejects_unsafe_or_duplicate_attachment_names() -> None:
    with pytest.raises(ValueError, match="unique basenames"):
        freeze_package(
            dispute_id="dpc_proof",
            draft_id="dpd_proof",
            response_html=b"safe",
            attachments=(PackageFile("../secret", "text/plain", b"no"),),
            signing_secret=b"synthetic",
        )
