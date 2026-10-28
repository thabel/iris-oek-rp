"""Attribute normalisation.

Rules are deliberately conservative: values are cleaned (Unicode NFC,
whitespace) and converted only where the convention is declared in config.
Nothing is inferred from other fields; a missing source value stays NULL and
is reported as a quality flag.
"""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
_NUMBER_DE_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(\.\d{3})+$")
_NUMBER_EN_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+$")


def clean_text(value: str | None) -> str | None:
    """NFC-normalise, turn every Unicode space into a plain space, collapse
    runs of whitespace and trim. Empty results become None."""
    if value is None:
        return None
    text = unicodedata.normalize("NFC", str(value))
    text = "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in text)
    text = _WS.sub(" ", text).strip()
    return text or None


def name_key(value: str | None) -> str | None:
    """Matching key for place names: cleaned, casefolded (so 'ß' == 'ss').
    The display name itself is never rewritten."""
    cleaned = clean_text(value)
    return cleaned.casefold() if cleaned else None


def parse_decimal(value: str | None, style: str = "de") -> tuple[float | None, str | None]:
    """Parse a number written in German ("1.234,5") or English ("1,234.5")
    convention. Returns (value, issue_code_or_None)."""
    text = clean_text(value)
    if text is None:
        return None, None
    text = text.replace(" ", "").replace(" ", "")
    if style == "de":
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            text = text.replace(",", ".")
        elif _NUMBER_DE_THOUSANDS.match(text):
            text = text.replace(".", "")
    else:
        if "," in text and "." in text:
            text = text.replace(",", "")
        elif _NUMBER_EN_THOUSANDS.match(text):
            text = text.replace(",", "")
    try:
        return float(text), None
    except ValueError:
        return None, "unparseable_number"


def normalize_status(value: str | None, mapping: dict[str, str]) -> tuple[str, str | None]:
    """Map a source status to the controlled vocabulary.

    Returns (status, issue_code). Missing values become 'unknown'; values
    with no configured mapping become 'unmapped' so they are never silently
    folded into a real status.
    """
    cleaned = clean_text(value)
    if cleaned is None:
        return "unknown", "missing_status"
    mapped = mapping.get(cleaned.casefold())
    if mapped is None:
        return "unmapped", "unmapped_status"
    return mapped, None


def normalize_code(value: str | None) -> str | None:
    """Official keys (AGS, Gemarkung numbers) are identifiers, not numbers:
    keep leading zeros, drop internal whitespace only."""
    cleaned = clean_text(value)
    return cleaned.replace(" ", "") if cleaned else None
