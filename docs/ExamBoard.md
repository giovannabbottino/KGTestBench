# ExamBoard

Uses Ollama with Qwen2.5-7B-Instruct (`qwen2.5:7b-instruct`) to resolve disagreements between human reviewers **a1** and **a2**, then generates the reviewed RDF.

## Run

Requires Python 3.10+, [requirements-review.txt](../requirements-review.txt), and Ollama running at `http://127.0.0.1:11434` with `qwen2.5:7b-instruct` installed. The [Docker setup](../ExamBoard/docker/compose.yaml) downloads this model automatically.

Run the complete workflow from the repository root:

```powershell
docker compose -f ExamBoard/docker/compose.yaml -f ExamBoard/docker/compose.gpu.yaml up -d
docker compose -f ExamBoard/docker/compose.yaml -f ExamBoard/docker/compose.gpu.yaml run --rm model-init
python -m pip install -r requirements-review.txt
python -m ExamBoard
python -m ExamBoard.generate_rdf
docker compose -f ExamBoard/docker/compose.yaml -f ExamBoard/docker/compose.gpu.yaml down
```

## How it works

1. Reads pending cases from [disagreements.csv](../data/disagreements.csv) and normalizes each CSV row into one adjudication case. The older JSON export remains supported for compatibility.
2. Sends each case's source text and human alternatives to the model.
3. Selects **a1 or a2**, with explanations and literal source quotes. Multiple disagreement types receive one combined decision.
4. Reads the JSON and saves the response and decision directly. A proposed replacement triple requires explicit model approval.
5. Builds the final RDF from shared human triples plus approved alternatives, removing rejected triples. Exclusions take priority.

## Prompts

The prompts are system instructions stored in [ExamBoard/prompts/](../ExamBoard/prompts/). The program selects one prompt according to the disagreement types in each case. The model always receives the source text and the two complete alternatives, **a1** and **a2**.

| Prompt | Purpose |
| --- | --- |
| `unilateral_approvals.txt` | Compare an approval of the original triple with a rejection. |
| `unilateral_unresolved_entities.txt` | Compare entity assessments when only one reviewer marked the entity as unresolved. |
| `shared_rejection_diagnosis_divergence.txt` | Choose the better explanation when both reviewers rejected the original. |
| `competing_rdf_relations.txt` | Compare two complete RDF relation sets, including predicate meaning and direction. |
| `multiple_disagreements.txt` | Resolve several disagreement types for the same case in one decision. |

The [multiple_disagreements.txt](../ExamBoard/prompts/multiple_disagreements.txt) prompt is important because it does not produce one vote per type. It evaluates all types listed in `case_disagreement_types`, explains each one in `reason_by_type`, and returns one final `selected` value: `a1` or `a2`. This prevents the pipeline from combining parts of both alternatives.

Every prompt requires the same JSON response:

- `selected`: exactly `a1` or `a2`;
- `rationale`: explanation of the decision;
- `evidence_quotes`: literal excerpts from the source text;
- `limitations`: remaining uncertainty;
- `reason_by_type`: one explanation for every disagreement type;
- `approve_correction`: whether an explicit replacement triple is approved.

The prompts tell the model to use only the supplied evidence, give neither reviewer priority, avoid external verification, and never invent RDF. For annotation cases, a correction is approved only when the selected reviewer supplied one explicit RDF triple supported by the source. For RDF relation groups, selecting a reviewer copies that reviewer's complete set and `approve_correction` must remain `false`.

## Output

Results are saved in `data/adjudication/`:

| File | Content |
| --- | --- |
| `adjudications.json` | Decisions and source hash; updated after each case. |
| `<identifier>-<disagreement_type>-<number>.json` | Compact decision record for one case. |
| `rdf/exam_rdf.json` | Separate RDF graphs by identifier. |

Example `case_id`: `Amazon-unilateral_approvals-001`. The number prevents collisions; `source_case_id` preserves the original input ID. Cases with multiple types use `multiple_disagreements`.

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
