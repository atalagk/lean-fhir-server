"""Resource -> search_index rows (SPEC 5.4). Dotted-path walker; no FHIRPath."""

import calendar
import re
from datetime import datetime, timedelta
from typing import Any

from app.registry import REGISTRY, SearchParam

Row = dict[str, Any]
VALUE_COLUMNS = (
    "value_string",
    "value_exact",
    "token_system",
    "token_code",
    "date_start",
    "date_end",
    "reference",
)

_DATE_RE = re.compile(
    r"(\d{4})(?:-(\d{2})(?:-(\d{2})"
    r"(?:T(\d{2}):(\d{2})(?::(\d{2})(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?)?)?"
)
_LOCAL_REF_RE = re.compile(r"([A-Z][A-Za-z]+)/([A-Za-z0-9\-.]{1,64})(?:/_history/.*)?")


def walk(node: Any, path: str) -> list[Any]:
    """Values at a dotted path; lists are flattened at every step, nulls dropped."""
    values = [node]
    for part in path.split("."):
        found: list[Any] = []
        for v in values:
            if isinstance(v, dict) and part in v:
                child = v[part]
                found.extend(child if isinstance(child, list) else [child])
        values = found
    return [v for v in values if v is not None]


def fmt_ts(dt: datetime) -> str:
    """Fixed-width UTC timestamp (24 chars) so lexical order == chronological order."""
    return (
        f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}T{dt.hour:02d}:{dt.minute:02d}:"
        f"{dt.second:02d}.{dt.microsecond // 1000:03d}Z"
    )


def date_range(value: str) -> tuple[str, str] | None:
    """FHIR date/dateTime/instant -> inclusive (start, end) by precision; no timezone = UTC."""
    m = _DATE_RE.fullmatch(value.strip()) if isinstance(value, str) else None
    if not m:
        return None
    y, mo, d, hh, mi, ss, frac, tz = m.groups()
    try:
        year = int(y)
        if mo is None:
            start, end = datetime(year, 1, 1), datetime(year, 12, 31) + timedelta(days=1)
        elif d is None:
            last = calendar.monthrange(year, int(mo))[1]
            start, end = datetime(year, int(mo), 1), datetime(year, int(mo), last)
            end += timedelta(days=1)
        elif hh is None:
            start = datetime(year, int(mo), int(d))
            end = start + timedelta(days=1)
        else:
            ms = int((frac[1:] + "000")[:3]) if frac else 0
            start = datetime(year, int(mo), int(d), int(hh), int(mi), int(ss or 0), ms * 1000)
            step = timedelta(minutes=1) if ss is None else timedelta(seconds=1)
            end = start + (timedelta(milliseconds=1) if frac else step)
            if tz and tz != "Z":
                sign = 1 if tz[0] == "+" else -1
                offset = sign * timedelta(hours=int(tz[1:3]), minutes=int(tz[4:6]))
                start, end = start - offset, end - offset
        return fmt_ts(start), fmt_ts(end - timedelta(milliseconds=1))
    except (ValueError, OverflowError):
        return None


def normalise_reference(ref: str, base: str | None) -> str | None:
    """Local `Type/id` for relative refs or absolute URLs under base; other refs as-is.

    Contained (`#...`) references return None (not indexed).
    """
    if not ref or ref.startswith("#"):
        return None
    if base and ref.startswith(base.rstrip("/") + "/"):
        ref = ref[len(base.rstrip("/")) + 1 :]
    m = _LOCAL_REF_RE.fullmatch(ref)
    return f"{m.group(1)}/{m.group(2)}" if m else ref


def _token(v: Any) -> list[tuple[str | None, str]]:
    if isinstance(v, bool):
        return [(None, "true" if v else "false")]
    if isinstance(v, str):
        return [(None, v)]
    if not isinstance(v, dict):
        return []
    if "coding" in v:  # CodeableConcept
        return [t for c in v["coding"] or [] for t in _token(c) if isinstance(c, dict)]
    key = "code" if "code" in v else "value"  # Coding | Identifier / ContactPoint
    code = v.get(key)
    return [(v.get("system") or None, code)] if isinstance(code, str) and code else []


def extract(param: SearchParam, resource: dict[str, Any], base: str | None) -> list[Row]:
    rows: list[Row] = []
    for value in (v for path in param.paths for v in walk(resource, path)):
        if param.type == "token":
            rows += [{"token_system": s, "token_code": c} for s, c in _token(value)]
        elif param.type == "string" and isinstance(value, str):
            rows.append({"value_string": value.lower(), "value_exact": value})
        elif param.type == "date":
            if isinstance(value, dict):  # Period
                start = date_range(value.get("start", ""))
                end = date_range(value.get("end", ""))
                if start or end:
                    rows.append({"date_start": start and start[0], "date_end": end and end[1]})
            elif rng := date_range(value):
                rows.append({"date_start": rng[0], "date_end": rng[1]})
        elif param.type == "reference" and isinstance(value, dict):
            ref = normalise_reference(value.get("reference") or "", base)
            if ref and (param.target is None or ref.startswith(param.target + "/")):
                rows.append({"reference": ref})
    return rows


def index_rows(rtype: str, resource: dict[str, Any], base: str | None) -> list[Row]:
    """All search_index rows for a resource (deduplicated)."""
    out: list[Row] = []
    seen: set[tuple] = set()
    for param in REGISTRY[rtype].params:
        for row in extract(param, resource, base):
            key = (param.name, *sorted(row.items()))
            if key not in seen:
                seen.add(key)
                out.append(
                    {"resource_type": rtype, "resource_id": resource["id"], "param": param.name}
                    | dict.fromkeys(VALUE_COLUMNS)
                    | row
                )
    return out
