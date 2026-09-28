from pathlib import Path


def test_redis_loss_keeps_postgresql_poller_available() -> None:
    compose = Path("compose.yaml").read_text()
    assert "redis:" not in compose
    assert "celery" not in compose
    poller = compose.split("  poller:", maxsplit=1)[1]
    assert "apps.worker.poller" in poller
    assert "migrate:" in poller


def test_queue_notification_loss_is_repaired_by_periodic_authoritative_scans() -> None:
    poller = Path("apps/worker/poller.py").read_text()
    assert "while True:" in poller
    assert "poll_once(" in poller
    assert "run_recovery_batch" in poller
    assert "run_payout_batch" in poller
    assert "run_recovery_action_batch" in poller
    assert "run_forecast_batch" in poller
    assert '"process_inbound_webhooks": 2.0' in poller
    assert '"record_settlement_forecasts": 3600.0' in poller
