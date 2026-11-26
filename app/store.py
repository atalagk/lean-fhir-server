"""Read/create/update/delete with versioning (SPEC 2.3, 4.4)."""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import IntegrityError

from app.db import resource, search_index
from app.errors import FhirError
from app.indexer import index_rows
from app.registry import REGISTRY, ResourceDef
from app.search import Search, execute

ID_RE = re.compile(r"[A-Za-z0-9\-.]{1,64}")


@dataclass(frozen=True)
class Stored:
    content: dict[str, Any]
    version_id: int
    last_updated: str


@dataclass(frozen=True)
class WriteOptions:
    base: str | None  # server base URL for reference normalisation
    enforce_references: bool = True


def now_ts() -> str:
    """UTC ISO-8601, millisecond precision, fixed width (24 chars), `Z` suffix."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:23] + "Z"


def valid_id(rid: str) -> bool:
    return ID_RE.fullmatch(rid) is not None


def _prepare(rdef: ResourceDef, body: dict[str, Any], rid: str, version: int, ts: str) -> dict:
    """Body as received with server-set id and meta.versionId/lastUpdated; validated."""
    meta = body.get("meta", {})
    if not isinstance(meta, dict):
        raise FhirError(422, "structure", "meta must be a JSON object")
    rest = {k: v for k, v in body.items() if k not in ("resourceType", "id", "meta")}
    content = {
        "resourceType": body["resourceType"],
        "id": rid,
        "meta": {**meta, "versionId": str(version), "lastUpdated": ts},
        **rest,
    }
    rdef.model.model_validate(content)  # ValidationError -> 422 handler
    return content


def _load(conn: Connection, rtype: str, rid: str) -> Stored | None:
    row = conn.execute(
        sa.select(resource.c.content, resource.c.version_id, resource.c.last_updated).where(
            resource.c.resource_type == rtype, resource.c.id == rid
        )
    ).first()
    return Stored(row.content, row.version_id, row.last_updated) if row else None


def _check_references(conn: Connection, rtype: str, rid: str, rows: list[dict]) -> None:
    """F9: indexed local references to supported types must resolve (self-references allowed)."""
    targets = set()
    for row in rows:
        target_type, _, target_id = (row["reference"] or "").partition("/")
        if target_type in REGISTRY and target_id and (target_type, target_id) != (rtype, rid):
            targets.add((target_type, target_id))
    if not targets:
        return
    key = sa.tuple_(resource.c.resource_type, resource.c.id)
    found = {tuple(r) for r in conn.execute(sa.select(*key.clauses).where(key.in_(targets)))}
    missing = sorted(f"{t}/{i}" for t, i in targets - found)
    if missing:
        raise FhirError(422, "processing", f"Unresolved reference(s): {', '.join(missing)}")


def _write_index(conn: Connection, rtype: str, content: dict[str, Any], opts: WriteOptions) -> None:
    rows = index_rows(rtype, content, opts.base)
    if opts.enforce_references:
        _check_references(conn, rtype, content["id"], rows)
    conn.execute(
        sa.delete(search_index).where(
            search_index.c.resource_type == rtype, search_index.c.resource_id == content["id"]
        )
    )
    if rows:
        conn.execute(sa.insert(search_index), rows)


def read(engine: Engine, rtype: str, rid: str) -> Stored:
    with engine.connect() as conn:
        found = _load(conn, rtype, rid)
    if found is None:
        raise FhirError(404, "not-found", f"{rtype}/{rid} not found")
    return found


def create(
    engine: Engine,
    rdef: ResourceDef,
    rtype: str,
    body: dict[str, Any],
    opts: WriteOptions,
    condition: Search | None = None,
) -> tuple[Stored, bool]:
    """Returns (stored, created). With `condition` (If-None-Exist): one match -> existing."""
    rid, ts = str(uuid.uuid4()), now_ts()
    content = _prepare(rdef, body, rid, 1, ts)
    with engine.begin() as conn:
        if condition is not None:
            condition.count, condition.offset = 2, 0
            total, matches = execute(conn, condition)
            if total > 1:
                raise FhirError(412, "duplicate", f"If-None-Exist matched {total} resources")
            if total == 1:
                existing = _load(conn, rtype, matches[0]["id"])
                assert existing is not None
                return existing, False
        conn.execute(
            sa.insert(resource).values(
                resource_type=rtype, id=rid, version_id=1, last_updated=ts, content=content
            )
        )
        _write_index(conn, rtype, content, opts)
    return Stored(content, 1, ts), True


def update(
    engine: Engine,
    rdef: ResourceDef,
    rtype: str,
    rid: str,
    body: dict[str, Any],
    if_match: int | None,
    opts: WriteOptions,
) -> tuple[Stored, bool]:
    """Returns (stored, created)."""
    ts = now_ts()
    try:
        with engine.begin() as conn:
            current = _load(conn, rtype, rid)
            current_version = current.version_id if current else 0
            if if_match is not None and if_match != current_version:
                raise FhirError(
                    412,
                    "conflict",
                    f"If-Match version {if_match} does not match current version "
                    f"{current_version or 'none (resource does not exist)'}",
                )
            version = current_version + 1
            content = _prepare(rdef, body, rid, version, ts)
            values = {"version_id": version, "last_updated": ts, "content": content}
            if current is None:
                conn.execute(sa.insert(resource).values(resource_type=rtype, id=rid, **values))
            else:
                result = conn.execute(
                    sa.update(resource)
                    .where(
                        resource.c.resource_type == rtype,
                        resource.c.id == rid,
                        resource.c.version_id == current_version,
                    )
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise FhirError(409, "conflict", "Concurrent update; retry")
            _write_index(conn, rtype, content, opts)
    except IntegrityError as exc:
        raise FhirError(409, "conflict", "Concurrent create; retry") from exc
    return Stored(content, version, ts), current is None


def remove(engine: Engine, rtype: str, rid: str, enforce_references: bool = True) -> None:
    si = search_index.c
    with engine.begin() as conn:
        if enforce_references:
            referrers = conn.execute(
                sa.select(si.resource_type, si.resource_id)
                .where(
                    si.reference == f"{rtype}/{rid}",
                    sa.not_(sa.and_(si.resource_type == rtype, si.resource_id == rid)),
                )
                .distinct()
                .order_by(si.resource_type, si.resource_id)
                .limit(5)
            ).all()
            if referrers:
                names = ", ".join(f"{t}/{i}" for t, i in referrers)
                raise FhirError(409, "conflict", f"{rtype}/{rid} is referenced by {names}")
        conn.execute(
            sa.delete(resource).where(resource.c.resource_type == rtype, resource.c.id == rid)
        )


def reindex(engine: Engine, base: str | None, batch: int = 500) -> int:
    """Rebuild search_index from stored resources in one transaction. Returns resource count."""
    count, last = 0, ("", "")
    with engine.begin() as conn:
        conn.execute(sa.delete(search_index))
        while True:
            rows = conn.execute(
                sa.select(resource.c.resource_type, resource.c.id, resource.c.content)
                .where(
                    sa.or_(
                        resource.c.resource_type > last[0],
                        sa.and_(resource.c.resource_type == last[0], resource.c.id > last[1]),
                    )
                )
                .order_by(resource.c.resource_type, resource.c.id)
                .limit(batch)
            ).all()
            if not rows:
                return count
            index = [r for row in rows for r in index_rows(row.resource_type, row.content, base)]
            if index:
                conn.execute(sa.insert(search_index), index)
            count += len(rows)
            last = (rows[-1].resource_type, rows[-1].id)
