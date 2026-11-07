"""Pure triple cleanup and RDF graph serialization."""

import re
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDFS

WD = Namespace("http://www.wikidata.org/entity/")
KG = Namespace("https://example.org/wikidata-description/")


def _normalize_triple_value(value: str) -> str:
    """Normalize text for duplicate triple comparison."""
    return re.sub(r"\s+", " ", value).strip().casefold()


def _normalize_object_value(value: str) -> str:
    """Normalize object text for semantic redundancy checks."""
    normalized = value.replace("-", " ")
    normalized = re.sub(r"[^A-Za-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def dedupe_triples(triples: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Deduplicate triples while preserving readable values."""
    unique: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    for subject, predicate, object_value in triples:
        cleaned = (
            re.sub(r"\s+", " ", subject).strip(),
            re.sub(r"\s+", " ", predicate).strip(),
            re.sub(r"\s+", " ", object_value).strip(),
        )
        if not all(cleaned):
            continue
        key = tuple(_normalize_triple_value(value) for value in cleaned)
        unique.setdefault(key, cleaned)
    return sorted(unique.values(), key=lambda triple: tuple(value.casefold() for value in triple))


def _object_is_redundant(object_value: str, object_values: list[str]) -> bool:
    """Return whether an object is less useful than another object in the group."""
    normalized = _normalize_object_value(object_value)
    if not normalized:
        return True

    other_normalized = [
        _normalize_object_value(other)
        for other in object_values
        if other != object_value
    ]

    return any(
        other
        and other != normalized
        and re.search(rf"\b{re.escape(other)}\b", normalized)
        for other in other_normalized
    )


def prune_redundant_triples(
    triples: list[tuple[str, str, str]]
) -> list[tuple[str, str, str]]:
    """Remove redundant objects within each subject-predicate group."""
    grouped: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for triple in triples:
        grouped.setdefault((triple[0], triple[1]), []).append(triple)

    pruned = []
    for group_triples in grouped.values():
        objects = [triple[2] for triple in group_triples]
        pruned.extend(
            triple
            for triple in group_triples
            if not _object_is_redundant(triple[2], objects)
        )
    return sorted(pruned, key=lambda triple: tuple(value.casefold() for value in triple))


def dedupe_relations(
    relations: list[tuple[str, str, str]]
) -> list[tuple[str, str, str]]:
    """Deduplicate ID-based relation triples before RDF serialization."""
    return sorted(set(relations))


def prune_redundant_relations(
    label_by_id: dict[str, str], relations: list[tuple[str, str, str]]
) -> list[tuple[str, str, str]]:
    """Remove ID-based relations whose readable objects are redundant."""
    grouped: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for subject_id, predicate, object_id in dedupe_relations(relations):
        subject = label_by_id.get(subject_id, subject_id)
        grouped.setdefault((subject, predicate), []).append(
            (subject_id, predicate, object_id)
        )

    pruned = []
    for group_relations in grouped.values():
        objects = [
            label_by_id.get(object_id, object_id)
            for _subject_id, _predicate, object_id in group_relations
        ]
        pruned.extend(
            relation
            for relation in group_relations
            if not _object_is_redundant(
                label_by_id.get(relation[2], relation[2]), objects
            )
        )
    return sorted(pruned)


def build_text_triples(
    label_by_id: dict[str, str], relations: list[tuple[str, str, str]]
) -> list[tuple[str, str, str]]:
    """Build readable subject-predicate-object triples extracted from text."""
    triples = []
    for subject_id, predicate, object_id in sorted(set(relations)):
        subject = label_by_id.get(subject_id, subject_id)
        object_value = label_by_id.get(object_id, object_id)
        triples.append((subject, predicate, object_value))
    return triples


def relation_object_ids(relations: list[tuple[str, str, str]]) -> set[str]:
    """Return linked entity IDs without changing relation order or values."""
    return {object_id for _, _, object_id in relations}


def build_rdf(labels: dict[str, str], relations: list[tuple[str, str, str]], language: str) -> str:
    """Serialize text-derived edges and labels, without fetching type statements."""
    graph = Graph()
    graph.bind("wd", WD)
    graph.bind("kg", KG)
    graph.bind("rdfs", RDFS)
    for entity_id, label in sorted(labels.items()):
        if label:
            graph.add((WD[entity_id], RDFS.label, Literal(label, lang=language)))
    for subject, predicate, object_id in dedupe_relations(relations):
        graph.add((WD[subject], KG[predicate], WD[object_id]))
    return graph.serialize(format="turtle")


def combine_rdf_graphs(documents: list[str]) -> str:
    """Merge Turtle graphs as RDF sets, preserving distinct entity IDs."""
    graph = Graph()
    for document in documents:
        graph.parse(data=document, format="turtle")
    return graph.serialize(format="turtle")
