"""Read/create/update/delete with versioning (SPEC 2.3, 4.4)."""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import IntegrityError

from app.db import resource
from app.errors import FhirError
from app.registry import ResourceDef

ID_RE = re.compile(r"[A-Za-z0-9\-.]{1,64}")


@dataclass(frozen=True)
class Stored:
    content: dict[str, Any]
    version_id: int
    last_updated: str


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


def read(engine: Engine, rtype: str, rid: str) -> Stored:
    with engine.connect() as conn:
        found = _load(conn, rtype, rid)
    if found is None:
        raise FhirError(404, "not-found", f"{rtype}/{rid} not found")
    return found


def create(engine: Engine, rdef: ResourceDef, rtype: str, body: dict[str, Any]) -> Stored:
    rid, ts = str(uuid.uuid4()), now_ts()
    content = _prepare(rdef, body, rid, 1, ts)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(resource).values(
                resource_type=rtype, id=rid, version_id=1, last_updated=ts, content=content
            )
        )
    return Stored(content, 1, ts)


def update(
    engine: Engine,
    rdef: ResourceDef,
    rtype: str,
    rid: str,
    body: dict[str, Any],
    if_match: int | None,
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
    except IntegrityError as exc:
        raise FhirError(409, "conflict", "Concurrent create; retry") from exc
    return Stored(content, version, ts), current is None


def remove(engine: Engine, rtype: str, rid: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.delete(resource).where(resource.c.resource_type == rtype, resource.c.id == rid)
        )
