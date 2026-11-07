"""Wikipedia API communication for KGTestBench."""

from __future__ import annotations


from utils.api import request_json
from utils.config import SETTINGS, request_options
from utils.text import clean_phrases


def fetch_extract(
    title: str,
    language: str,
    *,
    api_url: str,
    timeout: float,
    interval: float,
    retry_statuses: set[int],
    user_agent: str,
) -> str:
    """Fetch the introductory Wikipedia extract for one page."""
    response = request_json(
        api_url.format(language=language),
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
        timeout=timeout,
        interval=interval,
        retry_statuses=retry_statuses,
        user_agent=user_agent,
    )
    pages = response.get("query", {}).get("pages", [])
    extract = pages[0].get("extract", "") if pages else ""
    return extract


def fetch_clean_phrases(title: str, language: str, limit: int) -> list[str]:
    """Fetch and clean leading phrases, without searching alternative pages."""
    if not title:
        return []
    extract = fetch_extract(
        title, language, **request_options(SETTINGS.wikipedia_url),
    )
    return clean_phrases(extract, limit)
