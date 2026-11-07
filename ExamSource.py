#!/usr/bin/env python3
"""Collect complete Wikipedia sources and manage experiment records."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from utils.config import SETTINGS, request_options, validate_collection_limits
from utils.wikidata import fetch_entity, find_candidate_ids, wikipedia_title
from utils.wikipedia import fetch_clean_phrases
from utils.file import read_json_records, read_lines, write_json_records
from utils.text import format_identifier, phrase_key, select_unique_phrases, sentence_text


def build_candidate(
    entity_id: str, entity: dict[str, Any], title: str,
    phrases: list[str], language: str,
) -> dict[str, Any]:
    """Build candidate metadata from collected data without performing I/O."""
    return {
        "wikidata_id": entity_id,
        "wikidata_label": entity.get("labels", {}).get(language, {}).get("value", ""),
        "wikipedia_title": title,
        "phrases": phrases,
        "description": " ".join(sentence_text(phrase) for phrase in phrases),
    }


def build_source(identifier: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Build an individual source record."""
    return {"identifier": identifier, "candidates": candidates}


def combine_group_source(
    identifier: str, candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Combine candidate descriptions without modifying the input records."""
    phrases = [phrase for candidate in candidates for phrase in candidate["phrases"]]
    return {
        **build_source(identifier, candidates),
        "description": " ".join(sentence_text(phrase) for phrase in phrases),
    }


def normalize_names(names: list[str]) -> list[str]:
    """Validate and trim one name or two pair terms."""
    normalized = [name.strip() for name in names]
    if len(normalized) not in (1, 2) or not all(normalized):
        raise ValueError("provide one or two non-empty entity names")
    return normalized


def fetch_candidate(
    entity_id: str, language: str, phrase_limit: int, excluded: set[str],
) -> dict[str, Any] | None:
    """Fetch one entity and return a candidate only when its text is complete."""
    entity = fetch_entity(entity_id, language, **request_options(SETTINGS.wikidata_url))
    title = wikipedia_title(entity, language)
    phrases = select_unique_phrases(
        fetch_clean_phrases(title, language, phrase_limit), excluded, phrase_limit,
    )
    if len(phrases) != phrase_limit:
        return None
    return build_candidate(entity_id, entity, title, phrases, language)


def collect_candidates(
    entity_ids: list[str], language: str, required: int, phrase_limit: int,
) -> list[dict[str, Any]]:
    """Collect complete candidates without reusing accepted phrases."""
    candidates = []
    seen: set[str] = set()
    for entity_id in entity_ids:
        candidate = fetch_candidate(entity_id, language, phrase_limit, seen)
        if candidate is None:
            continue
        candidates.append(candidate)
        seen.update(phrase_key(phrase) for phrase in candidate["phrases"])
        if len(candidates) == required:
            return candidates
    raise RuntimeError(
        f"must resolve to {required} entities with {phrase_limit} unique Wikipedia phrases each"
    )


def build_group_source(
    names: list[str], candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble a group without mutating its candidate dictionaries."""
    identifier = format_identifier(names)
    if len(names) == 1:
        return build_source(identifier, candidates)
    named = [dict(candidate, identifier=name) for name, candidate in zip(names, candidates)]
    return combine_group_source(identifier, named)


def collect_group_source(
    names: list[str], *, language: str = SETTINGS.language,
    candidate_limit: int = SETTINGS.candidate_limit,
    phrase_limit: int = SETTINGS.phrase_limit,
) -> dict[str, Any]:
    """Resolve names, collect complete texts, and assemble the source record."""
    names = normalize_names(names)
    validate_collection_limits(candidate_limit, phrase_limit)
    ids = find_candidate_ids(names, language)
    required = candidate_limit if len(names) == 1 else 2
    candidates = collect_candidates(ids, language, required, phrase_limit)
    return build_group_source(names, candidates)


def collect_source(identifier: str, **options: Any) -> dict[str, Any]:
    return collect_group_source([identifier], **options)


def normalize_source(source: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy records to the current identifier-only schema."""
    normalized = dict(source)
    identifier = str(
        normalized.get("identifier") or normalized.get("requested_name") or ""
    )
    normalized.pop("requested_name", None)
    normalized.pop("language", None)
    normalized["identifier"] = identifier
    return normalized


def load_sources(path: Path) -> list[dict[str, Any]]:
    """Load and normalize persisted experiment records."""
    return [normalize_source(row) for row in read_json_records(path)]


def split_pair(value: str) -> list[str]:
    """Split one synonym pair while preserving ordinary entity names."""
    if "," in value:
        parts = value.split(",", 1)
    elif "-" in value:
        parts = value.split("-", 1)
    else:
        parts = [value]
    return [part.strip() for part in parts if part.strip()]


def parse_input_line(line: str, paired: bool) -> list[str]:
    """Parse an input line, ignoring blanks and comments."""
    line = line.strip()
    if not line or line.startswith("#"):
        return []
    return split_pair(line) if paired else [line]


def read_input_groups(paths: tuple[Path, ...]) -> list[list[str]]:
    """Read unique groups from configured input files."""
    groups = []
    for path in paths:
        for line in read_lines(path):
            group = parse_input_line(line, path.name == "synonyms.txt")
            if group and group not in groups:
                groups.append(group)
    return groups


def source_key(source: dict[str, Any]) -> str:
    """Normalize an identifier for matching stored records."""
    return str(source.get("identifier", "")).strip().casefold()


def pair_terms(sources: list[dict[str, Any]]) -> set[str]:
    """Find standalone terms superseded by newly combined pair records."""
    return {
        term for source in sources if "-" in source_key(source)
        for term in source_key(source).split("-")
    }


def is_obsolete_source(source: dict[str, Any], terms: set[str]) -> bool:
    """Identify legacy standalone terms and empty comma-separated pairs."""
    key = source_key(source)
    return key in terms or (not source.get("candidates") and "," in key)


def merge_sources(
    existing: list[dict[str, Any]], new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep populated records and replace empty ones without mutating inputs."""
    terms = pair_terms(new)
    merged = {
        source_key(row): row for row in existing
        if not is_obsolete_source(row, terms)
    }
    for row in new:
        key = source_key(row)
        if key not in merged or (not merged[key].get("candidates") and row.get("candidates")):
            merged[key] = row
    return list(merged.values())


def pending_groups(
    groups: list[list[str]], existing: list[dict[str, Any]],
) -> list[list[str]]:
    """Filter completed identifiers without performing I/O."""
    completed = {
        source_key(row)
        for row in existing if row.get("candidates")
    }
    return [group for group in groups if format_identifier(group).casefold() not in completed]


def collect_sources(groups: list[list[str]]) -> list[dict[str, Any]]:
    """Collect a complete batch before any result is written."""
    return [
        collect_group_source(
            group, language=SETTINGS.language,
            candidate_limit=SETTINGS.candidate_limit, phrase_limit=SETTINGS.phrase_limit,
        )
        for group in groups
    ]


def generate_sources() -> int:
    """Read inputs, collect pending records, and persist successful results."""
    groups = read_input_groups(SETTINGS.input_files)
    if not groups:
        raise ValueError("No entity names found in the data files.")
    existing = load_sources(SETTINGS.output_file)
    sources = collect_sources(pending_groups(groups, existing))
    if sources:
        write_json_records(SETTINGS.output_file, merge_sources(existing, sources))
    return len(sources)


def main() -> int:
    """Translate configuration and collection outcomes into CLI exit codes."""
    try:
        validate_collection_limits(SETTINGS.candidate_limit, SETTINGS.phrase_limit)
    except ValueError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2
    try:
        count = generate_sources()
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Error collecting sources: {exc}", file=sys.stderr)
        return 1
    print(f"Saved {count} new source(s) to {SETTINGS.output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
