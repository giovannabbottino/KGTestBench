"""Offline unit tests for the deterministic ExamSource helpers."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import ExamSource  # noqa: E402


class ExamSourcePureTests(unittest.TestCase):
    """Test deterministic helpers without calling external APIs."""

    def test_clean_text(self) -> None:
        text = 'A  "discard this"  (old (detail))  river  ,  stream.'

        self.assertEqual(ExamSource.clean_text(text), "A river, stream.")

    def test_clean_phrases(self) -> None:
        self.assertEqual(
            ExamSource.clean_phrases('First. "discarded" Second.', 2),
            ["First.", "Second."],
        )

    def test_parse_values(self) -> None:
        self.assertEqual(ExamSource.parse_int("7", 1), 7)
        self.assertEqual(ExamSource.parse_int(None, 1), 1)
        self.assertEqual(ExamSource.parse_float("2.5", 1.0), 2.5)
        self.assertEqual(ExamSource.parse_float(None, 1.0), 1.0)

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
            ExamSource.extract_entity_ids(response), ["Q72946", "Q2615722"]
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

    def test_collect_group_source_combines_pair_records(self) -> None:
        forest = {
            "identifier": "Forest",
            "candidates": [
                {
                    "wikidata_id": "Q1",
                    "wikidata_label": "Forest",
                    "wikipedia_title": "Forest",
                    "phrases": ["Forest phrase."],
                    "description": "Forest phrase.",
                }
            ],
        }
        woodland = {
            "identifier": "Woodland",
            "candidates": [
                {
                    "wikidata_id": "Q2",
                    "wikidata_label": "Woodland",
                    "wikipedia_title": "Woodland",
                    "phrases": ["Woodland phrase."],
                    "description": "Woodland phrase.",
                }
            ],
        }
        with patch.object(ExamSource, "collect_source", side_effect=[forest, woodland]):
            result = ExamSource.collect_group_source(["Forest", "Woodland"])

        self.assertEqual(result["identifier"], "Forest-Woodland")
        self.assertEqual(
            [candidate["identifier"] for candidate in result["candidates"]],
            ["Forest", "Woodland"],
        )
        self.assertEqual(
            result["description"], "Forest phrase. Woodland phrase."
        )

    def test_split_phrases(self) -> None:
        text = "First sentence. Second sentence! Third sentence?"

        self.assertEqual(
            ExamSource.split_phrases(text, 2),
            ["First sentence.", "Second sentence!"],
        )

    def test_split_phrases_empty(self) -> None:
        self.assertEqual(ExamSource.split_phrases("  ", 2), [])

    def test_wikipedia_title(self) -> None:
        entity = {"sitelinks": {"enwiki": {"title": "Python"}}}

        self.assertEqual(ExamSource.wikipedia_title(entity, "en"), "Python")

    def test_wikipedia_title_missing(self) -> None:
        self.assertEqual(ExamSource.wikipedia_title({}, "en"), "")

    def test_load_env(self) -> None:
        with TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "NEW_SETTING=from-file\nEXISTING_SETTING=from-file\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ, {"EXISTING_SETTING": "from-shell"}, clear=False
            ):
                ExamSource.load_env(env_path)
                self.assertEqual(os.environ["NEW_SETTING"], "from-file")
                self.assertEqual(os.environ["EXISTING_SETTING"], "from-shell")

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

    def test_normalize_source_removes_legacy_fields_and_limits_candidates(self) -> None:
        source = {
            "requested_name": "Forest",
            "language": "en",
            "candidates": [1, 2, 3],
        }

        self.assertEqual(
            ExamSource.normalize_source(source),
            {"identifier": "Forest", "candidates": [1, 2]},
        )

if __name__ == "__main__":
    unittest.main()
