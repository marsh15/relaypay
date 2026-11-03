from typing import Annotated

from fastapi import Body, FastAPI, Header
from relaypay.body_limit import install_request_body_limit
from relaypay.disputes.network import DeterministicDisputeNetwork
from relaypay.disputes.package import MAX_PACKAGE_BYTES


def create_app(network: DeterministicDisputeNetwork | None = None) -> FastAPI:
    resolved = network or DeterministicDisputeNetwork()
    app = FastAPI(
        title="RelayPay synthetic dispute network",
        version="1.0.1",
        description="Synthetic data only. Never send real dispute or customer evidence.",
    )
    # Packages are raw zips capped at MAX_PACKAGE_BYTES (20 MiB), so a 2 MiB cap would reject
    # valid submissions. The cap is the package bound itself.
    install_request_body_limit(app, max_bytes=MAX_PACKAGE_BYTES)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.post("/v1/submissions")
    def submit(
        package: Annotated[bytes, Body(media_type="application/zip")],
        stable_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
    ) -> dict[str, object]:
        observation = resolved.submit(stable_key=stable_key, package_bytes=package)
        return {
            "status": observation.status,
            "code": observation.code,
            "effectCount": resolved.effect_count,
        }

    @app.get("/v1/submissions/{stable_key}")
    def lookup(stable_key: str) -> dict[str, object]:
        observation = resolved.lookup(stable_key=stable_key)
        return {
            "status": observation.status,
            "code": observation.code,
            "effectCount": resolved.effect_count,
        }

    return app
