"""Application configuration loaded from environment variables."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Runtime settings used by source collection."""

    language: str
    candidate_limit: int
    search_candidate_limit: int
    phrase_limit: int
    wikidata_url: str
    wikipedia_url: str
    request_timeout: float
    request_interval: float
    retry_statuses: set[int]
    user_agent: str
    input_files: tuple[Path, ...]
    output_file: Path


def load_env(path: Path) -> None:
    """Load a dotenv file without overriding existing environment values."""
    load_dotenv(path, override=False)


def env_int(name: str, default: int) -> int:
    """Read an integer setting from the environment."""
    import os

    value = os.getenv(name)
    return int(value) if value is not None else default


def env_float(name: str, default: float) -> float:
    """Read a floating-point setting from the environment."""
    import os

    value = os.getenv(name)
    return float(value) if value is not None else default


def env_statuses(name: str, default: str) -> set[int]:
    """Read comma-separated HTTP status codes from the environment."""
    import os

    value = os.getenv(name, default)
    return {int(status.strip()) for status in value.split(",") if status.strip()}


def load_settings(base_dir: Path | None = None) -> Settings:
    """Load application settings relative to the project directory."""
    import os

    root = base_dir or Path(__file__).resolve().parent
    load_env(root / ".env")
    return Settings(
        language=os.getenv("KG_LANGUAGE", "en"),
        candidate_limit=env_int("KG_CANDIDATE_LIMIT", 2),
        search_candidate_limit=env_int("KG_SEARCH_CANDIDATE_LIMIT", 10),
        phrase_limit=env_int("KG_PHRASE_LIMIT", 2),
        wikidata_url=os.getenv(
            "KG_WIKIDATA_URL", "https://www.wikidata.org/w/api.php"
        ),
        wikipedia_url=os.getenv(
            "KG_WIKIPEDIA_URL", "https://{language}.wikipedia.org/w/api.php"
        ),
        request_timeout=env_float("KG_REQUEST_TIMEOUT", 15.0),
        request_interval=env_float("KG_REQUEST_INTERVAL", 1.0),
        retry_statuses=env_statuses("KG_RETRY_STATUSES", "429,500,502,503,504"),
        user_agent=os.getenv(
            "KG_USER_AGENT",
            "KGTestBench/0.1 (source collection; contact: you@example.org)",
        ),
        input_files=(
            root / "data" / "ambiguous_words.txt",
            root / "data" / "synonyms.txt",
        ),
        output_file=root / "data" / "exam_sources.json",
    )


SETTINGS = load_settings()
