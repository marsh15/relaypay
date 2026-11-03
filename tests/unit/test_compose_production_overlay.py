import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY_SERVICES = {"otel-collector", "prometheus", "grafana"}
LOOPBACK_PORTS = {
    "otel-collector": {"4317", "4318"},
    "prometheus": {"9090"},
    "grafana": {"3001"},
}


def _render_production_config(*profile_args: str) -> dict[str, Any]:
    standalone = shutil.which("docker-compose")
    docker = shutil.which("docker")
    if standalone is None and docker is None:
        pytest.skip("docker CLI not available; native Compose rendering needs it")
    # Shell-level COMPOSE_* variables (COMPOSE_PROFILES, COMPOSE_FILE) would change the graph.
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
    # Fixed argv; the Compose files are repository-controlled.
    command = [standalone] if standalone else [docker, "compose"]
    with tempfile.TemporaryDirectory(prefix="relaypay-compose-check-") as home:
        env["HOME"] = home
        env["DOCKER_CONFIG"] = home
        result = subprocess.run(  # noqa: S603
            [
                *command,
                "--env-file",
                ".env.production.example",
                "-f",
                "compose.yaml",
                "-f",
                "compose.production.yaml",
                *profile_args,
                "config",
                "--format",
                "json",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    assert result.returncode == 0, result.stderr
    config: dict[str, Any] = json.loads(result.stdout)
    return config


def test_observability_services_stay_absent_without_their_profile() -> None:
    config = _render_production_config()

    assert OBSERVABILITY_SERVICES.isdisjoint(config["services"])


def test_observability_ports_bind_only_to_loopback_when_profile_enabled() -> None:
    services = _render_production_config("--profile", "observability")["services"]

    for name, expected_ports in LOOPBACK_PORTS.items():
        ports = services[name]["ports"]
        assert {str(port["published"]) for port in ports} == expected_ports, name
        assert {port.get("host_ip") for port in ports} == {"127.0.0.1"}, name


def test_only_caddy_publishes_on_all_interfaces() -> None:
    config = _render_production_config("--profile", "observability", "--profile", "edge")
    services = config["services"]

    for name, service in services.items():
        if name == "caddy":
            continue
        for port in service.get("ports", []):
            assert port.get("host_ip") == "127.0.0.1", (name, port)
    assert {str(port["published"]) for port in services["caddy"]["ports"]} == {"80", "443"}


def test_internal_telemetry_path_still_targets_the_collector_service() -> None:
    services = _render_production_config("--profile", "observability")["services"]

    assert (
        services["api"]["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"]
        == "http://otel-collector:4318"
    )
    assert services["grafana"]["depends_on"]["prometheus"]["condition"] == "service_started"
