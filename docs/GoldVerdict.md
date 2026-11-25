# GoldVerdict

[GoldVerdict.py](../GoldVerdict.py) generates [gold_verdict.csv](../data/gold_verdict.csv) using original text to select entities and golden RDF to compute answers locally with RDFLib.

## Inputs and execution

- [exam_sources.json](../data/exam_sources.json): original descriptions and candidate entities.
- [exam_rdf.json](../data/adjudication/rdf/exam_rdf.json): golden RDF graphs in Turtle, produced by ExamBoard.

```powershell
python GoldVerdict.py
```

Custom paths:

```powershell
python GoldVerdict.py --input data/adjudication/rdf/exam_rdf.json --sources data/exam_sources.json --output data/gold_verdict.csv
```

## Query generation

Each source text generates three queries against its golden graph:

| ID suffix | Type | Result |
| --- | --- | --- |
| `_01` | `entities` | Labels of linked entities. |
| `_02` | `predicates` | IRIs of linking predicates. |
| `_03` | `id` | IRI of a selected linked entity. |

Subjects follow the original candidate order. IDs are matched to the golden graph, with a unique label match as fallback. For `id`, the target is the golden neighbor mentioned first in the original description, ignoring case and accents. If no label matches, selection falls back to deterministic label/IRI ordering. This query is generated only when a named target exists.

Queries are regenerated using [ExamQuestions.py](../ExamQuestions.py), not loaded from [exam_questions.json](../data/exam_questions.json). All use `SELECT`; answers come exclusively from the golden RDF.

Current dataset: **10 golden graphs, 2 texts per graph, 3 queries per text = 60 queries**, all with results (2026-11-24).

## CSV columns

| Column | Content |
| --- | --- |
| `identifier` | Source, text index and query index, e.g. `Amazon_01_03`. |
| `text` | All candidate descriptions for the source group, joined in stored order with one space between descriptions. |
| `rdf` | Golden Turtle string. |
| `question` | Question in English. |
| `sparql` | Executable query. |
| `sparql_type` | `entities`, `predicates` or `id`. |
| `result` | Non-empty JSON array of unique, sorted RDF terms in N3 notation. |

The seven-column CSV includes the complete group text because its RDF combines the group's candidates. Each description is preserved verbatim, including Unicode, quotes, and newlines; only the separator space is added. The same group text is repeated for all its queries. The identifier still identifies the focal candidate. Text comes from the candidate descriptions in the source JSON, not the review workbooks or a possibly stale top-level combined description.

The CSV uses UTF-8 and standard quoting for multiline fields. Empty answers or invalid inputs stop generation and preserve the existing CSV; empty-answer errors identify the query.

## Tests

```powershell
python -m unittest discover -s tests -p test_gold_verdict.py -v
```
