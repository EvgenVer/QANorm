"""CLI entrypoint."""

from __future__ import annotations

import argparse
from dataclasses import asdict
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
from qanorm.stage2a.indexing.backfill import (
    backfill_derived_retrieval_data_worker,
    backfill_retrieval_unit_embeddings,
    read_derived_backfill_state,
    read_embedding_backfill_state,
    run_document_alias_backfill,
    run_embedding_preflight,
    run_rebuild_derived_retrieval_data,
    run_retrieval_unit_backfill,
    start_derived_backfill_process,
    start_parallel_embedding_backfill_processes,
    start_parallel_derived_backfill_processes,
    start_embedding_backfill_process,
)


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

    stage2a_alias_parser = subparsers.add_parser("stage2a-build-aliases", help="Rebuild Stage 2A document aliases.")
    stage2a_alias_parser.add_argument("--document-code", help="Optional canonical document code.", default=None)

    stage2a_units_parser = subparsers.add_parser("stage2a-build-units", help="Rebuild Stage 2A retrieval units.")
    stage2a_units_parser.add_argument("--document-code", help="Optional canonical document code.", default=None)

    stage2a_rebuild_parser = subparsers.add_parser(
        "stage2a-rebuild-derived",
        help="Rebuild all Stage 2A derived retrieval data.",
    )
    stage2a_rebuild_parser.add_argument("--document-code", help="Optional canonical document code.", default=None)

    stage2a_derived_start_parser = subparsers.add_parser(
        "stage2a-derived-start",
        help="Start or resume detached Stage 2A derived retrieval-data rebuild.",
    )
    stage2a_derived_start_parser.add_argument("--document-code", help="Optional canonical document code.", default=None)
    stage2a_derived_start_parser.add_argument("--state-path", default=None, help="Optional path to the checkpoint state JSON.")
    stage2a_derived_start_parser.add_argument("--log-path", default=None, help="Optional path to the derived rebuild log file.")
    stage2a_derived_start_parser.add_argument("--parallel-workers", type=int, default=1, help="Optional number of shard workers.")
    stage2a_derived_start_parser.add_argument("--manifest-path", default=None, help="Optional path to the parallel manifest JSON.")

    stage2a_derived_status_parser = subparsers.add_parser(
        "stage2a-derived-status",
        help="Read the persisted state of the Stage 2A derived retrieval-data rebuild.",
    )
    stage2a_derived_status_parser.add_argument("--state-path", default=None, help="Optional path to the checkpoint state JSON.")
    stage2a_derived_status_parser.add_argument("--log-path", default=None, help="Optional path to the derived rebuild log file.")
    stage2a_derived_status_parser.add_argument("--manifest-path", default=None, help="Optional path to the parallel manifest JSON.")

    stage2a_derived_worker_parser = subparsers.add_parser(
        "stage2a-derived-backfill-worker",
        help=argparse.SUPPRESS,
    )
    stage2a_derived_worker_parser.add_argument("--document-code", default=None)
    stage2a_derived_worker_parser.add_argument("--state-path", required=True)
    stage2a_derived_worker_parser.add_argument("--log-path", required=True)
    stage2a_derived_worker_parser.add_argument("--shard-index", type=int, default=0)
    stage2a_derived_worker_parser.add_argument("--shard-count", type=int, default=1)

    stage2a_preflight_parser = subparsers.add_parser(
        "stage2a-embed-preflight",
        help="Estimate Stage 2A embedding workload before running backfill.",
    )
    stage2a_preflight_parser.add_argument(
        "--price-per-1m-tokens",
        type=float,
        default=None,
        help="Optional override for estimated input price in USD per 1M tokens.",
    )

    stage2a_embed_start_parser = subparsers.add_parser(
        "stage2a-embed-start",
        help="Start or resume detached Stage 2A embedding backfill.",
    )
    stage2a_embed_start_parser.add_argument("--state-path", default=None, help="Optional path to the checkpoint state JSON.")
    stage2a_embed_start_parser.add_argument("--log-path", default=None, help="Optional path to the embedding backfill log file.")
    stage2a_embed_start_parser.add_argument("--parallel-workers", type=int, default=1, help="Optional number of parallel workers.")
    stage2a_embed_start_parser.add_argument("--manifest-path", default=None, help="Optional path to the parallel manifest JSON.")

    stage2a_embed_status_parser = subparsers.add_parser(
        "stage2a-embed-status",
        help="Read the persisted state of the Stage 2A embedding backfill.",
    )
    stage2a_embed_status_parser.add_argument("--state-path", default=None, help="Optional path to the checkpoint state JSON.")
    stage2a_embed_status_parser.add_argument("--log-path", default=None, help="Optional path to the embedding backfill log file.")
    stage2a_embed_status_parser.add_argument("--manifest-path", default=None, help="Optional path to the parallel manifest JSON.")

    stage2a_embed_worker_parser = subparsers.add_parser(
        "stage2a-embed-backfill-worker",
        help=argparse.SUPPRESS,
    )
    stage2a_embed_worker_parser.add_argument("--state-path", required=True)
    stage2a_embed_worker_parser.add_argument("--log-path", required=True)

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

    if args.command == "stage2a-build-aliases":
        print(json.dumps(run_document_alias_backfill(document_code=args.document_code), ensure_ascii=False, indent=2))
        return

    if args.command == "stage2a-build-units":
        print(json.dumps(run_retrieval_unit_backfill(document_code=args.document_code), ensure_ascii=False, indent=2))
        return

    if args.command == "stage2a-rebuild-derived":
        print(json.dumps(run_rebuild_derived_retrieval_data(document_code=args.document_code), ensure_ascii=False, indent=2))
        return

    if args.command == "stage2a-derived-start":
        if args.parallel_workers > 1:
            print(
                json.dumps(
                    asdict(
                        start_parallel_derived_backfill_processes(
                            worker_count=args.parallel_workers,
                            document_code=args.document_code,
                            state_path=args.state_path,
                            log_path=args.log_path,
                            manifest_path=args.manifest_path,
                        )
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        print(
            json.dumps(
                start_derived_backfill_process(
                    document_code=args.document_code,
                    state_path=args.state_path,
                    log_path=args.log_path,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "stage2a-derived-status":
        print(
            json.dumps(
                read_derived_backfill_state(
                    state_path=args.state_path,
                    log_path=args.log_path,
                    manifest_path=args.manifest_path,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "stage2a-derived-backfill-worker":
        print(
            json.dumps(
                asdict(
                    backfill_derived_retrieval_data_worker(
                        document_code=args.document_code,
                        state_path=args.state_path,
                        log_path=args.log_path,
                        shard_index=args.shard_index,
                        shard_count=args.shard_count,
                    )
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "stage2a-embed-preflight":
        print(
            json.dumps(
                run_embedding_preflight(price_per_million_tokens=args.price_per_1m_tokens),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "stage2a-embed-start":
        if args.parallel_workers > 1:
            print(
                json.dumps(
                    asdict(
                        start_parallel_embedding_backfill_processes(
                            worker_count=args.parallel_workers,
                            state_path=args.state_path,
                            log_path=args.log_path,
                            manifest_path=args.manifest_path,
                        )
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        print(
            json.dumps(
                start_embedding_backfill_process(state_path=args.state_path, log_path=args.log_path),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "stage2a-embed-status":
        print(
            json.dumps(
                read_embedding_backfill_state(
                    state_path=args.state_path,
                    log_path=args.log_path,
                    manifest_path=args.manifest_path,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "stage2a-embed-backfill-worker":
        print(
            json.dumps(
                asdict(backfill_retrieval_unit_embeddings(state_path=args.state_path, log_path=args.log_path)),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    parser.print_help()


if __name__ == "__main__":
    main()
