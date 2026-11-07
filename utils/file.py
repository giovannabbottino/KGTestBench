"""Reusable UTF-8 JSON and text-file operations."""

import json
from pathlib import Path
from typing import Any


def read_json_records(path: Path) -> list[dict[str, Any]]:
    """Read a JSON array of objects; a missing file is an empty collection."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ValueError(f"file must contain a JSON list of objects: {path}")
    return data


def write_json_records(path: Path, records: list[dict[str, Any]]) -> None:
    """Write records as readable UTF-8 JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def read_lines(path: Path) -> list[str]:
    """Read UTF-8 lines, treating an absent optional input as empty."""
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
