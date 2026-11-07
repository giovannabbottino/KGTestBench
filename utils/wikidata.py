"""Wikidata API communication for KGTestBench."""

from __future__ import annotations

from typing import Any

from utils.api import request_json
from utils.config import SETTINGS, request_options


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


def wikipedia_title(entity: dict[str, Any], language: str) -> str:
    return entity.get("sitelinks", {}).get(f"{language}wiki", {}).get("title", "")


def find_first_entity_id(name: str, language: str) -> str:
    """Return the first match, failing when the search returns no entity."""
    matches = find_entity_ids(
        name, language, 1, **request_options(SETTINGS.wikidata_url),
    )
    if not matches:
        raise RuntimeError(f"no entity found for '{name}'")
    return matches[0]


def find_candidate_ids(names: list[str], language: str) -> list[str]:
    """Search candidates for one ambiguous name or the first match of each term."""
    if len(names) == 1:
        return find_entity_ids(
            names[0], language, SETTINGS.search_candidate_limit,
            **request_options(SETTINGS.wikidata_url),
        )
    ids = [find_first_entity_id(name, language) for name in names]
    if ids[0] == ids[1]:
        raise RuntimeError("the two names resolved to the same Wikidata entity")
    return ids
