#!/usr/bin/env python3
"""Prepare individual and combined text-derived RDF graphs from source JSON."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from utils.config import EXAM_PREP_FILE, SETTINGS
from utils.file import read_json_records, write_json_records
from utils.rdf import (
    build_rdf, combine_rdf_graphs, dedupe_relations, prune_redundant_relations,
    relation_object_ids,
)
from utils.relations import extract_relations
from utils.wikidata import fetch_labels


def validate_candidate(candidate: dict[str, Any]) -> None:
    """Require an entity ID and source text without enforcing phrase counts."""
    if not re.fullmatch(r"Q[1-9][0-9]*", str(candidate.get("wikidata_id", ""))):
        raise ValueError("candidate requires a valid wikidata_id")
    if not isinstance(candidate.get("description"), str) or not candidate["description"].strip():
        raise ValueError("candidate requires a non-empty description")
    if not isinstance(candidate.get("wikidata_label", ""), str):
        raise ValueError("wikidata_label must be a string")


def validate_sources(sources: list[dict[str, Any]], language: str) -> None:
    """Validate the entire batch before NLP or remote requests."""
    if language != "en":
        raise ValueError("ExamPrep currently supports English text only")
    if not sources:
        raise ValueError("source JSON must contain at least one record")
    identifiers = set()
    for source in sources:
        identifier = source.get("identifier")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("source requires a non-empty identifier")
        if identifier.strip().casefold() in identifiers:
            raise ValueError(f"duplicate source identifier: {identifier}")
        identifiers.add(identifier.strip().casefold())
        validate_candidates(source.get("candidates"))


def validate_candidates(candidates: Any) -> None:
    """Validate candidate records without altering curated source content."""
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("source requires a non-empty candidates list")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("candidate must be an object")
        validate_candidate(candidate)


def candidate_labels(candidate: dict[str, Any], labels: dict[str, str]) -> dict[str, str]:
    """Add the original source label without modifying fetched labels."""
    result = dict(labels)
    if candidate.get("wikidata_label"):
        result[candidate["wikidata_id"]] = candidate["wikidata_label"]
    return result


def graph_fields(labels: dict[str, str], relations: list[tuple[str, str, str]], language: str) -> dict[str, Any]:
    """Serialize the pruned relations as RDF only."""
    return {"rdf": build_rdf(labels, relations, language)}


def prepare_candidate(candidate: dict[str, Any], language: str) -> dict[str, Any]:
    """Extract relations using the candidate's existing text and entity ID."""
    relations = dedupe_relations(extract_relations(
        candidate["description"], language, candidate["wikidata_id"],
    ))
    labels = candidate_labels(candidate, fetch_labels(relation_object_ids(relations), language))
    relations = prune_redundant_relations(labels, relations)
    retained = relation_object_ids(relations) | {candidate["wikidata_id"]}
    labels = {key: value for key, value in labels.items() if key in retained}
    return graph_fields(labels, relations, language)


def combine_candidates(identifier: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a minimal record linking the source identifier to its combined RDF."""
    return {
        "identifier": identifier,
        "rdf": combine_rdf_graphs([candidate["rdf"] for candidate in candidates]),
    }


def prepare_source(source: dict[str, Any], language: str) -> dict[str, Any]:
    """Prepare independent candidate graphs before combining them."""
    candidates = [prepare_candidate(candidate, language) for candidate in source["candidates"]]
    return combine_candidates(source["identifier"], candidates)


def prepare_sources(sources: list[dict[str, Any]], language: str) -> list[dict[str, Any]]:
    """Validate and prepare a complete batch without mutating source records."""
    validate_sources(sources, language)
    return [prepare_source(source, language) for source in sources]


def prepare_exam(input_path: Path, output_path: Path, language: str) -> int:
    """Write a separate prepared JSON only after all records succeed."""
    if input_path.resolve() == output_path.resolve():
        raise ValueError("preparation output must differ from source input")
    sources = read_json_records(input_path)
    prepared = prepare_sources(sources, language)
    write_json_records(output_path, prepared)
    return len(prepared)


def main() -> int:
    """Prepare the configured sources and report a CLI exit code."""
    try:
        count = prepare_exam(SETTINGS.output_file, EXAM_PREP_FILE, SETTINGS.language)
    except (OSError, ValueError, RuntimeError, LookupError) as exc:
        print(f"Error preparing exam: {exc}", file=sys.stderr)
        return 1
    print(f"Prepared {count} source(s) in {EXAM_PREP_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
