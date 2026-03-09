"""Run resumable chunk-embedding backfill with periodic checkpoints and log-friendly output."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from qanorm.settings import get_settings
from qanorm.services.qa.retrieval_service import backfill_chunk_embeddings


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the resumable backfill runner."""

    parser = argparse.ArgumentParser(description="Backfill chunk embeddings in resumable slices.")
    parser.add_argument("--batch-size", type=int, default=16, help="Embedding request batch size.")
    parser.add_argument(
        "--checkpoint-every-batches",
        type=int,
        default=25,
        help="Commit after this many embedding batches inside one slice.",
    )
    parser.add_argument(
        "--generation-batches-per-run",
        type=int,
        default=25,
        help="Maximum embedding batches to generate in one outer loop iteration.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=int,
        default=None,
        help="Override provider request timeout for this backfill run only.",
    )
    return parser.parse_args()


def utc_now_iso() -> str:
    """Render a compact UTC timestamp for log lines."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def count_saved_embeddings(session: Session) -> int:
    """Return the number of persisted chunk embeddings."""

    return int(session.execute(text("select count(*) from chunk_embeddings")).scalar_one())


def count_unique_hashes(session: Session) -> int:
    """Return the total number of unique retrieval chunk hashes to embed."""

    return int(session.execute(text("select count(distinct chunk_hash) from retrieval_chunks")).scalar_one())


def main() -> None:
    """Run the resumable backfill until no missing embeddings remain."""

    args = parse_args()
    runtime_config = get_settings()
    if args.request_timeout_seconds is not None:
        # Keep the timeout override scoped to this backfill process instead of
        # mutating the shared application config for every other runtime path.
        runtime_config = runtime_config.model_copy(
            update={
                "app": runtime_config.app.model_copy(
                    update={"request_timeout_seconds": args.request_timeout_seconds}
                )
            }
        )
    engine = create_engine(runtime_config.env.db_url)

    with Session(engine) as session:
        total_unique_hashes = count_unique_hashes(session)
        print(f"{utc_now_iso()} total_unique_hashes={total_unique_hashes}", flush=True)

    cycle = 0
    while True:
        cycle += 1
        with Session(engine) as session:
            result = asyncio.run(
                backfill_chunk_embeddings(
                    session,
                    runtime_config=runtime_config,
                    batch_size=args.batch_size,
                    checkpoint_every_batches=args.checkpoint_every_batches,
                    max_generation_batches=args.generation_batches_per_run,
                )
            )
            session.commit()
            saved_embeddings = count_saved_embeddings(session)

        print(
            (
                f"{utc_now_iso()} cycle={cycle} "
                f"processed_batches={result['processed_batches']} "
                f"generated={result['generated_embedding_count']} "
                f"reused={result['reused_embedding_count']} "
                f"missing_before_cycle={result['missing_hash_count']} "
                f"saved_total={saved_embeddings}/{total_unique_hashes}"
            ),
            flush=True,
        )

        if result["generated_embedding_count"] == 0:
            print(f"{utc_now_iso()} status=completed saved_total={saved_embeddings}/{total_unique_hashes}", flush=True)
            break


if __name__ == "__main__":
    main()
