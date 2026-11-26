"""Generic FHIR router (SPEC 5.1), CapabilityStatement (F7) and the auth seam (N8a)."""

import json
import re
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from starlette.convertors import StringConvertor, register_url_convertor

from app import store
from app.errors import FhirError, FhirResponse
from app.registry import COMMON_PARAMS, REGISTRY, ResourceDef

SOFTWARE_VERSION = "0.1.0"
INTERACTIONS = ("read", "create", "update", "delete", "search-type")


JSON_MEDIA = {"json", "application/json", "application/fhir+json"}
ACCEPT_ANY = {"*/*", "application/*"}


class ResourceTypeConvertor(StringConvertor):
    """Path segments match only registered types, so unknown types fall through to 404."""

    regex = "|".join(sorted(map(re.escape, REGISTRY), key=len, reverse=True))


register_url_convertor("rtype", ResourceTypeConvertor())


def require_auth() -> None:
    """No-op auth seam (D4). Replace with API-key/OIDC checks later."""


def _media(value: str) -> str:
    return value.split(";")[0].strip().lower()


def negotiate_format(request: Request) -> None:
    """JSON only: `_format` or `Accept` that rules out JSON -> 406."""
    fmt = request.query_params.get("_format")
    if fmt is not None and _media(fmt) not in JSON_MEDIA:
        raise FhirError(406, "not-supported", f"Unsupported _format: {fmt}")
    accept = request.headers.get("accept")
    if accept and not any(
        m in JSON_MEDIA or m in ACCEPT_ANY or m.endswith("+json")
        for m in map(_media, accept.split(","))
    ):
        raise FhirError(406, "not-supported", f"Only JSON is supported; Accept: {accept}")


router = APIRouter(prefix="/fhir", dependencies=[Depends(require_auth), Depends(negotiate_format)])


def fhir_base(request: Request) -> str:
    configured = request.app.state.settings.base_url
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/") + "/fhir"


async def json_body(request: Request) -> dict[str, Any]:
    ctype = _media(request.headers.get("content-type", ""))
    if ctype and ctype not in JSON_MEDIA:
        raise FhirError(415, "not-supported", f"Unsupported Content-Type: {ctype}")
    try:
        body = json.loads(await request.body())
    except ValueError as exc:
        raise FhirError(400, "invalid", f"Malformed JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise FhirError(400, "invalid", "Body must be a JSON object")
    return body


Body = Annotated[dict[str, Any], Depends(json_body)]


def check_resource_type(body: dict[str, Any], resource_type: str) -> ResourceDef:
    if body.get("resourceType") != resource_type:
        raise FhirError(
            400,
            "invalid",
            f"resourceType '{body.get('resourceType')}' does not match URL type '{resource_type}'",
        )
    return REGISTRY[resource_type]


def parse_if_match(value: str | None) -> int | None:
    if value is None:
        return None
    m = re.fullmatch(r'\s*(?:W/)?"(\d+)"\s*', value)
    if not m:
        raise FhirError(400, "invalid", f'Malformed If-Match header (expected W/"n"): {value}')
    return int(m.group(1))


def resource_response(
    stored: store.Stored, status: int = 200, location: str | None = None
) -> FhirResponse:
    ts = datetime.strptime(stored.last_updated, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    headers = {
        "ETag": f'W/"{stored.version_id}"',
        "Last-Modified": format_datetime(ts, usegmt=True),
    }
    if location:
        headers["Location"] = location
    return FhirResponse(stored.content, status_code=status, headers=headers)


def capability_statement(base: str) -> dict[str, Any]:
    resources = []
    for rtype, rdef in REGISTRY.items():
        resources.append(
            {
                "type": rtype,
                "interaction": [{"code": code} for code in INTERACTIONS],
                "versioning": "versioned-update",
                "readHistory": False,
                "updateCreate": True,
                "conditionalCreate": True,
                "conditionalUpdate": False,
                "conditionalDelete": "not-supported",
                "referencePolicy": ["literal", "local"],
                "searchParam": [
                    {"name": p.name, "type": p.type} for p in (*COMMON_PARAMS, *rdef.params)
                ],
            }
        )
    return {
        "resourceType": "CapabilityStatement",
        "status": "active",
        "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "instance",
        "software": {"name": "LeanFHIR", "version": SOFTWARE_VERSION},
        "implementation": {"description": "LeanFHIR demographic/admin server", "url": base},
        "fhirVersion": "4.0.1",
        "format": ["json"],
        "rest": [{"mode": "server", "resource": resources}],
    }


@router.get("/metadata")
def metadata(request: Request) -> FhirResponse:
    return FhirResponse(capability_statement(fhir_base(request)))


@router.get("/{resource_type:rtype}/{rid}")
def read(request: Request, resource_type: str, rid: str) -> FhirResponse:
    return resource_response(store.read(request.app.state.engine, resource_type, rid))


@router.post("/{resource_type:rtype}")
def create(request: Request, resource_type: str, body: Body) -> FhirResponse:
    rdef = check_resource_type(body, resource_type)
    stored = store.create(request.app.state.engine, rdef, resource_type, body)
    location = f"{fhir_base(request)}/{resource_type}/{stored.content['id']}"
    return resource_response(stored, 201, location)


@router.put("/{resource_type:rtype}/{rid}")
def update(
    request: Request,
    resource_type: str,
    rid: str,
    body: Body,
    if_match: Annotated[str | None, Header()] = None,
) -> FhirResponse:
    rdef = check_resource_type(body, resource_type)
    if not store.valid_id(rid):
        raise FhirError(400, "invalid", f"Invalid id format: {rid}")
    if body.get("id") != rid:
        raise FhirError(400, "invalid", f"Body id '{body.get('id')}' must equal URL id '{rid}'")
    stored, created = store.update(
        request.app.state.engine, rdef, resource_type, rid, body, parse_if_match(if_match)
    )
    if created:
        return resource_response(stored, 201, f"{fhir_base(request)}/{resource_type}/{rid}")
    return resource_response(stored)


@router.delete("/{resource_type:rtype}/{rid}", status_code=204)
def delete(request: Request, resource_type: str, rid: str) -> Response:
    store.remove(request.app.state.engine, resource_type, rid)
    return Response(status_code=204)
