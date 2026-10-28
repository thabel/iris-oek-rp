import pytest

from oek_harvester.normalize import (
    clean_text,
    name_key,
    normalize_code,
    normalize_status,
    parse_decimal,
)


def test_clean_text_nfc_and_whitespace():
    assert clean_text("  Bad  Kreuznach ") == "Bad Kreuznach"
    assert clean_text("Kürenz") == "Kürenz"  # decomposed -> composed
    assert clean_text("   ") is None
    assert clean_text(None) is None


def test_name_key_casefolds_without_rewriting_display():
    assert name_key("Groß-Umstadt") == name_key("GROSS-UMSTADT")
    assert clean_text("BAD KREUZNACH") == "BAD KREUZNACH"


@pytest.mark.parametrize("raw,style,expected", [
    ("1.234,5", "de", 1234.5),
    ("120.000", "de", 120000.0),
    ("45.000,5", "de", 45000.5),
    ("0,5", "de", 0.5),
    ("12.5", "de", 12.5),          # single dot not in thousands pattern -> decimal
    ("1 234,5", "de", 1234.5),
    ("1,234.5", "en", 1234.5),
    ("1,234", "en", 1234.0),
    ("-3,25", "de", -3.25),
])
def test_parse_decimal(raw, style, expected):
    value, issue = parse_decimal(raw, style)
    assert issue is None
    assert value == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["unbekannt", "1 ha", "n/a"])
def test_parse_decimal_rejects_text_instead_of_guessing(raw):
    assert parse_decimal(raw) == (None, "unparseable_number")


def test_parse_decimal_missing_is_not_an_issue():
    assert parse_decimal(None) == (None, None)
    assert parse_decimal("  ") == (None, None)


def test_status_mapping():
    mapping = {"eingerichtet": "active", "teilweise abgebucht": "partially_debited"}
    assert normalize_status(" Teilweise  abgebucht", mapping) == ("partially_debited", None)
    assert normalize_status("EINGERICHTET", mapping) == ("active", None)
    assert normalize_status("in Prüfung", mapping) == ("unmapped", "unmapped_status")
    assert normalize_status(None, mapping) == ("unknown", "missing_status")


def test_codes_keep_leading_zeros():
    assert normalize_code("07133 006") == "07133006"
    assert normalize_code("0712") == "0712"
