"""Shared pytest configuration: Hypothesis profiles for the proof harness."""

import os

from hypothesis import HealthCheck, settings

# `ci` is deterministic and fast enough for every PR. `nightly` explores widely and
# randomly. Select with HYPOTHESIS_PROFILE=nightly (the default is `ci`).
settings.register_profile(
    "ci",
    derandomize=True,
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    database=None,
)
settings.register_profile(
    "nightly",
    max_examples=500,
    deadline=None,
    derandomize=False,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
