"""Offline unit tests for the deterministic ExamSource helpers."""

from __future__ import annotations

import json
from copy import deepcopy
import sys
import unittest
from pathlib import Path
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import ExamSource
from utils.file import read_json_records, read_lines, write_json_records
from utils import wikidata
from utils import wikipedia
from utils import text as text_utils
from utils.text import phrase_key, select_unique_phrases
from utils.wikidata import extract_entity_ids  # noqa: E402


class ExamSourcePureTests(unittest.TestCase):
    """Test deterministic helpers without calling external APIs."""

    def test_clean_text(self) -> None:
        text = 'A  "discard this"  (old (detail))  river  ,  stream.'

        self.assertEqual(text_utils.clean_text(text), "A river, stream.")

    def test_clean_phrases(self) -> None:
        self.assertEqual(
            text_utils.clean_phrases('First. "discarded" Second.', 2),
            ["First.", "Second."],
        )

    def test_split_pair_supports_comma_and_hyphen(self) -> None:
        self.assertEqual(
            ExamSource.split_pair("Lake,Pond"),
            ["Lake", "Pond"],
        )
        self.assertEqual(
            ExamSource.split_pair("Forest-Woodland"),
            ["Forest", "Woodland"],
        )

    def test_format_identifier(self) -> None:
        self.assertEqual(
            ExamSource.format_identifier(["Forest", "Woodland"]),
            "Forest-Woodland",
        )

    def test_extract_entity_ids(self) -> None:
        response = {
            "search": [
                {"id": "Q72946"},
                {"id": "Q2615722"},
                {"label": "missing id"},
            ]
        }
        self.assertEqual(
            extract_entity_ids(response), ["Q72946", "Q2615722"]
        )

    def test_build_candidate(self) -> None:
        candidate = ExamSource.build_candidate(
            "Q1",
            {"labels": {"en": {"value": "Forest"}}},
            "Forest",
            ["A forest."],
            "en",
        )
        self.assertEqual(candidate["wikidata_id"], "Q1")
        self.assertEqual(candidate["wikidata_label"], "Forest")
        self.assertEqual(candidate["description"], "A forest.")

    def test_build_source(self) -> None:
        candidates = [{"wikidata_id": "Q1"}]
        self.assertEqual(
            ExamSource.build_source("Forest", candidates),
            {"identifier": "Forest", "candidates": candidates},
        )

    def test_combine_group_source(self) -> None:
        candidates = [
            {"identifier": "Forest", "phrases": ["A forest."]},
            {"identifier": "Woodland", "phrases": ["A woodland."]},
        ]
        self.assertEqual(
            ExamSource.combine_group_source("Forest-Woodland", candidates),
            {
                "identifier": "Forest-Woodland",
                "candidates": candidates,
                "description": "A forest. A woodland.",
            },
        )

    def test_split_phrases(self) -> None:
        text = "First sentence. Second sentence! Third sentence?"

        self.assertEqual(
            text_utils.split_phrases(text, 2),
            ["First sentence.", "Second sentence!"],
        )

    def test_split_phrases_empty(self) -> None:
        self.assertEqual(text_utils.split_phrases("  ", 2), [])

    def test_wikipedia_title(self) -> None:
        entity = {"sitelinks": {"enwiki": {"title": "Python"}}}

        self.assertEqual(wikidata.wikipedia_title(entity, "en"), "Python")

    def test_wikipedia_title_missing(self) -> None:
        self.assertEqual(wikidata.wikipedia_title({}, "en"), "")

    def test_read_input_groups(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ambiguous = root / "ambiguous_words.txt"
            synonyms = root / "synonyms.txt"
            ambiguous.write_text("Python\n\n", encoding="utf-8")
            synonyms.write_text("Forest-Woodland\n", encoding="utf-8")

            self.assertEqual(
                ExamSource.read_input_groups((ambiguous, synonyms)),
                [["Python"], ["Forest", "Woodland"]],
            )

    def test_merge_sources_preserves_existing_names(self) -> None:
        existing = [{"identifier": "Python", "candidates": []}]
        new = [
            {"identifier": "Python", "candidates": ["new"]},
            {"identifier": "Mercury", "candidates": []},
        ]

        self.assertEqual(
            ExamSource.merge_sources(existing, new),
            [
                {"identifier": "Python", "candidates": ["new"]},
                {"identifier": "Mercury", "candidates": []},
            ],
        )

    def test_merge_sources_replaces_empty_previous_result(self) -> None:
        existing = [{"identifier": "Python", "candidates": []}]
        new = [{"identifier": "Python", "candidates": [{"id": "Q28865"}]}]

        self.assertEqual(ExamSource.merge_sources(existing, new), new)

    def test_merge_sources_removes_empty_combined_synonym_record(self) -> None:
        existing = [{"identifier": "Lake,Pond", "candidates": []}]
        new = [
            {"identifier": "Lake", "candidates": [{"id": "Q1"}]},
            {"identifier": "Pond", "candidates": [{"id": "Q2"}]},
        ]

        self.assertEqual(ExamSource.merge_sources(existing, new), new)

    def test_load_sources_returns_existing_json(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            expected = [{"identifier": "Python"}]
            path.write_text(json.dumps(expected), encoding="utf-8")

            self.assertEqual(ExamSource.load_sources(path), expected)

    def test_normalize_source_removes_legacy_fields_and_preserves_candidates(self) -> None:
        source = {
            "requested_name": "Forest",
            "language": "en",
            "candidates": [1, 2, 3],
        }

        self.assertEqual(
            ExamSource.normalize_source(source),
            {"identifier": "Forest", "candidates": [1, 2, 3]},
        )



def entity(qid, title=True):
    return {"id": qid, "labels": {"en": {"value": qid}},
            "sitelinks": {"enwiki": {"title": qid}} if title else {}}


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.search = patch.object(wikidata, "find_entity_ids").start()
        self.fetch = patch.object(ExamSource, "fetch_entity", side_effect=lambda qid, *args, **kwargs: entity(qid)).start()
        self.extract = patch.object(wikipedia, "fetch_extract").start()
        self.addCleanup(patch.stopall)

    def test_ambiguous_skips_incomplete_and_overlapping_texts(self):
        self.search.return_value = ["Q1", "Q2", "Q3", "Q4", "Q5"]
        self.extract.side_effect = ["Only one.", "First. Second.",
                                   "FIRST. Third.", "Fourth. Fifth."]
        result = ExamSource.collect_source("python")
        self.assertEqual(result["identifier"], "Python")
        self.assertEqual([c["wikidata_id"] for c in result["candidates"]], ["Q2", "Q4"])
        self.assertEqual(self.search.call_args.args, ("python", "en", 10))
        self.assertEqual(self.fetch.call_count, 4)

    def test_missing_wikipedia_page_is_skipped(self):
        self.search.return_value = ["Q1", "Q2", "Q3"]
        self.fetch.side_effect = [entity("Q1", False), entity("Q2"), entity("Q3")]
        self.extract.side_effect = ["One. Two.", "Three. Four."]
        result = ExamSource.collect_source("Example")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(self.extract.call_count, 2)

    def test_pair_uses_only_first_match_for_each_term(self):
        self.search.side_effect = [["Q1"], ["Q2"]]
        self.extract.side_effect = ["A forest. Trees grow.", "A woodland. Birds sing."]
        result = ExamSource.collect_group_source(["forest", "woodland"])
        self.assertEqual([call.args for call in self.search.call_args_list],
                         [("forest", "en", 1), ("woodland", "en", 1)])
        self.assertEqual(result["identifier"], "Forest-Woodland")
        self.assertEqual([c["identifier"] for c in result["candidates"]], ["forest", "woodland"])
        self.assertEqual(result["description"], "A forest. Trees grow. A woodland. Birds sing.")

    def test_pair_same_entity_fails_before_fetch(self):
        self.search.side_effect = [["Q1"], ["Q1"]]
        with self.assertRaisesRegex(RuntimeError, "same Wikidata entity"):
            ExamSource.collect_group_source(["car", "automobile"])
        self.fetch.assert_not_called()

    def test_pair_missing_search_result_fails(self):
        self.search.side_effect = [["Q1"], []]
        with self.assertRaisesRegex(RuntimeError, "no entity found"):
            ExamSource.collect_group_source(["forest", "woodland"])
        self.fetch.assert_not_called()

    def test_pair_incomplete_first_match_does_not_search_alternatives(self):
        self.search.side_effect = [["Q1"], ["Q2"]]
        self.extract.side_effect = ["One sentence.", "Two. Three."]
        with self.assertRaisesRegex(RuntimeError, "2 entities"):
            ExamSource.collect_group_source(["forest", "woodland"])
        self.assertEqual(self.search.call_count, 2)

    def test_duplicate_phrases_within_candidate_are_insufficient(self):
        self.search.return_value = ["Q1", "Q2"]
        self.extract.side_effect = ["Same. same.", "Other. Text."]
        with self.assertRaises(RuntimeError):
            ExamSource.collect_source("Example")

    def test_no_candidates_fails(self):
        self.search.return_value = []
        with self.assertRaises(RuntimeError):
            ExamSource.collect_source("Unknown")

    def test_invalid_inputs_do_not_call_api(self):
        for names in ([], [""], ["a", "b", "c"]):
            with self.subTest(names=names), self.assertRaises(ValueError):
                ExamSource.collect_group_source(names)
        self.search.assert_not_called()

    def test_phrase_comparison_matches_dataset_punctuation_rule(self):
        self.assertEqual(phrase_key(" Text "), phrase_key("text."))
        self.assertEqual(select_unique_phrases(["Text", "text.", "Other"], set(), 2),
                         ["Text", "Other"])


class CommandTests(unittest.TestCase):
    def test_existing_identifier_skips_network_and_preserves_bytes(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "sources.json"
            original = '[{"identifier": "python", "candidates": [{"wikidata_id": "Q1"}]}]'
            output.write_text(original, encoding="utf-8")
            with patch.object(ExamSource, "SETTINGS", replace(ExamSource.SETTINGS, output_file=output)), \
                 patch.object(ExamSource, "read_input_groups", return_value=[["Python"]]), \
                 patch.object(ExamSource, "collect_group_source") as collect:
                self.assertEqual(ExamSource.main(), 0)
                collect.assert_not_called()
            self.assertEqual(output.read_text(encoding="utf-8"), original)

    def test_failed_collection_does_not_write_partial_output(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "sources.json"
            output.write_text("[]", encoding="utf-8")
            with patch.object(ExamSource, "SETTINGS", replace(ExamSource.SETTINGS, output_file=output)), \
                 patch.object(ExamSource, "read_input_groups", return_value=[["Python"], ["Seal"]]), \
                 patch.object(ExamSource, "collect_group_source", side_effect=[
                     {"identifier": "Python", "candidates": [{"wikidata_id": "Q1"}]},
                     RuntimeError("incomplete source")]):
                self.assertEqual(ExamSource.main(), 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "[]")

    def test_success_writes_utf8_metadata(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "sources.json"
            record = {"identifier": "Python", "candidates": [{"description": "François."}]}
            with patch.object(ExamSource, "SETTINGS", replace(ExamSource.SETTINGS, output_file=output)), \
                 patch.object(ExamSource, "read_input_groups", return_value=[["Python"]]), \
                 patch.object(ExamSource, "collect_group_source", return_value=record):
                self.assertEqual(ExamSource.main(), 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), [record])
            self.assertIn("François", output.read_text(encoding="utf-8"))


class FunctionalHelpersTests(unittest.TestCase):
    def test_pair_builder_does_not_mutate_candidate_metadata(self):
        candidates = [{"phrases": ["One."]}, {"phrases": ["Two."]}]
        before = deepcopy(candidates)
        result = ExamSource.build_group_source(["First", "Second"], candidates)
        self.assertEqual(candidates, before)
        self.assertEqual(result["candidates"][0]["identifier"], "First")
        self.assertIsNot(result["candidates"][0], candidates[0])

    def test_phrase_selection_leaves_exclusions_unchanged(self):
        excluded = {"first."}
        result = select_unique_phrases(["First", "Second", "second.", "Third"], excluded, 2)
        self.assertEqual(result, ["Second", "Third"])
        self.assertEqual(excluded, {"first."})

    def test_merge_matches_case_and_preserves_inputs(self):
        existing = [{"identifier": "python", "candidates": []}]
        new = [{"identifier": "Python", "candidates": [{"wikidata_id": "Q1"}]}]
        before = deepcopy((existing, new))
        self.assertEqual(ExamSource.merge_sources(existing, new), new)
        self.assertEqual((existing, new), before)

    def test_json_reader_rejects_non_record_collections(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "records.json"
            for payload in ({"identifier": "Python"}, ["Python"], [None]):
                with self.subTest(payload=payload):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        read_json_records(path)

    def test_invalid_limits_fail_before_collection(self):
        with patch.object(ExamSource, "SETTINGS", replace(ExamSource.SETTINGS, phrase_limit=0)), \
             patch.object(ExamSource, "generate_sources") as generate:
            self.assertEqual(ExamSource.main(), 2)
            generate.assert_not_called()


class TextUtilsTests(unittest.TestCase):
    def test_split_phrases(self):
        for value, limit, expected in ((" One.\nTwo!\tThree? Last ", 3, ["One.", "Two!", "Three?"]),
                                       (" \t", 2, []), ("One.", 0, []), ("No punctuation", 2, ["No punctuation"])):
            with self.subTest(value=value, limit=limit):
                self.assertEqual(text_utils.split_phrases(value, limit), expected)

    def test_clean_text(self):
        self.assertEqual(text_utils.clean_text(' A "quoted" (outer (inner)) river , flowing ! '), "A river, flowing!")
        self.assertEqual(text_utils.clean_text('"quoted" (parenthesized)'), "")
        self.assertEqual(text_utils.clean_text("Fran\u00e7ois's river"), "Fran\u00e7ois's river")

    def test_clean_phrases(self):
        self.assertEqual(text_utils.clean_phrases('"discard"', 2), [])
        self.assertEqual(text_utils.clean_phrases('One (detail). Two. Three.', 2), ["One.", "Two."])

    def test_sentence_text(self):
        for source, expected in (("", ""), ("  ", ""), (" Text ", "Text."),
                                 ("Text.", "Text."), ("Text!", "Text!"), ("Text?", "Text?")):
            with self.subTest(source=source):
                self.assertEqual(text_utils.sentence_text(source), expected)

    def test_phrase_key(self):
        self.assertEqual(phrase_key(" STRASSE "), phrase_key("Stra\u00dfe."))
        self.assertEqual(phrase_key(""), "")
        self.assertNotEqual(phrase_key("Text!"), phrase_key("Text?"))

    def test_select_unique_phrases_preserves_order_and_inputs(self):
        phrases = ["", " ", "First", "first.", "Second", "Third"]
        before = list(phrases)
        excluded = {"second."}
        self.assertEqual(select_unique_phrases(phrases, excluded, 2), ["First", "Third"])
        self.assertEqual(phrases, before)
        self.assertEqual(excluded, {"second."})

    def test_select_unique_phrases_boundaries(self):
        self.assertEqual(select_unique_phrases([], set(), 2), [])
        self.assertEqual(select_unique_phrases(["One"], {"one."}, 2), [])
        self.assertEqual(select_unique_phrases(["One"], set(), 2), ["One"])
        self.assertEqual(select_unique_phrases(["One", "Two"], set(), 0), [])

    def test_format_identifier(self):
        for names, expected in ((["python"], "Python"), (["river", "stream"], "River-Stream"),
                                ([], ""), (["iPhone"], "IPhone")):
            with self.subTest(names=names):
                self.assertEqual(text_utils.format_identifier(names), expected)


class FileUtilsTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_read_json_records_missing_empty_and_valid(self):
        path = self.root / "records.json"
        self.assertEqual(read_json_records(path), [])
        for records in ([], [{"name": "Fran\u00e7ois", "values": [1, None]}]):
            path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(read_json_records(path), records)

    def test_read_json_records_malformed_json(self):
        path = self.root / "records.json"
        path.write_text("[{", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            read_json_records(path)

    def test_read_json_records_rejects_non_objects(self):
        path = self.root / "records.json"
        for value in ({}, None, [1], [{}, "bad"]):
            with self.subTest(value=value):
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_json_records(path)

    def test_write_json_records_creates_parents_and_preserves_unicode(self):
        path = self.root / "nested" / "records.json"
        records = [{"name": "Fran\u00e7ois", "nested": {"value": 1}}]
        before = deepcopy(records)
        write_json_records(path, records)
        self.assertEqual(read_json_records(path), records)
        self.assertEqual(records, before)
        self.assertIn("Fran\u00e7ois", path.read_text(encoding="utf-8"))
        self.assertTrue(path.read_bytes().endswith(b"\n"))

    def test_write_json_records_replaces_existing_contents(self):
        path = self.root / "records.json"
        write_json_records(path, [{"old": True}])
        write_json_records(path, [])
        self.assertEqual(read_json_records(path), [])

    def test_read_lines(self):
        path = self.root / "lines.txt"
        self.assertEqual(read_lines(path), [])
        self.assertEqual(read_lines(self.root), [])
        path.write_text("", encoding="utf-8")
        self.assertEqual(read_lines(path), [])
        path.write_bytes("Fran\u00e7ois\r\n\r\nLast\n".encode("utf-8"))
        self.assertEqual(read_lines(path), ["Fran\u00e7ois", "", "Last"])

    def test_file_io_errors_propagate(self):
        path = self.root / "records.json"
        path.write_text("[]", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                read_json_records(path)
            with self.assertRaises(PermissionError):
                read_lines(path)
        with patch.object(Path, "write_text", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                write_json_records(self.root / "out.json", [])


class ConfigurationPathTests(unittest.TestCase):
    def test_default_paths_remain_at_project_root(self):
        from utils.config import load_settings
        with patch("utils.config.load_env") as load_env:
            settings = load_settings()
        root = Path(__file__).resolve().parents[1]
        load_env.assert_called_once_with(root / ".env")
        self.assertEqual(settings.output_file, root / "data" / "exam_sources.json")
        self.assertEqual(settings.input_files, (
            root / "data" / "ambiguous_words.txt", root / "data" / "synonyms.txt",
        ))

    def test_explicit_base_directory_is_respected(self):
        from utils.config import load_settings
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("utils.config.load_env") as load_env:
                settings = load_settings(root)
            load_env.assert_called_once_with(root / ".env")
            self.assertEqual(settings.output_file, root / "data" / "exam_sources.json")


if __name__ == "__main__":
    unittest.main()
