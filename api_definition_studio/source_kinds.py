"""Source-system types. Each type relabels the same optional source-mapping
fields, so a Siebel entity talks about business components and LOV types
while a REST, database or file-based entity uses its own terms.

The model always stores the fields under the same keys:
  entity.source_object, entity.source_io, attribute.source_field,
  attribute.source_column, attribute.lov_type, relationship.source_link
"""
from __future__ import annotations

from typing import Any, Dict, List

SIEBEL_TOKENS = ["siebel", "attrib", "x_", "bc_", "s_", "row_id", "lov", "integrationobject"]

KINDS: Dict[str, Dict[str, Any]] = {
    "siebel": {
        "label": "Siebel CRM",
        "object": "Business component", "container": "Integration object", "field": "BC field",
        "location": "Table.column", "code_list": "LOV type", "link": "Siebel link",
        "ph": {"object": "Contact", "container": "Contact IO", "field": "First Name",
               "location": "S_CONTACT.FST_NAME", "code_list": "CASE_STATUS", "link": "HLS Case/Contact"},
        "tokens": SIEBEL_TOKENS,
        "ext": {"container": "x-source-integration-object", "location": "x-source-column", "code_list": "x-source-lov-type"},
    },
    "rest": {
        "label": "REST / web API",
        "object": "Resource", "container": "Service / API", "field": "JSON path",
        "location": "Endpoint", "code_list": "Reference data", "link": "Relationship",
        "ph": {"object": "/v2/payments", "container": "Payments API v2", "field": "$.amount.value",
               "location": "GET /v2/payments/{id}", "code_list": "PaymentStatus", "link": "payment.caseReference"},
        "tokens": [],
    },
    "database": {
        "label": "Database",
        "object": "Table / view", "container": "Schema", "field": "Column",
        "location": "Source data type", "code_list": "Lookup table", "link": "Foreign key",
        "ph": {"object": "PAYMENT", "container": "FIN", "field": "AMOUNT",
               "location": "NUMBER(10,2)", "code_list": "REF_PAYMENT_STATUS", "link": "FK_PAYMENT_CASE"},
        "tokens": [],
    },
    "file": {
        "label": "File / batch feed",
        "object": "Record type", "container": "File / feed", "field": "Field name",
        "location": "Position / column", "code_list": "Code list", "link": "Record key",
        "ph": {"object": "PAYMENT_REC", "container": "daily-payments.csv", "field": "amount",
               "location": "column 7", "code_list": "payment-status codes", "link": "case_ref"},
        "tokens": [],
    },
    "mainframe": {
        "label": "Mainframe",
        "object": "Copybook / record", "container": "Dataset / program", "field": "Copybook field",
        "location": "PIC / offset", "code_list": "Code table", "link": "Key reference",
        "ph": {"object": "PAYREC", "container": "PAY.DAILY.EXTRACT", "field": "PAY-AMOUNT",
               "location": "PIC 9(8)V99 @ 41", "code_list": "PAYSTAT", "link": "CASE-REF"},
        "tokens": [],
    },
    "other": {
        "label": "Other",
        "object": "Source object", "container": "Source container", "field": "Source field",
        "location": "Source location", "code_list": "Code list", "link": "Source link",
        "ph": {"object": "", "container": "", "field": "", "location": "", "code_list": "", "link": ""},
        "tokens": [],
    },
}

# Labels used when an entity has no source system (derived or new data).
UNSOURCED = dict(KINDS["other"], label="Not sourced")

GENERIC_EXT = {"container": "x-source-container", "location": "x-source-location", "code_list": "x-source-code-list"}


def kind_of(system: Any) -> Dict[str, Any]:
    if not system:
        return UNSOURCED
    return KINDS.get(system.get("kind", "other"), KINDS["other"])


def ext_name(system: Any, part: str) -> str:
    """OpenAPI extension name for a source part. Siebel keeps the names used
    since 1.0 (x-source-column, x-source-lov-type, …); other types use
    neutral names."""
    k = kind_of(system)
    return k.get("ext", {}).get(part, GENERIC_EXT[part])


def default_tokens(kind: str) -> List[str]:
    return list(KINDS.get(kind, KINDS["other"])["tokens"])


def meta() -> Dict[str, Any]:
    """Served to the UI so labels are defined in one place."""
    return {k: {x: v[x] for x in ("label", "object", "container", "field", "location", "code_list", "link", "ph", "tokens")}
            for k, v in KINDS.items()}
