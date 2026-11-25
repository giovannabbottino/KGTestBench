import csv
import json
import tempfile
import unittest
from pathlib import Path

from rdflib import Graph

from GoldVerdict import FIELDS, generate_gold, generate_verdicts, main


RDF = '''@prefix ex: <https://example.org/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:a rdfs:label "São Paulo, city"@en ; ex:link ex:b .
ex:b rdfs:label "Neighbor"@en .
'''
RDF = RDF.replace("ex:a", "<http://www.wikidata.org/entity/Q9>")


def sources_for(*identifiers, description="Neighbor appears in the original text."):
    return [{"identifier": identifier, "candidates": [
        {"wikidata_id": "Q9", "description": description}
    ]} for identifier in identifiers]


class GoldVerdictTests(unittest.TestCase):
    def test_csv_roundtrip_and_executable_results(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "final.json"
            output = Path(directory) / "gold.csv"
            source.write_text(json.dumps([{"identifier": "Example", "rdf": RDF}]), encoding="utf-8")
            before = source.read_bytes()
            metadata = Path(directory) / "sources.json"
            description = 'Original text, with "quotes".\nS\u00e3o Paulo and Neighbor.'
            metadata.write_text(json.dumps(sources_for("Example", description=description)), encoding="utf-8")
            metadata_before = metadata.read_bytes()
            self.assertEqual(main(["--input", str(source), "--output", str(output),
                                   "--sources", str(metadata)]), 0)
            with output.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, list(FIELDS))
                self.assertEqual(reader.fieldnames, ["identifier", "text", "rdf",
                                                    "question", "sparql", "sparql_type", "result"])
                rows = list(reader)
            self.assertEqual(len(rows), 3)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(metadata.read_bytes(), metadata_before)
            self.assertEqual(rows[0]["identifier"], "Example_01_01")
            self.assertEqual([row["question"] for row in rows], [
                "Which entities are directly linked to São Paulo, city?",
                "Which predicates directly link entities to São Paulo, city?",
                "Is the entity São Paulo, city directly linked to Neighbor?",
            ])
            self.assertEqual([row["sparql_type"] for row in rows],
                             ["entities", "predicates", "id"])
            for row in rows:
                self.assertEqual(row["text"], description)
                self.assertEqual(row["rdf"], RDF)
                result = Graph().parse(data=row["rdf"], format="turtle").query(row["sparql"])
                self.assertEqual(result.type, "SELECT")
                self.assertEqual(json.loads(row["result"]), sorted({answer[0].n3() for answer in result}))
            self.assertEqual(json.loads(rows[0]["result"]), ['"Neighbor"@en'])
            self.assertEqual(json.loads(rows[1]["result"]), ["<https://example.org/link>"])
            self.assertEqual(json.loads(rows[2]["result"]), ["<https://example.org/b>"])

    def test_original_text_covers_both_candidates_in_source_order(self):
        rdf = RDF.replace("ex:b", "<http://www.wikidata.org/entity/Q2>")
        sources = sources_for("Example_Group", description='First source, "quoted".\n')
        sources[0]["candidates"].append({"wikidata_id": "Q2", "description": "Second source."})
        sources[0]["description"] = "Stale combined description must not replace candidate text."
        before = json.dumps(sources)
        rows = generate_verdicts([{"identifier": "Example_Group", "rdf": rdf}], sources)
        self.assertEqual(len(rows), 6)
        self.assertEqual({row["text"] for row in rows}, {'First source, "quoted".\n Second source.'})
        self.assertEqual(rows[3]["identifier"], "Example_Group_02_01")
        self.assertEqual(json.dumps(sources), before)

    def test_graphs_are_isolated_and_input_order_is_irrelevant(self):
        records = [{"identifier": "B", "rdf": RDF.replace('"Neighbor"', '"Other"')},
                   {"identifier": "A", "rdf": RDF}]
        rows = generate_verdicts(records, sources_for("A", "B"))
        self.assertEqual(rows, generate_verdicts(list(reversed(records)), sources_for("A", "B")))
        self.assertEqual(json.loads(rows[0]["result"]), ['"Neighbor"@en'])
        self.assertEqual(json.loads(rows[3]["result"]), ['"Other"@en'])

    def test_empty_answers_are_rejected_with_question_identifier(self):
        with self.assertRaisesRegex(ValueError, "A_01_01: query returned no answers"):
            generate_verdicts([{"identifier": "A", "rdf": RDF.replace('ex:b rdfs:label "Neighbor"@en .', '')}], sources_for("A"))

    def test_original_text_selects_target_but_answers_use_golden_graph(self):
        rdf = RDF + '''
<http://www.wikidata.org/entity/Q9> ex:link ex:c .
ex:c rdfs:label "Zebra"@en .
ex:extra ex:link ex:b .
'''
        records = [{"identifier": "A", "rdf": rdf}]
        rows = generate_verdicts(records, sources_for("A", description="Zebra before Neighbor."))
        self.assertEqual(len(rows), 3)  # Extra golden subject is not a source candidate.
        self.assertEqual(json.loads(rows[2]["result"]), ["<https://example.org/c>"])
        changed = generate_verdicts(records, sources_for("A", description="Neighbor before Zebra."))
        self.assertEqual(json.loads(changed[2]["result"]), ["<https://example.org/b>"])
        self.assertTrue(all(row["rdf"] == rdf for row in rows))
        with self.assertRaisesRegex(ValueError, "missing source record"):
            generate_verdicts(records, [])

    def test_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "final.json"
            output = Path(directory) / "gold.csv"
            output.write_text("previous", encoding="utf-8")
            metadata = Path(directory) / "sources.json"
            metadata.write_text(json.dumps(sources_for("A")), encoding="utf-8")
            for records in ([], [{}], [{"identifier": "A", "rdf": "invalid"}],
                            [{"identifier": "A", "rdf": RDF.replace('ex:b rdfs:label "Neighbor"@en .', '')}],
                            [{"identifier": "A", "rdf": RDF}] * 2):
                source.write_text(json.dumps(records), encoding="utf-8")
                with self.assertRaises(ValueError):
                    generate_gold(source, output, metadata)
                self.assertEqual(output.read_text(encoding="utf-8"), "previous")
            with self.assertRaises(ValueError):
                generate_gold(source, source)
            with self.assertRaises(FileNotFoundError):
                generate_gold(Path(directory) / "missing.json", output)


if __name__ == "__main__":
    unittest.main()
