# ExamReview

ExamReview compares a1/a2 annotations and their final RDF graphs, then isolates cases for human adjudication. [ExamReview.ipynb](../ExamReview/ExamReview.ipynb) follows the data from input validation to agreement, error diagnosis, graph comparison, and adjudication. Reading and analysis preserve source workbooks and saved decisions.

## Organization

| File | Purpose |
| --- | --- |
| [ExamReview.ipynb](../ExamReview/ExamReview.ipynb) | Eight questions: input audit, agreement, error diagnosis, final graphs, and unique adjudication cases, with tables and charts. |
| [review_helpers.py](../ExamReview/review_helpers.py) | Shared validation, matching, agreement calculations, whole-triple judgments, and error profiles. |
| [rdf_comparison.py](../ExamReview/rdf_comparison.py) | Turtle parsing, graph overlap, projections, and candidate structural differences. |
| [export_disagreements.py](../ExamReview/export_disagreements.py) | Selects and exports unique cases; validates and summarizes saved JSON for the notebook. |
| [disagreements.json](../data/disagreements.json) | Review cases, source evidence, subset references, and adjudication fields. |

The exporter reads the workbooks directly. The notebook reads the workbooks and the exported JSON, verifying that its source hashes match the current files. The flow is: **workbooks → validation and matching → analysis / case selection → JSON → human adjudication**. Editing the JSON does not automatically change annotations or graphs.

## Inputs and scope

- [a1-rdf.xlsx](../data/inter-rater-reliability/a1-rdf.xlsx) and [a2-rdf.xlsx](../data/inter-rater-reliability/a2-rdf.xlsx), worksheet `resultado_template_processado`.
- Item key: `Identifier` + `tripla_original`. Identifiers are forward-filled; ratings are never forward-filled. Matching is independent of row order and trims only surrounding key whitespace.
- Required rating columns: `label_corresponde_wikidata`, `entidade_correta`, `predicado_correto`, `direcao_relacao_correta`, and `fidelidade_semantica`.
- Accepted ratings: booleans, numeric 0/1, and strings `true/false`, `sim/não/nao`, or `0/1`, ignoring case. Missing values remain missing. Invalid values, duplicate keys, and conflicting source text stop the analysis.
- Final graphs: one `rdf completo` per Identifier in a1 and one `rdf corrigido` per Identifier in a2. Invalid Turtle, missing groups, duplicate identifiers, and unsupported blank nodes cause errors.

The current a1 input includes a user-requested correction: when G is True, J and L were set to True in 296 rows. The 407 trailing a2 rows without triples were removed. Results describe these corrected inputs, not a new independent annotation round. Source hashes are stored in audits and the JSON.

## Setup and execution

Use Python 3.10+ and the dependencies in [requirements-review.txt](../requirements-review.txt), which include Jupyter Notebook. From the repository root, with the review environment selected:

```powershell
python -m pip install -r requirements-review.txt
python -m notebook ExamReview/ExamReview.ipynb
```

## Exploratory questions

1. How much do raters agree on each criterion, and what is the coverage?
2. Is agreement greater on correct or incorrect relationships?
3. Do raters agree on which whole triples contain errors? Which error criteria and combinations do they share, and where do diagnoses differ?
4. Is agreement concentrated in a particular Identifier?
5. Does an incorrect predicate also have an incorrect direction?
6. Do raters agree when an entity correction is explicitly unresolved?
7. How similar are the final RDF graphs, and do differences involve predicates, entities, or direction?
8. Which unique cases need adjudication, what types are they, and where do types overlap?


## Metrics

For complete pairs on one criterion, **TT** means both True, **FF** both False, and **D** the number of disagreements. “Correct” refers to a rater's judgment, not verified ground truth.

| Metric | Definition |
| --- | --- |
| Coverage | Complete pairs / matched items. |
| Observed agreement | `(TT + FF) / (TT + FF + D)`. Joint rejections count as agreement. |
| Cohen's kappa | `(observed − expected) / (1 − expected)`, where expected agreement comes from the two raters' marginal category proportions. |
| Positive / negative agreement | `2TT / (2TT + D)` / `2FF / (2FF + D)`. |
| Joint approval / rejection among any approval / rejection | `TT / (TT + D)` / `FF / (FF + D)`. These differ from category-specific agreement. |
| RDF Jaccard | Shared triples / union of triples. |
| RDF Dice | `2 × shared / (size a1 + size a2)`. |

A whole relationship triple is approved only when entity, predicate, direction, and semantic fidelity are all True. All eight ratings must be observed. Labels are excluded from this composite. Shared-error counts overlap; exact shared-error combinations do not. Graph similarity uses RDFLib-parsed terms and reports labels separately. Projections ignore predicates or direction intentionally and cannot be interpreted as edit counts. Graph counts remain scoped by Identifier.

## Results snapshot

Snapshot from the corrected inputs on **2026-11-08**; rerun the notebook after any input change.

There are **592 matched items** across ten Identifiers: **297 labels** and **295 relationships**. Semantic fidelity has 295 missing a1 ratings; other criteria are complete.

| Criterion | Complete pairs | Agreement | Kappa |
| --- | ---: | ---: | ---: |
| Label matches Wikidata | 592 | 99.83% | 0.9966 |
| Entity | 592 | 60.64% | 0.2971 |
| Predicate | 592 | 79.22% | 0.5192 |
| Direction | 592 | 99.32% | 0.0000 |
| Semantic fidelity | 297 | 63.30% | 0.3001 |

- **Whole triples:** 56 jointly approved, 145 jointly rejected, 83 approved only by a1, and 11 only by a2. Agreement is **68.14%**, kappa **0.3420**. a2 marks every direction correct, explaining zero direction kappa despite high percentage agreement.
- **Shared errors:** among the 145 jointly rejected triples, both reject fidelity in 127, predicate in 113, and entity in 102. In 75 cases all three are shared. Full diagnoses match in 82, partly overlap in 60, and have no shared reason in 3. The latter three belong to Forest-Woodland.
- **Identifiers:** whole-triple agreement ranges from **53.85% in Mercury** to **85.71% in Amazon**. High agreement includes shared errors and does not mean high quality.
- **Unresolved corrections:** a1 explicitly marks 71 cases; both reject the original entity in 58 (**81.69%**). a2 has no equivalent explicit marker. This does not establish that both failed to find a replacement.
- **Final RDFs:** 343/372 total triples, 271 shared (**61.04% Jaccard**). Relations alone: 171/184, 114 shared (**47.30% Jaccard**, **64.23% Dice**). Entity-IRI similarity is 80.90%; directed endpoint-pair similarity is 77.32%. Of 57 exclusive a1 relations, 37 have an a2 counterpart differing only in predicate. No reversed-endpoint candidate was found. Candidate categories can overlap and do not establish an edit history or factual correctness.

## JSON adjudication export

```powershell
python ExamReview/export_disagreements.py
```

The default output is [disagreements.json](../data/disagreements.json), UTF-8, with booleans and `null` for missing values. The current snapshot has **247 unique cases** across four subsets:

| Subset | Count | Rule |
| --- | ---: | --- |
| `unilateral_approvals` | 94 | Complete relationship ratings: one approves all four substantive criteria, the other rejects at least one. |
| `shared_rejection_diagnosis_divergence` | 63 | Both reject, but their error sets differ: 60 partially shared and 3 disjoint diagnoses. |
| `competing_rdf_relations` | 43 | Both graphs connect the same unordered endpoint pair, with different predicate/direction triple sets. All alternatives belong to one case. |
| `unilateral_unresolved_entities` | 71 | Exactly one rater explicitly marks an unresolved entity correction. Absence of a marker on the other side means unknown. |

Each case appears once in `cases`; `subsets` references its stable `case_id`. The **271 memberships** include **24 cases belonging to multiple subsets**, so subset counts must not be added as unique cases. Section 8 charts types, exclusive membership combinations, Identifier counts, and saved review statuses. These are workload counts, not error rates.

The JSON retains source hashes and coverage metadata. Annotation cases contain both ratings, error sets, source text, comments, and Excel rows. RDF cases contain both sides' alternative triples as N3 terms. Unpaired graph relations, label-only differences, and incomplete relationships are reported in coverage, not forced into a subset.

Record review work only in each case's `adjudication` object:

| Field | Initial value / use |
| --- | --- |
| `status` | `pending`; change to reflect review progress. |
| `decision` | `null`; for example `accept_a1`, `accept_a2`, `new_correction`, `not_applicable`, or `defer`. |
| `approved_ratings` / `approved_triples` | `null` / `[]`; approved criterion values or RDF alternatives. |
| `reviewer` / `notes` | `null`; responsible person and rationale. |

## Tests

From the repository root in the review environment:

```powershell
python -m unittest discover -s tests -p "test_exam_review.py" -v
python -m unittest discover -s tests -p "test_export_disagreements.py" -v
```
