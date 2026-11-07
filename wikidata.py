"""Wikidata API communication for KGTestBench."""

from __future__ import annotations

from typing import Any

from api import request_json


WIKIDATA_URL = "https://www.wikidata.org/w/api.php"


def find_entity_ids(
    name: str,
    language: str,
    limit: int,
    *,
    api_url: str,
    timeout: float,
    interval: float,
    retry_statuses: set[int],
    user_agent: str,
) -> list[str]:
    """Search Wikidata and return matching entity IDs."""
    response = request_json(
        api_url,
        {
            "action": "wbsearchentities",
            "search": name,
            "language": language,
            "uselang": language,
            "limit": limit,
            "type": "item",
            "format": "json",
        },
        timeout=timeout,
        interval=interval,
        retry_statuses=retry_statuses,
        user_agent=user_agent,
    )
    return extract_entity_ids(response)


def extract_entity_ids(response: dict[str, Any]) -> list[str]:
    """Extract Wikidata IDs from a wbsearchentities response."""
    return [item["id"] for item in response.get("search", []) if item.get("id")]


def fetch_entity(
    entity_id: str,
    language: str,
    *,
    api_url: str,
    timeout: float,
    interval: float,
    retry_statuses: set[int],
    user_agent: str,
) -> dict[str, Any]:
    """Fetch one Wikidata entity with its label and sitelinks."""
    response = request_json(
        api_url,
        {
            "action": "wbgetentities",
            "ids": entity_id,
            "props": "labels|sitelinks",
            "languages": language,
            "format": "json",
        },
        timeout=timeout,
        interval=interval,
        retry_statuses=retry_statuses,
        user_agent=user_agent,
    )
    return response.get("entities", {}).get(entity_id, {})
