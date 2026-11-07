"""Deterministic Wikipedia text preparation, matching dataset generation."""

import re


def split_phrases(text: str, limit: int) -> list[str]:
    """Split normalized text at punctuation and retain leading phrases."""
    normalized = re.sub(r"\s+", " ", text).strip()
    return [part for part in re.split(r"(?<=[.!?])\s+", normalized) if part][:limit]


def clean_text(text: str) -> str:
    """Remove quoted and parenthesized fragments, then normalize spacing."""
    text = re.sub(r'"[^"]*"', "", text)
    while re.search(r"\([^()]*\)", text):
        text = re.sub(r"\([^()]*\)", "", text)
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"\s+([,.;:!?])", r"\1", text).strip()


def clean_phrases(text: str, limit: int) -> list[str]:
    """Clean leading phrases and discard empty results."""
    return [cleaned for part in split_phrases(text, limit) if (cleaned := clean_text(part))]


def sentence_text(text: str) -> str:
    """Ensure terminal punctuation without changing existing punctuation."""
    text = text.strip()
    return text if not text or text.endswith((".", "!", "?")) else f"{text}."


def phrase_key(phrase: str) -> str:
    """Build a case-insensitive comparison key with terminal punctuation."""
    return sentence_text(phrase).casefold()


def select_unique_phrases(phrases: list[str], excluded: set[str], limit: int) -> list[str]:
    """Select unseen phrases without mutating the exclusions."""
    if limit <= 0:
        return []
    selected: list[str] = []
    seen = set(excluded)
    for phrase in phrases:
        key = phrase_key(phrase)
        if not key or key in seen:
            continue
        selected.append(phrase)
        seen.add(key)
        if len(selected) == limit:
            break
    return selected


def format_identifier(names: list[str]) -> str:
    """Capitalize initial letters and join names with a hyphen."""
    return "-".join(name[:1].upper() + name[1:] for name in names)
