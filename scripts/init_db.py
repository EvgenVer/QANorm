"""Database initialization script."""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config


def main() -> None:
    """Run the database initialization script."""

    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "src"))
    command.upgrade(Config(str(project_root / "alembic.ini")), "head")


if __name__ == "__main__":
    main()
