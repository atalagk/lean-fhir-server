"""Single source of truth for resource types and search parameters (SPEC 5.2)."""

from dataclasses import dataclass
from typing import Literal

from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.location import Location
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.practitionerrole import PractitionerRole
from fhir.resources.R4B.resource import Resource

ParamType = Literal["token", "string", "date", "reference"]


@dataclass(frozen=True)
class SearchParam:
    name: str
    type: ParamType
    paths: tuple[str, ...]  # dotted paths; arrays are flattened implicitly
    target: str | None = None  # reference type filter, e.g. "Patient" for Encounter.patient


@dataclass(frozen=True)
class ResourceDef:
    model: type[Resource]
    params: tuple[SearchParam, ...]

    def param(self, name: str) -> SearchParam | None:
        return next((p for p in self.params if p.name == name), None)


# Search parameters answered from the `resource` table on every type (SPEC 4.3).
COMMON_PARAMS: tuple[SearchParam, ...] = (
    SearchParam("_id", "token", ()),
    SearchParam("_lastUpdated", "date", ()),
)

_NAME_PATHS = ("name.family", "name.given", "name.text", "name.prefix", "name.suffix")


def _identifier() -> SearchParam:
    return SearchParam("identifier", "token", ("identifier",))


def _active() -> SearchParam:
    return SearchParam("active", "token", ("active",))


REGISTRY: dict[str, ResourceDef] = {
    "Patient": ResourceDef(
        Patient,
        (
            _identifier(),
            SearchParam("name", "string", _NAME_PATHS),
            SearchParam("family", "string", ("name.family",)),
            SearchParam("given", "string", ("name.given",)),
            SearchParam("birthdate", "date", ("birthDate",)),
            SearchParam("gender", "token", ("gender",)),
            _active(),
            SearchParam("telecom", "token", ("telecom",)),
            SearchParam("organization", "reference", ("managingOrganization",)),
            SearchParam("general-practitioner", "reference", ("generalPractitioner",)),
        ),
    ),
    "Practitioner": ResourceDef(
        Practitioner,
        (
            _identifier(),
            SearchParam("name", "string", _NAME_PATHS),
            SearchParam("family", "string", ("name.family",)),
            SearchParam("given", "string", ("name.given",)),
            _active(),
        ),
    ),
    "PractitionerRole": ResourceDef(
        PractitionerRole,
        (
            _identifier(),
            SearchParam("practitioner", "reference", ("practitioner",)),
            SearchParam("organization", "reference", ("organization",)),
            SearchParam("location", "reference", ("location",)),
            SearchParam("role", "token", ("code",)),
            SearchParam("specialty", "token", ("specialty",)),
            _active(),
        ),
    ),
    "Organization": ResourceDef(
        Organization,
        (
            _identifier(),
            SearchParam("name", "string", ("name", "alias")),
            SearchParam("type", "token", ("type",)),
            _active(),
            SearchParam("partof", "reference", ("partOf",)),
        ),
    ),
    "Location": ResourceDef(
        Location,
        (
            _identifier(),
            SearchParam("name", "string", ("name", "alias")),
            SearchParam("status", "token", ("status",)),
            SearchParam("type", "token", ("type",)),
            SearchParam("organization", "reference", ("managingOrganization",)),
            SearchParam("partof", "reference", ("partOf",)),
        ),
    ),
    "Encounter": ResourceDef(
        Encounter,
        (
            _identifier(),
            SearchParam("subject", "reference", ("subject",)),
            SearchParam("patient", "reference", ("subject",), target="Patient"),
            SearchParam("status", "token", ("status",)),
            SearchParam("class", "token", ("class",)),
            SearchParam("type", "token", ("type",)),
            SearchParam("date", "date", ("period",)),
            SearchParam("participant", "reference", ("participant.individual",)),
            SearchParam("location", "reference", ("location.location",)),
            SearchParam("service-provider", "reference", ("serviceProvider",)),
            SearchParam("part-of", "reference", ("partOf",)),
        ),
    ),
}
