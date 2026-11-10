"""RDF consolidation must preserve scope and exclude rejected/conflicting triples."""
import copy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rdflib import Graph
from ExamBoard import adjudicate, generate_rdf as rdf

ORIGINAL = 'wd:Q1 kg:be wd:Q2 .'
CORRECTED = 'wd:Q1 kg:beIn wd:Q2 .'


def annotation():
    left = {"ratings": dict.fromkeys(adjudicate.CRITERIA, True)}
    right = {"ratings": {**left["ratings"], "predicado_correto": False}}
    return {"case_id": "annotation", "identifier": "Example", "case_type": "annotation",
            "original_triple": ORIGINAL, "evidence": {"a1": left, "a2": right},
            "subset_ids": ["unilateral_approvals"], "adjudication": {"status": "pending"}}


def graph_case():
    return {"case_id": "graph", "identifier": "Example", "case_type": "rdf_relation_group",
            "a1_triples": [{"turtle": ORIGINAL}], "a2_triples": [{"turtle": CORRECTED}],
            "subset_ids": ["competing_rdf_relations"], "adjudication": {"status": "pending"}}


def record(case, side, kind=None):
    result = {"selected": side, "rationale": "Evidence", "evidence_quotes": [], "limitations": "", "approve_correction": False, "reason_by_type": {}}
    return adjudicate.decision(case, kind or case["subset_ids"][0], result)


class GenerationTests(unittest.TestCase):
    def test_intersection_uses_rdf_terms_and_identifier_scope(self):
        original = rdf.make_graph(rdf.parse_triples([ORIGINAL]))
        corrected = rdf.make_graph(rdf.parse_triples([CORRECTED]))
        left = {"A": original, "B": corrected}
        right = {"A": original, "B": original}
        with patch.object(rdf, "load_final_graphs", side_effect=[(left, None), (right, None)]):
            result = rdf.load_agreement()
        self.assertEqual(result["A"], set(original))
        self.assertEqual(result["B"], set())

    def test_selected_correction_replaces_rejected_alternative(self):
        case = graph_case()
        graphs, audit = rdf.combine({"Example": set()}, {case["case_id"]: case}, [record(case, "a2")])
        self.assertEqual(graphs["Example"], rdf.parse_triples([CORRECTED]))
        self.assertNotIn("conflicts", audit)

    def test_rejected_original_and_prose_suggestion_are_not_added(self):
        case = annotation()
        case["evidence"]["a2"]["suggested_entities"] = "some proposed entity"
        graphs, _ = rdf.combine({"Example": set()}, {case["case_id"]: case}, [record(case, "a2")])
        self.assertEqual(graphs["Example"], set())

    def test_combined_decision_is_used_for_case_with_multiple_types(self):
        case = annotation()
        case["subset_ids"].append("unilateral_unresolved_entities")
        rows = [record(case, "a1", adjudicate.MULTIPLE)]
        report = {"input_sha256": "hash", "total": 1, "results": rows}
        self.assertEqual(len(rdf.validate_report(report, {"cases": [case]}, "hash")), 1)
        old_report = {**report, "results": [record(case, "a1")]}
        with self.assertRaises(ValueError):
            rdf.validate_report(old_report, {"cases": [case]}, "hash")
        graphs, audit = rdf.combine({"Example": set()}, {case["case_id"]: case}, rows)
        self.assertEqual(graphs["Example"], rdf.parse_triples([ORIGINAL]))
        self.assertNotIn("conflicts", audit)

    def test_cross_case_rejection_cannot_be_reintroduced(self):
        original, corrected = annotation(), graph_case()
        cases = {item["case_id"]: item for item in (original, corrected)}
        rows = [record(original, "a1"), record(corrected, "a2")]
        graphs, audit = rdf.combine({"Example": set()}, cases, rows)
        self.assertEqual(graphs["Example"], rdf.parse_triples([CORRECTED]))
        self.assertTrue(audit["excluded_despite_other_support"])

    def test_mutated_approved_triples_are_rejected(self):
        case = annotation()
        row = record(case, "a1")
        row["approved_triples"] = [CORRECTED]
        with self.assertRaises(ValueError):
            rdf.validate_selection(row, case)

    def test_explicitly_approved_replacement_reaches_final_rdf(self):
        case = annotation()
        case["evidence"]["a2"]["suggested_rdf"] = CORRECTED
        row = record(case, "a2")
        row.update(adjudicate.selected_values(case, "a2", True), approve_correction=True)
        graphs, _ = rdf.combine({"Example": set()}, {case["case_id"]: case}, [row])
        self.assertEqual(graphs["Example"], rdf.parse_triples([CORRECTED]))
        row["approve_correction"] = False
        with self.assertRaises(ValueError):
            rdf.validate_selection(row, case)

    def test_incomplete_failed_stale_duplicate_reports_are_rejected(self):
        case = annotation()
        payload = {"cases": [case]}
        report = {"input_sha256": "hash", "total": 1, "results": [record(case, "a1")]}
        self.assertEqual(len(rdf.validate_report(report, payload, "hash")), 1)
        for change in ({"input_sha256": "old"}, {"results": []},
                       {"results": report["results"] * 2},
                       {"results": [{**record(case, "a1"), "status": "failed"}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                rdf.validate_report({**report, **change}, payload, "hash")

    def test_approved_candidate_identical_to_original_is_kept(self):
        case = annotation()
        case["evidence"]["a2"]["suggested_rdf"] = ORIGINAL
        row = record(case, "a2")
        row.update(adjudicate.selected_values(case, "a2", True), approve_correction=True)
        graphs, _ = rdf.combine({"Example": set()}, {case["case_id"]: case}, [row])
        self.assertEqual(graphs["Example"], rdf.parse_triples([ORIGINAL]))

    def test_turtle_prefixes_language_and_invalid_documents(self):
        triple = 'wd:Q1 rdfs:label "Forest"@en .'
        self.assertEqual(len(rdf.parse_triples([triple, triple])), 1)
        for document in ('invalid Turtle', ORIGINAL + CORRECTED, '[] kg:be wd:Q2 .'):
            with self.assertRaises(ValueError):
                rdf.parse_triples([document])

    def test_output_keeps_per_identifier_graphs_and_deduplicates_union(self):
        shared = rdf.parse_triples([ORIGINAL])
        graphs = {"A": shared, "B": shared | rdf.parse_triples([CORRECTED])}
        with TemporaryDirectory() as directory:
            output = Path(directory) / "rdf"
            rdf.save_graphs(output, graphs, {})
            merged = Graph().parse(output / "exam_rdf.ttl", format="turtle")
            documents = json.loads((output / "exam_rdf.json").read_text(encoding="utf-8"))
            self.assertEqual(len(merged), 2)
            self.assertEqual([row["identifier"] for row in documents], ["A", "B"])
            self.assertEqual(len(Graph().parse(data=documents[0]["rdf"], format="turtle")), 1)
            rdf.save_graphs(output, {"A": shared}, {})
            self.assertEqual(len(Graph().parse(output / "exam_rdf.ttl", format="turtle")), 1)

    def test_missing_results_do_not_produce_a_consensus_only_final_graph(self):
        with TemporaryDirectory() as directory, patch.object(rdf, "RESULTS_DIR", Path(directory)), patch.object(rdf, "LLM_RESULTS", None):
            with self.assertRaises(FileNotFoundError):
                rdf.find_results()

    def test_end_to_end_with_saved_llm_results(self):
        case = annotation()
        payload = {"cases": [case], "sources": {}}
        named, _ = adjudicate.select_disagreements([case])[0]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "adjudications.json"
            path.write_text(json.dumps({"input_sha256": "hash", "total": 1, "results": [record(named, "a1")]}), encoding="utf-8")
            before = path.read_bytes()
            with patch.object(rdf, "LLM_RESULTS", path), patch.object(rdf, "load_export", return_value=(copy.deepcopy(payload), "hash")), patch.object(rdf, "load_agreement", return_value={"Example": set()}):
                output = rdf.run()
            self.assertEqual(len(Graph().parse(output / "exam_rdf.ttl", format="turtle")), 1)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
