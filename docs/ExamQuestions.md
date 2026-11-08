# ExamQuestions

Generates questions, SPARQL queries and answers locally using RDFLib. Reads graphs from [exam_prep.json](../data/exam_prep.json), consults candidate metadata in [exam_sources.json](../data/exam_sources.json), and writes [exam_questions.json](../data/exam_questions.json). Input files remain unchanged.

## Usage

Run from `KGTestBench`. Paths are fixed relative to the script; no arguments are needed.

```powershell
python ExamQuestions.py
```

## Output

- `Identifier`: source, candidate number and question type, such as `Amazon_01_01`.
- `pergunta`: question in English.
- `sparql`: a `SELECT DISTINCT` query projecting `?answer`, with `ORDER BY ?answer`.
- `expected_answers`: distinct, sorted answers encoded as N3 RDF terms.

## Questions

1. **Which entities are directly linked to X?** Returns neighboring entity labels.
2. **Which predicates directly link entities to X?** Returns linking predicate IRIs.
3. **Is the entity X directly linked to Y?** Returns Y's IRI when linked, otherwise an empty list. Omitted when no named neighbor exists.

Queries check both directions. The first two match by IRI or case-insensitive label substring, so similarly named entities may contribute answers.

The first question returns labels, so neighbors without labels are omitted and identical labels are deduplicated. The third question uses exact subject and target IRIs and returns an IRI rather than a Boolean. Since generation chooses an existing named neighbor, generated questions of this type have a positive reference answer on the prepared graph.

`ORDER BY ?answer` orders query results without changing the answer set. Reference answers are independently deduplicated and sorted after conversion to N3 strings; their order need not match SPARQL term ordering.

## Generation

Candidates follow the source order. The third question selects the neighbor whose label appears first in the description, using deterministic tie breakers. Repeated names use Wikipedia titles or IRIs for disambiguation. No reference question file is used.

Target selection matches whole labels in normalized text. Normalization ignores case and combining accents, retains Unicode letters and numbers, and replaces punctuation and underscores with spaces. For example, `São Paulo`, `Sao Paulo` and a decomposed Unicode spelling of `São Paulo` match the same mention; labels such as `東京` remain searchable. Original labels are retained in question text and SPARQL. This normalization applies only to target ranking, not to the SPARQL label filters or source-to-RDF identity matching.

The description is normalized once per target selection. Preferred labels are cached within each record's graph, and candidate label frequencies are computed once to detect duplicate names. The cache is not shared between records. When no distinct Wikipedia title is available, duplicate names are displayed with their IRIs without repeating the duplicate-name count.

Validation, question construction and query execution are separate steps. If a source ID is absent from the RDF, a unique main subject with the same label is used, ignoring case and surrounding spaces. Missing or ambiguous matches and duplicate resolved candidates cause errors. This matching does not modify either input file. Answers are read by `?answer`; query errors identify the question. Output is written after all questions succeed.

## Tests

```powershell
python -m unittest discover -s tests -p "test_exam_questions.py" -v
```
