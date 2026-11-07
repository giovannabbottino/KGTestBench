# ExamSource

`ExamSource.py` collects source material for knowledge-graph test-bench
experiments. It produces a compact JSON document containing candidate entities
and their introductory text.

## What the script does

For each requested name, the script:

1. searches Wikidata for candidate entity IDs;
2. fetches each candidate entity from Wikidata;
3. finds the entity's English Wikipedia sitelink;
4. retrieves introductory Wikipedia phrases;
5. removes quoted and parenthesized text and normalizes whitespace; and
6. writes the candidates and cleaned descriptions to JSON.

The API requests use a descriptive user agent, request throttling, retries, and
response decoding so that temporary service failures can be handled without
changing the source-collection workflow.

## Usage

Run from the repository root:

```powershell
python KGTestBench/ExamSource.py bass mouse jaguar
```

If your current directory is `KGTestBench`, omit the `KGTestBench/` prefix:

```powershell
python ExamSource.py bass mouse jaguar
```

The default output path is `KGTestBench/data/exam_sources.json`.

Before running the script, copy the environment template:

```powershell
Copy-Item KGTestBench/.env.example KGTestBench/.env
```

The script reads `.env` automatically. Shell variables take precedence over
values loaded from that file. The available settings are `KG_LANGUAGE`,
`KG_WIKIDATA_URL`, `KG_WIKIPEDIA_URL`, `KG_CANDIDATE_LIMIT`,
`KG_PHRASE_LIMIT`, `KG_REQUEST_TIMEOUT`, `KG_REQUEST_INTERVAL`,
`KG_RETRY_STATUSES`, and `KG_USER_AGENT`.

## Command-line options

| Option | Default | Description |
| --- | --- | --- |
| positional `names` | required | One or more names to resolve. |
| `--output` | `KGTestBench/data/exam_sources.json` | JSON output path. |
| `--language` | `en` | Wikidata and Wikipedia language code. |
| `--candidate-limit` | `10` | Maximum Wikidata candidates per name. |
| `--phrase-limit` | `2` | Maximum introductory phrases per candidate. |

Example with a different language and limits:

```powershell
python KGTestBench/ExamSource.py "Rio de Janeiro" --language en --candidate-limit 5 --phrase-limit 3 --output KGTestBench/data/rio_sources.json
```

## Input files

The repository includes two example data files:

- `KGTestBench/data/entities.txt` contains one entity name per line.
- `KGTestBench/data/synonyms.txt` contains comma-separated pairs such as
  `car,automobile` for experiments that compare related names.

`ExamSource.py` accepts names directly. The input files are intentionally
kept separate so an experiment can choose its own iteration strategy.

Example PowerShell loop:

```powershell
Get-Content KGTestBench/data/entities.txt |
  Where-Object { $_.Trim() } |
  ForEach-Object {
    python KGTestBench/ExamSource.py $_.Trim()
  }
```

## Output schema

The output is a JSON array. Each requested name produces an object with this
shape:

```json
{
  "requested_name": "bass",
  "language": "en",
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

The actual Wikidata IDs and text depend on the live API responses. A name can
have multiple candidates, and candidates without a Wikipedia sitelink or
without introductory text are omitted.

## Scope

`ExamSource.py` is focused on collecting and documenting source descriptions.
It does not infer predicates, create Turtle RDF, or generate SPARQL questions.
The JSON output is an inspectable source layer for later knowledge-graph
experiments.
