"""Single broker-less scheduler for every RelayPay background batch.

PostgreSQL is the only correctness authority: each batch claims leased work
with row locks (``SKIP LOCKED`` plus lease tokens), so a crashed poller simply
restarts and reclaims. There is no broker — one process owns the whole
schedule below, time-gated at the cadences the platform was shipped with.
"""

import logging
import time
from datetime import UTC, datetime

from relaypay.agent_runtime.events import RedpandaPublisher, publish_one
from relaypay.config import Settings, get_settings
from relaypay.connectors.service import claim_inbound_webhook, process_inbound_claim
from relaypay.database import build_engine, build_session_factory
from relaypay.event_delivery.delivery import HTTPWebhookTransport, run_delivery_batch
from relaypay.event_delivery.materializer import materialize_deliveries
from relaypay.mock_commerce.service import synchronize_event
from relaypay.observability.metrics import observe_worker_task, start_worker_metrics_server
from relaypay.observability.telemetry import configure_tracing
from relaypay.payouts.service import HTTPBankTransport, run_payout_batch
from relaypay.provider_operations.recovery import run_recovery_batch
from relaypay.provider_operations.service import HTTPProviderTransport
from relaypay.reconciliation.service import run_reconciliation_batch
from relaypay.settlement_intelligence.execution import run_forecast_batch
from relaypay.subscriptions.execution import run_recovery_action_batch
from relaypay.subscriptions.network import HTTPCommunicationNetwork, HTTPRecurringPaymentNetwork
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

# Seconds between runs of each batch; cadences preserved from the shipped
# beat schedule. Every task is a periodic authoritative scan of PostgreSQL.
SCHEDULE_SECONDS: dict[str, float] = {
    "recover_provider_operations": 1.0,
    "materialize_webhook_deliveries": 1.0,
    "deliver_webhooks": 1.0,
    "reconcile_statements": 1.0,
    "dispatch_payouts": 1.0,
    "run_subscription_recovery": 1.0,
    "process_inbound_webhooks": 2.0,
    "record_settlement_forecasts": 3600.0,
}
OUTBOX_DRAIN_CAP = 100


@observe_worker_task("recover_provider_operations")
def _recover_provider_operations(factory: sessionmaker[Session], settings: Settings) -> int:
    return run_recovery_batch(
        factory,
        provider_account_id=settings.PROVIDER_ACCOUNT_ID,
        provider_signing_secret=settings.PROVIDER_SIGNING_SECRET.get_secret_value(),
        transport=HTTPProviderTransport(base_url=settings.PROVIDER_BASE_URL),
    )


@observe_worker_task("materialize_webhook_deliveries")
def _materialize_webhook_deliveries(factory: sessionmaker[Session], settings: Settings) -> int:
    del settings
    return materialize_deliveries(factory)


@observe_worker_task("deliver_webhooks")
def _deliver_webhooks(factory: sessionmaker[Session], settings: Settings) -> int:
    receiver_url = f"{settings.RECEIVER_BASE_URL.rstrip('/')}/webhooks/relaypay"
    return run_delivery_batch(
        factory,
        encryption_key=settings.WEBHOOK_SECRET_ENCRYPTION_KEY.get_secret_value(),
        transport=HTTPWebhookTransport(allowed_url=receiver_url),
    )


@observe_worker_task("reconcile_statements")
def _reconcile_statements(factory: sessionmaker[Session], settings: Settings) -> int:
    del settings
    return run_reconciliation_batch(factory)


@observe_worker_task("dispatch_payouts")
def _dispatch_payouts(factory: sessionmaker[Session], settings: Settings) -> int:
    return run_payout_batch(
        factory,
        bank_account_id=settings.BANK_ACCOUNT_ID,
        bank_signing_secret=settings.BANK_SIGNING_SECRET.get_secret_value(),
        transport=HTTPBankTransport(base_url=settings.BANK_BASE_URL),
    )


@observe_worker_task("run_subscription_recovery")
def _run_subscription_recovery(factory: sessionmaker[Session], settings: Settings) -> int:
    return run_recovery_action_batch(
        factory,
        communication_network=HTTPCommunicationNetwork(settings.RECOVERY_NETWORK_BASE_URL),
        payment_network=HTTPRecurringPaymentNetwork(settings.RECOVERY_NETWORK_BASE_URL),
    )


@observe_worker_task("process_inbound_webhooks")
def _process_inbound_webhooks(
    factory: sessionmaker[Session], commerce_factory: sessionmaker[Session]
) -> int:
    processed = 0
    while (claim := claim_inbound_webhook(factory)) is not None:
        process_inbound_claim(
            factory,
            claim,
            handler=lambda payload, event_id: synchronize_event(
                commerce_factory, payload, event_id
            ),
        )
        processed += 1
    return processed


@observe_worker_task("record_settlement_forecasts")
def _record_settlement_forecasts(factory: sessionmaker[Session], settings: Settings) -> int:
    del settings
    return run_forecast_batch(factory)


def poll_once(
    settings: Settings,
    *,
    factory: sessionmaker[Session] | None = None,
    commerce_factory: sessionmaker[Session] | None = None,
    due: dict[str, float] | None = None,
) -> dict[str, int]:
    """Run every batch that is due. Callers that pass a persistent factory
    avoid per-tick engine churn; standalone callers get disposable engines."""
    owned_relay_engine: Engine | None = None
    owned_commerce_engine: Engine | None = None
    if factory is None:
        owned_relay_engine = build_engine(
            settings.RELAYPAY_DATABASE_URL.get_secret_value(),
            application_name="relaypay-postgres-poller",
        )
        factory = build_session_factory(owned_relay_engine)
    if commerce_factory is None:
        owned_commerce_engine = build_engine(
            settings.COMMERCE_DATABASE_URL.get_secret_value(),
            application_name="relaypay-inbound-commerce",
        )
        commerce_factory = build_session_factory(owned_commerce_engine)
    event_publisher: RedpandaPublisher | None = None
    try:
        now = time.monotonic()
        results: dict[str, int] = {}
        for task in SCHEDULE_SECONDS:
            if due is not None and now < due.get(task, 0.0):
                continue
            if task == "process_inbound_webhooks":
                results[task] = _process_inbound_webhooks(factory, commerce_factory)
            else:
                results[task] = _RUNNERS[task](factory, settings)
        event_publisher = RedpandaPublisher(settings.REDPANDA_BROKERS)
        published = 0
        while published < OUTBOX_DRAIN_CAP and int(
            publish_one(factory, event_publisher, now=datetime.now(UTC))
        ):
            published += 1
        results["published"] = published
        return results
    finally:
        if event_publisher is not None:
            event_publisher.close()
        if owned_relay_engine is not None:
            owned_relay_engine.dispose()
        if owned_commerce_engine is not None:
            owned_commerce_engine.dispose()


_RUNNERS = {
    "recover_provider_operations": _recover_provider_operations,
    "materialize_webhook_deliveries": _materialize_webhook_deliveries,
    "deliver_webhooks": _deliver_webhooks,
    "reconcile_statements": _reconcile_statements,
    "dispatch_payouts": _dispatch_payouts,
    "run_subscription_recovery": _run_subscription_recovery,
    "record_settlement_forecasts": _record_settlement_forecasts,
}


def main() -> None:
    settings = get_settings()
    configure_tracing(settings, service_name="relaypay-poller")
    start_worker_metrics_server(settings.PROMETHEUS_WORKER_PORT)
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(),
        application_name="relaypay-postgres-poller",
    )
    commerce_engine = build_engine(
        settings.COMMERCE_DATABASE_URL.get_secret_value(),
        application_name="relaypay-inbound-commerce",
    )
    factory = build_session_factory(engine)
    commerce_factory = build_session_factory(commerce_engine)
    next_due: dict[str, float] = {}
    try:
        while True:
            try:
                poll_once(
                    settings,
                    factory=factory,
                    commerce_factory=commerce_factory,
                    due=next_due,
                )
                now = time.monotonic()
                for task, interval in SCHEDULE_SECONDS.items():
                    next_due[task] = now + interval
            except Exception:
                logger.exception("postgres_poller_iteration_failed")
            time.sleep(1)
    finally:
        commerce_engine.dispose()
        engine.dispose()


if __name__ == "__main__":
    main()
