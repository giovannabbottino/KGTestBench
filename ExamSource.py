#!/usr/bin/env python3
"""Collect source texts for KG test-bench experiments.

For each input name this script:

1. searches Wikidata for candidate entities;
2. resolves each candidate to its English Wikipedia page; and
3. retrieves the introductory sentences from that page.

The output is JSON so it can be used as source material for later
knowledge-graph construction experiments.

Example::

    python KGTestBench/ExamSource.py bass mouse
    python KGTestBench/ExamSource.py jaguar --output KGTestBench/sources.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ENV_FILE = Path(__file__).resolve().parent / ".env"


def load_env(path: Path) -> None:
    """Load simple KEY=VALUE settings without overwriting shell variables."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_int(name: str, default: int) -> int:
    """Read an integer environment setting."""
    return int(os.getenv(name, str(default)))


def env_float(name: str, default: float) -> float:
    """Read a floating-point environment setting."""
    return float(os.getenv(name, str(default)))


load_env(ENV_FILE)

LANGUAGE = os.getenv("KG_LANGUAGE", "en")
DEFAULT_CANDIDATE_LIMIT = env_int("KG_CANDIDATE_LIMIT", 10)
DEFAULT_PHRASE_LIMIT = env_int("KG_PHRASE_LIMIT", 2)
WIKIDATA_URL = os.getenv("KG_WIKIDATA_URL", "https://www.wikidata.org/w/api.php")
WIKIPEDIA_URL = os.getenv(
    "KG_WIKIPEDIA_URL", "https://{language}.wikipedia.org/w/api.php"
)
REQUEST_TIMEOUT = env_float("KG_REQUEST_TIMEOUT", 15.0)
REQUEST_INTERVAL = env_float("KG_REQUEST_INTERVAL", 1.0)
RETRY_STATUSES = {
    int(status)
    for status in os.getenv("KG_RETRY_STATUSES", "429,500,502,503,504").split(",")
    if status.strip()
}
USER_AGENT = os.getenv(
    "KG_USER_AGENT",
    "KGTestBench/0.1 (source collection; contact: you@example.org)",
)
_last_request_time = 0.0


def wait_for_request() -> None:
    """Keep external requests at least one second apart."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < REQUEST_INTERVAL:
        time.sleep(REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()


def decode_response(response: Any) -> str:
    """Decode a UTF-8 API response, including gzip content."""
    body = response.read()
    if response.headers.get("Content-Encoding") == "gzip":
        body = gzip.decompress(body)
    return body.decode("utf-8")


def request_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """Fetch JSON from an API with throttling and retries."""
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT,
        },
    )
    last_error: Exception | None = None
    for attempt in range(3):
        wait_for_request()
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(decode_response(response))
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in RETRY_STATUSES or attempt == 2:
                raise
            time.sleep(2**attempt)
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("API request failed") from last_error


def find_entity_ids(name: str, language: str, limit: int) -> list[str]:
    """Search Wikidata and return matching entity IDs."""
    response = request_json(
        WIKIDATA_URL,
        {
            "action": "query",
            "list": "search",
            "srsearch": name,
            "srnamespace": 0,
            "srlimit": limit,
            "uselang": language,
            "format": "json",
        },
    )
    return [item["title"] for item in response.get("query", {}).get("search", [])]


def fetch_entity(entity_id: str, language: str) -> dict[str, Any]:
    """Fetch one Wikidata entity with its label and sitelinks."""
    response = request_json(
        WIKIDATA_URL,
        {
            "action": "wbgetentities",
            "ids": entity_id,
            "props": "labels|sitelinks",
            "languages": language,
            "format": "json",
        },
    )
    return response.get("entities", {}).get(entity_id, {})


def split_phrases(text: str, limit: int) -> list[str]:
    """Split introductory text into at most ``limit`` sentences."""
    phrases = re.split(r"(?<=[.!?])\s+", text.strip())
    return [phrase for phrase in phrases if phrase][:limit]


def fetch_phrases(title: str, language: str, limit: int) -> list[str]:
    """Fetch introductory Wikipedia sentences for one page."""
    response = request_json(
        WIKIPEDIA_URL.format(language=language),
        {
            "action": "query",
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "redirects": 1,
            "titles": title,
            "format": "json",
            "formatversion": 2,
        },
    )
    pages = response.get("query", {}).get("pages", [])
    extract = pages[0].get("extract", "") if pages else ""
    return split_phrases(extract, limit)


def clean_text(text: str) -> str:
    """Remove quoted/parenthesized fragments and normalize whitespace."""
    text = re.sub(r'"[^"]*"', "", text)
    while re.search(r"\([^()]*\)", text):
        text = re.sub(r"\([^()]*\)", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip()


def wikipedia_title(entity: dict[str, Any], language: str) -> str:
    """Return the Wikipedia title linked from a Wikidata entity."""
    sitelinks = entity.get("sitelinks", {})
    return sitelinks.get(f"{language}wiki", {}).get("title", "")


def collect_source(
    requested_name: str,
    *,
    language: str = LANGUAGE,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    phrase_limit: int = DEFAULT_PHRASE_LIMIT,
) -> dict[str, Any]:
    """Resolve one name and collect its available introductory phrases."""
    candidates: list[dict[str, Any]] = []
    for entity_id in find_entity_ids(requested_name, language, candidate_limit):
        entity = fetch_entity(entity_id, language)
        title = wikipedia_title(entity, language)
        if not title:
            continue

        phrases = [
            cleaned
            for phrase in fetch_phrases(title, language, phrase_limit)
            if (cleaned := clean_text(phrase))
        ]
        if not phrases:
            continue

        candidates.append(
            {
                "wikidata_id": entity_id,
                "wikidata_label": entity.get("labels", {})
                .get(language, {})
                .get("value", ""),
                "wikipedia_title": title,
                "phrases": phrases,
                "description": " ".join(phrases),
            }
        )

    return {
        "requested_name": requested_name,
        "language": language,
        "candidates": candidates,
    }


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Resolve Wikidata entities and collect Wikipedia source phrases."
    )
    parser.add_argument(
        "names",
        nargs="+",
        help="Entity names to resolve, for example bass mouse",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "exam_sources.json",
        help="JSON output path (default: KGTestBench/exam_sources.json)",
    )
    parser.add_argument("--language", default=LANGUAGE)
    parser.add_argument("--candidate-limit", type=int, default=DEFAULT_CANDIDATE_LIMIT)
    parser.add_argument("--phrase-limit", type=int, default=DEFAULT_PHRASE_LIMIT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run source collection and write the JSON result."""
    args = parse_arguments(argv)
    if args.candidate_limit < 1 or args.phrase_limit < 1:
        print("candidate-limit and phrase-limit must be positive", file=sys.stderr)
        return 2

    sources: list[dict[str, Any]] = []
    try:
        for name in args.names:
            print(f"Resolving {name!r}...", file=sys.stderr)
            sources.append(
                collect_source(
                    name.strip(),
                    language=args.language,
                    candidate_limit=args.candidate_limit,
                    phrase_limit=args.phrase_limit,
                )
            )
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Error collecting sources: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(sources, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(sources)} source(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
