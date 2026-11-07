"""Offline tests for RDF preparation and each new utility function."""

import copy
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ExamPrep as prep
from utils import relations as r, rdf, wikidata
from rdflib import Graph, Literal
from rdflib.namespace import RDF, RDFS


def candidate(qid="Q1", label="Root", description="Root is a tree."):
    return {"wikidata_id": qid, "wikidata_label": label, "description": description, "phrases": [description]}


def fake_tags(tokens):
    verbs = {"is": "VBZ", "grows": "VBZ", "was": "VBD"}
    return [(word, verbs.get(word.lower(), "IN" if word.lower() == "in" else "NN")) for word in tokens]


class RelationTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(r, "_pos_tag", side_effect=fake_tags).start()
        patch.object(r, "_verb_lemma", side_effect=lambda word: {"is": "be", "was": "be", "grows": "grow"}.get(word, word)).start()

    def test_tokens_names_and_unique_phrases(self):
        self.assertEqual(r._tokens("A red-tree!"), ["A", "red-tree"])
        self.assertEqual([m.span() for m in r._token_matches("A tree")], [(0, 1), (2, 6)])
        self.assertEqual(r._capitalized_phrases("New York grows"), ["New York"])
        self.assertEqual(r._word_phrases(["a", "red", "tree"], {"a"}), ["red", "red tree", "tree"])
        self.assertEqual(r._unique_phrases([" Tree ", "tree", "a", ""], {"a"}), ["Tree"])
        with patch.object(r, "_stop_words", return_value={"a"}):
            self.assertIn("red tree", r._candidate_phrases("A red tree", "en"))

    def test_stopword_cache(self):
        with patch.dict(r._STOP_WORD_CACHE, {}, clear=True), patch.object(r, "load_stopwords", return_value={"The"}) as load:
            self.assertEqual(r._stop_words("en"), {"the"})
            self.assertEqual(r._stop_words("en"), {"the"})
            load.assert_called_once_with("en")

    def test_spans_and_sentence_boundaries(self):
        self.assertEqual(r._phrase_span("A TREE.", "tree"), (2, 6))
        self.assertIsNone(r._phrase_span("A tree", "trees"))
        self.assertEqual(r._sentence_start("One. Two!", 6), 4)
        self.assertEqual(r._sentence_start("One", 2), 0)
        self.assertEqual(r._sentence_end("One. Two", 0), 3)
        self.assertEqual(r._sentence_end("One", 0), 3)

    def test_tags_verbs_particles_and_predicates(self):
        self.assertEqual([tag for _, tag in r._tag_text("Root is tree")], ["NN", "VBZ", "NN"])
        self.assertEqual(r._verb_matches("Root is tree"), [("is", 5, 7)])
        self.assertEqual(r._verb_token_starts("Root is tree. It grows."), {5, 17})
        self.assertEqual(r._predicate_from_parts("Grow", "IN"), "growIn")
        self.assertEqual(r._predicate_from_parts("123"), "")
        self.assertEqual(r._relation_particle(" in the "), "in")
        self.assertEqual(r._relation_particle(", in "), "")
        self.assertEqual(r._relation_particle(" the "), "")
        self.assertEqual(r._best_verb_before_phrase("Root is tree", 8), ("be", 7))
        self.assertIsNone(r._best_verb_before_phrase("Root tree", 5))
        self.assertEqual(r._predicate_from_verb_context("grow", " in "), "growIn")
        self.assertEqual(r._verb_relationship("Root grows in France.", "France"), "growIn")
        self.assertIsNone(r._verb_relationship("Root tree", "tree"))
        self.assertIsNone(r._verb_relationship("Root tree", "missing"))

    def test_resolve_filters_self_and_no_match(self):
        with patch.object(r, "find_entity_id_by_name", side_effect=["Q1", None, "Q2"]):
            self.assertIsNone(r._resolve_phrase("Root", "en", "Q1"))
            self.assertIsNone(r._resolve_phrase("Missing", "en", "Q1"))
            self.assertEqual(r._resolve_phrase("Tree", "en", "Q1"), "Q2")

    def test_extract_relations_and_entity_cap(self):
        with patch.object(r, "_candidate_phrases", return_value=["Root", "is", "tree", "France"]), patch.object(r, "find_entity_id_by_name", side_effect=lambda word, lang: {"Root": "Q1", "tree": "Q2", "France": "Q3"}.get(word)) as lookup:
            self.assertEqual(r.extract_relations("Root is tree in France.", "en", "Q1"), [("Q1", "be", "Q2"), ("Q1", "beIn", "Q3")])
            self.assertNotIn("is", [call.args[0] for call in lookup.call_args_list])
            with patch.object(r, "MAX_ENTITIES", 1):
                self.assertEqual(r.extract_relations("Root is tree in France.", "en", "Q1"), [("Q1", "be", "Q2")])
        self.assertEqual(r.extract_relations(" ", "en", "Q1"), [])


class NlpResourceTests(unittest.TestCase):
    def test_tagging_retries_after_resource_download(self):
        with patch.object(r.nltk, "pos_tag", side_effect=[LookupError(), [("tree", "NN")]]), patch.object(r.nltk, "download") as download:
            self.assertEqual(r._pos_tag(["tree"]), [("tree", "NN")])
            self.assertEqual(download.call_args.args[0], "averaged_perceptron_tagger_eng")

    def test_lemmatizer_retries_after_resource_download(self):
        with patch.object(r._LEMMATIZER, "lemmatize", side_effect=[LookupError(), "be"]), patch.object(r.nltk, "download") as download:
            self.assertEqual(r._verb_lemma("Was"), "be")
            self.assertEqual(download.call_args.args[0], "wordnet")


class RdfTests(unittest.TestCase):
    def test_normalization_deduplication_and_pruning(self):
        self.assertEqual(rdf._normalize_triple_value(" Tree  Root "), "tree root")
        self.assertEqual(rdf._normalize_object_value("Tropical-fruit!"), "tropical fruit")
        triples = [("Root", "be", "fruit"), (" Root ", " be ", "fruit"), ("Root", "be", "tropical fruit"), ("", "be", "fruit")]
        self.assertEqual(rdf.prune_redundant_triples(rdf.dedupe_triples(triples)), [("Root", "be", "fruit")])
        self.assertTrue(rdf._object_is_redundant("", []))
        self.assertFalse(rdf._object_is_redundant("fruit", ["fruit"]))
        edges = [("Q1", "be", "Q2"), ("Q1", "be", "Q2"), ("Q1", "be", "Q3")]
        before = copy.deepcopy(edges)
        self.assertEqual(rdf.prune_redundant_relations({"Q2": "fruit", "Q3": "tropical fruit"}, edges), [("Q1", "be", "Q2")])
        self.assertEqual(edges, before)
        self.assertEqual(rdf.dedupe_relations(edges), [edges[0], edges[2]])
        self.assertEqual(rdf.relation_object_ids(edges), {"Q2", "Q3"})
        self.assertEqual(rdf.build_text_triples({"Q1": "Root"}, [edges[0]]), [("Root", "be", "Q2")])

    def test_turtle_escapes_labels_and_contains_only_requested_edges(self):
        label = 'Root "quoted" \\ path\nFran\u00e7ois'
        document = rdf.build_rdf({"Q1": label, "Q2": "Leaf", "Q3": ""}, [("Q1", "growIn", "Q2")], "en")
        graph = Graph().parse(data=document, format="turtle")
        self.assertIn((rdf.WD.Q1, RDFS.label, Literal(label, lang="en")), graph)
        self.assertIn((rdf.WD.Q1, rdf.KG.growIn, rdf.WD.Q2), graph)
        self.assertEqual(len(graph), 3)
        self.assertFalse(list(graph.triples((None, RDF.type, None))))

    def test_empty_and_combined_graphs(self):
        self.assertEqual(len(Graph().parse(data=rdf.build_rdf({}, [], "en"), format="turtle")), 0)
        self.assertEqual(len(Graph().parse(data=rdf.combine_rdf_graphs([]), format="turtle")), 0)
        first = rdf.build_rdf({"Q1": "Same"}, [("Q1", "be", "Q3")], "en")
        second = rdf.build_rdf({"Q2": "Same"}, [("Q2", "be", "Q3")], "en")
        graph = Graph().parse(data=rdf.combine_rdf_graphs([first, second, first]), format="turtle")
        self.assertEqual(len(graph), 4)


class WikidataPrepTests(unittest.TestCase):
    def test_phrase_resolution_and_empty_result(self):
        with patch.object(wikidata, "find_entity_ids", side_effect=[["Q2"], []]):
            self.assertEqual(wikidata.find_entity_id_by_name("tree", "en"), "Q2")
            self.assertIsNone(wikidata.find_entity_id_by_name("missing", "en"))

    def test_batched_labels_empty_missing_and_language(self):
        with patch.object(wikidata, "request_json", return_value={"entities": {"Q1": {"labels": {"en": {"value": "One"}}}, "Q2": {"labels": {"fr": {"value": "Deux"}}}}}) as request:
            self.assertEqual(wikidata.fetch_labels(set(), "en"), {})
            request.assert_not_called()
            self.assertEqual(wikidata.fetch_labels({f"Q{i}" for i in range(1, 52)}, "en"), {"Q1": "One"})
            self.assertEqual(request.call_count, 2)
            self.assertTrue(all(len(call.args[1]["ids"].split("|")) <= 50 for call in request.call_args_list))


class PrepTests(unittest.TestCase):
    def source(self):
        return {"identifier": "Example", "candidates": [candidate(), candidate("Q2", "Other", "Other grows in France.")]}

    def test_prepare_preserves_sources_and_combines_independent_graphs(self):
        source = self.source()
        before = copy.deepcopy(source)
        with patch.object(prep, "extract_relations", side_effect=[[("Q1", "be", "Q3")], [("Q2", "growIn", "Q4")]]), patch.object(prep, "fetch_labels", side_effect=[{"Q3": "tree"}, {"Q4": "France"}]):
            result = prep.prepare_sources([source], "en")[0]
        self.assertEqual(source, before)
        self.assertEqual(set(result), {"identifier", "rdf"})
        self.assertEqual(result["identifier"], source["identifier"])
        graph = Graph().parse(data=result["rdf"], format="turtle")
        self.assertEqual(len(graph), 6)
        self.assertIn((rdf.WD.Q1, rdf.KG.be, rdf.WD.Q3), graph)
        self.assertIn((rdf.WD.Q2, rdf.KG.growIn, rdf.WD.Q4), graph)

    def test_pruning_keeps_only_retained_relations_in_rdf(self):
        with patch.object(prep, "extract_relations", return_value=[("Q1", "be", "Q2"), ("Q1", "be", "Q3")]), patch.object(prep, "fetch_labels", return_value={"Q2": "fruit", "Q3": "tropical fruit"}):
            result = prep.prepare_candidate(candidate(), "en")
        self.assertEqual(set(result), {"rdf"})
        graph = Graph().parse(data=result["rdf"], format="turtle")
        self.assertIn((rdf.WD.Q1, rdf.KG.be, rdf.WD.Q2), graph)
        self.assertNotIn("Q3", result["rdf"])

    def test_invalid_batch_fails_before_network(self):
        bad = self.source()
        bad["candidates"][0]["wikidata_id"] = "bad"
        with patch.object(prep, "extract_relations") as extract:
            for rows in ([], [bad], [{"identifier": "x", "candidates": []}], [self.source(), self.source()]):
                with self.subTest(rows=rows), self.assertRaises(ValueError):
                    prep.prepare_sources(rows, "en")
            with self.assertRaises(ValueError):
                prep.prepare_sources([self.source()], "pt")
            extract.assert_not_called()

    def test_json_success_and_failure_preserve_input_and_previous_output(self):
        with TemporaryDirectory() as directory:
            source_path, output = Path(directory) / "source.json", Path(directory) / "prep.json"
            source_path.write_text(json.dumps([self.source()]), encoding="utf-8")
            original = source_path.read_bytes()
            with patch.object(prep, "extract_relations", return_value=[]), patch.object(prep, "fetch_labels", return_value={}):
                self.assertEqual(prep.prepare_exam(source_path, output, "en"), 1)
            saved = output.read_bytes()
            with patch.object(prep, "extract_relations", side_effect=RuntimeError("offline")):
                with self.assertRaises(RuntimeError):
                    prep.prepare_exam(source_path, output, "en")
            self.assertEqual(output.read_bytes(), saved)
            self.assertEqual(source_path.read_bytes(), original)
            with self.assertRaises(ValueError):
                prep.prepare_exam(source_path, source_path, "en")

    def test_saved_schema_excludes_source_data_and_preserves_input(self):
        source = self.source()
        source["triples"] = [["legacy", "be", "value"]]
        source["description"] = "Combined source text."
        source["custom_metadata"] = {"origin": "curated"}
        for item in source["candidates"]:
            item["triples"] = [["old", "be", "value"]]
        before = copy.deepcopy(source)
        with TemporaryDirectory() as directory:
            input_path = Path(directory) / "source.json"
            output_path = Path(directory) / "prepared.json"
            input_path.write_text(json.dumps([source]), encoding="utf-8")
            input_bytes = input_path.read_bytes()
            with patch.object(prep, "extract_relations", return_value=[]), patch.object(prep, "fetch_labels", return_value={}):
                prepared = prep.prepare_sources([source], "en")
                prep.prepare_exam(input_path, output_path, "en")
            saved = json.loads(output_path.read_text(encoding="utf-8"))
            for row in prepared + saved:
                self.assertEqual(set(row), {"identifier", "rdf"})
                self.assertEqual(row["identifier"], source["identifier"])
                Graph().parse(data=row["rdf"], format="turtle")
            self.assertEqual(source, before)
            self.assertEqual(input_path.read_bytes(), input_bytes)

    def test_cli_exit_codes(self):
        with patch.object(prep, "prepare_exam", return_value=1):
            self.assertEqual(prep.main(), 0)
        with patch.object(prep, "prepare_exam", side_effect=ValueError("invalid")):
            self.assertEqual(prep.main(), 1)


if __name__ == "__main__":
    unittest.main()
