# ExamSource

`ExamSource.py` collects source material for knowledge-graph test-bench experiments. It produces a compact JSON document containing candidate entities and their introductory text.

## What the script does

For each input item, the script:

1. searches Wikidata;
2. selects entities with a Wikipedia page;
3. retrieves up to two introductory phrases; and
4. writes the result to `data/exam_sources.json`.

Shared HTTP communication is implemented in `api.py`; Wikipedia-specific
requests are implemented in `wikipedia.py`.

## Usage

Run from inside the `KGTestBench` directory:

```powershell
Copy-Item .env.example .env
python ExamSource.py
```

## Input files

The repository includes two example data files:

- `data/ambiguous_words.txt` contains one ambiguous or homonymous entity name
  per line.
- `data/synonyms.txt` contains synonym pairs separated by commas or hyphens,
  such as `Forest,Woodland` or `Forest-Woodland`.

## Output schema

The output is a JSON array. An individual name produces an object with this shape:

```json
{
  "identifier": "bass",
  "candidates": [
    {
      "wikidata_id": "Q...",
      "wikidata_label": "...",
      "wikipedia_title": "...",
      "phrases": ["...", "..."],
      "description": "..."
    }
  ]
}
```

Candidates without a Wikipedia page or introductory text are omitted.
