"""Unit tests for the path walker, date ranges and reference normalisation."""

import pytest

from app.indexer import date_range, normalise_reference, walk


def test_walk_flattens_lists_and_drops_nulls() -> None:
    res = {"name": [{"given": ["A", None]}, {"given": ["B"]}, {"family": "F"}]}
    assert walk(res, "name.given") == ["A", "B"]
    assert walk(res, "name.family") == ["F"]
    assert walk(res, "missing.path") == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024", ("2024-01-01T00:00:00.000Z", "2024-12-31T23:59:59.999Z")),
        ("2024-02", ("2024-02-01T00:00:00.000Z", "2024-02-29T23:59:59.999Z")),
        ("2023-12-31", ("2023-12-31T00:00:00.000Z", "2023-12-31T23:59:59.999Z")),
        ("2024-03-01T10:15", ("2024-03-01T10:15:00.000Z", "2024-03-01T10:15:59.999Z")),
        ("2024-03-01T10:15:30Z", ("2024-03-01T10:15:30.000Z", "2024-03-01T10:15:30.999Z")),
        (
            "2024-03-01T10:15:30.1234+13:00",
            ("2024-02-29T21:15:30.123Z", "2024-02-29T21:15:30.123Z"),
        ),
    ],
)
def test_date_range(value: str, expected: tuple[str, str]) -> None:
    assert date_range(value) == expected


@pytest.mark.parametrize("value", ["", "24", "2024-1", "2024-02-30", "x2024", "2024-01-01T25:00"])
def test_date_range_invalid(value: str) -> None:
    assert date_range(value) is None


def test_normalise_reference() -> None:
    base = "https://fhir.example/fhir"
    assert normalise_reference("Patient/1", base) == "Patient/1"
    assert normalise_reference("Patient/1/_history/2", base) == "Patient/1"
    assert normalise_reference(f"{base}/Patient/1", base) == "Patient/1"
    assert normalise_reference("https://other.example/fhir/Patient/1", base) == (
        "https://other.example/fhir/Patient/1"
    )
    assert normalise_reference("urn:uuid:abc", base) == "urn:uuid:abc"
    assert normalise_reference("#contained", base) is None
