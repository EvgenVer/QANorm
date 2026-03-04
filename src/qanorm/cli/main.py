"""CLI entrypoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from alembic import command
from alembic.config import Config

from qanorm.indexing.indexer import reindex
from qanorm.jobs.worker import run_worker_loop
from qanorm.services.health import get_health_report
from qanorm.services.ingestion import check_configuration, run_seed_crawl
from qanorm.services.metrics import get_ingestion_metrics, get_ingestion_test_run_report
from qanorm.services.refresh_service import request_document_refresh, run_document_refresh


def _build_alembic_config() -> Config:
    return Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))


def init_db() -> None:
    """Apply all database migrations."""

    command.upgrade(_build_alembic_config(), "head")


def build_parser() -> argparse.ArgumentParser:
    """Build the root CLI parser."""

    parser = argparse.ArgumentParser(prog="qanorm", description="QANorm command line interface.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("check-config", help="Validate runtime configuration.")
    subparsers.add_parser("health-check", help="Show a minimal health report.")
    subparsers.add_parser("init-db", help="Apply all database migrations.")
    subparsers.add_parser("crawl-seeds", help="Run the seed crawl entrypoint.")
    subparsers.add_parser("run-worker", help="Run the background worker entrypoint.")
    subparsers.add_parser("ingestion-metrics", help="Show aggregated ingestion quality metrics.")
    subparsers.add_parser("ingestion-report", help="Show the Stage 1 ingestion test run report.")

    reindex_parser = subparsers.add_parser("reindex", help="Run reindex entrypoint.")
    reindex_parser.add_argument("--document-code", help="Optional canonical document code.", default=None)

    refresh_parser = subparsers.add_parser("refresh-document", help="Queue a document refresh request.")
    refresh_parser.add_argument("document_code", help="Canonical or display document code.")

    update_parser = subparsers.add_parser("update-document", help="Refresh one document immediately.")
    update_parser.add_argument("document_code", help="Canonical or display document code.")

    return parser


def main() -> None:
    """Run the command line interface."""

    parser = build_parser()
    args = parser.parse_args()

    if args.command == "check-config":
        print(json.dumps(check_configuration(), ensure_ascii=False, indent=2))
        return

    if args.command == "health-check":
        print(json.dumps(get_health_report(), ensure_ascii=False, indent=2))
        return

    if args.command == "init-db":
        init_db()
        return

    if args.command == "crawl-seeds":
        print(json.dumps(run_seed_crawl(), ensure_ascii=False, indent=2))
        return

    if args.command == "run-worker":
        print(json.dumps(run_worker_loop(), ensure_ascii=False, indent=2))
        return

    if args.command == "ingestion-metrics":
        print(json.dumps(get_ingestion_metrics(), ensure_ascii=False, indent=2))
        return

    if args.command == "ingestion-report":
        print(json.dumps(get_ingestion_test_run_report(), ensure_ascii=False, indent=2))
        return

    if args.command == "reindex":
        print(json.dumps(reindex(document_code=args.document_code), ensure_ascii=False, indent=2))
        return

    if args.command == "refresh-document":
        print(json.dumps(request_document_refresh(args.document_code), ensure_ascii=False, indent=2))
        return

    if args.command == "update-document":
        print(json.dumps(run_document_refresh(args.document_code), ensure_ascii=False, indent=2))
        return

    parser.print_help()
