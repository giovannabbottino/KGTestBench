"""Wikipedia API communication for KGTestBench."""

from __future__ import annotations

from typing import Any

from api import request_json


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
