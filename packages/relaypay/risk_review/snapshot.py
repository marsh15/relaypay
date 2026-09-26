"""Transports for the synthetic merchant-site snapshot service."""

from __future__ import annotations

from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from relaypay.errors import RelayPayError


class SiteSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    site_ref: str = Field(alias="siteRef", min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=64)
    snapshot: dict[str, object]
    snapshot_sha256: str = Field(alias="snapshotSha256", pattern=r"^[0-9a-f]{64}$")


class SiteSnapshotSource(Protocol):
    def fetch(self, site_ref: str) -> SiteSnapshot: ...


class DeterministicSiteSnapshotSource:
    """In-process deterministic snapshots mirroring the mock service fixtures."""

    def fetch(self, site_ref: str) -> SiteSnapshot:
        from fastapi.testclient import TestClient

        from apps.merchant_site.main import build_app

        client = TestClient(build_app())
        response = client.get(f"/v1/sites/{site_ref}/snapshot")
        if response.status_code != 200:
            raise RelayPayError(
                code="SITE_SNAPSHOT_UNKNOWN",
                message="Unknown synthetic site reference",
                http_status=404,
            )
        return SiteSnapshot.model_validate(response.json())


class HTTPSiteSnapshotSource:
    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def fetch(self, site_ref: str) -> SiteSnapshot:
        try:
            response = httpx.get(
                f"{self._base_url}/v1/sites/{site_ref}/snapshot",
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise RelayPayError(
                code="SITE_SNAPSHOT_UNAVAILABLE",
                message="Synthetic merchant-site service is unavailable",
                http_status=503,
                retry_after=5,
            ) from exc
        if response.status_code != 200:
            raise RelayPayError(
                code="SITE_SNAPSHOT_UNKNOWN",
                message="Unknown synthetic site reference",
                http_status=404,
            )
        return SiteSnapshot.model_validate(response.json())
