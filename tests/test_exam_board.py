"""Checks for the sequential ExamBoard pipeline."""
import copy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ExamBoard import adjudicate as board, llm_api


def case():
    left = {"source_text": "Forest is bordered by Uccle.",
            "ratings": dict.fromkeys(board.CRITERIA, True), "comment": "Supported"}
    right = copy.deepcopy(left)
    right["ratings"]["predicado_correto"] = False
    return {"case_id": "example", "identifier": "Forest", "case_type": "annotation",
            "subset_ids": ["unilateral_approvals", "unilateral_unresolved_entities"],
            "original_triple": "wd:Q1 kg:border wd:Q2", "evidence": {"a1": left, "a2": right},
            "explicit_unresolved_marker": {"a1": True, "a2": False},
            "adjudication": {"status": "pending"}}


def answer(side="a1", types=None):
    return {"selected": side, "rationale": "The source supports this assessment.",
            "evidence_quotes": ["bordered by Uccle"], "limitations": "No external lookup.", "approve_correction": False,
            "reason_by_type": dict.fromkeys(types or case()["subset_ids"], "The source supports this choice.")}


def response(result):
    return {"done": True, "message": {"content": json.dumps(result)}}


class BoardTests(unittest.TestCase):
    def test_single_and_multiple_types_each_get_one_call(self):
        original = case()
        single = {**case(), "case_id": "single", "subset_ids": ["unilateral_approvals"]}
        done = {**case(), "case_id": "done", "adjudication": {"status": "adjudicated"}}
        tasks = board.select_disagreements([original, original, single, done])
        self.assertEqual([kind for _, kind in tasks], [board.MULTIPLE, "unilateral_approvals"])
        self.assertEqual([item["source_case_id"] for item, _ in tasks], ["example", "single"])

    def test_readable_ids_are_unique_and_stable(self):
        first = case()
        second = {**case(), "case_id": "second"}
        tasks = board.select_disagreements([first, second])
        self.assertEqual(tasks, board.select_disagreements([second, first]))
        self.assertEqual([item["case_id"] for item, _ in tasks],
                         ["Forest-multiple_disagreements-001", "Forest-multiple_disagreements-002"])
        done = {**first, "adjudication": {"status": "adjudicated"}}
        self.assertEqual(board.select_disagreements([done, second]), tasks[1:])
        for invalid in ({**first, "identifier": "../Forest"},
                        {**first, "identifier": "Forest/Tree"}):
            with self.assertRaises(ValueError):
                board.select_disagreements([invalid])
        with self.assertRaises(ValueError):
            board.select_disagreements([first, {**first, "identifier": "Other"}])

    def test_packet_preserves_disagreement_but_not_saved_decision(self):
        original = case()
        before = copy.deepcopy(original)
        packet = board.build_packet(original, board.prompt_type(original))
        self.assertEqual(packet["alternatives"]["a1"]["ratings"], original["evidence"]["a1"]["ratings"])
        self.assertNotIn("evidence", packet)
        self.assertNotIn("source_text", packet["alternatives"]["a1"])
        self.assertEqual(packet["source_text"], "Forest is bordered by Uccle.")
        self.assertEqual(packet["disagreement_type"], board.MULTIPLE)
        self.assertEqual(packet["case_disagreement_types"], original["subset_ids"])
        self.assertNotIn("adjudication", packet)
        self.assertEqual(original, before)

    def test_conflicting_source_rejected(self):
        original = case()
        original["evidence"]["a2"]["source_text"] = "Different source."
        with self.assertRaises(ValueError):
            board.build_packet(original, "unilateral_approvals")

    def test_chosen_ratings_are_copied_without_approving_rejected_triple(self):
        record = board.decision(case(), "unilateral_approvals", answer("a2"))
        self.assertEqual(record["decision"], "accept_a2")
        self.assertFalse(record["approved_ratings"]["predicado_correto"])
        self.assertEqual(record["approved_triples"], [])
        self.assertFalse(record["selected_unresolved_marker"])
        self.assertEqual(record["marker_absence_means"], "unknown")

    def test_rdf_choice_copies_exact_set(self):
        original = {"case_type": "rdf_relation_group", "a2_triples": [
            {"turtle": "<urn:s> <urn:p> <urn:o> ."}, {"turtle": "<urn:s> <urn:q> <urn:o> ."}]}
        result = board.selected_values(original, "a2")
        self.assertEqual(result["approved_triples"], [item["turtle"] for item in original["a2_triples"]])
        self.assertIsNone(result["approved_ratings"])

    def test_explicit_correction_requires_model_approval(self):
        original = case()
        original["evidence"]["a2"]["suggested_rdf"] = "wd:Q1 kg:borderBy wd:Q2 ."
        packet = board.build_packet(original, board.MULTIPLE)
        self.assertIsNotNone(packet["alternatives"]["a2"]["correction_candidate"])
        rejected = board.selected_values(original, "a2")
        approved = board.selected_values(original, "a2", True)
        self.assertEqual(rejected["rdf_outcome"], "original_rejected")
        self.assertEqual(rejected["approved_triples"], [])
        self.assertEqual(approved["rdf_outcome"], "correction_approved")
        self.assertFalse(approved["approved_ratings"]["predicado_correto"])
        self.assertEqual(approved["approved_triples"], [packet["alternatives"]["a2"]["correction_candidate"]])

    def test_whole_graph_or_prose_is_not_a_correction_candidate(self):
        original = case()
        for suggestion in ("not an RDF triple", "wd:Q1 kg:p wd:Q2 ; kg:q wd:Q3 ."):
            original["evidence"]["a2"]["suggested_rdf"] = suggestion
            self.assertIsNone(board.correction_candidate(original["evidence"]["a2"]))
            with self.assertRaises(ValueError):
                board.selected_values(original, "a2", True)

    def test_explanations_are_kept_without_content_validation(self):
        for reasons in ({}, {"unilateral_approvals": "Only one explained"},
                        dict.fromkeys(case()["subset_ids"], "")):
            result = llm_api.parse_response(response({**answer(), "reason_by_type": reasons}))
            self.assertEqual(result["reason_by_type"], reasons)

    def test_five_single_line_english_prompts_without_persistence_instructions(self):
        prompts = board.read_prompts()
        self.assertEqual(len(set(prompts.values())), 5)
        self.assertTrue(all(text.startswith("You adjudicate") and "\n" not in text for text in prompts.values()))
        for kind, text in prompts.items():
            self.assertIn("case_disagreement_types", text)
            self.assertNotIn("repeated execution", text)
            self.assertNotIn("another vote", text)
        for kind in board.TYPES:
            self.assertIn(f"This call addresses only {kind}.", prompts[kind])
        self.assertIn("more than one disagreement", prompts[board.MULTIPLE])
        self.assertIn("single call", prompts[board.MULTIPLE])

    def test_nonliteral_quote_is_saved_without_failing(self):
        raw = response({**answer(), "evidence_quotes": ["Forest borders Uccle."]})
        with TemporaryDirectory() as directory, patch.object(llm_api, "complete", return_value=raw):
            path = Path(directory) / "case.json"
            result = board.process_case(case(), board.MULTIPLE, "Prompt.", path)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "adjudicated")
            self.assertEqual(saved["response"], raw)
            self.assertEqual(result["evidence_quotes"], ["Forest borders Uccle."])

    def test_pipeline_routes_multiple_types_once_and_overwrites_results(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "disagreements.json"
            single = {**case(), "case_id": "single", "subset_ids": ["unilateral_approvals"]}
            source.write_text(json.dumps({"schema_version": 2, "cases": [case(), single]}), encoding="utf-8")
            before = source.read_bytes()
            with patch.object(board, "INPUT_FILE", source), patch.object(board, "OUTPUT_DIR", root / "results"), patch.object(llm_api, "complete", side_effect=[response(answer()), response(answer(types=single["subset_ids"])), response(answer("a2")), response(answer("a2", single["subset_ids"]))]) as complete:
                report = board.run()
                updated = board.run()
            self.assertEqual(complete.call_count, 4)
            self.assertEqual([item["decision"] for item in report["results"]], ["accept_a1", "accept_a1"])
            self.assertEqual(source.read_bytes(), before)
            saved = [root / "results/adjudications.json"]
            self.assertEqual(json.loads(saved[0].read_text(encoding="utf-8")), updated)
            self.assertEqual(updated["results"][0]["decision"], "accept_a2")
            self.assertEqual(updated["total"], 2)
            self.assertEqual(len(list((root / "results").glob("Forest-*.json"))), 2)
            self.assertTrue((root / "results/Forest-multiple_disagreements-001.json").is_file())
            self.assertTrue((root / "results/Forest-unilateral_approvals-001.json").is_file())
            prompts = board.read_prompts()
            for call in complete.call_args_list:
                request = call.args[0]
                packet = json.loads(request["messages"][1]["content"])
                self.assertEqual(request["messages"][0]["content"], prompts[packet["disagreement_type"]])
            for path in (root / "results").glob("Forest-*.json"):
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["result"]["decision"], "accept_a2")


if __name__ == "__main__":
    unittest.main()
