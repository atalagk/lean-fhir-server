"""Search: query parsing, value parsing, SQL building, Bundle assembly (SPEC 2.4, 4.4)."""

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import sqlalchemy as sa
from sqlalchemy import ColumnElement, Connection

from app.config import Settings
from app.db import resource, search_index
from app.errors import FhirError
from app.indexer import date_range, normalise_reference
from app.registry import COMMON_PARAMS, REGISTRY, SearchParam

CONTROL = {"_count", "_offset", "_sort", "_format"}
MODIFIERS = {"string": {"exact", "contains"}}
DATE_PREFIXES = {"eq", "ge", "gt", "le", "lt"}


@dataclass
class Search:
    rtype: str
    where: list[ColumnElement[bool]]
    count: int
    offset: int = 0
    ascending: bool = False
    link_params: list[tuple[str, str]] = field(default_factory=list)


def _bad(msg: str) -> FhirError:
    return FhirError(400, "invalid", msg)


def split_escaped(value: str, sep: str) -> list[str]:
    """Split on `sep` not preceded by a backslash; escapes are kept for later unescaping."""
    parts, cur, i = [], "", 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            cur += value[i : i + 2]
            i += 2
            continue
        if ch == sep:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    return [*parts, cur]


def unescape(value: str) -> str:
    out, i = "", 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            i += 1
        out += value[i]
        i += 1
    return out


def _date_preds(
    name: str, raw: str, start: ColumnElement, end: ColumnElement
) -> ColumnElement[bool]:
    prefix = raw[:2] if raw[:2].isalpha() else "eq"
    if prefix not in DATE_PREFIXES:
        raise _bad(f"Unsupported date prefix '{prefix}' in {name}={raw}")
    rng = date_range(raw[2:] if raw[:2].isalpha() else raw)
    if rng is None:
        raise _bad(f"Invalid date value for {name}: {raw}")
    lo, hi = rng
    return {
        "eq": lambda: sa.and_(start >= lo, end <= hi),
        "ge": lambda: sa.or_(end.is_(None), end >= lo),
        "gt": lambda: sa.or_(end.is_(None), end > hi),
        "le": lambda: sa.or_(start.is_(None), start <= hi),
        "lt": lambda: sa.or_(start.is_(None), start < lo),
    }[prefix]()


def _value_pred(
    param: SearchParam, modifier: str | None, raw: str, base: str
) -> ColumnElement[bool]:
    s = search_index.c
    if param.type == "date":
        return _date_preds(param.name, raw, s.date_start, s.date_end)
    if param.type == "string":
        value = unescape(raw)
        if modifier == "exact":
            return s.value_exact == value
        if modifier == "contains":
            return s.value_string.contains(value.lower(), autoescape=True)
        return s.value_string.startswith(value.lower(), autoescape=True)
    if param.type == "token":
        parts = split_escaped(raw, "|")
        if len(parts) == 1:
            return s.token_code == unescape(raw)
        if len(parts) != 2 or parts == ["", ""]:
            raise _bad(f"Invalid token value for {param.name}: {raw}")
        system, code = map(unescape, parts)
        if not system:
            return sa.and_(s.token_system.is_(None), s.token_code == code)
        if not code:
            return s.token_system == system
        return sa.and_(s.token_system == system, s.token_code == code)
    # reference
    value = unescape(raw)
    if "/" not in value:  # bare id: any supported type (or the target type)
        types = [param.target] if param.target else list(REGISTRY)
        return s.reference.in_([f"{t}/{value}" for t in types])
    ref = normalise_reference(value, base)
    if ref is None:
        raise _bad(f"Invalid reference value for {param.name}: {raw}")
    return s.reference == ref


def _param_clause(
    rtype: str, param: SearchParam, modifier: str | None, raws: list[str], base: str
) -> ColumnElement[bool]:
    r = resource.c
    if param.name == "_id":
        return r.id.in_([unescape(v) for v in raws])
    if param.name == "_lastUpdated":
        return sa.or_(
            *(_date_preds("_lastUpdated", v, r.last_updated, r.last_updated) for v in raws)
        )
    s = search_index.c
    return sa.exists().where(
        s.resource_type == r.resource_type,
        s.resource_id == r.id,
        s.param == param.name,
        sa.or_(*(_value_pred(param, modifier, v, base) for v in raws)),
    )


def _int(name: str, value: str) -> int:
    if not value.isdigit():
        raise _bad(f"{name} must be a non-negative integer: {value}")
    return int(value)


def parse(rtype: str, items: list[tuple[str, str]], settings: Settings, base: str) -> Search:
    rdef = REGISTRY[rtype]
    search = Search(rtype, [resource.c.resource_type == rtype], settings.default_count)
    seen_control: set[str] = set()
    for key, value in items:
        name, _, modifier = key.partition(":")
        if name in CONTROL:
            if modifier or name in seen_control:
                raise _bad(f"Parameter {key} may appear once and takes no modifier")
            seen_control.add(name)
            if name == "_count":
                search.count = min(_int(key, value), settings.max_count)
            elif name == "_offset":
                search.offset = _int(key, value)
            elif name == "_sort":
                if value not in ("_lastUpdated", "-_lastUpdated"):
                    raise _bad(f"Unsupported _sort: {value} (only _lastUpdated, -_lastUpdated)")
                search.ascending = value == "_lastUpdated"
                search.link_params.append((key, value))
            continue  # _format already handled by content negotiation
        param = next((p for p in COMMON_PARAMS if p.name == name), None) or rdef.param(name)
        if param is None:
            raise _bad(f"Unknown search parameter for {rtype}: {name}")
        if modifier and modifier not in MODIFIERS.get(param.type, set()):
            raise _bad(f"Unsupported modifier :{modifier} on parameter {name}")
        raws = split_escaped(value, ",")
        if any(v == "" for v in raws):
            raise _bad(f"Empty value for search parameter {key}")
        search.where.append(_param_clause(rtype, param, modifier or None, raws, base))
        search.link_params.append((key, value))
    return search


def execute(conn: Connection, search: Search) -> tuple[int, list[dict[str, Any]]]:
    r = resource.c
    total = conn.execute(sa.select(sa.func.count()).select_from(resource).where(*search.where))
    total_n = total.scalar_one()
    if search.count == 0:
        return total_n, []
    order = r.last_updated.asc() if search.ascending else r.last_updated.desc()
    rows = conn.execute(
        sa.select(r.content)
        .where(*search.where)
        .order_by(order, r.id)
        .limit(search.count)
        .offset(search.offset)
    )
    return total_n, [row.content for row in rows]


def bundle(search: Search, total: int, resources: list[dict[str, Any]], base: str) -> dict:
    def url(offset: int) -> str:
        params = [*search.link_params, ("_count", str(search.count)), ("_offset", str(offset))]
        return f"{base}/{search.rtype}?{urlencode(params)}"

    links = [{"relation": "self", "url": url(search.offset)}]
    if search.count and search.offset + search.count < total:
        links.append({"relation": "next", "url": url(search.offset + search.count)})
    if search.count and search.offset > 0:
        links.append({"relation": "previous", "url": url(max(search.offset - search.count, 0))})
    out: dict[str, Any] = {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": total,
        "link": links,
    }
    if resources:
        out["entry"] = [
            {
                "fullUrl": f"{base}/{search.rtype}/{res['id']}",
                "resource": res,
                "search": {"mode": "match"},
            }
            for res in resources
        ]
    return out
