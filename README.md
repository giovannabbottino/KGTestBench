# KGTestBench

KGTestBench collects source material for knowledge-graph experiments. Given an
entity name, it searches Wikidata, finds the linked Wikipedia page, and saves
the page's introductory phrases together with the resolved entity metadata.

The result is a small, inspectable JSON source layer that can be used by later
graph-construction or evaluation workflows.

## Requirements

- Python 3.10 or newer
- Internet access to the Wikidata and Wikipedia APIs
- Packages listed in [requirements.txt](./requirements.txt)

## Installation

From the KGTestBench directory, create a virtual environment and install the
dependencies:

```powershell
cd KGTestBench
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, allow scripts for the current session only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Copy the configuration template:

```powershell
Copy-Item .env.example .env
```

Edit `.env` if you need to change the language, API URLs, limits, timeout,
retry behavior, or user agent. The local `.env` file is ignored by Git.

## Usage

From the repository root:

```powershell
python KGTestBench/ExamSource.py Python Mercury Saturn
```

Or, from inside the KGTestBench directory:

```powershell
python ExamSource.py Python Mercury Saturn --output data/exam_sources.json
```

The default output is `data/exam_sources.json` in the KGTestBench directory. To
write a different file inside `data/`:

```powershell
python KGTestBench/ExamSource.py Amazon Seal --output KGTestBench/data/amazon_seal.json
```

The script also supports `--language`, `--candidate-limit`, and
`--phrase-limit` command-line options. Command-line options override the
corresponding defaults from `.env`.

## Entity files

- [data/entities.txt](./data/entities.txt) contains one ambiguous or homonymous
  entity name per line.
- [data/synonyms.txt](./data/synonyms.txt) contains synonym pairs separated by
  commas.

Process every entity in the default list with PowerShell:

```powershell
Get-Content KGTestBench/data/entities.txt |
  Where-Object { $_.Trim() } |
  ForEach-Object {
    python KGTestBench/ExamSource.py $_.Trim()
  }
```

## Documentation

See [docs/ExamSource.md](./docs/ExamSource.md) for the script's behavior,
configuration variables, input format, output schema, and examples.

## Output

Each requested name produces an object containing:

- the requested name and language;
- the Wikidata ID and localized label;
- the linked Wikipedia title;
- cleaned introductory phrases; and
- the combined description.

The source APIs can change over time. Preserve generated JSON files when an
experiment needs to be reproduced.
