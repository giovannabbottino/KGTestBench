#!/usr/bin/env python3
"""Collect source texts for KG test-bench experiments.

For each input name this script:

1. searches Wikidata for candidate entities;
2. resolves each candidate to its English Wikipedia page; and
3. retrieves the introductory sentences from that page.

The output is JSON so it can be used as source material for later
knowledge-graph construction experiments.

Run from the ``KGTestBench`` directory with no command-line arguments:

    python ExamSource.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from wikidata import WIKIDATA_URL, fetch_entity, find_entity_ids
from wikipedia import fetch_extract


ENV_FILE = Path(__file__).resolve().parent / ".env"


def load_env(path: Path) -> None:
    """Load simple KEY=VALUE settings without overwriting shell variables."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_int(value: str | None, default: int) -> int:
    """Parse an integer value or return its default."""
    return int(value) if value is not None else default


def parse_float(value: str | None, default: float) -> float:
    """Parse a floating-point value or return its default."""
    return float(value) if value is not None else default


load_env(ENV_FILE)

LANGUAGE = os.getenv("KG_LANGUAGE", "en")
DEFAULT_CANDIDATE_LIMIT = parse_int(os.getenv("KG_CANDIDATE_LIMIT"), 2)
SEARCH_CANDIDATE_LIMIT = parse_int(os.getenv("KG_SEARCH_CANDIDATE_LIMIT"), 10)
DEFAULT_PHRASE_LIMIT = parse_int(os.getenv("KG_PHRASE_LIMIT"), 2)
INPUT_FILES = (
    Path(__file__).resolve().parent / "data" / "ambiguous_words.txt",
    Path(__file__).resolve().parent / "data" / "synonyms.txt",
)
WIKIPEDIA_URL = os.getenv(
    "KG_WIKIPEDIA_URL", "https://{language}.wikipedia.org/w/api.php"
)
REQUEST_TIMEOUT = parse_float(os.getenv("KG_REQUEST_TIMEOUT"), 15.0)
REQUEST_INTERVAL = parse_float(os.getenv("KG_REQUEST_INTERVAL"), 1.0)
RETRY_STATUSES = {
    int(status)
    for status in os.getenv("KG_RETRY_STATUSES", "429,500,502,503,504").split(",")
    if status.strip()
}
USER_AGENT = os.getenv(
    "KG_USER_AGENT",
    "KGTestBench/0.1 (source collection; contact: you@example.org)",
)
def split_phrases(text: str, limit: int) -> list[str]:
    """Split introductory text into at most ``limit`` sentences."""
    phrases = re.split(r"(?<=[.!?])\s+", text.strip())
    return [phrase for phrase in phrases if phrase][:limit]


def clean_text(text: str) -> str:
    """Remove quoted/parenthesized fragments and normalize whitespace."""
    text = re.sub(r'"[^"]*"', "", text)
    while re.search(r"\([^()]*\)", text):
        text = re.sub(r"\([^()]*\)", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip()


def wikipedia_title(entity: dict[str, Any], language: str) -> str:
    """Return the Wikipedia title linked from a Wikidata entity."""
    sitelinks = entity.get("sitelinks", {})
    return sitelinks.get(f"{language}wiki", {}).get("title", "")


def clean_phrases(text: str, limit: int) -> list[str]:
    """Split and clean introductory phrases, keeping non-empty results."""
    return [
        cleaned
        for phrase in split_phrases(text, limit)
        if (cleaned := clean_text(phrase))
    ]


def build_candidate(
    entity_id: str,
    entity: dict[str, Any],
    title: str,
    phrases: list[str],
    language: str,
) -> dict[str, Any]:
    """Build one selected candidate record from resolved data."""
    return {
        "wikidata_id": entity_id,
        "wikidata_label": entity.get("labels", {})
        .get(language, {})
        .get("value", ""),
        "wikipedia_title": title,
        "phrases": phrases,
        "description": " ".join(phrases),
    }


def build_source(identifier: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Build an individual source record."""
    return {"identifier": identifier, "candidates": candidates}


def collect_source(
    identifier: str,
    *,
    language: str = LANGUAGE,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    phrase_limit: int = DEFAULT_PHRASE_LIMIT,
) -> dict[str, Any]:
    """Resolve one name and collect its available introductory phrases."""
    candidates: list[dict[str, Any]] = []
    for entity_id in find_entity_ids(
        identifier,
        language,
        SEARCH_CANDIDATE_LIMIT,
        api_url=os.getenv("KG_WIKIDATA_URL", WIKIDATA_URL),
        timeout=REQUEST_TIMEOUT,
        interval=REQUEST_INTERVAL,
        retry_statuses=RETRY_STATUSES,
        user_agent=USER_AGENT,
    ):
        entity = fetch_entity(
            entity_id,
            language,
            api_url=os.getenv("KG_WIKIDATA_URL", WIKIDATA_URL),
            timeout=REQUEST_TIMEOUT,
            interval=REQUEST_INTERVAL,
            retry_statuses=RETRY_STATUSES,
            user_agent=USER_AGENT,
        )
        title = wikipedia_title(entity, language)
        if not title:
            continue

        phrases = clean_phrases(
            fetch_extract(
                title,
                language,
                api_url=WIKIPEDIA_URL,
                timeout=REQUEST_TIMEOUT,
                interval=REQUEST_INTERVAL,
                retry_statuses=RETRY_STATUSES,
                user_agent=USER_AGENT,
            ),
            phrase_limit,
        )
        if not phrases:
            continue

        candidates.append(build_candidate(entity_id, entity, title, phrases, language))
        if len(candidates) == candidate_limit:
            break

    return build_source(identifier, candidates)


def normalize_source(source: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy records to the current identifier-only schema."""
    normalized = dict(source)
    identifier = str(
        normalized.get("identifier") or normalized.get("requested_name") or ""
    )
    normalized.pop("requested_name", None)
    normalized.pop("language", None)
    normalized["identifier"] = identifier
    candidates = normalized.get("candidates", [])
    if isinstance(candidates, list):
        limit = 2
        normalized["candidates"] = candidates[:limit]
    return normalized


def load_sources(path: Path) -> list[dict[str, Any]]:
    """Load and normalize existing source records."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"source file must contain a JSON list: {path}")
    return [normalize_source(source) for source in data]


def split_pair(value: str) -> list[str]:
    """Split one synonym pair while preserving ordinary entity names."""
    if "," in value:
        parts = value.split(",", 1)
    elif "-" in value:
        parts = value.split("-", 1)
    else:
        parts = [value]
    return [part.strip() for part in parts if part.strip()]


def format_identifier(names: list[str]) -> str:
    """Create the stable identifier for one entity or a synonym pair."""
    return "-".join(names)


def read_input_groups(paths: tuple[Path, ...] = INPUT_FILES) -> list[list[str]]:
    """Read individual names and synonym pairs from the input files."""
    groups: list[list[str]] = []
    for path in paths:
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            group = split_pair(line) if path.name == "synonyms.txt" else [line]
            if group and group not in groups:
                groups.append(group)
    return groups


def collect_group_source(
    names: list[str],
    *,
    language: str = LANGUAGE,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    phrase_limit: int = DEFAULT_PHRASE_LIMIT,
) -> dict[str, Any]:
    """Collect one source record for an individual name or synonym pair."""
    if len(names) == 1:
        return collect_source(
            names[0],
            language=language,
            candidate_limit=candidate_limit,
            phrase_limit=phrase_limit,
        )

    candidates: list[dict[str, Any]] = []
    for name in names:
        source = collect_source(
            name,
            language=language,
            candidate_limit=1,
            phrase_limit=phrase_limit,
        )
        for candidate in source["candidates"]:
            candidates.append({"identifier": name, **candidate})

    return combine_group_source(format_identifier(names), candidates)


def combine_group_source(
    identifier: str, candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build one synonym-pair record from its selected candidates."""
    return {
        "identifier": identifier,
        "candidates": candidates,
        "description": " ".join(
            phrase for candidate in candidates for phrase in candidate["phrases"]
        ),
    }


def merge_sources(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve records and repair previous empty results when possible."""
    new_pair_terms = {
        term
        for source in new
        if source.get("identifier") and "-" in str(source["identifier"])
        for term in str(source["identifier"]).split("-")
    }
    merged = [
        source
        for source in existing
        if not (
            str(source.get("identifier", "")) in new_pair_terms
            or (
                not source.get("candidates")
                and "," in str(source.get("identifier", ""))
            )
        )
    ]
    positions = {
        source.get("identifier"): index
        for index, source in enumerate(merged)
        if source.get("identifier")
    }
    for source in new:
        name = source.get("identifier")
        if name not in positions:
            positions[name] = len(merged)
            merged.append(source)
        elif not merged[positions[name]].get("candidates") and source.get(
            "candidates"
        ):
            merged[positions[name]] = source
    return merged


def main() -> int:
    """Collect names from the data files and write the fixed JSON output."""
    groups = read_input_groups()
    if not groups:
        print("No entity names found in the data files.", file=sys.stderr)
        return 1
    if DEFAULT_CANDIDATE_LIMIT < 1 or DEFAULT_PHRASE_LIMIT < 1:
        print("candidate-limit and phrase-limit must be positive", file=sys.stderr)
        return 2

    sources: list[dict[str, Any]] = []
    try:
        for group in groups:
            print(f"Resolving {format_identifier(group)!r}...", file=sys.stderr)
            sources.append(
                collect_group_source(
                    group,
                    language=LANGUAGE,
                    candidate_limit=DEFAULT_CANDIDATE_LIMIT,
                    phrase_limit=DEFAULT_PHRASE_LIMIT,
                )
            )
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Error collecting sources: {exc}", file=sys.stderr)
        return 1

    output = Path(__file__).resolve().parent / "data" / "exam_sources.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    merged_sources = merge_sources(load_sources(output), sources)
    output.write_text(
        json.dumps(merged_sources, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(merged_sources)} source(s) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
