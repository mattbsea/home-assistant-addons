"""Direct tests for the shared records module (the single follow-the-file implementation)."""
import importlib

records = importlib.import_module("records")


def test_parse_line_valid():
    assert records.parse_line('{"a": 1}\n') == {"a": 1}


def test_parse_line_skips_blank_and_non_json():
    assert records.parse_line("") is None
    assert records.parse_line("   \n") is None
    assert records.parse_line("not json\n") is None       # doesn't start with {
    assert records.parse_line("{bad json}\n") is None     # starts with { but invalid


def test_parse_line_strips_whitespace():
    assert records.parse_line('   {"x": "y"}   ') == {"x": "y"}
