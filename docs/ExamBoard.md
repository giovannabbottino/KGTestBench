# ExamBoard

tentar qween - um juiz mais poderoso com mais parametros
Uses Ollama `llama3.1:8b` to resolve disagreements between human reviewers **a1** and **a2**, then generates the reviewed RDF.

## Run

Requires Python 3.10+, [requirements-review.txt](../requirements-review.txt), and Ollama running at `http://127.0.0.1:11434` with `llama3.1:8b` installed. A [Docker setup](../ExamBoard/docker/compose.yaml) is included.

From the repository root:

```powershell
python -m pip install -r requirements-review.txt
python -m ExamBoard
python -m ExamBoard.generate_rdf
```

## How it works

1. Reads pending cases from [disagreements.json](../data/disagreements.json).
2. Sends each case's source text and human alternatives to the model.
3. Selects **a1 or a2**, with explanations and literal source quotes. Multiple disagreement types receive one combined decision.
4. Reads the JSON and saves the response and decision directly. A proposed replacement triple requires explicit model approval.
5. Builds the final RDF from shared human triples plus approved alternatives, removing rejected triples. Exclusions take priority.

RDF generation requires complete results matching the input JSON and source workbooks. Source files remain unchanged.

The request's `format` uses a JSON Schema: `selected` accepts only `a1` or `a2`, `approve_correction` is boolean, explanations are strings, and quotes are a list of strings. All six response fields are required; extra fields are disallowed.

## Output

Results are saved in `data/adjudication/`:

| File | Content |
| --- | --- |
| `adjudications.json` | Decisions, model name and source hashes; updated after each case. |
| `<identifier>-<disagreement_type>-<number>.json` | Request, raw response and decision for one case. |
| `rdf/exam_rdf.ttl` | Combined RDF graph. |
| `rdf/exam_rdf.json` | Separate RDF graphs by identifier. |
| `rdf/rdf_report.json` | Counts, hashes and excluded triples. |

Example `case_id`: `Amazon-unilateral_approvals-001`. The number prevents collisions; `source_case_id` preserves the original input ID. Cases with multiple types use `multiple_disagreements`.

Running again rebuilds the aggregate and replaces processed case files. The request uses the JSON Schema directly, without response validation, default values or exception handling.

## CSV export

To export one CSV per reviewed RDF:

```powershell
python -m ExamBoard.export_csv
```

Files are saved in `data/adjudication/rdf/csv/`, using UTF-8 with BOM and a semicolon separator. Each query occupies one physical line with `identifier`, `texto`, `rdf`, `sparql`, `tipo_consulta` and `resultado_consulta`; embedded line breaks are replaced with spaces. Text comes from the human-review evidence. Queries reuse ExamQuestions templates and run on the final RDF; results are JSON lists of N3 terms (`[]` for no matches).

## Configuration

- [adjudicate.py](../ExamBoard/adjudicate.py): input and output paths.
- [llm_api.py](../ExamBoard/llm_api.py): server URL, model and generation options.
- [prompts/](../ExamBoard/prompts/): instructions for each disagreement type.
- [generate_rdf.py](../ExamBoard/generate_rdf.py): RDF generation; `LLM_RESULTS` selects another results file.

## Tests

```powershell
python -m unittest discover -s tests -p "test_exam_board*.py" -v
python -m unittest discover -s tests -p "test_generate_rdf.py" -v
```

The live Ollama check is optional: set `EXAMBOARD_LIVE_CHECK=1` to enable it. It uses three synthetic examples, not an accuracy benchmark.
