from pathlib import Path


def test_redis_loss_keeps_postgresql_poller_available() -> None:
    compose = Path("compose.yaml").read_text()
    poller = compose.split("  poller:", maxsplit=1)[1].split("  worker:", maxsplit=1)[0]
    assert "redis:" not in poller
    assert "apps.worker.poller" in poller
    assert "migrate:" in poller


def test_queue_notification_loss_is_repaired_by_periodic_authoritative_scans() -> None:
    poller = Path("apps/worker/poller.py").read_text()
    worker = Path("apps/worker/celery_app.py").read_text()
    assert "while True:" in poller
    assert "poll_once(settings)" in poller
    assert "run_recovery_batch" in poller
    assert 'schedule": 1.0' in worker
