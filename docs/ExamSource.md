# ExamSource

[ExamSource.py](../ExamSource.py) resolves entities through Wikidata, collects introductory Wikipedia text and saves candidates in [data/exam_sources.json](../data/exam_sources.json).

## Usage

Requires Python 3.10+, the packages in [requirements.txt](../requirements.txt) and internet access. Run from `KGTestBench`:

```powershell
python ExamSource.py
```

The command accepts no arguments. Configuration, input and output paths are resolved relative to the project root.

## Inputs and collection

- [ambiguous_words.txt](../data/ambiguous_words.txt): one name per line, such as `Python`. Searches up to ten candidates and accepts two complete texts by default.
- [synonyms.txt](../data/synonyms.txt): pairs such as `House,Building` or `River-Stream`. Uses only the first result for each term and requires two distinct entity IDs.

Blank lines and comments beginning with `#` are ignored; repeated groups are deduplicated. Identifiers capitalize each name's first letter and join pair terms with `-`.

Each candidate must provide two usable phrases by default. Text preparation normalizes whitespace, removes quoted and parenthesized fragments, and excludes repeated phrases within the group. Insufficient results abort collection before writing.

Selection follows search order without semantic validation. Sentence splitting uses punctuation and may mishandle abbreviations such as `Inc.`; cleaning can also leave incomplete prose.

## Output

The UTF-8 JSON array contains records with `identifier` and `candidates`. Each candidate has:

| Field | Content |
| --- | --- |
| `wikidata_id` | Wikidata item ID |
| `wikidata_label` | Label in the configured language |
| `wikipedia_title` | Linked Wikipedia page |
| `phrases` | Selected, cleaned text segments |
| `description` | Joined phrases |
| `identifier` | Original term, for pair candidates only |

Pair records also contain a top-level `description` combining both candidates. Manually curated records can differ from automatic phrase-count rules; the current Amazon company candidate contains one complete sentence.

## Configuration

[utils/config.py](../utils/config.py) loads the project-root `.env` without overriding process environment variables.

| Variable | Default | Purpose |
| --- | --- | --- |
| `KG_LANGUAGE` | `en` | Label and Wikipedia language |
| `KG_CANDIDATE_LIMIT` | `2` | Accepted candidates for a single name |
| `KG_SEARCH_CANDIDATE_LIMIT` | `10` | Search pool for a single name |
| `KG_PHRASE_LIMIT` | `2` | Required phrases per candidate |
| `KG_REQUEST_TIMEOUT` | `15.0` | HTTP timeout in seconds |
| `KG_REQUEST_INTERVAL` | `1.0` | Minimum request interval in seconds |
| `KG_RETRY_STATUSES` | `429,500,502,503,504` | Retriable HTTP statuses |

Set `KG_USER_AGENT` to identify the client and provide a contact. `KG_WIKIDATA_URL` and `KG_WIKIPEDIA_URL` override the public API endpoints. Candidate and phrase limits must be positive; pairs always require two candidates.

## Persistence

Records with non-empty candidate lists are skipped before API calls, matching identifiers case-insensitively. Existing content is not revalidated, so manual corrections are preserved. Empty records can be replaced.

New records are merged only after the pending batch succeeds. Legacy standalone terms superseded by new pairs and empty comma-separated pair placeholders are removed. With no pending records, the file remains unchanged.

Writes replace the JSON directly and are not atomic. Archive the output before fresh collection; live search results and article wording may change.

Exit codes: `0` for success, `1` for handled collection/file errors, and `2` for non-positive collection limits. Invalid environment values can fail during configuration loading.

## Modules

| Module | Responsibility |
| --- | --- |
| [ExamSource.py](../ExamSource.py) | Input parsing, candidate assembly, merging and orchestration |
| [utils/config.py](../utils/config.py) | Settings and collection-limit validation |
| [utils/api.py](../utils/api.py) | HTTP requests, decoding, throttling and retries |
| [utils/wikidata.py](../utils/wikidata.py) | Entity search, selection and metadata |
| [utils/wikipedia.py](../utils/wikipedia.py) | Introductory text retrieval |
| [utils/text.py](../utils/text.py) | Pure text transformations |
| [utils/file.py](../utils/file.py) | UTF-8 JSON and text-file operations |

## Tests

Run the offline suite with mocked network calls and temporary files:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

