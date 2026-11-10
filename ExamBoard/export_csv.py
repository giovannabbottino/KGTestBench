"""Export one CSV per reviewed RDF, with executed SPARQL queries."""
import csv
import json
from pathlib import Path

from ExamQuestions import index_sources, identifier_prefix, questions_for_record
from ExamBoard.adjudicate import source_text

ROOT = Path(__file__).resolve().parents[1]
RDF_FILE = ROOT / "data/adjudication/rdf/exam_rdf.json"
OUTPUT_DIR = RDF_FILE.parent / "csv"
FIELDS = ["identifier", "texto", "rdf", "sparql", "tipo_consulta", "resultado_consulta"]
QUERY_TYPES = {
    "01": "SELECT - entidades ligadas",
    "02": "SELECT - predicados",
    "03": "SELECT - ligacao direta",
}


def export_csv(records, sources, texts, output_dir):
    indexed = index_sources(sources)
    output_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for record in records:
        identifier = record["identifier"]
        source = indexed[identifier_prefix(identifier).casefold()]
        questions = questions_for_record(record, source)
        path = output_dir / f"{identifier}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter=";")
            writer.writeheader()
            for question in questions:
                row = {
                    "identifier": identifier,
                    "texto": texts[identifier],
                    "rdf": record["rdf"],
                    "sparql": question["sparql"],
                    "tipo_consulta": QUERY_TYPES[question["Identifier"].rsplit("_", 1)[1]],
                    "resultado_consulta": json.dumps(question["expected_answers"], ensure_ascii=False),
                }
                writer.writerow({key: value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
                                 for key, value in row.items()})
        total += len(questions)
        print(f"{identifier}: {len(questions)} queries -> {path}")
    return total


def main():
    records = json.loads(RDF_FILE.read_text(encoding="utf-8-sig"))
    sources = json.loads((ROOT / "data/exam_sources.json").read_text(encoding="utf-8-sig"))
    review = json.loads((ROOT / "data/disagreements.json").read_text(encoding="utf-8-sig"))
    texts = {case["identifier"]: source_text(case) for case in review["cases"]}
    total = export_csv(records, sources, texts, OUTPUT_DIR)
    print(f"Exported {len(records)} CSV files with {total} queries.")


if __name__ == "__main__":
    main()
