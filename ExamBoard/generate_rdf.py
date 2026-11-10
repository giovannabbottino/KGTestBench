"""Combine shared final RDF triples with validated LLM adjudications."""
import hashlib
import json
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rdflib import BNode, Graph, Namespace
from ExamBoard import adjudicate
from ExamReview.export_disagreements import load_export
from ExamReview.rdf_comparison import load_final_graphs, validate_graph_keys

ROOT = Path(__file__).resolve().parents[1]
DISAGREEMENTS = ROOT / "data/disagreements.json"
RESULTS_DIR = ROOT / "data/adjudication"
LLM_RESULTS = None  # Set a Path to override data/adjudication/adjudications.json.
WORKBOOKS = {side: ROOT / f"data/inter-rater-reliability/{side}-rdf.xlsx" for side in ("a1", "a2")}
PREFIXES = """@prefix wd: <http://www.wikidata.org/entity/> .
@prefix kg: <https://example.org/wikidata-description/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
"""


def find_results():
    if LLM_RESULTS is not None:
        return Path(LLM_RESULTS)
    path = RESULTS_DIR / "adjudications.json"
    if not path.is_file():
        raise FileNotFoundError("No LLM results in data/adjudication. Run python -m ExamBoard first.")
    return path


def parse_triples(documents):
    graph = Graph()
    for document in documents:
        try:
            parsed = Graph().parse(data=PREFIXES + document, format="turtle")
        except Exception as error:
            raise ValueError(f"Invalid Turtle triple: {document}") from error
        if len(parsed) != 1 or any(isinstance(term, BNode) for triple in parsed for term in triple):
            raise ValueError("Expected one RDF triple without blank nodes per entry")
        graph += parsed
    return set(graph)


def validate_report(report, payload, input_hash):
    if report.get("input_sha256") != input_hash:
        raise ValueError("LLM results do not match the current disagreements.json")
    expected = {(case["source_case_id"], kind) for case, kind in adjudicate.select_disagreements(payload["cases"])}
    records = report.get("results", [])
    actual = [(item["source_case_id"], item["disagreement_type"]) for item in records]
    if report.get("total") != len(expected) or len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Incomplete, duplicate, or unexpected LLM results")
    if any(item.get("status") != "adjudicated" for item in records):
        raise ValueError("LLM results contain failed or unfinished cases")
    return records


def validate_selection(record, case):
    if record.get("decision") not in ("accept_a1", "accept_a2") or record.get("identifier") != case["identifier"]:
        raise ValueError(f"Invalid decision or Identifier for {case['case_id']}")
    approval = record.get("approve_correction", False)
    if type(approval) is not bool:
        raise ValueError("approve_correction must be a boolean")
    expected = adjudicate.selected_values(case, record["decision"][-2:], approval)
    if record.get("approved_ratings") != expected["approved_ratings"]:
        raise ValueError(f"Ratings differ from the selected assessment: {case['case_id']}")
    if parse_triples(record.get("approved_triples", [])) != parse_triples(expected["approved_triples"]):
        raise ValueError(f"Triples differ from the selected alternative: {case['case_id']}")


def load_agreement():
    left, _ = load_final_graphs(WORKBOOKS["a1"], "rdf completo")
    right, _ = load_final_graphs(WORKBOOKS["a2"], "rdf corrigido")
    validate_graph_keys(left, right)
    return {identifier: set(left[identifier]) & set(right[identifier]) for identifier in left}


def case_triples(case):
    if case["case_type"] == "rdf_relation_group":
        return parse_triples([item["turtle"] for side in ("a1", "a2") for item in case[f"{side}_triples"]])
    return parse_triples([case["original_triple"].rstrip().rstrip(".") + " ."])


def resolve_case(case, record):
    candidates = case_triples(case)
    approved = parse_triples(record["approved_triples"])
    return approved, candidates - approved


def n3_triples(triples):
    return sorted(" ".join(term.n3() for term in triple) + " ." for triple in triples)


def combine(agreement, cases, records):
    accepted = {identifier: set() for identifier in agreement}
    excluded = {identifier: set() for identifier in agreement}
    for record in records:
        case = cases[record["source_case_id"]]
        validate_selection(record, case)
        identifier = case["identifier"]
        if identifier not in agreement:
            raise ValueError(f"No agreement graph for {identifier}")
        positive, negative = resolve_case(case, record)
        accepted[identifier].update(positive)
        excluded[identifier].update(negative)
    graphs = {key: (agreement[key] | accepted[key]) - excluded[key] for key in agreement}
    collisions = {key: n3_triples((agreement[key] | accepted[key]) & excluded[key])
                  for key in agreement if (agreement[key] | accepted[key]) & excluded[key]}
    audit = {"agreement_basis": "Intersection of final a1/a2 RDF graphs, scoped by Identifier",
             "excluded_despite_other_support": collisions,
             "counts": {key: {"agreement": len(agreement[key]), "llm_approved": len(accepted[key]),
                              "excluded": len(excluded[key]), "final": len(graphs[key])} for key in graphs}}
    return graphs, audit


def make_graph(triples):
    graph = Graph()
    graph.bind("wd", Namespace("http://www.wikidata.org/entity/"))
    graph.bind("kg", Namespace("https://example.org/wikidata-description/"))
    for triple in triples:
        graph.add(triple)
    return graph


def save_graphs(directory, graphs, audit):
    documents = [{"identifier": key, "rdf": make_graph(graphs[key]).serialize(format="turtle")}
                 for key in sorted(graphs)]
    combined = make_graph(set().union(*graphs.values()))
    turtle = combined.serialize(format="turtle")
    Graph().parse(data=turtle, format="turtle")
    directory.mkdir(exist_ok=True)
    adjudicate.write_json(directory / "exam_rdf.json", documents)
    (directory / "exam_rdf.ttl").write_text(turtle, encoding="utf-8")
    adjudicate.write_json(directory / "rdf_report.json", {**audit, "combined_triples": len(combined)})


def run():
    result_path = find_results()
    payload, input_hash = load_export(DISAGREEMENTS, WORKBOOKS)
    result_bytes = result_path.read_bytes()
    report = json.loads(result_bytes.decode("utf-8-sig"))
    records = validate_report(report, payload, input_hash)
    cases = {case["case_id"]: case for case in payload["cases"]}
    graphs, audit = combine(load_agreement(), cases, records)
    audit.update(input_sha256=input_hash, results_sha256=hashlib.sha256(result_bytes).hexdigest(),
                 results_file=str(result_path), sources=payload["sources"])
    output = result_path.parent / "rdf"
    save_graphs(output, graphs, audit)
    print(f"Saved RDF to {output}", flush=True)
    return output


def main():
    try:
        run()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"RDF generation failed: {error}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
