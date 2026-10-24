"""Generate an OpenAPI 3.0.3 definition for one namespace from the model.

Business names travel with the contract as `title` and `x-business-name`; the
source mapping travels as `x-source-*` extensions so the definition and the
mapping sheet can never disagree."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from . import __version__, naming, variants
from .model import INVERSE_CARDINALITY, derived_from, to_many
from .source_kinds import ext_name
from .validate import nav_properties

PROBLEM = "application/problem+json"
# OpenAPI ignores header parameters with these names; Authorization comes from the security scheme
IGNORED_HEADERS = {"authorization", "accept", "content-type"}


# -- small helpers -------------------------------------------------------------

def _ents(m: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {e["id"]: e for e in m["entities"]}


def _nss(m: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {n["id"]: n for n in m["namespaces"]}


def key_attr(e: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for a in e["attributes"]:
        if a["key"]:
            return a
    return None


# money columns: a column flagged as money is sent as { "amount": 2999, "currency": "GBP" }.
# The amount keeps the column's type: integer = minor units (pence), number = decimal (29.99).
MONEY: Dict[str, Any] = {"currency": "GBP", "integer": "Money", "number": "DecimalMoney"}
MONEY_RE = r"(amount|price|liabilit|balance|arrears|payment|fee|cost|charge|total|income|salary|debt|refund|value)"


def is_money(a: Dict[str, Any]) -> bool:
    return bool(a.get("money")) and a.get("type") in ("integer", "number")


def money_amount(text: str, kind: str = "integer") -> Any:
    """An amount as the API sends it: for integer (pence) 29.99 -> 2999 (a whole number is taken
    as pence already); for number 29.99 stays 29.99."""
    v = float(text.replace(",", "").replace("£", "").strip())
    if kind == "integer":
        return int(round(v * 100)) if ("." in text) else int(v)
    return round(v, 2)


def money_scalar(a: Dict[str, Any]) -> Dict[str, Any]:
    if a["type"] == "integer":
        return {"type": "integer", "format": a.get("format") or "int64"}
    return {"type": "number", "format": a.get("format") or "decimal"}


def money_schema(kind: str) -> Dict[str, Any]:
    minor = kind == "integer"
    amount = dict(money_scalar({"type": kind}), description=(
        "Amount in the currency's minor unit (pence for GBP), e.g. 2999 for £29.99." if minor
        else "Amount in the currency's major unit, e.g. 29.99."), example=2999 if minor else 29.99)
    return {"type": "object", "title": "Money" if minor else "Money (decimal amount)",
            "description": "An amount of money with its currency.",
            "properties": {"amount": amount,
                           "currency": {"type": "string", "pattern": "^[A-Z]{3}$", "minLength": 3, "maxLength": 3,
                                        "description": "ISO 4217 currency code.", "example": MONEY["currency"]}},
            "required": ["amount", "currency"], "example": {"amount": 2999 if minor else 29.99, "currency": MONEY["currency"]},
            "x-money-amount": "minor-units" if minor else "decimal"}


def typed_example(a: Dict[str, Any]) -> Any:
    ex = (a.get("example") or "").strip()
    if not ex:
        return None
    if is_money(a):
        try:
            return {"amount": money_amount(ex, a["type"]), "currency": MONEY["currency"]}
        except ValueError:
            return None
    try:
        if a["type"] == "integer":
            return int(ex)
        if a["type"] == "number":
            return float(ex)
        if a["type"] == "boolean":
            return ex.lower() == "true"
    except ValueError:
        return ex
    if a["type"] in ("date", "date-time"):
        return naming.iso_example(a["type"], ex)
    return ex


def attr_schema(a: Dict[str, Any], src: Optional[Dict[str, Any]] = None,
                e: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    t = a["type"]
    s: Dict[str, Any] = {}
    money = is_money(a)
    if money:
        s["allOf"] = [{"$ref": "#/components/schemas/" + MONEY[t]}]
    elif t in ("string", "enum"):
        s["type"] = "string"
    elif t in ("date", "date-time"):
        s["type"] = "string"
        s["format"] = t
    else:
        s["type"] = t
    if a.get("format") and t not in ("date", "date-time", "enum") and not money:
        s["format"] = a["format"]
    if a.get("max_length") and s.get("type") == "string" and t == "string":
        s["maxLength"] = a["max_length"]
    if (a.get("pattern") or "").strip() and t == "string":
        s["pattern"] = a["pattern"].strip()
    s["title"] = a["business_name"]
    s["description"] = a["description"] or a["business_name"] + "."
    if money:
        s["description"] += (" { amount, currency }: amount in pence (minor units)." if t == "integer"
                             else " { amount, currency }: amount in pounds (decimal).")
        s["x-money"] = True
    if t == "enum":
        s["enum"] = [v["code"] for v in a["enum"]]
        s["x-enum-descriptions"] = [v["label"] for v in a["enum"]]
    if a["read_only"]:
        s["readOnly"] = True
    if a.get("system") and not a["read_only"]:
        s["description"] += " System column: set by the source system; not accepted on create or replace."
    ex = typed_example(a)
    if ex is not None:
        s["example"] = ex
    s["x-business-name"] = a["business_name"]
    if e is not None and variants.has_variants(e):
        # the source depends on the record: each variant's mapping, in full
        per = variants.extension(e, a, src)
        if per:
            s["x-source-variants"] = per
        if a.get("system"):
            s["x-system-column"] = True
        if t == "enum" and any(v["source_value"] for v in a["enum"]):
            s["x-source-values"] = {v["code"]: v["source_value"] for v in a["enum"]}
        if a["lov_type"]:
            s[ext_name(src, "code_list")] = a["lov_type"]
        if a["pii"]:
            s["x-pii"] = True
        return s
    if a.get("dd_reference"):
        s["x-dd-attribute"] = a["dd_reference"]
    if a.get("data_item"):
        s["x-data-item"] = a["data_item"]
    if a.get("system"):
        s["x-system-column"] = True
    if a.get("source_object"):
        s["x-source-object"] = a["source_object"]      # the column's own business component
    if a["source_field"]:
        s["x-source-field"] = a["source_field"]
    if a["source_column"]:
        s[ext_name(src, "location")] = a["source_column"]
    if a["lov_type"]:
        s[ext_name(src, "code_list")] = a["lov_type"]
    if t == "enum" and any(v["source_value"] for v in a["enum"]):
        s["x-source-values"] = {v["code"]: v["source_value"] for v in a["enum"]}
    if a["transform"]:
        s["x-transform"] = a["transform"]
    if a["pii"]:
        s["x-pii"] = True
    return s


def default_description(method: str, op: Dict[str, Any]) -> str:
    """A plain description for an operation that has none, from what it does."""
    ok = next((r for c, r in op.get("responses", {}).items() if c.startswith("2")), {})
    sch = next(iter((ok.get("content") or {}).values()), {}).get("schema", {})
    ref_ = sch.get("$ref", "")
    if_match = any(p.get("name") == "If-Match" for p in op.get("parameters", []))
    lock = " Send If-Match with the ETag from your last read; 412 if the record changed since." if if_match else ""
    summary = (op.get("summary") or "").rstrip(".")
    if method == "get" and ref_.endswith("Page"):
        return summary + ". Returns one page of records at a time; pass nextCursor as cursor for the next page."
    if method == "get" and sch.get("type") == "array":
        return summary + ". Returns every matching record as an array."
    if method == "get" and ref_.endswith("List"):
        return summary + ". Returns every matching record in one object."
    if method == "get":
        return summary + ". The ETag header carries the record's version."
    if method == "post" and "201" in op.get("responses", {}):
        return summary + ". Returns 201 Created with the new record's Location." + lock
    if method == "put":
        return summary + ": the body replaces the whole record." + lock
    if method == "patch":
        return summary + ": only the fields sent change (JSON merge patch)." + lock
    if method == "delete":
        return summary + "." + lock
    return summary + "." + lock


DERIVED_SCHEMA_RE = re.compile(r"(Create|Update|Page|List|Request|Response)$")


def prune_unused_schemas(doc: Dict[str, Any]) -> None:
    """Drop generated request, response and page schemas nothing refers to (e.g. an embedded
    entity's Update form when no PATCH sends it). Entity schemas themselves are kept."""
    schemas = doc.get("components", {}).get("schemas", {})
    while True:
        text = json.dumps({k: v for k, v in doc.items()})
        used = set(re.findall(r'#/components/schemas/([A-Za-z0-9_.-]+)"', text))
        drop = [k for k in schemas if k not in used and DERIVED_SCHEMA_RE.search(k)]
        if not drop:
            return
        for k in drop:
            del schemas[k]


def resource_path(n: Dict[str, Any], e: Dict[str, Any], with_base: bool) -> str:
    base = n["base_path"] if with_base else ""
    return base + "/" + e["plural"]


def full_path(n: Dict[str, Any], e: Dict[str, Any]) -> str:
    """Path including the namespace base path, whatever the placement setting."""
    return n["base_path"] + "/" + e["plural"]


def navs_with_target(m: Dict[str, Any], e: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Navigation properties of e, each with its target entity and whether the
    target end holds many records."""
    ents = _ents(m)
    out = []
    for nav in nav_properties(m, e["id"]):
        r = nav["rel"]
        if nav["side"] == "from":
            target, card = ents.get(r["to_entity_id"]), r["cardinality"]
            business = r["business_name"]
        else:
            target, card = ents.get(r["from_entity_id"]), INVERSE_CARDINALITY.get(r["cardinality"], r["cardinality"])
            business = r["inverse_business_name"]
        if not target:
            continue
        out.append(dict(nav, target=target, many=to_many(card), cardinality=card,
                        business_name=business or " ".join(naming.words(nav["api_name"])).capitalize()))
    return out


def group_properties(e: Dict[str, Any], props: Dict[str, Any], required: List[str],
                     variant: str = "full") -> Tuple[Dict[str, Any], List[str]]:
    """Move grouped columns into their group's property: an object {…} or a list [{…}], placed
    where its first column stands. variant: full (responses), create (POST/PUT) or update (PATCH)."""
    groups = {g["id"]: g for g in e.get("column_groups") or []}
    if not groups:
        return props, required
    member: Dict[str, str] = {}
    for a in e["attributes"]:
        if a.get("group_id") in groups and a["api_name"] in props:
            member[a["api_name"]] = a["group_id"]
    if not member:
        return props, required
    out: Dict[str, Any] = {}
    req_out = [r for r in required if r not in member]
    done = set()
    for name, sch in props.items():
        gid = member.get(name)
        if not gid:
            out[name] = sch
            continue
        if gid in done:
            continue
        done.add(gid)
        g = groups[gid]
        names = [n for n, x in member.items() if x == gid]
        inner: Dict[str, Any] = {"type": "object", "properties": {n: props[n] for n in names}}
        inner_req = [n for n in names if n in required]
        if inner_req:
            inner["required"] = inner_req
        desc = g.get("description") or g["business_name"] + "."
        ext = {"x-business-name": g["business_name"], "x-column-group": True}
        if g.get("many"):
            if variant == "update":
                desc += " When sent, replaces the whole list."
            out[g["api_name"]] = dict({"type": "array", "title": g["business_name"], "description": desc, "items": inner}, **ext)
        else:
            if variant == "update":
                inner.pop("required", None)    # merge patch: send only what changes
            out[g["api_name"]] = dict(inner, title=g["business_name"], description=desc, **ext)
        if g.get("required") and variant != "update":
            req_out.append(g["api_name"])
    return out, req_out


def group_example(e: Dict[str, Any], obj: Dict[str, Any]) -> Dict[str, Any]:
    """Shape an example object like group_properties shapes the schema."""
    groups = {g["id"]: g for g in e.get("column_groups") or []}
    if not groups:
        return obj
    gid_of = {a["api_name"]: a.get("group_id") for a in e["attributes"] if a.get("group_id") in groups}
    out: Dict[str, Any] = {}
    for k, v in obj.items():
        gid = gid_of.get(k)
        if not gid:
            out[k] = v
            continue
        g = groups[gid]
        if g.get("many"):
            out.setdefault(g["api_name"], [{}])[0][k] = v
        else:
            out.setdefault(g["api_name"], {})[k] = v
    return out


def example_object(m: Dict[str, Any], e: Dict[str, Any], depth: int = 0) -> Dict[str, Any]:
    nss = _nss(m)
    obj: Dict[str, Any] = {}
    for a in e["attributes"]:
        ex = typed_example(a)
        if ex is not None:
            obj[a["api_name"]] = ex
    for nav in navs_with_target(m, e):
        t = nav["target"]
        if nav["expose"] == "link":
            k = key_attr(t)
            tid = typed_example(k) if k else None
            tid = tid if tid is not None else "1-000001"
            n = nss.get(t["namespace_id"])
            link = {"id": tid, "href": (full_path(n, t) if n else "/" + t["plural"]) + "/" + str(tid)}
            obj[nav["api_name"]] = [link] if nav["many"] else link
        elif nav["expose"] == "embedded" and depth < 2:
            child = example_object(m, t, depth + 1)
            obj[nav["api_name"]] = [child] if nav["many"] else child
    return group_example(e, obj)


# -- the generator ---------------------------------------------------------------

def _problem_responses() -> Dict[str, Any]:
    def r(desc: str) -> Dict[str, Any]:
        return {"description": desc, "content": {PROBLEM: {"schema": {"$ref": "#/components/schemas/Problem"}}}}
    return {
        "BadRequest": r("The request is malformed."),
        "NotFound": r("The resource does not exist."),
        "Conflict": r("The request conflicts with the current state of the resource."),
        "PreconditionFailed": r("The If-Match ETag does not match the current version."),
        "ValidationFailed": r("One or more fields are invalid."),
    }


def _common_schemas() -> Dict[str, Any]:
    return {
        "Problem": {
            "type": "object", "description": "Error details (RFC 9457 problem details).",
            "properties": {
                "type": {"type": "string", "format": "uri", "example": "https://errors.example.internal/validation"},
                "title": {"type": "string", "example": "Validation failed"},
                "status": {"type": "integer", "example": 422},
                "detail": {"type": "string"},
                "instance": {"type": "string"},
                "errors": {"type": "array", "items": {"type": "object", "properties": {
                    "field": {"type": "string"}, "message": {"type": "string"}}}},
            },
            "required": ["type", "title", "status"],
        },
        "ResourceLink": {
            "type": "object", "description": "A reference to another resource.",
            "properties": {"id": {"type": "string", "description": "Identifier of the linked resource."},
                           "href": {"type": "string", "description": "Path of the linked resource.", "readOnly": True}},
            "required": ["id"],
        },
        "ResourceRef": {
            "type": "object", "description": "Identifies another resource when creating or updating.",
            "properties": {"id": {"type": "string"}}, "required": ["id"],
        },
    }


def _security(n: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, List[str]]]]:
    sec = n["security"]
    if sec == "bearer":
        return {"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}, [{"bearerAuth": []}]
    if sec == "apiKey":
        return {"apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}, [{"apiKey": []}]
    if sec == "oauth2":
        return ({"oauth2": {"type": "oauth2", "flows": {"clientCredentials": {
            "tokenUrl": "https://auth.example.internal/oauth2/token", "scopes": {
                n["key"] + ".read": "Read " + n["title"], n["key"] + ".write": "Change " + n["title"]}}}}},
                [{"oauth2": [n["key"] + ".read"]}])
    return {}, []


def source_of(m: Dict[str, Any], e: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sid = e.get("source_system_id")
    for s in m.get("source_systems", []):
        if s["id"] == sid:
            return s
    return None


def build_openapi(m: Dict[str, Any], ns_id: str, trace: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """OpenAPI document for one namespace. If `trace` is given, one entry per
    generated operation is appended, saying which model setting produced it."""
    nss, ents = _nss(m), _ents(m)
    n = nss[ns_id]
    st_ = m.get("settings") or {}
    taken = {x["schema_name"] for x in m["entities"]}
    MONEY.update(currency=(st_.get("money_currency") or "GBP").upper(),
                 integer="MonetaryAmount" if "Money" in taken else "Money",
                 number="MonetaryDecimalAmount" if "DecimalMoney" in taken else "DecimalMoney")
    in_paths = n.get("base_path_in", "paths") != "server"
    members = [e for e in m["entities"] if e["namespace_id"] == ns_id]

    # -- what the operations refer to ------------------------------------------------
    def ov_of(e: Dict[str, Any], op: str) -> Dict[str, Any]:
        return (e.get("op_overrides") or {}).get(op) or {}

    def req_entity(e: Dict[str, Any], ov: Dict[str, Any], default: Dict[str, Any]) -> Dict[str, Any]:
        return ents.get(ov.get("request_entity_id") or "") or default

    def resp_entity(e: Dict[str, Any], ov: Dict[str, Any], default: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        rid = ov.get("response_entity_id") or ""
        if rid == "none" or ov.get("status") == "204":
            return None
        return ents.get(rid) or default

    def action_request(e: Dict[str, Any], act: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
        t = ents.get(act.get("request_entity_id") or "")
        return (t, act.get("request_variant") or "create") if t else (None, "")

    def action_response(e: Dict[str, Any], act: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        rid = act.get("response_entity_id", "self")
        if act.get("status") == "204" or not rid:
            return None
        return e if rid == "self" else ents.get(rid)

    list_default = (m.get("settings") or {}).get("list_response") or "page"

    def list_shape(ov: Dict[str, Any]) -> str:
        """page: {items, nextCursor} with paging; array: [ … ]; wrapped: {agreements: [ … ]}."""
        mode = ov.get("response_mode") or ""
        return mode if mode in ("page", "array", "wrapped") else list_default

    def as_array(ov: Dict[str, Any]) -> bool:
        """No paging: the list answers with every match, as an array or an object holding one."""
        return list_shape(ov) in ("array", "wrapped")

    refs_create, refs_update, refs_page, refs_full = set(), set(), set(), set()
    for e in members:
        ops = e["operations"]
        for op in ("create", "update"):
            if op in ops:
                refs_create.add(req_entity(e, ov_of(e, op), e)["id"])
        if "patch" in ops:
            refs_update.add(req_entity(e, ov_of(e, "patch"), e)["id"])
        if "list" in ops:
            r = resp_entity(e, ov_of(e, "list"), e)
            if r:
                (refs_full if as_array(ov_of(e, "list")) else refs_page).add(r["id"])
        for op in ("create", "get", "update", "patch"):
            if op in ops:
                r = resp_entity(e, ov_of(e, op), e)
                if r:
                    refs_full.add(r["id"])
        for act in e.get("actions", []):
            t, variant = action_request(e, act)
            if t:
                {"create": refs_create, "update": refs_update}.get(variant, refs_full).add(t["id"])
            r = action_response(e, act)
            if r:
                (refs_page if act.get("response_mode") == "page" else refs_full).add(r["id"])
        for nav in navs_with_target(m, e):
            t, sov = nav["target"], nav.get("sub_overrides") or {}
            for op in nav.get("sub_ops") or []:
                if op == "list" and nav["many"]:
                    r = resp_entity(e, sov.get(op) or {}, t)
                    if r:
                        (refs_full if as_array(sov.get(op) or {}) else refs_page).add(r["id"])
                elif op in ("create", "update"):
                    refs_create.add(req_entity(e, sov.get(op) or {}, t)["id"])
                    r = resp_entity(e, sov.get(op) or {}, t)
                    if r:
                        refs_full.add(r["id"])
                elif op == "get":
                    refs_full.add(t["id"])
    # POST, PUT and PATCH bodies can include embedded children marked as accepted in requests
    grew = True
    while grew:
        grew = False
        for eid in list(refs_create | refs_update):
            for nav in navs_with_target(m, ents[eid]):
                if nav["expose"] == "embedded" and nav.get("nested_create"):
                    tid = nav["target"]["id"]
                    if tid not in refs_create:
                        refs_create.add(tid)
                        grew = True
                    if eid in refs_update and not nav["many"] and tid not in refs_update:
                        refs_update.add(tid)
                        grew = True

    # schemas needed: namespace entities, everything operations refer to, and what those embed
    needed: List[str] = []

    def need(e: Dict[str, Any]) -> None:
        if e["id"] in needed:
            return
        needed.append(e["id"])
        for nav in navs_with_target(m, e):
            if nav["expose"] in ("embedded", "subresource"):
                need(nav["target"])

    for e in members:
        need(e)
    for eid in sorted(refs_create | refs_update | refs_page | refs_full):
        if eid in ents:
            need(ents[eid])

    schemas: Dict[str, Any] = {}
    paths: Dict[str, Any] = {}
    tags: List[Dict[str, Any]] = []
    for e in members:
        tags.append(dict({"name": e["business_name"], "description": e["description"] or e["business_name"],
                          "x-api-resource": e["api_name"]}, **({"x-group": e["group"]} if e.get("group") else {})))

    # -- schemas
    for eid in needed:
        e = ents[eid]
        props: Dict[str, Any] = {}
        required: List[str] = []
        for a in e["attributes"]:
            props[a["api_name"]] = attr_schema(a, source_of(m, e), e)
            drv, other = derived_from(e, a)
            if drv:
                props[a["api_name"]]["x-derived"] = True
                if other:
                    props[a["api_name"]]["x-derived-from"] = other
            if a["required"] or a["key"]:
                required.append(a["api_name"])
        props, required = group_properties(e, props, required, "full")
        for nav in navs_with_target(m, e):
            t = nav["target"]
            desc = nav["business_name"] + " (" + t["business_name"] + ")."
            if nav["expose"] == "link":
                item = {"$ref": "#/components/schemas/ResourceLink"}
            elif nav["expose"] == "embedded":
                item = {"$ref": "#/components/schemas/" + t["schema_name"]}
            else:
                continue
            ext = {"x-business-name": nav["business_name"], "x-linked-entity": t["business_name"],
                   "x-cardinality": nav["cardinality"]}
            if nav["rel"]["source_link"]:
                ext["x-source-link"] = nav["rel"]["source_link"]
            if nav["expose"] == "link" and nav["side"] == "from" and nav.get("read_only"):
                ext["readOnly"] = True
            if nav["many"]:
                props[nav["api_name"]] = dict({"type": "array", "description": desc, "items": item}, **ext)
            else:
                props[nav["api_name"]] = dict({"description": desc, "allOf": [item]}, **ext)
        sch: Dict[str, Any] = {"type": "object", "title": e["business_name"],
                               "description": e["description"] or e["business_name"] + ".",
                               "properties": props}
        if required:
            sch["required"] = required
        sch["x-business-name"] = e["business_name"]
        if e.get("tags"):
            sch["x-tags"] = list(e["tags"])
        src = source_of(m, e)
        if src:
            sch["x-source-system"] = src["name"]
            sch["x-source-system-type"] = src["kind"]
        if e["source_object"]:
            sch["x-source-object"] = e["source_object"]
        if e["source_io"]:
            sch[ext_name(src, "container")] = e["source_io"]
        if variants.has_variants(e):
            sch["x-source-variants"] = variants.entity_extension(e, src)
        sch["example"] = example_object(m, e)
        schemas[e["schema_name"]] = sch

        if eid in refs_create or eid in refs_update:
            # POST and PUT take XCreate: no read-only or system columns, plus nested children.
            # PATCH takes XUpdate: no read-only columns; system columns allowed.
            iprops: Dict[str, Any] = {}
            uprops: Dict[str, Any] = {}
            ireq: List[str] = []
            for a in e["attributes"]:
                if a["read_only"]:
                    continue
                uprops[a["api_name"]] = attr_schema(a, source_of(m, e), e)
                if a.get("system"):
                    continue
                iprops[a["api_name"]] = attr_schema(a, source_of(m, e), e)
                if a["required"]:
                    ireq.append(a["api_name"])
            iprops, ireq = group_properties(e, iprops, ireq, "create")
            uprops, _ = group_properties(e, uprops, [], "update")
            for nav in navs_with_target(m, e):
                if nav["expose"] == "link" and nav["side"] == "from" and not nav.get("read_only"):
                    ref_ = {"$ref": "#/components/schemas/ResourceRef"}
                    iprops[nav["api_name"]] = ({"type": "array", "items": ref_} if nav["many"]
                                               else {"allOf": [ref_], "description": nav["business_name"] + "."})
                    uprops[nav["api_name"]] = json.loads(json.dumps(iprops[nav["api_name"]]))
                elif nav["expose"] == "embedded" and nav.get("nested_create"):
                    tsn = nav["target"]["schema_name"]
                    child = {"$ref": "#/components/schemas/" + tsn + "Create"}
                    iprops[nav["api_name"]] = ({"type": "array", "items": child,
                                                "description": nav["business_name"] + ", sent with it."}
                                               if nav["many"] else {"allOf": [child],
                                                                    "description": nav["business_name"] + ", sent with it."})
                    uprops[nav["api_name"]] = ({"type": "array", "items": child,
                                                "description": nav["business_name"] + ". When sent, replaces the whole list."}
                                               if nav["many"] else
                                               {"allOf": [{"$ref": "#/components/schemas/" + tsn + "Update"}],
                                                "description": nav["business_name"] + ": only the fields to change."})
            create = {"type": "object", "title": e["business_name"] + " (create)",
                      "description": "Fields accepted when creating or replacing a " + e["business_name"].lower() + ".",
                      "properties": iprops}
            if ireq:
                create["required"] = ireq
            update = {"type": "object", "title": e["business_name"] + " (update)",
                      "description": "Fields that can be changed. Send only the fields to change (JSON merge patch).",
                      "properties": uprops, "minProperties": 1}
            schemas[e["schema_name"] + "Create"] = create
            schemas[e["schema_name"] + "Update"] = update
        if eid in refs_page:
            schemas[e["schema_name"] + "Page"] = {
                "type": "object", "description": "A page of " + e["business_name"].lower() + " records.",
                "properties": {"items": {"type": "array", "items": {"$ref": "#/components/schemas/" + e["schema_name"]}},
                               "nextCursor": {"type": "string", "nullable": True,
                                              "description": "Pass as `cursor` to get the next page; null on the last page."}},
                "required": ["items"]}

    def ref(name: str) -> Dict[str, str]:
        return {"$ref": "#/components/schemas/" + name}

    def json_body(schema: Dict[str, Any], media: str = "application/json") -> Dict[str, Any]:
        return {"content": {media: {"schema": schema}}}

    def err(*names: str) -> Dict[str, Any]:
        codes = {"BadRequest": "400", "NotFound": "404", "Conflict": "409",
                 "PreconditionFailed": "412", "ValidationFailed": "422"}
        return {codes[x]: {"$ref": "#/components/responses/" + x} for x in names if x in codes}

    paging_params = [
        {"name": "limit", "in": "query", "required": False, "description": "Maximum records to return.",
         "schema": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}},
        {"name": "cursor", "in": "query", "required": False, "description": "Cursor from the previous page.",
         "schema": {"type": "string"}},
    ]
    etag_hdr = {"ETag": {"description": "Version of the resource; send as If-Match when changing it.",
                         "schema": {"type": "string"}}}
    if_match = {"name": "If-Match", "in": "header", "required": True,
                "description": "ETag from the last read, to prevent lost updates.", "schema": {"type": "string"}}

    def path_param(a: Dict[str, Any]) -> Dict[str, Any]:
        ps = {x: y for x, y in attr_schema(a).items() if x in ("type", "format", "enum")}
        if a["type"] not in ("integer", "enum"):
            ps = {"type": "string"}
        if (a.get("pattern") or "").strip() and a["type"] == "string":
            ps["pattern"] = a["pattern"].strip()
        p_ = {"name": a["api_name"], "in": "path", "required": True, "description": a["business_name"] + ".", "schema": ps}
        ex = typed_example(a)
        if ex is not None:
            p_["example"] = ex
        return p_

    attr_index = {a["id"]: (ent_, a) for ent_ in m["entities"] for a in ent_["attributes"]}

    def query_param(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        """OpenAPI parameter(s) for one column: a query filter (equals, in, range, contains), or on
        a write a value sent as a query or header parameter instead of in the body. Each carries
        the column's description and example."""
        hit = attr_index.get(spec.get("attr_id", ""))
        if not hit:
            return []
        ent_, a = hit
        name = spec.get("name") or a["api_name"]
        match = spec.get("match") or "equals"
        req = bool(spec.get("required"))
        write = bool(spec.get("_write"))
        base = {x: y for x, y in attr_schema(a).items() if x in ("type", "format", "enum", "maxLength", "pattern")}
        if is_money(a):
            base = money_scalar(a)
        bn_ = " ".join(w if (w.isupper() and len(w) > 1) else w.lower() for w in a["business_name"].split())
        Bn = bn_[:1].upper() + bn_[1:]
        more = (" " + a["description"].strip()) if (a.get("description") or "").strip() else ""
        if more and not more.rstrip().endswith("."):
            more += "."
        if is_money(a):
            more += " Amount in pence, " + MONEY["currency"] + "." if a["type"] == "integer" else " Amount in pounds."
        ex = param_example(a)
        ext = {"x-business-name": a["business_name"]}
        if not write and spec.get("location") != "header":
            ext["x-filter-match"] = match
        if a.get("source_field"):
            ext["x-source-field"] = a["source_field"]
        if a.get("dd_reference"):
            ext["x-dd-attribute"] = a["dd_reference"]

        def one(nm: str, desc: str, schema: Dict[str, Any], example: Any, loc: str = "query",
                extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            p_ = dict({"name": nm, "in": loc, "required": req, "description": desc + more, "schema": schema}, **(extra or {}))
            if example is not None:
                p_["example"] = example
            return dict(p_, **ext)

        if spec.get("location") == "header" or write:
            loc = "header" if spec.get("location") == "header" else "query"
            desc = Bn + "." + (" Sent here, not in the request body." if write else "")
            return [one(name, desc, base, ex, loc)]
        if match == "in":
            return [one(name, "One or more " + bn_ + " values, comma separated.", {"type": "array", "items": base},
                        [ex] if ex is not None else None, extra={"style": "form", "explode": False})]
        if match == "range":
            return [one(name + "From", "Only records whose " + bn_ + " is on or after this.", base, ex),
                    one(name + "To", "Only records whose " + bn_ + " is on or before this.", base, ex)]
        if match == "contains":
            return [one(name, "Only records whose " + bn_ + " contains this text (case-insensitive).", {"type": "string"},
                        ex if isinstance(ex, str) else None)]
        return [one(name, ("Required: " if req else "Filter by ") + bn_ + ".", base, ex)]

    def param_example(a: Dict[str, Any]) -> Any:
        """The column's example; without one, a sample by type (enum: its first code)."""
        ex = typed_example(a)
        if isinstance(ex, dict) and "amount" in ex:
            return ex["amount"]
        if ex is not None:
            return ex
        t = a["type"]
        if is_money(a):
            return 2999 if t == "integer" else 29.99
        if t == "enum" and a["enum"]:
            return a["enum"][0]["code"]
        return {"date": "2026-01-31", "date-time": "2026-01-31T09:30:00Z", "integer": 1, "number": 1.5,
                "boolean": True}.get(t)

    def column_filters(t: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [{"attr_id": a["id"], "required": bool(a.get("filter_required")), "match": a.get("filter_match") or "equals"}
                for a in t["attributes"] if a["filterable"]]

    def qparams(ov: Dict[str, Any], t: Dict[str, Any], is_list: bool) -> List[Dict[str, Any]]:
        """Query parameters of a GET: the operation's own list if it has one, otherwise the
        listed entity's column filters (lists) or none (single records)."""
        specs = ov.get("query_params")
        if specs is None:
            specs = column_filters(t) if is_list else []
        out_: List[Dict[str, Any]] = []
        for sp in specs:
            out_.extend(query_param(sp))
        out_.sort(key=lambda p_: not p_["required"])   # required ones first
        return uniq(out_)

    def uniq(params: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Drop repeated query parameters (same name): the first one wins."""
        seen, out_ = set(), []
        for p_ in params:
            nm = (p_.get("name"), p_.get("in")) if "name" in p_ else id(p_)
            if nm in seen:
                continue
            seen.add(nm)
            out_.append(p_)
        return out_

    def with_params(op: Dict[str, Any], params: List[Dict[str, Any]]) -> Dict[str, Any]:
        if params:
            op["parameters"] = uniq(params + op.get("parameters", []))
        return op

    def key_query(t: Dict[str, Any], required: bool) -> List[Dict[str, Any]]:
        kk = next((a for a in t["attributes"] if a["key"]), None)
        if not kk:
            return []
        q = query_param({"attr_id": kk["id"], "required": required, "match": "equals"})
        q[0]["description"] = (kk["business_name"] + " of the " + t["business_name"].lower() + " to return."
                               + ("" if required else " With it, the page holds at most that one record."))
        return q

    def list_params(ov: Dict[str, Any], t: Dict[str, Any]) -> List[Dict[str, Any]]:
        """A GET list's query parameters. When the entity gets one record by query and
        also has a list, the list takes the key as a filter: GET /things?thingId=..."""
        ps = qparams(ov, t, True)
        if t.get("get_by") == "query" and "get" in t["operations"]:
            kq = key_query(t, False)
            if kq and not any(p_["name"] == kq[0]["name"] for p_ in ps):
                ps = [p_ for p_ in ps if p_["required"]] + kq + [p_ for p_ in ps if not p_["required"]]
        return ps

    taken: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
    literal: Dict[str, Tuple[str, Optional[Dict[str, Any]]]] = {}

    def add(path: str, method: str, op: Dict[str, Any], params: Optional[List[Dict[str, Any]]] = None,
            src: Optional[Dict[str, Any]] = None) -> None:
        # two settings generating the same method and path: the first one wins, the other is
        # reported (path_conflicts) instead of silently replacing it
        # /x/{a} and /x/{b} are one path to OpenAPI and to a client: the first spelling wins the whole path
        np_ = re.sub(r"\{[^}]+\}", "{}", path)
        first = literal.setdefault(np_, (path, src))
        clash = (np_, method) in taken or first[0] != path
        if clash:
            if trace is not None and src is not None:
                trace.append(dict(src, path=path, method=method.upper(), status="", dropped=True,
                                  kept=taken.get((np_, method), first[1])))
            return
        taken[(np_, method)] = src
        item = paths.setdefault(path, {})
        if params and "parameters" not in item:
            item["parameters"] = params
        item[method] = op
        if trace is not None and src is not None:
            status = next((c for c in op["responses"] if c.startswith("2")), "")
            trace.append(dict(src, path=path, method=method.upper(), status=status))

    def list_response(e: Dict[str, Any], ov: Dict[str, Any], default: Dict[str, Any], what: str) -> Dict[str, Any]:
        """200 response of a GET list: a page ({items, nextCursor}) or a plain array of records."""
        if not as_array(ov):
            return respond(e, ov, "200", resp_name(e, ov, default, True), "A page of " + what + ".")
        r = resp_entity(e, ov, default)
        st = ov.get("status") or "200"
        body: Dict[str, Any] = {"description": "All " + what + " that match the query (no paging)."}
        if r and st != "204":
            if list_shape(ov) == "wrapped":
                body.update(json_body(ref(wrapper_schema(r, ov.get("list_property") or r["api_name"], what))))
            else:
                body.update(json_body({"type": "array", "items": ref(r["schema_name"])}))
        return {st: body}

    def wrapper_schema(r: Dict[str, Any], prop: str, what: str) -> str:
        """{ <prop>: [ records ] }, named <Schema>List (or <Schema><Prop>List for another property name)."""
        name = r["schema_name"] + "List"
        if prop != r["api_name"]:
            name = r["schema_name"] + naming.pascal(prop) + "List"
        schemas[name] = {"type": "object", "title": r["business_name"] + " list",
                         "description": "All " + what + " that match the query, under " + prop + ".",
                         "properties": {prop: {"type": "array", "items": ref(r["schema_name"])}},
                         "required": [prop], "x-business-name": r["business_name"], "x-response-of": r["schema_name"]}
        return name

    def list_paging(ov: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [] if as_array(ov) else paging_params

    def respond(owner: Dict[str, Any], ov: Dict[str, Any], status: str, schema: Optional[str], desc: str,
                location: bool = False) -> Dict[str, Any]:
        """Success response, applying the operation's override (status and body)."""
        st = ov.get("status") or status
        r: Dict[str, Any] = {"description": desc}
        headers: Dict[str, Any] = {}
        if st == "201" and location:
            headers["Location"] = {"description": "Path of the new resource.", "schema": {"type": "string"}}
        if schema and st != "204":
            r.update(json_body(ref(schema)))
            if not schema.endswith("Page"):
                headers.update(etag_hdr)
        if headers:
            r["headers"] = headers
        if st == "204":
            r["description"] = desc if desc != "Created." else "Done."
        return {st: r}

    ents_by_schema = {x["schema_name"]: x for x in m["entities"]}

    def params_not_body(op: Dict[str, Any], specs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """On POST/PUT/PATCH/DELETE: the chosen columns become query or header parameters, and
        are taken out of the request body (a copy of the body schema, <OperationId>Request)."""
        if not specs:
            return op
        with_params(op, qparams({"query_params": [dict(sp, match="equals", _write=True) for sp in specs]},
                                {"attributes": []}, False))
        for c in (op.get("requestBody") or {}).get("content", {}).values():
            base_name = c["schema"].get("$ref", "").split("/")[-1]
            base = schemas.get(base_name)
            owner = ents_by_schema.get(re.sub(r"(Create|Update)$", "", base_name))
            if not base or not owner:
                continue
            ids = {sp.get("attr_id") for sp in specs}

            def holders(sch: Dict[str, Any]) -> List[Dict[str, Any]]:
                """The object schemas columns can sit in: the body, and its column groups."""
                out_ = [sch]
                for pv in sch.get("properties", {}).values():
                    if pv.get("x-column-group"):
                        out_.append(pv["items"] if pv.get("type") == "array" else pv)
                return out_
            names_ = {a["api_name"] for a in owner["attributes"] if a["id"] in ids}
            drop = [n for h_ in holders(base) for n in h_.get("properties", {}) if n in names_]
            if not drop:
                continue
            new = json.loads(json.dumps(base))
            for h_ in holders(new):
                for d in drop:
                    h_.get("properties", {}).pop(d, None)
                if h_.get("required"):
                    h_["required"] = [x for x in h_["required"] if x not in drop]
                    if not h_["required"]:
                        h_.pop("required")
            new["description"] = (new.get("description", "") + " " + ", ".join(drop)
                                  + (" is" if len(drop) == 1 else " are") + " sent as a parameter, not in the body.").strip()
            new["x-request-of"] = base_name
            name = naming.pascal(op["operationId"]) + "Request"
            schemas[name] = new
            c["schema"] = ref(name)
        return op

    def request_fields_schema(ent: Dict[str, Any], field_ids: List[str], required_ids: List[str], op_id: str) -> str:
        """A request body of only the chosen columns, e.g. a withdrawal with just a reason:
        schema <OperationId>Request."""
        want, req_ids = set(field_ids), set(required_ids)
        attrs = [a for a in ent["attributes"] if a["id"] in want]
        props = {a["api_name"]: attr_schema(a, source_of(m, ent), ent) for a in attrs}
        req_ = [a["api_name"] for a in attrs if a["id"] in req_ids]
        props, req_ = group_properties(ent, props, req_, "create")
        name = naming.pascal(op_id) + "Request"
        sch: Dict[str, Any] = {"type": "object", "title": ent["business_name"] + " (selected fields)",
                               "description": "Fields sent in this request: " + ", ".join(a["api_name"] for a in attrs) + ".",
                               "properties": props, "x-request-of": ent["schema_name"]}
        if req_:
            sch["required"] = req_
        ex = group_example(ent, {a["api_name"]: typed_example(a) for a in attrs if typed_example(a) is not None})
        if ex:
            sch["example"] = ex
        schemas[name] = sch
        return name

    def described(op: Dict[str, Any], ov: Dict[str, Any]) -> Dict[str, Any]:
        if ov.get("operation_id"):
            op["operationId"] = ov["operation_id"]
        if ov.get("request_mode") == "fields" and "requestBody" in op:
            for c in op["requestBody"]["content"].values():
                base = re.sub(r"(Create|Update)$", "", c["schema"].get("$ref", "").split("/")[-1])
                ent = ents_by_schema.get(base)
                if ent:
                    c["schema"] = ref(request_fields_schema(ent, ov.get("request_fields") or [],
                                                            ov.get("request_required") or [], op["operationId"]))
        if ("requestBody" in op or op.get("operationId", "").startswith("delete")) and ov.get("query_params"):
            params_not_body(op, ov["query_params"])
        if ov.get("summary"):
            op["summary"] = ov["summary"]
        if ov.get("description"):
            op["description"] = ov["description"]
        return op

    post_default = (m.get("settings") or {}).get("post_response") or "key"
    put_default = (m.get("settings") or {}).get("put_response") or "full"

    def content(mode: str, fields: List[str], ent: Optional[Dict[str, Any]], op_id: str) -> Optional[str]:
        """Schema name for a response body: the whole record, only its key (the row id of the
        record), chosen fields, or none. Partial bodies get their own schema, <OperationId>Response."""
        if ent is None or mode == "none":
            return None
        if mode in ("", "full"):
            return ent["schema_name"]
        if mode == "key":
            attrs = [a for a in ent["attributes"] if a["key"]]
            title, desc = (ent["business_name"] + " (key)",
                           "The identifier (row id) of the " + ent["business_name"].lower() + ".")
        else:
            wanted = set(fields)
            attrs = [a for a in ent["attributes"] if a["id"] in wanted]
            title, desc = (ent["business_name"] + " (selected fields)",
                           "Selected fields of the " + ent["business_name"].lower() + ".")
        if not attrs:
            return ent["schema_name"]
        name = naming.pascal(op_id) + "Response"
        props = {a["api_name"]: attr_schema(a, source_of(m, ent), ent) for a in attrs}
        req_ = [a["api_name"] for a in attrs if a["key"] or a["required"]]
        props, req_ = group_properties(ent, props, req_, "full")
        ex = group_example(ent, {a["api_name"]: typed_example(a) for a in attrs if typed_example(a) is not None})
        sch = {"type": "object", "title": title, "description": desc, "properties": props,
               "required": req_,
               "x-business-name": ent["business_name"], "x-response-of": ent["schema_name"]}
        if not sch["required"]:
            sch.pop("required")
        if ex:
            sch["example"] = ex
        schemas[name] = sch
        return name

    def resp_body(ov: Dict[str, Any], default: Dict[str, Any], op_id: str, default_mode: str) -> Optional[str]:
        ent = resp_entity({}, ov, default)
        mode = ov.get("response_mode") or ""
        if ov.get("response_entity_id") == "none":
            mode = "none"
        return content(mode or default_mode, ov.get("response_fields") or [], ent, op_id)

    def resp_name(e: Dict[str, Any], ov: Dict[str, Any], default: Dict[str, Any], page: bool = False) -> Optional[str]:
        r = resp_entity(e, ov, default)
        return (r["schema_name"] + ("Page" if page else "")) if r else None

    for e in members:
        S = e["schema_name"]
        tag = [e["business_name"]]
        coll = resource_path(n, e, in_paths)
        k = key_attr(e)
        bn = e["business_name"].lower()
        ops = e["operations"]
        if "list" in ops:
            ov = ov_of(e, "list")
            add(coll, "get", described({
                "tags": tag, "operationId": "list" + naming.pascal(e["plural"]), "summary": "List " + naming.plural(bn),
                "parameters": list_params(ov, e) + list_paging(ov),
                "responses": dict(list_response(e, ov, e, naming.plural(bn)),
                                  **err("BadRequest"))}, ov), src={"kind": "operation", "entity_id": e["id"], "op": "list"})
        if "create" in ops:
            ov = ov_of(e, "create")
            add(coll, "post", described({
                "tags": tag, "operationId": "create" + S, "summary": "Create a " + bn,
                "requestBody": dict({"required": True}, **json_body(ref(req_entity(e, ov, e)["schema_name"] + "Create"))),
                "responses": dict(respond(e, ov, "201", resp_body(ov, e, "create" + S, post_default), "Created.", location=True),
                                  **err("BadRequest", "Conflict", "ValidationFailed"))}, ov), src={"kind": "operation", "entity_id": e["id"], "op": "create"})
        pk = path_param(k) if k else None
        # get one by query: GET /things?thingId=... (through the list when List is also ticked)
        by_query = e.get("get_by") == "query" and k is not None
        if by_query and "get" in ops and "list" not in ops:
            ov = ov_of(e, "get")
            key_q = key_query(e, True)
            add(coll, "get", described(with_params({
                "tags": tag, "operationId": "get" + S, "summary": "Get a " + bn,
                "responses": dict(respond(e, ov, "200", resp_body(ov, e, "get" + S, "full"), "The " + bn + "."),
                                  **err("BadRequest", "NotFound"))}, key_q + qparams(ov, e, False)), ov),
                src={"kind": "operation", "entity_id": e["id"], "op": "get"})
        if k and any(o in ops for o in ("get", "update", "patch", "delete")):
            item_path = coll + "/{" + k["api_name"] + "}"
            if "get" in ops and not by_query:
                ov = ov_of(e, "get")
                add(item_path, "get", described(with_params({
                    "tags": tag, "operationId": "get" + S, "summary": "Get a " + bn,
                    "responses": dict(respond(e, ov, "200", resp_body(ov, e, "get" + S, "full"), "The " + bn + "."), **err("NotFound"))},
                    qparams(ov, e, False)), ov),
                    [pk], src={"kind": "operation", "entity_id": e["id"], "op": "get"})
            if "update" in ops:
                ov = ov_of(e, "update")
                add(item_path, "put", described({
                    "tags": tag, "operationId": "replace" + S, "summary": "Replace a " + bn, "parameters": [if_match],
                    "requestBody": dict({"required": True}, **json_body(ref(req_entity(e, ov, e)["schema_name"] + "Create"))),
                    "responses": dict(respond(e, ov, "200", resp_body(ov, e, "replace" + S, put_default), "Replaced."),
                                      **err("BadRequest", "NotFound", "Conflict", "PreconditionFailed", "ValidationFailed"))}, ov),
                    [pk], src={"kind": "operation", "entity_id": e["id"], "op": "update"})
            if "patch" in ops:
                ov = ov_of(e, "patch")
                add(item_path, "patch", described({
                    "tags": tag, "operationId": "update" + S, "summary": "Update a " + bn, "parameters": [if_match],
                    "requestBody": dict({"required": True}, **json_body(ref(req_entity(e, ov, e)["schema_name"] + "Update"),
                                                                       "application/merge-patch+json")),
                    "responses": dict(respond(e, ov, "200", resp_body(ov, e, "update" + S, "full"), "Updated."),
                                      **err("BadRequest", "NotFound", "Conflict", "PreconditionFailed", "ValidationFailed"))}, ov),
                    [pk], src={"kind": "operation", "entity_id": e["id"], "op": "patch"})
            if "delete" in ops:
                ov = ov_of(e, "delete")
                add(item_path, "delete", described({
                    "tags": tag, "operationId": "delete" + S, "summary": "Delete a " + bn, "parameters": [if_match],
                    "responses": dict(respond(e, dict(ov, status=ov.get("status") or "204"), "204",
                                              resp_name(e, dict(ov, response_entity_id=ov.get("response_entity_id") or "none"), e),
                                              "Deleted."),
                                      **err("NotFound", "PreconditionFailed"))}, ov),
                    [pk], src={"kind": "operation", "entity_id": e["id"], "op": "delete"})

        # sub-resources under one record: list/create on the collection, get/replace/delete by key
        for nav in navs_with_target(m, e):
            sub_ops = nav.get("sub_ops") or []
            if not sub_ops or not k:
                continue
            t, sov = nav["target"], nav.get("sub_overrides") or {}
            P = naming.pascal(nav["api_name"])
            base = coll + "/{" + k["api_name"] + "}/" + naming.kebab(nav["api_name"])
            ext = {"x-business-name": nav["business_name"], "x-linked-entity": t["business_name"],
                   "x-cardinality": nav["cardinality"]}
            if nav["rel"]["source_link"]:
                ext["x-source-link"] = nav["rel"]["source_link"]
            what = nav["business_name"][:1].lower() + nav["business_name"][1:]
            sub_src = {"kind": "sub-resource", "entity_id": e["id"], "rel_id": nav["rel"]["id"], "side": nav["side"],
                       "link": nav["api_name"]}

            def sub_op(op_id: str, summary: str, ov: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
                return described(dict({"tags": tag, "operationId": op_id, "summary": summary}, **extra, **ext), ov)

            if nav["many"]:
                if "list" in sub_ops:
                    ov = sov.get("list") or {}
                    add(base, "get", sub_op("get" + S + P, nav["business_name"] + " of a " + bn, ov, {
                        "parameters": qparams(ov, t, True) + list_paging(ov),
                        "responses": dict(list_response(e, ov, t, nav["business_name"].lower()),
                                          **err("NotFound"))}), [pk], src=dict(sub_src, op="list"))
                if "create" in sub_ops:
                    ov = sov.get("create") or {}
                    add(base, "post", sub_op("create" + S + P, "Add " + what + " to a " + bn, ov, {
                        "requestBody": dict({"required": True}, **json_body(ref(req_entity(e, ov, t)["schema_name"] + "Create"))),
                        "responses": dict(respond(e, ov, "201", resp_body(ov, t, "create" + S + P, post_default), "Created.", location=True),
                                          **err("BadRequest", "NotFound", "Conflict", "ValidationFailed"))}), [pk], src=dict(sub_src, op="create"))
                skey = next((a for a in t["attributes"] if a["id"] == nav.get("sub_key")), None) or key_attr(t)
                if skey and any(o in sub_ops for o in ("get", "update", "delete")):
                    ip = base + "/{" + skey["api_name"] + "}"
                    by = "By" + naming.pascal(skey["api_name"])
                    params = [pk, path_param(skey)]
                    if "get" in sub_ops:
                        ov = sov.get("get") or {}
                        add(ip, "get", with_params(sub_op("get" + S + P + by, "Get one of the " + what + " of a " + bn, ov, {
                            "responses": dict(respond(e, ov, "200", resp_body(ov, t, "get" + S + P + by, "full"), "Found."), **err("NotFound"))}),
                            qparams(ov, t, False)), params, src=dict(sub_src, op="get"))
                    if "update" in sub_ops:
                        ov = sov.get("update") or {}
                        add(ip, "put", sub_op("replace" + S + P + by, "Replace one of the " + what + " of a " + bn, ov, {
                            "parameters": [if_match],
                            "requestBody": dict({"required": True}, **json_body(ref(req_entity(e, ov, t)["schema_name"] + "Create"))),
                            "responses": dict(respond(e, ov, "200", resp_body(ov, t, "replace" + S + P + by, put_default), "Replaced."),
                                              **err("BadRequest", "NotFound", "Conflict", "PreconditionFailed", "ValidationFailed"))}),
                            params, src=dict(sub_src, op="update"))
                    if "delete" in sub_ops:
                        ov = sov.get("delete") or {}
                        add(ip, "delete", sub_op("delete" + S + P + by, "Remove one of the " + what + " of a " + bn, ov, {
                            "parameters": [if_match],
                            "responses": dict(respond(e, dict(ov, status=ov.get("status") or "204"), "204", None, "Removed."),
                                              **err("NotFound", "PreconditionFailed"))}), params, src=dict(sub_src, op="delete"))
            else:
                if "get" in sub_ops:
                    ov = sov.get("get") or {}
                    add(base, "get", with_params(sub_op("get" + S + P, nav["business_name"] + " of a " + bn, ov, {
                        "responses": dict(respond(e, ov, "200", resp_body(ov, t, "get" + S + P, "full"), nav["business_name"] + "."),
                                          **err("NotFound"))}), qparams(ov, t, False)), [pk], src=dict(sub_src, op="get"))
                if "update" in sub_ops:
                    ov = sov.get("update") or {}
                    add(base, "put", sub_op("replace" + S + P, "Replace the " + what + " of a " + bn, ov, {
                        "parameters": [if_match],
                        "requestBody": dict({"required": True}, **json_body(ref(req_entity(e, ov, t)["schema_name"] + "Create"))),
                        "responses": dict(respond(e, ov, "200", resp_body(ov, t, "replace" + S + P, put_default), "Replaced."),
                                          **err("BadRequest", "NotFound", "Conflict", "PreconditionFailed", "ValidationFailed"))}),
                        [pk], src=dict(sub_src, op="update"))
                if "delete" in sub_ops:
                    ov = sov.get("delete") or {}
                    add(base, "delete", sub_op("delete" + S + P, "Remove the " + what + " of a " + bn, ov, {
                        "parameters": [if_match],
                        "responses": dict(respond(e, dict(ov, status=ov.get("status") or "204"), "204", None, "Removed."),
                                          **err("NotFound", "PreconditionFailed"))}), [pk], src=dict(sub_src, op="delete"))

        # actions: operations beyond create/read/update/delete
        for act in e.get("actions", []):
            if act.get("scope", "item") == "item":
                if not k:
                    continue
                apath, params = coll + "/{" + k["api_name"] + "}/" + act["path"], [pk]
            else:
                apath, params = coll + "/" + act["path"], None
            op = {"tags": tag, "operationId": act.get("operation_id") or naming.camel(S + " " + act["name"]),
                  "summary": act.get("summary") or (act["name"][:1].upper() + " ".join(naming.words(act["name"]))[1:]
                                                     + " for a " + bn),
                  "x-action": act["name"]}
            if act.get("description"):
                op["description"] = act["description"]
            if act.get("if_match"):
                op["parameters"] = [if_match]
            t, variant = action_request(e, act)
            errs = list(act.get("errors") or [])
            if t:
                suffix = {"create": "Create", "update": "Update"}.get(variant, "")
                media = "application/merge-patch+json" if variant == "update" else "application/json"
                body_name = (request_fields_schema(t, act.get("request_fields") or [], act.get("request_required") or [],
                                                   op["operationId"]) if variant == "fields" else t["schema_name"] + suffix)
                op["requestBody"] = dict({"required": True}, **json_body(ref(body_name), media))
                errs = ["BadRequest"] + errs
            if act.get("method", "POST") == "GET" or not t:
                with_params(op, qparams({"query_params": act.get("query_params") or []}, e, False))
            else:
                params_not_body(op, act.get("query_params") or [])
            if act.get("if_match") and "PreconditionFailed" not in errs:
                errs.append("PreconditionFailed")
            r = action_response(e, act)
            st = act.get("status") or "200"
            if r and act.get("response_mode") in ("page", "array", "wrapped"):
                # a list of records, e.g. GET /agreements/changes
                lov = {"response_mode": act["response_mode"], "list_property": act.get("list_property") or "", "status": st}
                if act["response_mode"] == "page":
                    op["parameters"] = op.get("parameters", []) + paging_params
                ok = list_response(e, lov, r, naming.plural(r["business_name"].lower()))
            else:
                body = content(act.get("response_mode") or "full", act.get("response_fields") or [], r,
                               naming.camel(S + " " + act["name"])) if r else None
                ok = respond(e, {}, st, body, "Done." if st != "202" else "Accepted; the change is applied asynchronously.",
                             location=st == "201")
            op["responses"] = dict(ok, **err(*[x for x in dict.fromkeys(errs)]))
            add(apath, act["method"].lower(), op, params,
                src={"kind": "action", "entity_id": e["id"], "action_id": act["id"], "op": act["name"]})

    # -- standard headers: request parameters and response headers on every matching operation
    hdr_params: Dict[str, Any] = {}
    hdr_resp: Dict[str, Any] = {}
    applicable = [hd for hd in m.get("headers", []) if not hd.get("namespace_ids") or ns_id in hd["namespace_ids"]]
    for hd in applicable:
        if hd["name"].lower() in IGNORED_HEADERS:
            continue
        cname = naming.pascal(hd["name"]) or "Header"
        sch_: Dict[str, Any] = {"type": hd.get("type") or "string"}
        if hd.get("format"):
            sch_["format"] = hd["format"]
        if hd.get("pattern"):
            sch_["pattern"] = hd["pattern"]
        if hd.get("direction", "request") in ("request", "both"):
            prm: Dict[str, Any] = {"name": hd["name"], "in": "header", "required": bool(hd.get("required")),
                                   "description": hd.get("description") or hd["name"], "schema": sch_}
            if hd.get("example"):
                prm["example"] = hd["example"]
            hdr_params[cname] = (hd, prm)
        if hd.get("direction", "request") in ("response", "both"):
            rh: Dict[str, Any] = {"description": hd.get("description") or hd["name"], "schema": sch_}
            if hd.get("example"):
                rh["example"] = hd["example"]
            if hd.get("required"):
                rh["required"] = True
            hdr_resp[cname] = (hd, rh)

    # every operation gets a description (style checkers such as Spectral expect one)
    for _path, item in paths.items():
        for method, op in item.items():
            if method == "parameters" or (op.get("description") or "").strip():
                continue
            op["description"] = default_description(method, op)

    # say in each operation's description which columns it takes as parameters
    for _path, item in paths.items():
        for method, op in item.items():
            if method == "parameters":
                continue
            cols = [p_ for p_ in op.get("parameters", []) if p_.get("x-business-name") and p_.get("in") in ("query", "header")]
            if not cols:
                continue
            def fmt(p_: Dict[str, Any]) -> str:
                ex_ = p_.get("example")
                ex_ = ",".join(str(x) for x in ex_) if isinstance(ex_, list) else ex_
                return "`" + p_["name"] + "`" + (" (required)" if p_.get("required") else "") + \
                    (", e.g. `" + str(ex_) + "`" if ex_ is not None else "")
            parts = []
            q_ = [fmt(p_) for p_ in cols if p_["in"] == "query"]
            h_ = [fmt(p_) for p_ in cols if p_["in"] == "header"]
            if q_:
                parts.append("Query parameters: " + "; ".join(q_) + ".")
            if h_:
                parts.append("Headers: " + "; ".join(h_) + ".")
            op["description"] = ((op.get("description") or "").rstrip() + "\n\n" + " ".join(parts)).strip()

    def applies(hd: Dict[str, Any], method: str) -> bool:
        return not hd.get("methods") or method.upper() in hd["methods"]

    for _path, item in paths.items():
        for method, op in item.items():
            if method == "parameters":
                continue
            refs = [{"$ref": "#/components/parameters/" + c} for c, (hd, _p) in hdr_params.items() if applies(hd, method)]
            if refs:
                op["parameters"] = refs + op.get("parameters", [])
            for code, resp in op["responses"].items():
                if "$ref" in resp:
                    continue
                hs = {hd["name"]: {"$ref": "#/components/headers/" + c} for c, (hd, _h) in hdr_resp.items()
                      if applies(hd, method)}
                if hs:
                    resp["headers"] = dict(hs, **resp.get("headers", {}))

    sec_schemes, sec_req = _security(n)
    servers = []
    for s in n["servers"]:
        url = s["url"].rstrip("/") + ("" if in_paths else n["base_path"])
        servers.append({"url": url, "description": s["description"]} if s["description"] else {"url": url})
    desc = n["description"] or n["title"]
    if n["security"] == "mutualTLS":
        desc += "\n\nClients authenticate with mutual TLS (client certificate)."
    info: Dict[str, Any] = {"title": n["title"], "version": n["version"], "description": desc}
    if n["owner"]:
        info["contact"] = {"name": n["owner"]}
    info["x-namespace"] = n["key"]
    info["x-base-path"] = n["base_path"]
    used = []
    for eid in needed:
        src = source_of(m, ents[eid])
        if src and src["name"] not in used:
            used.append(src["name"])
    if used:
        info["x-source-systems"] = used
    info["x-model-fingerprint"] = fingerprint(m, ns_id)
    info["x-generated-by"] = "api-definition-studio " + __version__

    all_schemas = _common_schemas()
    all_schemas.update(schemas)
    used_refs = json.dumps([paths, schemas])
    for name in ("ResourceLink", "ResourceRef"):
        if "#/components/schemas/" + name not in used_refs:
            all_schemas.pop(name)
    for kind in ("integer", "number"):
        if "#/components/schemas/" + MONEY[kind] + '"' in used_refs:
            all_schemas[MONEY[kind]] = money_schema(kind)
    doc: Dict[str, Any] = {"openapi": "3.0.3", "info": info}
    if servers:
        doc["servers"] = servers
    if sec_req:
        doc["security"] = sec_req
    doc["tags"] = tags
    # logical groups become tag groups (Redoc and similar tools show them as sections)
    grouped: Dict[str, List[str]] = {}
    for e in members:
        if e.get("group"):
            grouped.setdefault(e["group"], []).append(e["business_name"])
    if grouped:
        rest = [e["business_name"] for e in members if not e.get("group")]
        doc["x-tagGroups"] = [{"name": g, "tags": ts} for g, ts in grouped.items()] + \
                             ([{"name": "Other", "tags": rest}] if rest else [])
    doc["paths"] = paths
    problems = _problem_responses()
    shared = {hd["name"]: {"$ref": "#/components/headers/" + c} for c, (hd, _h) in hdr_resp.items() if not hd.get("methods")}
    if shared:
        for r_ in problems.values():
            r_["headers"] = dict(shared)
    comps: Dict[str, Any] = {"schemas": all_schemas, "responses": problems}
    if hdr_params:
        comps["parameters"] = {c: p_ for c, (_hd, p_) in hdr_params.items()}
    if hdr_resp:
        comps["headers"] = {c: h_ for c, (_hd, h_) in hdr_resp.items()}
    if sec_schemes:
        comps["securitySchemes"] = sec_schemes
    doc["components"] = comps
    prune_unused_schemas(doc)
    return doc


def fingerprint(m: Dict[str, Any], ns_id: str) -> str:
    """Hash of the parts of the model that shape this namespace's contract
    (ignores diagram positions), so a changed definition is easy to spot."""
    ents = [dict(e, x=None, y=None) for e in m["entities"]]
    part = {"ns": [n for n in m["namespaces"] if n["id"] == ns_id], "entities": ents,
            "relationships": m["relationships"]}
    return hashlib.sha256(json.dumps(part, sort_keys=True).encode()).hexdigest()[:12]


def endpoints(m: Dict[str, Any]) -> List[Dict[str, str]]:
    """Flat list of every generated operation, for the UI and the mapping sheet."""
    out = []
    for n in m["namespaces"]:
        trace: List[Dict[str, Any]] = []
        doc = build_openapi(m, n["id"], trace)
        source = {(t["path"], t["method"]): t for t in trace if not t.get("dropped")}
        prefix = "" if n.get("base_path_in", "paths") != "server" else n["base_path"]
        for path, item in doc["paths"].items():
            for method in ("get", "post", "put", "patch", "delete"):
                if method not in item:
                    continue
                op = item[method]
                req = ""
                rb = op.get("requestBody", {}).get("content", {})
                for media in rb.values():
                    req = media["schema"].get("$ref", "").split("/")[-1]
                resp = ""
                for code, r in op["responses"].items():
                    if code.startswith("2"):
                        for media in r.get("content", {}).values():
                            sch_ = media["schema"]
                            resp = (sch_.get("items", {}).get("$ref", "").split("/")[-1] + "[]"
                                    if sch_.get("type") == "array" else sch_.get("$ref", "").split("/")[-1])
                        break
                src = source.get((path, method.upper()), {})
                qnames, hnames = [], []
                for p_ in op.get("parameters", []):
                    if p_.get("in") == "query" and p_["name"] not in ("limit", "cursor"):
                        qnames.append(p_["name"] + ("*" if p_.get("required") else ""))
                    elif p_.get("in") == "header" and p_.get("x-business-name"):   # column headers, not standard ones
                        hnames.append(p_["name"] + ("*" if p_.get("required") else ""))
                out.append({"namespace": n["key"], "method": method.upper(), "path": prefix + path,
                            "operation_id": op["operationId"], "summary": op["summary"], "tag": op["tags"][0],
                            "request": req, "response": resp, "status": src.get("status", ""),
                            "kind": src.get("kind", ""), "entity_id": src.get("entity_id", ""),
                            "op": src.get("op", ""), "rel_id": src.get("rel_id", ""), "side": src.get("side", ""),
                            "link": src.get("link", ""), "action_id": src.get("action_id", ""),
                            "query": ", ".join(qnames), "header_params": ", ".join(hnames)})
    return out


def _what(m: Dict[str, Any], src: Optional[Dict[str, Any]]) -> str:
    """A setting that generates an operation, in words: 'Agreement Change's GET list'."""
    if not src:
        return "another setting"
    e = _ents(m).get(src.get("entity_id", ""), {})
    name = e.get("business_name", "?")
    kind, op = src.get("kind"), src.get("op", "")
    if kind == "action":
        return name + "'s action " + op
    if kind == "sub-resource":
        return name + "'s sub-resource " + op + (" (" + src.get("link", "") + ")" if src.get("link") else "")
    return name + "'s " + {"list": "GET list", "get": "GET one", "create": "POST", "update": "PUT", "patch": "PATCH",
                           "delete": "DELETE"}.get(op, op)


# Extensions that describe where data comes from. A namespace whose source mapping is not
# published (source_in_openapi = False), or ?view=contract, leaves them out, so consumers
# see only the API's own concepts.
SOURCE_EXTENSIONS = ("x-dd-attribute", "x-data-item", "x-transform", "x-system-column", "x-derived",
                     "x-derived-from")


def contract_only(doc: Any) -> Any:
    """A copy of an OpenAPI document without source-mapping extensions (x-source-*, DD
    attribute, data item, transformation, system column, derived)."""
    if isinstance(doc, dict):
        return {k: contract_only(v) for k, v in doc.items()
                if not (isinstance(k, str) and (k.startswith("x-source") or k in SOURCE_EXTENSIONS))}
    if isinstance(doc, list):
        return [contract_only(v) for v in doc]
    return doc


def published(m: Dict[str, Any], ns_id: str, view: str = "") -> Dict[str, Any]:
    """The OpenAPI for a namespace as published: with or without the source mapping, by the
    namespace's setting unless view is 'full' or 'contract'."""
    doc = build_openapi(m, ns_id)
    ns = next((n for n in m["namespaces"] if n["id"] == ns_id), {})
    strip = view == "contract" or (view != "full" and ns.get("source_in_openapi", True) is False)
    return contract_only(doc) if strip else doc


def path_conflicts(m: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Errors for operations left out because another setting already generates the same method and path,
    e.g. two entities with the path segment agreements both ticking List."""
    out: List[Dict[str, Any]] = []
    for n in m["namespaces"]:
        trace: List[Dict[str, Any]] = []
        try:
            build_openapi(m, n["id"], trace)
        except Exception:  # noqa: BLE001 - a broken model is reported by validate()
            continue
        prefix = n["base_path"] if n.get("base_path_in", "paths") == "server" else ""
        doc_ = build_openapi(m, n["id"])
        seen: Dict[str, str] = {}
        for path, item in doc_["paths"].items():
            for method, op in item.items():
                if method == "parameters":
                    continue
                oid = op.get("operationId", "")
                where = method.upper() + " " + prefix + path
                if oid in seen:
                    t = next((x for x in trace if x["path"] == path and x["method"] == method.upper()), {})
                    out.append({"level": "error", "object_id": t.get("action_id") or t.get("entity_id", ""),
                                "where": _ents(m).get(t.get("entity_id", ""), {}).get("business_name", "?"), "blocking": False,
                                "message": "Operation ID " + oid + " is used by both " + seen[oid] + " and " + where
                                + ". Operation IDs must be unique: change one in its Edit dialog."})
                else:
                    seen[oid] = where
        for t in trace:
            if not t.get("dropped"):
                continue
            e = _ents(m).get(t.get("entity_id", ""), {})
            out.append({"level": "error", "object_id": t.get("action_id") or t.get("entity_id", ""),
                        "where": e.get("business_name", "?"), "blocking": False,
                        "message": t["method"] + " " + prefix + t["path"] + " is already generated by " + _what(m, t.get("kept"))
                        + ", so " + _what(m, t) + " is left out. Give one of the entities a different path segment, "
                        "or untick one of the operations."})
    return out
