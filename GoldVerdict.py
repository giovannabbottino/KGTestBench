#!/usr/bin/env python3
"""Export SPARQL reference answers computed from final exam RDF graphs."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from ExamQuestions import identifier_prefix, index_sources, questions_for_record, validate_records
from utils.file import read_json_records

DATA_DIR = Path(__file__).resolve().parent / "data"
FIELDS = ("identifier", "text", "rdf", "question", "sparql", "sparql_type", "result")
# Template suffixes assigned by ExamQuestions.entity_questions.
QUERY_TYPES = {"01": "entities", "02": "predicates", "03": "id"}


def generate_verdicts(records: list[dict[str, Any]], sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Reuse ExamQuestions templates, executing them against each final graph.

    Original text joins all candidate descriptions in source order to match
    the combined group graph, preserving each description verbatim.
    The identifier identifies a question (source_entity_template). Results are
    JSON arrays of RDF terms in N3 notation, retaining languages and datatypes.
    """
    validate_records(records)
    indexed = index_sources(sources)
    rows = []
    for record in sorted(records, key=lambda item: identifier_prefix(item["identifier"])):
        source = indexed.get(identifier_prefix(record["identifier"]).casefold())
        if source is None:
            raise ValueError(f"missing source record: {record['identifier']}")
        questions = questions_for_record(record, source)
        original_text = " ".join(candidate["description"] for candidate in source["candidates"])
        for question in questions:
            if not question["expected_answers"]:
                raise ValueError(
                    f"{question['Identifier']}: query returned no answers in final RDF"
                )
            rows.append({
                "identifier": question["Identifier"],
                "text": original_text,
                "rdf": record["rdf"],
                "question": question["pergunta"],
                "sparql": question["sparql"],
                "sparql_type": QUERY_TYPES[question["Identifier"].rsplit("_", 1)[1]],
                "result": json.dumps(question["expected_answers"], ensure_ascii=False),
            })
    return rows


def generate_gold(input_path: Path, output_path: Path,
                  sources_path: Path = DATA_DIR / "exam_sources.json") -> int:
    """Validate the entire batch before atomically replacing the CSV."""
    if input_path.resolve() == output_path.resolve():
        raise ValueError("CSV output must differ from RDF input")
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if sources_path.resolve() == output_path.resolve():
        raise ValueError("CSV output must differ from source input")
    if not sources_path.is_file():
        raise FileNotFoundError(sources_path)
    rows = generate_verdicts(read_json_records(input_path), read_json_records(sources_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=output_path.parent,
            prefix=f".{output_path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DATA_DIR / "adjudication/rdf/exam_rdf.json",
                        help="JSON records containing identifier and final Turtle rdf")
    parser.add_argument("--output", type=Path, default=DATA_DIR / "gold_verdict.csv")
    parser.add_argument("--sources", type=Path, default=DATA_DIR / "exam_sources.json",
                        help="Original source entities and descriptions used to select query subjects and targets")
    args = parser.parse_args(argv)
    try:
        count = generate_gold(args.input, args.output, args.sources)
    except (OSError, ValueError) as exc:
        print(f"Error generating GoldVerdict: {exc}", file=sys.stderr)
        return 1
    print(f"Generated {count} verdict(s) in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
