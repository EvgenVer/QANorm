"""Bootstrap entrypoint for the Stage 2 ARQ worker."""

from __future__ import annotations

from pathlib import Path

from arq.worker import run_worker

from qanorm.workers.stage2 import Stage2WorkerSettings


def run_stage2_worker() -> None:
    """Start the Stage 2 ARQ worker runtime."""

    # The readiness file is used by docker-compose health checks.
    Path("/tmp/qanorm-stage2-worker.ready").write_text("ready", encoding="utf-8")
    run_worker(Stage2WorkerSettings)


if __name__ == "__main__":
    run_stage2_worker()
