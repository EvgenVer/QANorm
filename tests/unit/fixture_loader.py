from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"


def fixture_path(*parts: str) -> Path:
    return FIXTURE_ROOT.joinpath(*parts)


def read_fixture_text(*parts: str) -> str:
    return fixture_path(*parts).read_text(encoding="utf-8")


def read_fixture_json(*parts: str) -> dict[str, Any]:
    return json.loads(read_fixture_text(*parts))
