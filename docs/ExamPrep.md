# ExamPrep

[ExamPrep.py](../ExamPrep.py) reads [exam_sources.json](../data/exam_sources.json) and writes text-derived RDF to `data/exam_prep.json`.

## Usage

Install [requirements.txt](../requirements.txt), then run from `KGTestBench`:

```powershell
python ExamPrep.py
```

Preparation uses English text and requires Wikidata access to resolve extracted terms and fetch labels. NLTK downloads its English tagger and WordNet into `.nltk_data` on first use if they are missing.

## Processing

1. Validate identifiers and candidate descriptions before making requests.
2. Extract capitalized names, individual words and adjacent word pairs, excluding stop words.
3. Resolve each phrase to the first Wikidata result, excluding the source entity and tokens tagged as verbs.
4. Use the nearest preceding verb and optional preposition to construct a camelCase predicate, such as `beIn`.
5. Deduplicate relations and remove longer object labels containing a shorter label in the same subject/predicate group.
6. Serialize each candidate as Turtle and combine its graph with the other candidates in the source record.

Relations always use the candidate's original entity ID as subject. No Wikidata `P31` classes or external factual triples are added; only text-derived relations and labels are serialized. Search order and nearby verbs are heuristics and do not guarantee correct entity senses or grammatical relations.

## Output

Each record contains only `identifier` and `rdf`, the combined Turtle graph. The identifier links the graph to its record in `exam_sources.json`. Descriptions, phrases, candidate metadata, individual graphs and separate `triples` lists are not duplicated in the output.

The source JSON is unchanged. Preparation uses the stored descriptions verbatim, including manually curated single-sentence texts, and does not fetch Wikipedia again.

A successful run replaces the preparation output after processing the whole batch. Collection errors leave existing output untouched; the final file write is not atomic. Exit code `0` means success and `1` means a handled error.

## Modules and tests

- [utils/relations.py](../utils/relations.py): phrase extraction and verb-based relations.
- [utils/rdf.py](../utils/rdf.py): pure triple cleanup, Turtle serialization and graph combination.
- [utils/wikidata.py](../utils/wikidata.py): phrase resolution and batched labels.
- [utils/file.py](../utils/file.py): JSON reading and writing.

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

Tests use mocked Wikidata/NLP boundaries and parse generated Turtle with RDFLib to verify graph structure.
