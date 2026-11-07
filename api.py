"""Shared HTTP communication helpers for external JSON APIs."""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


_last_request_time = 0.0


def wait_for_request(interval: float) -> None:
    """Wait until the next external request is allowed."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < interval:
        time.sleep(interval - elapsed)
    _last_request_time = time.monotonic()


def decode_response(response: Any) -> str:
    """Decode a UTF-8 response, including gzip content."""
    body = response.read()
    if response.headers.get("Content-Encoding") == "gzip":
        body = gzip.decompress(body)
    return body.decode("utf-8")


def request_json(
    url: str,
    params: dict[str, Any],
    *,
    timeout: float,
    interval: float,
    retry_statuses: set[int],
    user_agent: str,
) -> dict[str, Any]:
    """Fetch JSON with throttling, retries, and response decoding."""
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": user_agent,
        },
    )
    last_error: Exception | None = None
    for attempt in range(3):
        wait_for_request(interval)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(decode_response(response))
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in retry_statuses or attempt == 2:
                raise
            time.sleep(2**attempt)
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("API request failed") from last_error
