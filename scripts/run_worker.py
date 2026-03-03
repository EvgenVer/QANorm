"""Worker startup script."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    """Run the background worker."""

    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "src"))

    from qanorm.cli.main import main as cli_main

    original_argv = sys.argv[:]
    try:
        sys.argv = [original_argv[0], "run-worker"]
        cli_main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    main()
