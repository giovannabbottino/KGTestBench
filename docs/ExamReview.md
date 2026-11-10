# ExamReview

Compare a1 and a2 ratings, inspect graph overlap, and export disagreements for review.

| Notebook | Purpose |
| --- | --- |
| [Agreements.ipynb](../ExamReview/Agreements.ipynb) | Joint approvals/rejections, agreement metrics, and Jaccard overall and by Identifier. |
| [Disagreements.ipynb](../ExamReview/Disagreements.ipynb) | Different judgments, proposed corrections, graph differences, and the review CSV. |

## Run

From the repository root, with `.venv` activated:

```powershell
python -m pip install -r requirements-review.txt
python ExamReview/export_disagreements.py
python -m notebook ExamReview/Agreements.ipynb
python -m notebook ExamReview/Disagreements.ipynb
```

Run each notebook from top to bottom. They run independently and do not modify the inputs.

## Inputs and metrics

Sources: [a1-rdf.xlsx](../data/inter-rater-reliability/a1-rdf.xlsx) and [a2-rdf.xlsx](../data/inter-rater-reliability/a2-rdf.xlsx), worksheet `resultado_template_processado`. Both have 16 columns; items are matched by `Identifier` + `tripla_original`.

Entity, predicate, and direction ratings are in H, J, and L; their correction suggestions are in I, K, and M. N holds semantic fidelity, O comments, and P the complete `rdf_final` graph. **Suggestions are proposals, not verified answers.** Missing source ratings remain missing.

| Metric | Meaning |
| --- | --- |
| Observed agreement | Share of complete pairs where both approve or both reject. |
| Cohen kappa / Scott pi | Chance-adjusted agreement using individual / pooled category frequencies. |
| Coverage | Share of items with both ratings present. |
| Jaccard | Shared triples / distinct triples in either graph. |
| Dice | Twice the shared count / sum of graph sizes. |

Agreement and graph overlap do not establish factual correctness. The notebooks explain the formulas and show the underlying counts.

## Export disagreements

```powershell
python ExamReview/export_disagreements.py 
```

## Tests

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```
