"""Read disagreements, adjudicate them, and save compact results."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
from rdflib import Graph, BNode, RDFS

if __package__:
    from . import llm_api
else:
    import llm_api

ROOT = Path(__file__).resolve().parents[1]
INPUT_FILE = ROOT / "data/disagreements.csv"
OUTPUT_DIR = ROOT / "data/adjudication"
PROMPTS_DIR = Path(__file__).with_name("prompts")
TYPES = (
    "unilateral_approvals",
    "shared_rejection_diagnosis_divergence",
    "competing_rdf_relations",
    "unilateral_unresolved_entities",
)
MULTIPLE = "multiple_disagreements"
CRITERIA = ("entidade_correta", "predicado_correto",
            "direcao_relacao_correta", "fidelidade_semantica")
CSV_TYPE_MAP = {
    "Entity": "unilateral_unresolved_entities",
    "Predicate": "shared_rejection_diagnosis_divergence",
    "Direction": "shared_rejection_diagnosis_divergence",
    "Semantic fidelity": "shared_rejection_diagnosis_divergence",
}
PREFIXES = """@prefix wd: <http://www.wikidata.org/entity/> .
@prefix kg: <https://example.org/wikidata-description/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
"""
PERSISTED_RESULT_FIELDS = (
    "case_id", "identifier", "source_case_id", "disagreement_type", "status",
    "case_disagreement_types", "decision", "approve_correction",
    "approved_ratings", "approved_triples", "rdf_outcome",
)


def read_input():
    content = INPUT_FILE.read_bytes()
    if INPUT_FILE.suffix.lower() == ".csv":
        return csv_payload(content), hashlib.sha256(content).hexdigest()
    payload = json.loads(content.decode("utf-8-sig"))
    if payload.get("schema_version") != 2 or not isinstance(payload.get("cases"), list):
        raise ValueError("Expected an ExamReview schema_version 2 export with cases")
    return payload, hashlib.sha256(content).hexdigest()


def filter_payload(payload, identifier=None):
    """Return only the requested identifier while preserving the input schema."""
    if identifier is None:
        return payload
    wanted = identifier.strip()
    if not wanted:
        raise ValueError("Identifier cannot be empty")
    cases = [case for case in payload["cases"]
             if case.get("identifier", "").casefold() == wanted.casefold()]
    if not cases:
        raise ValueError(f"Identifier not found in disagreements input: {identifier}")
    return {**payload, "cases": cases}


def csv_decisions(row):
    """Read all numbered disagreement columns from one CSV row."""
    decisions = []
    number = 1
    while f"disagreement_type_{number}" in row:
        kind = (row.get(f"disagreement_type_{number}") or "").strip()
        if kind:
            decisions.append({
                "type": kind,
                "a1": (row.get(f"a1_decision_{number}") or "").strip(),
                "a2": (row.get(f"a2_decision_{number}") or "").strip(),
                "decision": (row.get(f"decision_{number}") or "").strip(),
            })
        number += 1
    return decisions


def csv_payload(content):
    """Normalize the current ExamReview CSV into the ExamBoard case shape."""
    rows = csv.DictReader(io.StringIO(content.decode("utf-8-sig", errors="strict")))
    if not rows.fieldnames:
        raise ValueError("The disagreements CSV has no header")
    required = {"identifier", "original_text", "original_triple"}
    missing = required - set(rows.fieldnames)
    if missing:
        raise ValueError(f"The disagreements CSV is missing columns: {sorted(missing)}")

    cases = []
    for index, row in enumerate(rows, 1):
        blocks = csv_decisions(row)
        if not blocks:
            continue
        mapped = list(dict.fromkeys(
            CSV_TYPE_MAP.get(item["type"], "shared_rejection_diagnosis_divergence")
            for item in blocks
        ))
        cases.append({
            "case_id": f"csv-{index:04d}",
            "identifier": (row.get("identifier") or "").strip(),
            "case_type": "csv_annotation",
            "subset_ids": mapped,
            "original_triple": (row.get("original_triple") or "").strip(),
            "evidence": {
                side: {"source_text": (row.get("original_text") or "").strip(),
                       "csv_decisions": [{"type": item["type"],
                                           "decision": item[side]}
                                          for item in blocks]}
                for side in ("a1", "a2")
            },
            "adjudication": {"status": "pending"},
        })
    return {"schema_version": 2, "sources": {"csv": str(INPUT_FILE)}, "cases": cases}


def read_prompts():
    prompts = {name: (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8-sig").strip()
               for name in (*TYPES, MULTIPLE)}
    if any(not text or "\n" in text or "\r" in text for text in prompts.values()):
        raise ValueError("Each prompt must be a single non-empty paragraph")
    return prompts


def prompt_type(case):
    kinds = [kind for kind in TYPES if kind in case["subset_ids"]]
    return MULTIPLE if len(kinds) > 1 else kinds[0]


def select_disagreements(cases):
    unique = {}
    for case in cases:
        key = case["case_id"]
        if key in unique and unique[key] != case:
            raise ValueError(f"Conflicting duplicate case_id: {key}")
        unique[key] = case
    counts = {}
    tasks = []
    for key in sorted(unique):
        case = unique[key]
        if not any(kind in TYPES for kind in case["subset_ids"]):
            continue
        identifier = case["identifier"]
        if not identifier or any(not (char.isalnum() or char in "-_ ") for char in identifier):
            raise ValueError(f"Unsafe Identifier for output filename: {identifier!r}")
        kind = prompt_type(case)
        base = f"{identifier}-{kind}"
        counts[base.casefold()] = counts.get(base.casefold(), 0) + 1
        named = {**case, "source_case_id": key,
                 "case_id": f"{base}-{counts[base.casefold()]:03d}"}
        if case["adjudication"]["status"] == "pending":
            tasks.append((named, kind))
    return tasks


def source_text(case):
    context = case.get("evidence", case.get("source_context", {}))
    texts = {item["source_text"].strip() for item in context.values()
             if isinstance(item.get("source_text"), str)}
    if len(texts) != 1 or not next(iter(texts), ""):
        raise ValueError("Missing or conflicting source text")
    return next(iter(texts))


def correction_candidate(item):
    """Only a single explicit relation is a row-level correction, never a whole graph."""
    text = item.get("suggested_rdf")
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        graph = Graph().parse(data=PREFIXES + text, format="turtle")
    except Exception:
        return None
    relations = [triple for triple in graph if triple[1] != RDFS.label]
    if len(relations) != 1 or any(isinstance(term, BNode) for term in relations[0]):
        return None
    return " ".join(term.n3() for term in relations[0]) + " ."


def annotation_alternative(item):
    fields = (
        "readable_triple", "comment", "suggested_entities",
        "suggested_predicates", "suggested_direction",
    )
    alternative = {"ratings": {key: item["ratings"].get(key) for key in CRITERIA}}
    alternative.update({key: item[key] for key in fields if item.get(key)})
    alternative["correction_candidate"] = correction_candidate(item)
    return alternative


def case_alternatives(case):
    if case["case_type"] == "csv_annotation":
        return {side: {"original_triple": case["original_triple"],
                       "decisions": case["evidence"][side]["csv_decisions"]}
                for side in ("a1", "a2")}
    if case["case_type"] == "rdf_relation_group":
        return {side: [{key: item[key] for key in ("turtle", "readable") if key in item}
                      for item in case[f"{side}_triples"]] for side in ("a1", "a2")}
    return {side: annotation_alternative(case["evidence"][side]) for side in ("a1", "a2")}


def build_packet(case, kind):
    fields = ("original_triple", "explicit_unresolved_marker")
    packet = {key: case[key] for key in fields if key in case}
    packet.update({
        "case_type": case["case_type"],
        "alternatives": case_alternatives(case),
        "disagreement_type": kind,
        "case_disagreement_types": list(case["subset_ids"]),
        "source_text": source_text(case),
    })
    return packet


def selection_result(ratings, triples, outcome, alternative, marker=None):
    """Build the common part of a selected alternative result."""
    return {
        "approved_ratings": ratings,
        "approved_triples": triples,
        "rdf_outcome": outcome,
        "selected_alternative": alternative,
        "selected_unresolved_marker": marker,
        "marker_absence_means": "unknown",
    }


def csv_selection(case, side):
    decisions = [item["decision"].upper() for item in case["evidence"][side]["csv_decisions"]]
    approved = bool(decisions) and all(value == "TRUE" for value in decisions)
    triple = case["original_triple"].rstrip().rstrip(".") + " ."
    triples = [triple] if approved else []
    outcome = "original_approved" if approved else "original_rejected"
    return selection_result(None, triples, outcome, case["evidence"][side])


def rdf_group_selection(case, side, approve_correction):
    if approve_correction:
        raise ValueError("RDF groups select an existing set; approve_correction must be false")
    triples = [item["turtle"] for item in case[f"{side}_triples"]]
    return selection_result(None, triples, "selected_rdf_set", case[f"{side}_triples"])


def annotation_selection(case, side, approve_correction):
    ratings = {key: case["evidence"][side]["ratings"].get(key) for key in CRITERIA}
    approved = all(value is True for value in ratings.values())
    triple = case["original_triple"].rstrip().rstrip(".") + " ."
    triples = [triple] if approved else []
    outcome = "original_approved" if approved else "original_rejected"
    if approve_correction:
        triples = approved_correction(case, side, ratings)
        outcome = "correction_approved"
    marker = case.get("explicit_unresolved_marker", {}).get(side)
    return selection_result(ratings, triples, outcome, case["evidence"][side], marker)


def selected_values(case, side, approve_correction=False):
    if case["case_type"] == "csv_annotation":
        return csv_selection(case, side)
    if case["case_type"] == "rdf_relation_group":
        return rdf_group_selection(case, side, approve_correction)
    return annotation_selection(case, side, approve_correction)


def approved_correction(case, side, ratings):
    candidate = correction_candidate(case["evidence"][side])
    if not candidate or not any(value is False for value in ratings.values()):
        raise ValueError("A correction requires a rejected original and a single explicit replacement")
    return [candidate]


def decision(case, kind, result):
    return {"case_id": case["case_id"], "identifier": case["identifier"],
            "source_case_id": case.get("source_case_id", case["case_id"]),
            "disagreement_type": kind, "status": "adjudicated",
            "case_disagreement_types": list(case["subset_ids"]),
            "decision": f"accept_{result['selected']}", "origin": "llm",
            "reviewer": f"ollama:{llm_api.MODEL}", "notes": result["rationale"],
            "evidence_quotes": result["evidence_quotes"], "limitations": result["limitations"],
            "reason_by_type": result["reason_by_type"], "approve_correction": result["approve_correction"],
            **selected_values(case, result["selected"], result["approve_correction"])}


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def compact_result(record):
    """Keep only fields needed to validate decisions and build the final RDF."""
    return {key: record[key] for key in PERSISTED_RESULT_FIELDS if key in record}


def process_case(case, kind, prompt, path):
    packet = build_packet(case, kind)
    request = llm_api.build_request(prompt, packet)
    response = llm_api.complete(request)
    result = llm_api.parse_response(response)
    record = decision(case, kind, result)
    write_json(path, compact_result(record))
    return record


def new_output(identifier=None):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    prefix = f"{identifier}-".casefold() if identifier else None
    for path in OUTPUT_DIR.glob("*.json"):
        if path.name != "adjudications.json" and (prefix is None or path.name.casefold().startswith(prefix)):
            path.unlink()
    return OUTPUT_DIR


def run(identifier=None):
    payload, input_hash = read_input()
    payload = filter_payload(payload, identifier)
    prompts = read_prompts()
    tasks = select_disagreements(payload["cases"])
    directory = new_output(identifier)
    report = {"input_sha256": input_hash, "identifier": identifier,
              "total": len(tasks), "results": []}
    write_json(directory / "adjudications.json", report)
    for index, (case, kind) in enumerate(tasks, 1):
        path = directory / f"{case['case_id']}.json"
        record = process_case(case, kind, prompts[kind], path)
        report["results"].append(compact_result(record))
        write_json(directory / "adjudications.json", report)
        print(f"{index}/{len(tasks)} {case['case_id']}: {record['status']}", flush=True)
    print(f"Results: {directory}", flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Adjudicate ExamBoard disagreements")
    parser.add_argument("--identifier", help="Run only this identifier, e.g. Seal")
    args = parser.parse_args(argv)
    try:
        llm_api.ensure_model()
        run(args.identifier)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        print(f"Adjudication failed: {error}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
