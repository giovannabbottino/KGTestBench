"""Read disagreements, adjudicate them sequentially, and save the results."""
import hashlib
import json
from pathlib import Path
from rdflib import Graph, BNode, RDFS

if __package__:
    from . import llm_api
else:
    import llm_api

ROOT = Path(__file__).resolve().parents[1]
INPUT_FILE = ROOT / "data/disagreements.json"
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
PREFIXES = """@prefix wd: <http://www.wikidata.org/entity/> .
@prefix kg: <https://example.org/wikidata-description/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
"""


def read_input():
    content = INPUT_FILE.read_bytes()
    payload = json.loads(content.decode("utf-8-sig"))
    if payload.get("schema_version") != 2 or not isinstance(payload.get("cases"), list):
        raise ValueError("Expected an ExamReview schema_version 2 export with cases")
    return payload, hashlib.sha256(content).hexdigest()


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
    fields = ("readable_triple", "comment", "suggested_entities", "suggested_predicates", "suggested_direction")
    return {"ratings": {key: item["ratings"].get(key) for key in CRITERIA},
            **{key: item[key] for key in fields if item.get(key)},
            "correction_candidate": correction_candidate(item)}


def case_alternatives(case):
    if case["case_type"] == "rdf_relation_group":
        return {side: [{key: item[key] for key in ("turtle", "readable") if key in item}
                      for item in case[f"{side}_triples"]] for side in ("a1", "a2")}
    return {side: annotation_alternative(case["evidence"][side]) for side in ("a1", "a2")}


def build_packet(case, kind):
    fields = ("original_triple", "explicit_unresolved_marker")
    return {**{key: case[key] for key in fields if key in case},
            "case_type": case["case_type"], "alternatives": case_alternatives(case),
            "disagreement_type": kind,
            "case_disagreement_types": list(case["subset_ids"]),
            "source_text": source_text(case)}


def selected_values(case, side, approve_correction=False):
    if case["case_type"] == "rdf_relation_group":
        if approve_correction:
            raise ValueError("RDF groups select an existing set; approve_correction must be false")
        return {"approved_ratings": None,
                "approved_triples": [item["turtle"] for item in case[f"{side}_triples"]],
                "selected_alternative": case[f"{side}_triples"], "rdf_outcome": "selected_rdf_set"}
    ratings = {key: case["evidence"][side]["ratings"].get(key) for key in CRITERIA}
    triples = [case["original_triple"].rstrip().rstrip(".") + " ."] if all(
        value is True for value in ratings.values()) else []
    outcome = "original_approved" if triples else "original_rejected"
    if approve_correction:
        triples = approved_correction(case, side, ratings)
        outcome = "correction_approved"
    return {"approved_ratings": ratings, "approved_triples": triples,
            "rdf_outcome": outcome,
            "selected_alternative": case["evidence"][side],
            "selected_unresolved_marker": case.get("explicit_unresolved_marker", {}).get(side),
            "marker_absence_means": "unknown"}


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


def process_case(case, kind, prompt, path):
    packet = build_packet(case, kind)
    request = llm_api.build_request(prompt, packet)
    response = llm_api.complete(request)
    result = llm_api.parse_response(response)
    record = decision(case, kind, result)
    write_json(path, {"request": request, "response": response, "result": record})
    return record


def new_output():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def run():
    payload, input_hash = read_input()
    prompts = read_prompts()
    tasks = select_disagreements(payload["cases"])
    directory = new_output()
    report = {"input_sha256": input_hash, "model": llm_api.MODEL,
              "prompt_sha256": {kind: hashlib.sha256(text.encode("utf-8")).hexdigest()
                                for kind, text in prompts.items()},
              "total": len(tasks), "results": []}
    write_json(directory / "adjudications.json", report)
    for index, (case, kind) in enumerate(tasks, 1):
        path = directory / f"{case['case_id']}.json"
        record = process_case(case, kind, prompts[kind], path)
        report["results"].append(record)
        write_json(directory / "adjudications.json", report)
        print(f"{index}/{len(tasks)} {case['case_id']}: {record['status']}", flush=True)
    print(f"Results: {directory}", flush=True)
    return report


def main():
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
