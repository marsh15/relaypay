import hashlib
import hmac
import io
import zipfile
from dataclasses import dataclass
from typing import Final

from relaypay.idempotency import canonical_json_bytes

MAX_ATTACHMENT_BYTES: Final = 5 * 1024 * 1024
MAX_PACKAGE_BYTES: Final = 20 * 1024 * 1024
_ZIP_TIME: Final = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class PackageFile:
    name: str
    media_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class FrozenPackage:
    content: bytes
    sha256: bytes
    manifest: dict[str, object]


def _write_file(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, content)


def freeze_package(
    *,
    dispute_id: str,
    draft_id: str,
    response_html: bytes,
    attachments: tuple[PackageFile, ...],
    signing_secret: bytes,
) -> FrozenPackage:
    if any(len(item.content) > MAX_ATTACHMENT_BYTES for item in attachments):
        raise ValueError("attachment exceeds 5 MiB")
    names = [item.name for item in attachments]
    if len(names) != len(set(names)) or any("/" in name or "\\" in name for name in names):
        raise ValueError("attachment names must be unique basenames")
    files = [
        {
            "name": "response.html",
            "mediaType": "text/html; charset=utf-8",
            "sha256": hashlib.sha256(response_html).hexdigest(),
            "byteLength": len(response_html),
        },
        *[
            {
                "name": item.name,
                "mediaType": item.media_type,
                "sha256": hashlib.sha256(item.content).hexdigest(),
                "byteLength": len(item.content),
            }
            for item in sorted(attachments, key=lambda value: value.name)
        ],
    ]
    unsigned = {"schemaVersion": 1, "disputeId": dispute_id, "draftId": draft_id, "files": files}
    signature = hmac.new(signing_secret, canonical_json_bytes(unsigned), hashlib.sha256).hexdigest()
    manifest: dict[str, object] = {**unsigned, "signature": signature}
    manifest_bytes = canonical_json_bytes(manifest)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        _write_file(archive, "manifest.json", manifest_bytes)
        _write_file(archive, "response.html", response_html)
        for item in sorted(attachments, key=lambda value: value.name):
            _write_file(archive, item.name, item.content)
    content = buffer.getvalue()
    if len(content) > MAX_PACKAGE_BYTES:
        raise ValueError("assembled package exceeds 20 MiB")
    return FrozenPackage(content, hashlib.sha256(content).digest(), manifest)
