"""Seeded chaos harness: deterministic operation schedules with crash and provider faults.

``ops``/``faults`` generate and describe a schedule (pure, seeded); ``driver`` executes one seed
in-process against the service layer (CI mode); ``full`` drives the real Compose stack over HTTP
with process kills (full mode); ``run`` is the CLI (``python -m scripts.chaos.run``).
"""
