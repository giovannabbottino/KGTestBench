"""Behavioral checks for deterministic SPARQL generation."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rdflib import Literal, URIRef

from ExamQuestions import (
    direct_link_query, expected_answers, generate_exam, generate_questions,
    neighborhood_query, parse_rdf, main, select_target,
    build_question, answer_question, entity_questions, candidate_context,
    validate_candidate_fields, validate_unique_candidates, validate_candidate_subjects,
    resolve_candidate_context, ordered_candidates,
)

RDF = '''
@prefix ex: <https://example.org/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:a ex:p ex:b, ex:c ; rdfs:label "Alpha"@en ; ex:text "quoted \\"text\\""@en ; ex:count 2 .
ex:b ex:p ex:c ; rdfs:label "Beta"@en .
ex:c rdfs:label "Gamma"@en .
'''


class ExamQuestionsTests(unittest.TestCase):
    def test_missing_id_resolves_unique_label_without_mutation(self):
        graph = parse_rdf(RDF)
        source = {"identifier": "Example", "candidates": [{
            "wikidata_id": "Q999", "wikidata_label": " alpha ",
            "description": "Gamma before Beta.", "wikipedia_title": "Alpha title",
        }]}
        before, triples = copy.deepcopy(source), set(graph)
        context = ordered_candidates(source, graph)[0]
        self.assertEqual(context["uri"], "https://example.org/a")
        questions = generate_questions([{"identifier": "Example", "rdf": RDF}], [source])
        self.assertEqual(questions[2]["expected_answers"], ["<https://example.org/c>"])
        self.assertIn("<https://example.org/a>", questions[2]["sparql"])
        self.assertEqual(source, before)
        self.assertEqual(set(graph), triples)

    def test_id_match_takes_priority_over_label(self):
        graph = parse_rdf(RDF.replace("ex:b", "<http://www.wikidata.org/entity/Q2>"))
        context = resolve_candidate_context({"wikidata_id": "Q2", "wikidata_label": "Alpha", "description": "Text."}, graph)
        self.assertEqual(context["uri"], "http://www.wikidata.org/entity/Q2")

    def test_label_fallback_rejects_ambiguous_missing_and_duplicate_matches(self):
        candidate = {"wikidata_id": "Q999", "wikidata_label": "Alpha", "description": "Text."}
        with self.assertRaisesRegex(ValueError, "ambiguous RDF label"):
            resolve_candidate_context(candidate, parse_rdf(RDF.replace('"Beta"', '"Alpha"')))
        for label in ("Al", "Gamma", "unknown"):
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "absent from prepared RDF"):
                ordered_candidates({"candidates": [{**candidate, "wikidata_label": label}]}, parse_rdf(RDF))
        with self.assertRaisesRegex(ValueError, "duplicate candidate"):
            ordered_candidates({"candidates": [candidate, {**candidate, "wikidata_id": "Q998"}]}, parse_rdf(RDF))

    def test_question_building_does_not_execute_queries(self):
        graph = parse_rdf(RDF)
        with patch.object(graph, "query", side_effect=AssertionError("unexpected query")):
            questions = entity_questions(graph, URIRef("https://example.org/a"), "Example_01", "Alpha")
        self.assertEqual(len(questions), 3)
        self.assertTrue(all(set(q) == {"Identifier", "pergunta", "sparql"} for q in questions))
        definition = questions[0]
        before = dict(definition)
        answered = answer_question(graph, definition)
        self.assertEqual(definition, before)
        self.assertEqual(answered["expected_answers"], ['"Beta"@en', '"Gamma"@en'])

    def test_answers_use_named_variable_and_skip_unbound_values(self):
        query = '''SELECT ?other ?answer WHERE {
          VALUES (?other ?answer) { (1 "yes"@en) (2 "yes"@en) (3 UNDEF) }
        }'''
        self.assertEqual(expected_answers(parse_rdf(RDF), query), ['"yes"@en'])
        self.assertEqual(expected_answers(parse_rdf(RDF), "SELECT ?answer WHERE { FILTER(false) }"), [])

    def test_answer_errors_identify_question(self):
        for query in ("SELECT ?other WHERE { FILTER(false) }", "ASK {}", "invalid sparql"):
            question = build_question("Example_01_02", "Question?", query)
            with self.subTest(query=query), self.assertRaisesRegex(ValueError, "Example_01_02"):
                answer_question(parse_rdf(RDF), question)

    def test_candidate_field_validation_and_context(self):
        valid = {"wikidata_id": "Q9", "description": "A description."}
        for candidate in (None, {}, {**valid, "wikidata_id": "bad"},
                          {**valid, "description": " "}, {**valid, "wikipedia_title": 3}):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                validate_candidate_fields(candidate)
        before = dict(valid)
        result = candidate_context(valid)
        self.assertEqual(valid, before)
        self.assertEqual(result, {"uri": "http://www.wikidata.org/entity/Q9",
                                  "description": "A description.", "wikipedia_title": ""})

    def test_candidate_identity_and_graph_validation(self):
        candidate = candidate_context({"wikidata_id": "Q9", "description": "Description"})
        with self.assertRaisesRegex(ValueError, "duplicate candidate"):
            validate_unique_candidates([candidate, candidate])
        with self.assertRaisesRegex(ValueError, "absent from prepared RDF"):
            validate_candidate_subjects([candidate], parse_rdf(RDF))
        graph = parse_rdf(RDF.replace("ex:a", "<http://www.wikidata.org/entity/Q9>"))
        validate_candidate_subjects([candidate], graph)

    def test_source_matching_rejects_missing_and_duplicate_records(self):
        records = [{"identifier": "Example", "rdf": RDF}]
        for sources in ([], [{"identifier": "other"}],
                        [{"identifier": "Example"}, {"identifier": " example "}]):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                generate_questions(records, sources)
        expected = generate_questions(records)
        records[0]["candidates"] = [{"wikidata_id": "invalid"}]
        self.assertEqual(generate_questions(records), expected)

    def test_target_uses_first_whole_label_mention(self):
        graph = parse_rdf(RDF)
        subject = URIRef("https://example.org/a")
        self.assertEqual(select_target(graph, subject, "Gamma before Beta."), URIRef("https://example.org/c"))
        self.assertEqual(select_target(graph, subject, "Betamax before Gamma, then Beta."), URIRef("https://example.org/c"))

    def test_target_matches_accent_variants_and_unicode_labels(self):
        subject = URIRef("https://example.org/a")
        target = URIRef("https://example.org/c")
        for label, description in (
            ("São Paulo", "Sao Paulo before Beta."),
            ("Sao Paulo", "São Paulo before Beta."),
            ("São Paulo", "Sa\u0303o Paulo before Beta."),
            ("東京", "東京 before Beta."),
        ):
            with self.subTest(label=label, description=description):
                graph = parse_rdf(RDF.replace('"Gamma"', f'"{label}"'))
                self.assertEqual(select_target(graph, subject, description), target)

    def test_candidates_preserve_order_description_and_titles(self):
        rdf = RDF.replace("ex:a", "<http://www.wikidata.org/entity/Q9>").replace("ex:b", "<http://www.wikidata.org/entity/Q2>")
        rdf = rdf.replace('"Beta"', '"Alpha"')
        candidates = [
            {"wikidata_id": "Q9", "description": "Gamma first, then Alpha.", "wikipedia_title": "Alpha (first)"},
            {"wikidata_id": "Q2", "description": "Gamma first.", "wikipedia_title": "Alpha (second)"},
        ]
        records = [{"identifier": "Example", "rdf": rdf}]
        sources = [{"identifier": "Example", "candidates": candidates}]
        source_before = copy.deepcopy(sources)
        before = copy.deepcopy(records)
        questions = generate_questions(records, sources)
        self.assertIn("Alpha (first)", questions[0]["pergunta"])
        self.assertEqual(questions[2]["expected_answers"], ["<https://example.org/c>"])
        self.assertIn("Alpha (second)", questions[3]["pergunta"])
        self.assertEqual(records, before)
        self.assertEqual(sources, source_before)
        candidates[0]["wikidata_id"] = "Q123"
        with self.assertRaisesRegex(ValueError, "absent from prepared RDF"):
            generate_questions(records, sources)

    def test_cli_reads_separate_sources_without_modifying_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "exam_prep.json"
            output = Path(directory) / "exam_questions.json"
            source.write_text(json.dumps([{"identifier": "example", "rdf": RDF}]), encoding="utf-8")
            rdf = RDF.replace("ex:a", "<http://www.wikidata.org/entity/Q9>").replace("ex:b", "<http://www.wikidata.org/entity/Q2>")
            source.write_text(json.dumps([{"identifier": "example", "rdf": rdf}]), encoding="utf-8")
            metadata = Path(directory) / "exam_sources.json"
            metadata.write_text(json.dumps([{"identifier": "example", "candidates": [
                {"wikidata_id": "Q9", "description": "Gamma before Beta."},
                {"wikidata_id": "Q2", "description": "Gamma."},
            ]}]), encoding="utf-8")
            before = source.read_bytes(), metadata.read_bytes()
            with patch("ExamQuestions.DATA_DIR", Path(directory)):
                self.assertEqual(main(), 0)
            self.assertEqual((source.read_bytes(), metadata.read_bytes()), before)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(result), 6)
            self.assertEqual(result[2]["expected_answers"], ["<https://example.org/c>"])
            with self.assertRaises(ValueError):
                generate_exam(source, metadata, metadata)

    def test_queries_return_complete_reference_answers(self):
        graph = parse_rdf(RDF)
        result = generate_questions([{"identifier": "example", "rdf": RDF}])
        self.assertEqual(len(result), 6)
        for question in result:
            actual = sorted({row[0].n3() for row in graph.query(question["sparql"])})
            self.assertEqual(actual, question["expected_answers"])
        self.assertEqual(result[0]["Identifier"], "example_01_01")
        self.assertEqual(result[0]["pergunta"], "Which entities are directly linked to Alpha?")
        self.assertEqual(result[0]["expected_answers"], ['"Beta"@en', '"Gamma"@en'])
        self.assertEqual(result[1]["expected_answers"], ["<https://example.org/p>"])
        self.assertEqual(result[2]["expected_answers"], ["<https://example.org/b>"])
        self.assertEqual(result[3]["expected_answers"], ['"Alpha"@en', '"Gamma"@en'])
        self.assertEqual(set(result[0]), {"Identifier", "pergunta", "sparql", "expected_answers"})

    def test_deterministic_without_mutating_input(self):
        records = [{"identifier": "example", "rdf": RDF}]
        original = copy.deepcopy(records)
        first = generate_questions(records)
        self.assertEqual(first, generate_questions(records))
        self.assertEqual(records, original)
        reordered = parse_rdf(RDF).serialize(format="turtle")
        self.assertEqual(first, generate_questions([{"identifier": "example", "rdf": reordered}]))

    def test_direct_link_uses_both_directions_and_handles_absence(self):
        graph = parse_rdf(RDF)
        subject = URIRef("https://example.org/b")
        for suffix, expected in (("a", ["<https://example.org/a>"]), ("missing", [])):
            query = direct_link_query(subject, URIRef(f"https://example.org/{suffix}"))
            self.assertEqual(expected_answers(graph, query), expected)

    def test_label_matching_preserves_example_semantics(self):
        graph = parse_rdf(RDF + '''
ex:other rdfs:label "ALPHA copy" ; ex:q ex:extra .
ex:extra rdfs:label "Extra" .
''')
        subject = URIRef("https://example.org/a")
        answers = expected_answers(graph, neighborhood_query(subject, "Alpha"))
        self.assertEqual(answers, ['"Beta"@en', '"Extra"', '"Gamma"@en'])

    def test_special_label_characters_are_not_regex_or_sparql_code(self):
        graph = parse_rdf(RDF)
        label = 'A.*[B] "quoted" \\ slash\nnext'
        other = URIRef("https://example.org/other")
        graph.add((other, URIRef("http://www.w3.org/2000/01/rdf-schema#label"), Literal(label)))
        graph.add((other, URIRef("https://example.org/p"), URIRef("https://example.org/c")))
        query = neighborhood_query(URIRef("https://example.org/missing"), label)
        self.assertEqual(expected_answers(graph, query), ['"Gamma"@en'])

    def test_duplicate_labels_are_disambiguated(self):
        result = generate_questions([{"identifier": "x", "rdf": RDF.replace('"Beta"', '"Alpha"')}])
        self.assertIn("Alpha (https://example.org/a)", result[0]["pergunta"])
        self.assertIn("Alpha (https://example.org/b)", result[3]["pergunta"])

    def test_blank_neighbors_and_unlabeled_entities(self):
        rdf = '''@prefix ex: <https://example.org/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:a ex:p [ rdfs:label "Blank neighbor" ] ; ex:p ex:a ; ex:text "literal" .
'''
        result = generate_questions([{"identifier": "x-y", "rdf": rdf}])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["Identifier"], "x_y_01_01")
        self.assertEqual(result[0]["expected_answers"], ['"Blank neighbor"'])
        self.assertIn("https://example.org/a", result[0]["pergunta"])
        self.assertEqual(result[1]["expected_answers"], ["<https://example.org/p>"])

    def test_invalid_inputs(self):
        for records in ([], [{}], [{"identifier": "x", "rdf": ""}],
                        [{"identifier": "x", "rdf": "invalid turtle"}],
                        [{"identifier": "x", "rdf": RDF}, {"identifier": " X ", "rdf": RDF}],
                        [{"identifier": "x-y", "rdf": RDF}, {"identifier": "x_y", "rdf": RDF}]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                generate_questions(records)

    def test_empty_graph_and_blank_subjects(self):
        for rdf in ("# empty graph", "[] <https://example.org/p> 1 ."):
            self.assertEqual(generate_questions([{"identifier": "x", "rdf": rdf}]), [])

    def test_file_output_and_failure_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            output = Path(directory) / "output.json"
            source.write_text(json.dumps([{"identifier": "example", "rdf": RDF}]), encoding="utf-8")
            self.assertEqual(generate_exam(source, output), 6)
            saved = output.read_bytes()
            with self.assertRaises(ValueError):
                generate_exam(source, source)
            source.write_text('[{"identifier": "bad", "rdf": "invalid"}]', encoding="utf-8")
            with self.assertRaises(ValueError):
                generate_exam(source, output)
            self.assertEqual(output.read_bytes(), saved)
            with self.assertRaises(FileNotFoundError):
                generate_exam(Path(directory) / "missing.json", output)


if __name__ == "__main__":
    unittest.main()
