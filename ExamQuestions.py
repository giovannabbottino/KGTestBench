#!/usr/bin/env python3
"""Generate deterministic SPARQL questions and reference answers from exam RDF."""

from __future__ import annotations

import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from rdflib import BNode, Graph, Literal, RDFS, URIRef, Variable

from utils.file import read_json_records, write_json_records

DATA_DIR = Path(__file__).resolve().parent / "data"


def identifier_prefix(identifier: str) -> str:
    """Normalize source names for IDs such as Forest_Woodland_01_01."""
    prefix = re.sub(r"\W+", "_", identifier.strip()).strip("_")
    if not prefix:
        raise ValueError("identifier must contain letters or numbers")
    return prefix


def validate_records(records: list[dict[str, Any]]) -> None:
    """Reject empty batches, missing fields and ambiguous identifiers."""
    if not records:
        raise ValueError("exam_prep must contain at least one record")
    seen = set()
    for record in records:
        for field in ("identifier", "rdf"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                raise ValueError(f"record requires a non-empty {field}")
        key = identifier_prefix(record["identifier"]).casefold()
        if key in seen:
            raise ValueError(f"duplicate identifier: {record['identifier']}")
        seen.add(key)


def parse_rdf(rdf: str) -> Graph:
    """Parse embedded Turtle without accessing a remote RDF source."""
    try:
        return Graph().parse(data=rdf, format="turtle")
    except Exception as exc:
        raise ValueError(f"invalid Turtle: {exc}") from exc


def main_entities(graph: Graph) -> list[URIRef]:
    """Select named subjects with outgoing entity links, excluding label-only nodes."""
    return sorted({
        subject for subject, predicate, obj in graph
        if isinstance(subject, URIRef) and isinstance(obj, (URIRef, BNode))
        and predicate != RDFS.label and subject != obj
    }, key=str)


def entity_label(graph: Graph, entity: URIRef, cache: dict[URIRef, str] | None = None) -> str:
    """Prefer English labels, then a stable label, falling back to the full IRI."""
    if cache is not None and entity in cache:
        return cache[entity]
    labels = sorted(
        (label for label in graph.objects(entity, RDFS.label) if isinstance(label, Literal)),
        key=lambda label: (label.language != "en", str(label), label.n3()),
    )
    label = str(labels[0]) if labels else str(entity)
    if cache is not None:
        cache[entity] = label
    return label


def subject_filter(subject: URIRef, label: str) -> str:
    """Match the example's IRI-or-label semantics, escaping regex metacharacters."""
    pattern = "".join("\\" + char if char in r"\.^$|?*+()[]{}" else char for char in label)
    return (
        "  FILTER(\n"
        f"    ?subject = {subject.n3()} ||\n"
        "    EXISTS {\n"
        "      ?subject rdfs:label ?label .\n"
        f'      FILTER(REGEX(STR(?label), {Literal(pattern).n3()}, "i"))\n'
        "    }\n  )\n"
    )


def bidirectional_pattern(predicate: str, neighbor: str) -> str:
    """Build the shared incoming/outgoing UNION from internal variable names."""
    return (
        f"  {{ ?subject {predicate} {neighbor} . }}\n"
        "  UNION\n"
        f"  {{ {neighbor} {predicate} ?subject . }}\n"
    )


def neighborhood_query(subject: URIRef, label: str, predicates: bool = False) -> str:
    """Select linked entity labels or the predicates that link them."""
    neighbor = "?neighbor" if predicates else "?answerEntity"
    predicate = "?answer" if predicates else "?p"
    return (
        f"PREFIX rdfs: <{RDFS}>\n\nSELECT DISTINCT ?answer\nWHERE {{\n"
        + bidirectional_pattern(predicate, neighbor)
        + subject_filter(subject, label)
        + f"  FILTER(isIRI({neighbor}) || isBlank({neighbor}))\n"
        + f"  FILTER({neighbor} != ?subject)\n"
        + ("" if predicates else "  ?answerEntity rdfs:label ?answer .\n")
        + "}\nORDER BY ?answer"
    )


def direct_link_query(subject: URIRef, target: URIRef) -> str:
    """Return the target IRI if either direction has a link, as in the example."""
    return (
        "SELECT DISTINCT ?answer\nWHERE {\n"
        f"  VALUES ?subject {{ {subject.n3()} }}\n"
        f"  VALUES ?answer {{ {target.n3()} }}\n"
        + bidirectional_pattern("?predicate", "?answer")
        + "}\nORDER BY ?answer"
    )


def normalized_text(value: str) -> str:
    """Match case and accent variants while retaining Unicode letters and numbers."""
    decomposed = unicodedata.normalize("NFD", value.casefold())
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[\W_]+", " ", unaccented).strip()


def target_rank(target: URIRef, label: str, text: str) -> tuple[int, int, int, str, str]:
    """Rank a label against normalized description text with stable tie breakers."""
    name = normalized_text(label)
    match = re.search(rf"\b{re.escape(name)}\b", text) if name else None
    return (
        0 if match else 1,
        match.start() if match else len(text) + 1,
        1 if name.replace(" ", "").isdigit() else 0,
        label.casefold(), str(target),
    )


def select_target(
    graph: Graph, subject: URIRef, description: str = "", *,
    labels: dict[URIRef, str] | None = None,
) -> URIRef | None:
    """Rank named neighbors by their first mention in the source description."""
    outgoing = {obj for _, predicate, obj in graph.triples((subject, None, None)) if predicate != RDFS.label}
    incoming = {node for node, predicate, _ in graph.triples((None, None, subject)) if predicate != RDFS.label}
    candidates = [node for node in outgoing | incoming if isinstance(node, URIRef) and node != subject]
    text = normalized_text(description)
    return min(candidates, key=lambda node: target_rank(node, entity_label(graph, node, labels), text), default=None)


def expected_answers(graph: Graph, sparql: str) -> list[str]:
    """Execute SELECT and read ?answer independently of projection order."""
    result = graph.query(sparql)
    variable = Variable("answer")
    if result.type != "SELECT" or variable not in (result.vars or []):
        raise ValueError("query must be SELECT and project ?answer")
    return sorted({binding[variable].n3() for binding in result.bindings
                   if binding.get(variable) is not None})


def build_question(identifier: str, question: str, sparql: str) -> dict[str, str]:
    """Build a question definition without accessing or executing a graph."""
    return {
        "Identifier": identifier,
        "pergunta": question,
        "sparql": sparql,
    }


def answer_question(graph: Graph, question: dict[str, str]) -> dict[str, Any]:
    """Compute answers separately, retaining question context on query failures."""
    try:
        answers = expected_answers(graph, question["sparql"])
    except Exception as exc:
        raise ValueError(f"{question['Identifier']}: failed to compute answers: {exc}") from exc
    return {**question, "expected_answers": answers}


def entity_questions(
    graph: Graph, subject: URIRef, prefix: str, name: str, description: str = "", *,
    labels: dict[URIRef, str] | None = None,
) -> list[dict[str, str]]:
    """Build question definitions from graph structure without executing SPARQL."""
    if labels is None:
        labels = {}
    label = entity_label(graph, subject, labels)
    templates = [
        (f"Which entities are directly linked to {name}?", neighborhood_query(subject, label)),
        (f"Which predicates directly link entities to {name}?", neighborhood_query(subject, label, True)),
    ]
    target = select_target(graph, subject, description, labels=labels)
    if target is not None:
        templates.append((
            f"Is the entity {name} directly linked to {entity_label(graph, target, labels)}?",
            direct_link_query(subject, target),
        ))
    return [
        build_question(f"{prefix}_{index:02d}", question, query)
        for index, (question, query) in enumerate(templates, start=1)
    ]


def source_candidates(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Require a non-empty candidate collection from a source record."""
    candidates = source.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("source candidates must be a non-empty list")
    return candidates


def validate_candidate_fields(candidate: Any) -> None:
    """Validate the fields used to build a candidate context."""
    if not isinstance(candidate, dict) or not re.fullmatch(r"Q[1-9][0-9]*", str(candidate.get("wikidata_id", ""))):
        raise ValueError("source candidate requires a valid wikidata_id")
    description = candidate.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("source candidate requires a non-empty description")
    if not isinstance(candidate.get("wikipedia_title", ""), str):
        raise ValueError("wikipedia_title must be a string")


def candidate_context(candidate: dict[str, Any]) -> dict[str, str]:
    """Convert validated source fields into a fresh generation context."""
    validate_candidate_fields(candidate)
    return {
        "uri": "http://www.wikidata.org/entity/" + candidate["wikidata_id"],
        "description": candidate["description"],
        "wikipedia_title": candidate.get("wikipedia_title", ""),
    }


def validate_unique_candidates(candidates: list[dict[str, str]]) -> None:
    """Reject repeated candidate URIs without changing their order."""
    seen = set()
    for candidate in candidates:
        uri = candidate["uri"]
        if uri in seen:
            raise ValueError(f"duplicate candidate: {uri}")
        seen.add(uri)


def validate_candidate_subjects(candidates: list[dict[str, str]], graph: Graph) -> None:
    """Require each candidate to occur as a subject in the prepared graph."""
    for candidate in candidates:
        uri = candidate["uri"]
        if not any(graph.triples((URIRef(uri), None, None))):
            raise ValueError(f"candidate {uri} is absent from prepared RDF and has no unique label match")


def resolve_candidate_context(candidate: dict[str, Any], graph: Graph) -> dict[str, str]:
    """Prefer the source ID; otherwise resolve a unique main subject by exact label."""
    context = candidate_context(candidate)
    if any(graph.triples((URIRef(context["uri"]), None, None))):
        return context
    label = candidate.get("wikidata_label", "")
    if not isinstance(label, str) or not label.strip():
        return context
    matches = [subject for subject in main_entities(graph)
               if any(isinstance(value, Literal) and str(value).strip().casefold() == label.strip().casefold()
                      for value in graph.objects(subject, RDFS.label))]
    if len(matches) > 1:
        raise ValueError(f"ambiguous RDF label {label!r} for candidate {context['uri']}")
    return {**context, "uri": str(matches[0])} if matches else context


def ordered_candidates(source: dict[str, Any] | None, graph: Graph) -> list[dict[str, str]]:
    """Coordinate candidate conversion and validation while retaining source order."""
    if source is None:
        return [{"uri": str(subject), "description": "", "wikipedia_title": ""}
                for subject in main_entities(graph)]
    result = [resolve_candidate_context(candidate, graph) for candidate in source_candidates(source)]
    validate_unique_candidates(result)
    validate_candidate_subjects(result, graph)
    return result


def candidate_name(candidate: dict[str, str], label: str, label_count: int) -> str:
    """Use source page titles to distinguish homonyms without inventing descriptions."""
    if label_count > 1:
        title = candidate["wikipedia_title"]
        if title and title.casefold() != label.casefold():
            return title
        return f"{label} ({candidate['uri']})"
    return label


def questions_for_record(record: dict[str, Any], source: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Generate flat question rows scoped to a single prepared graph."""
    try:
        graph = parse_rdf(record["rdf"])
    except ValueError as exc:
        raise ValueError(f"{record['identifier']}: {exc}") from exc
    candidates = ordered_candidates(source, graph)
    entities = [URIRef(candidate["uri"]) for candidate in candidates]
    labels: dict[URIRef, str] = {}
    label_counts = Counter(entity_label(graph, entity, labels).casefold() for entity in entities)
    prefix = identifier_prefix(record["identifier"])
    return [
        answer_question(graph, question)
        for index, candidate in enumerate(candidates, start=1)
        for question in entity_questions(
            graph, URIRef(candidate["uri"]), f"{prefix}_{index:02d}",
            candidate_name(candidate, labels[URIRef(candidate["uri"])],
                           label_counts[labels[URIRef(candidate["uri"])].casefold()]),
            candidate["description"], labels=labels,
        )
    ]


def index_sources(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index source records by normalized identifier and reject ambiguous matches."""
    indexed = {}
    for source in sources:
        identifier = source.get("identifier")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("source requires a non-empty identifier")
        key = identifier_prefix(identifier).casefold()
        if key in indexed:
            raise ValueError(f"duplicate source identifier: {identifier}")
        indexed[key] = source
    return indexed


def generate_questions(records: list[dict[str, Any]], sources: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Validate and transform records in stable identifier order."""
    validate_records(records)
    indexed = index_sources(sources) if sources is not None else {}
    if sources is not None:
        missing = [record["identifier"] for record in records
                   if identifier_prefix(record["identifier"]).casefold() not in indexed]
        if missing:
            raise ValueError(f"missing source records: {missing}")
    return [
        question
        for record in sorted(records, key=lambda item: identifier_prefix(item["identifier"]))
        for question in questions_for_record(record, indexed.get(identifier_prefix(record["identifier"]).casefold()))
    ]


def generate_exam(input_path: Path, output_path: Path, sources_path: Path | None = None) -> int:
    """Isolate file I/O; write only after the complete batch is generated."""
    if input_path.resolve() == output_path.resolve():
        raise ValueError("questions output must differ from preparation input")
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if sources_path is not None:
        if sources_path.resolve() == output_path.resolve():
            raise ValueError("questions output must differ from source input")
        if not sources_path.is_file():
            raise FileNotFoundError(sources_path)
    prepared = read_json_records(input_path)
    sources = read_json_records(sources_path) if sources_path is not None else None
    records = generate_questions(prepared, sources)
    write_json_records(output_path, records)
    return len(records)


def main() -> int:
    """Run with project data paths and report a CLI exit status."""
    output_path = DATA_DIR / "exam_questions.json"
    try:
        count = generate_exam(DATA_DIR / "exam_prep.json", output_path, DATA_DIR / "exam_sources.json")
    except (OSError, ValueError) as exc:
        print(f"Error generating questions: {exc}", file=sys.stderr)
        return 1
    print(f"Generated {count} question(s) in {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
