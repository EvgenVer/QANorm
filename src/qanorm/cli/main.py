"""CLI entrypoint."""

from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config


def _build_alembic_config() -> Config:
    return Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))


def init_db() -> None:
    """Apply all database migrations."""

    command.upgrade(_build_alembic_config(), "head")


def build_parser() -> argparse.ArgumentParser:
    """Build the root CLI parser."""

    parser = argparse.ArgumentParser(prog="qanorm", description="QANorm command line interface.")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("init-db", help="Apply all database migrations.")
    return parser


def main() -> None:
    """Run the command line interface."""

    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-db":
        init_db()
        return

    parser.print_help()
