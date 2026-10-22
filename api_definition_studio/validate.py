"""Model checks. Errors stop a definition being generated correctly; warnings
are design advice (naming style, missing source mapping, leaky names)."""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List

from . import names as name_check, naming
import re

from .model import (ATTRIBUTE_TYPES, CARDINALITIES, ERROR_NAMES, EXPOSE, FILTER_MATCHES, GET_BY, HEADER_DIRECTIONS,
                    HEADER_FORMATS,
                    INVERSE_CARDINALITY, METHODS, OPERATIONS, REQUEST_VARIANTS, RESPONSE_MODES, SECURITY, STATUSES,
                    SUB_OPS, to_many)

HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
from .source_kinds import KINDS, kind_of

RESERVED = {"items", "nextCursor", "links", "_links", "href"}


def _issue(level: str, obj: str, where: str, msg: str, block: bool = False) -> Dict[str, Any]:
    """block=True marks naming errors an edit is refused for; other errors are
    allowed while a design is in progress and shown as issues."""
    return {"level": level, "object_id": obj, "where": where, "message": msg, "blocking": block}


def _example_ok(a: Dict[str, Any]) -> bool:
    ex = (a.get("example") or "").strip()
    if not ex:
        return True
    t = a["type"]
    try:
        if t == "integer" and a.get("money"):
            float(ex.replace(",", "").replace("£", ""))     # 29.99 (pounds) or 2999 (pence)
        elif t == "integer":
            int(ex)
        elif t == "number":
            float(ex.replace(",", "").replace("£", "") if a.get("money") else ex)
        elif t == "boolean":
            return ex.lower() in ("true", "false")
        elif t == "date":
            dt.date.fromisoformat(ex)
        elif t == "date-time":
            dt.datetime.fromisoformat(ex.replace("Z", "+00:00"))
        elif t == "enum":
            return ex in [v["code"] for v in a["enum"]]
    except ValueError:
        return False
    return True


def nav_properties(m: Dict[str, Any], entity_id: str) -> List[Dict[str, Any]]:
    """Navigation properties an entity gets from relationships (either end)."""
    out = []
    for r in m["relationships"]:
        if r["from_entity_id"] == entity_id and (r["expose"] != "none" or r.get("sub_ops")) and r["api_name"]:
            out.append({"rel": r, "side": "from", "api_name": r["api_name"], "expose": r["expose"],
                        "sub_ops": r.get("sub_ops") or [], "sub_key": r.get("sub_key", ""),
                        "sub_overrides": r.get("sub_overrides") or {},
                        "read_only": bool(r.get("read_only")), "nested_create": bool(r.get("nested_create"))})
        if r["to_entity_id"] == entity_id and (r["inverse_expose"] != "none" or r.get("inverse_sub_ops")) \
                and r["inverse_api_name"]:
            out.append({"rel": r, "side": "to", "api_name": r["inverse_api_name"], "expose": r["inverse_expose"],
                        "sub_ops": r.get("inverse_sub_ops") or [], "sub_key": r.get("inverse_sub_key", ""),
                        "sub_overrides": r.get("inverse_sub_overrides") or {},
                        "read_only": True, "nested_create": bool(r.get("inverse_nested_create"))})
    return out


def validate(m: Dict[str, Any]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    ns_ids = {n["id"] for n in m["namespaces"]}
    ents = {e["id"]: e for e in m["entities"]}
    systems = {s["id"]: s for s in m.get("source_systems", [])}

    hnames: Dict[str, str] = {}
    ns_all = {n["id"] for n in m["namespaces"]}
    for hd in m.get("headers", []):
        w = "Header " + hd["name"]
        if not HEADER_NAME_RE.match(hd["name"] or ""):
            issues.append(_issue("error", hd["id"], w, "Header names are letters, digits and '-', e.g. request-id.", True))
        low = (hd["name"] or "").lower()
        if low in hnames:
            issues.append(_issue("error", hd["id"], w, "Header " + hd["name"] + " is defined twice.", True))
        hnames[low] = hd["id"]
        if hd["direction"] not in HEADER_DIRECTIONS:
            issues.append(_issue("error", hd["id"], w, "Direction must be request, response or both.", True))
        if hd["type"] not in ("string", "integer"):
            issues.append(_issue("error", hd["id"], w, "Type must be string or integer.", True))
        if hd["format"] not in HEADER_FORMATS:
            issues.append(_issue("error", hd["id"], w, "Format must be blank, uuid, date-time or date.", True))
        bad = [x for x in hd.get("methods") or [] if x not in METHODS]
        if bad:
            issues.append(_issue("error", hd["id"], w, "Unknown method " + ", ".join(bad) + ".", True))
        if any(x not in ns_all for x in hd.get("namespace_ids") or []):
            issues.append(_issue("error", hd["id"], w, "A namespace it applies to no longer exists."))
        if low == "authorization":
            issues.append(_issue("warning", hd["id"], w, "OpenAPI ignores an Authorization header parameter. It is "
                                 "generated from each namespace's security setting, so this one is left out."))
        elif low in ("accept", "content-type"):
            issues.append(_issue("warning", hd["id"], w, "OpenAPI ignores a " + hd["name"] + " header parameter; "
                                 "it is left out."))
        elif low == "if-match":
            issues.append(_issue("warning", hd["id"], w, "If-Match is already added to PUT, PATCH and DELETE."))
    st = m.get("settings") or {}
    if st.get("post_response", "key") not in ("key", "full"):
        issues.append(_issue("error", "settings", "Settings", "Default POST response must be key or full.", True))
    if st.get("put_response", "full") not in ("full", "key", "none"):
        issues.append(_issue("error", "settings", "Settings", "Default PUT response must be full, key or none.", True))
    if not re.match(r"^[A-Z]{3}$", st.get("money_currency", "GBP") or ""):
        issues.append(_issue("error", "settings", "Settings", "Currency must be an ISO 4217 code such as GBP.", True))
    if st.get("list_response", "page") not in ("page", "array", "wrapped"):
        issues.append(_issue("error", "settings", "Settings", "Default GET list response must be page, array or wrapped.", True))

    skeys: Dict[str, str] = {}
    for s in m.get("source_systems", []):
        w = "Source system " + (s["name"] or s["key"])
        if not naming.NS_KEY_RE.match(s["key"]):
            issues.append(_issue("error", s["id"], w, "Key must be lower case letters, digits or '-' (e.g. siebel).", True))
        if s["key"] in skeys:
            issues.append(_issue("error", s["id"], w, "Source system key is used twice.", True))
        skeys[s["key"]] = s["id"]
        if s["kind"] not in KINDS:
            issues.append(_issue("error", s["id"], w, "Unknown type " + s["kind"] + ".", True))

    keys: Dict[str, str] = {}
    paths: Dict[str, str] = {}
    for n in m["namespaces"]:
        w = "Namespace " + n["key"]
        if not naming.NS_KEY_RE.match(n["key"]):
            issues.append(_issue("error", n["id"], w, "Key must be lower case letters, digits or '-' (e.g. cm).", True))
        if n["key"] in keys:
            issues.append(_issue("error", n["id"], w, "Namespace key is used twice.", True))
        keys[n["key"]] = n["id"]
        if n["base_path"] and not naming.BASE_PATH_RE.match(n["base_path"]):
            issues.append(_issue("error", n["id"], w, "Base path must look like /cm/v1.", True))
        if n["base_path"] in paths:
            issues.append(_issue("error", n["id"], w, "Base path " + n["base_path"] + " is already used by namespace "
                                 + paths[n["base_path"]] + ".", True))
        paths[n["base_path"]] = n["key"]
        if n["security"] not in SECURITY:
            issues.append(_issue("error", n["id"], w, "Unknown security scheme " + n["security"] + ".", True))
        if n.get("base_path_in", "paths") not in ("paths", "server"):
            issues.append(_issue("error", n["id"], w, "Base path placement must be 'paths' or 'server'.", True))
        if not n["servers"]:
            issues.append(_issue("warning", n["id"], w, "No servers configured."))

    schemas: Dict[str, str] = {}
    for e in m["entities"]:
        w = e["business_name"]
        src = systems.get(e.get("source_system_id") or "")
        if e.get("source_system_id") and not src:
            issues.append(_issue("error", e["id"], w, "Source system not found.", True))
        labels = kind_of(src)
        tokens = src["internal_tokens"] if src else []
        if e["namespace_id"] not in ns_ids:
            issues.append(_issue("error", e["id"], w, "Namespace not found.", True))
        if not naming.CAMEL_RE.match(e["api_name"]):
            issues.append(_issue("error", e["id"], w, "API name '" + e["api_name"] + "' must be camelCase.", True))
        if not naming.PATH_SEGMENTS_RE.match(e["plural"]):
            issues.append(_issue("error", e["id"], w, "Path segment '" + e["plural"] + "' must be lower-case kebab-case; "
                                 "use / between segments for a nested path, e.g. agreements/changes.", True))
        if not naming.PASCAL_RE.match(e["schema_name"]):
            issues.append(_issue("error", e["id"], w, "Schema name '" + e["schema_name"] + "' must be PascalCase.", True))
        if e["schema_name"] in schemas:
            issues.append(_issue("error", e["id"], w, "Schema name " + e["schema_name"] + " is also used by "
                                 + schemas[e["schema_name"]] + ".", True))
        schemas[e["schema_name"]] = w
        for o in e["operations"]:
            if o not in OPERATIONS:
                issues.append(_issue("error", e["id"], w, "Unknown operation " + o + ".", True))
        for nm in (e["api_name"], e["plural"]):
            tok = naming.leaky(nm, tokens)
            if tok:
                issues.append(_issue("warning", e["id"], w, "'" + nm + "' looks like a " + src["name"]
                                     + " internal name (" + tok + ")."))
        if src and not e["source_object"]:
            issues.append(_issue("warning", e["id"], w, "No " + labels["object"].lower() + " mapped for " + src["name"] + "."))

        key_attrs = [a for a in e["attributes"] if a["key"]]
        needs_key = any(o in e["operations"] for o in ("get", "update", "patch", "delete"))
        if needs_key and len(key_attrs) != 1:
            issues.append(_issue("error", e["id"], w, "Needs exactly one key column for get/update/delete "
                                 "(has " + str(len(key_attrs)) + ")."))
        elif len(key_attrs) > 1:
            issues.append(_issue("error", e["id"], w, "Only one key column is supported."))

        seen: Dict[str, str] = {}
        for nav in nav_properties(m, e["id"]):
            if nav["expose"] in ("link", "embedded"):
                seen[nav["api_name"]] = "relationship " + (nav["rel"]["business_name"] or nav["api_name"])
        for a in e["attributes"]:
            aw = w + " › " + a["business_name"]
            if not naming.CAMEL_RE.match(a["api_name"]):
                issues.append(_issue("error", a["id"], aw, "API name '" + a["api_name"] + "' must be camelCase.", True))
            if a["api_name"] in RESERVED:
                issues.append(_issue("error", a["id"], aw, "'" + a["api_name"] + "' is reserved by the generator.", True))
            if a["api_name"] in seen:
                issues.append(_issue("error", a["id"], aw, "API name '" + a["api_name"] + "' clashes with "
                                     + seen[a["api_name"]] + ".", True))
            seen.setdefault(a["api_name"], "column " + a["business_name"])
            if a["type"] not in ATTRIBUTE_TYPES:
                issues.append(_issue("error", a["id"], aw, "Unknown type " + a["type"] + ".", True))
            if a["type"] == "enum":
                if not a["enum"]:
                    issues.append(_issue("error", a["id"], aw, "Enum column has no values."))
                codes = [v["code"] for v in a["enum"]]
                if len(set(codes)) != len(codes):
                    issues.append(_issue("error", a["id"], aw, "Enum codes must be unique.", True))
                bad = [c for c in codes if not naming.UPPER_SNAKE_RE.match(c)]
                if bad:
                    issues.append(_issue("warning", a["id"], aw, "Enum codes should be UPPER_SNAKE: " + ", ".join(bad)))
            if a["key"] and a["type"] not in ("string", "integer"):
                issues.append(_issue("error", a["id"], aw, "Key column must be string or integer."))
            tok = naming.leaky(a["api_name"], tokens)
            if tok:
                issues.append(_issue("warning", a["id"], aw, "'" + a["api_name"] + "' looks like a " + src["name"]
                                     + " internal name (" + tok + ")."))
            if src and not a["source_field"] and not a["transform"]:
                issues.append(_issue("warning", a["id"], aw, "No " + labels["field"] + " mapped."))
            fm = a.get("filter_match") or "equals"
            if fm not in FILTER_MATCHES:
                issues.append(_issue("error", a["id"], aw, "Filter match must be equals, in, range or contains.", True))
            elif a.get("filterable"):
                issues.extend(_match_issues(a["id"], aw, fm, a))
            if a.get("derived") and not (a.get("transform") or "").strip() and not (a.get("source_object") or "").strip():
                issues.append(_issue("warning", a["id"], aw, "Derived column: say where it comes from (its business "
                                     "component) or how it is worked out (Transformation)."))
            if a.get("money") and a["type"] not in ("integer", "number"):
                issues.append(_issue("warning", a["id"], aw, "A money column needs an integer (pence) or number "
                                     "amount; it is sent as a plain " + a["type"] + " until then."))
            pat = (a.get("pattern") or "").strip()
            if pat:
                try:
                    rx = re.compile(pat)
                except re.error as exc:
                    issues.append(_issue("error", a["id"], aw, "Pattern is not a valid regular expression: " + str(exc) + ".", True))
                    rx = None
                if a["type"] != "string":
                    issues.append(_issue("warning", a["id"], aw, "A pattern only applies to text columns; it is left out for "
                                         + a["type"] + "."))
                elif rx and (a.get("example") or "").strip() and not rx.search(a["example"].strip()):
                    issues.append(_issue("warning", a["id"], aw, "Example '" + a["example"] + "' does not match the pattern " + pat + "."))
            if not _example_ok(a):
                issues.append(_issue("warning", a["id"], aw, "Example '" + a["example"] + "' does not match type " + a["type"] + "."))

    for r in m["relationships"]:
        f, t = ents.get(r["from_entity_id"]), ents.get(r["to_entity_id"])
        w = "Link " + (f["business_name"] if f else "?") + " → " + (t["business_name"] if t else "?")
        if r["business_name"]:
            w += " (" + r["business_name"] + ")"
        if not f or not t:
            issues.append(_issue("error", r["id"], w, "Linked entity not found."))
            continue
        if r["cardinality"] not in CARDINALITIES:
            issues.append(_issue("error", r["id"], w, "Unknown cardinality " + r["cardinality"] + ".", True))
        for side, api, exp in (("from", r["api_name"], r["expose"]), ("to", r["inverse_api_name"], r["inverse_expose"])):
            if exp not in EXPOSE:
                issues.append(_issue("error", r["id"], w, "Unknown exposure " + exp + ".", True))
            sub_ops = r.get("sub_ops" if side == "from" else "inverse_sub_ops") or []
            if exp != "none" or sub_ops:
                if not api:
                    issues.append(_issue("error", r["id"], w, "An exposed link needs an API name."))
                elif not naming.CAMEL_RE.match(api):
                    issues.append(_issue("error", r["id"], w, "Link API name '" + api + "' must be camelCase.", True))
                owner = f if side == "from" else t
                target = t if side == "from" else f
                if sub_ops and not any(o in owner["operations"] for o in ("get", "list")):
                    issues.append(_issue("error", r["id"], w, owner["business_name"]
                                         + " has no get operation, so it cannot have a sub-resource path."))
                if sub_ops and not any(a["key"] for a in owner["attributes"]):
                    issues.append(_issue("error", r["id"], w, owner["business_name"] + " needs a key column for a sub-resource path."))
            bad_ops = [o for o in sub_ops if o not in SUB_OPS]
            if bad_ops:
                issues.append(_issue("error", r["id"], w, "Unknown sub-resource operation " + ", ".join(bad_ops) + ".", True))
            if sub_ops:
                target = t if side == "from" else f
                card = r["cardinality"] if side == "from" else INVERSE_CARDINALITY.get(r["cardinality"], r["cardinality"])
                key_id = r.get("sub_key" if side == "from" else "inverse_sub_key") or ""
                if key_id and not any(a["id"] == key_id for a in target["attributes"]):
                    issues.append(_issue("error", r["id"], w, "The sub-resource key is not a column of "
                                         + target["business_name"] + ".", True))
                if to_many(card) and any(o in sub_ops for o in ("get", "update", "delete")) and not key_id \
                        and not any(a["key"] for a in target["attributes"]):
                    issues.append(_issue("error", r["id"], w, "Getting, replacing or removing one of "
                                         + target["business_name"] + " needs a path key: pick a sub-resource key "
                                         "or give it a key column."))
                if not to_many(card) and any(o in sub_ops for o in ("list", "create")):
                    issues.append(_issue("warning", r["id"], w, "List and create apply to a 'many' end only; "
                                         "they are ignored here."))
                for op, ov in (r.get("sub_overrides" if side == "from" else "inverse_sub_overrides") or {}).items():
                    issues.extend(_override_issues(r["id"], w + " › " + op, ov, ents))
                    issues.extend(_query_issues(r["id"], w + " › " + op, ov.get("query_params") or [], m))
        if r.get("nested_create") and r["expose"] != "embedded":
            issues.append(_issue("warning", r["id"], w, "Accepting it in request bodies only applies when the link is embedded."))
        if r.get("inverse_nested_create") and r["inverse_expose"] != "embedded":
            issues.append(_issue("warning", r["id"], w, "Accepting the other side in request bodies only applies when it is embedded."))
        if r["expose"] == "link" and not any(a["key"] for a in t["attributes"]):
            issues.append(_issue("error", r["id"], w, t["business_name"] + " needs a key column to be linked."))
        if r["inverse_expose"] == "link" and not any(a["key"] for a in f["attributes"]):
            issues.append(_issue("error", r["id"], w, f["business_name"] + " needs a key column to be linked."))
        # two navigation properties with the same name on one entity
    for e in m["entities"]:
        names: Dict[str, int] = {}
        segs: Dict[str, str] = {}
        for nav in nav_properties(m, e["id"]):
            names[nav["api_name"]] = names.get(nav["api_name"], 0) + 1
            if nav["sub_ops"]:
                segs[naming.kebab(nav["api_name"])] = "link " + nav["api_name"]
        for nm, c in names.items():
            if c > 1:
                issues.append(_issue("error", e["id"], e["business_name"], "Two links are both named '" + nm + "'.", True))
        # column groups: bankDetails: {…} or bankDetails: [{…}]
        top = {a["api_name"] for a in e["attributes"] if not a.get("group_id")} | set(names)
        gnames: Dict[str, int] = {}
        for g in e.get("column_groups") or []:
            wg = e["business_name"] + " › " + (g["business_name"] or g["api_name"])
            gnames[g["api_name"]] = gnames.get(g["api_name"], 0) + 1
            if not naming.CAMEL_RE.match(g["api_name"]):
                issues.append(_issue("error", e["id"], wg, "Group API name '" + g["api_name"] + "' must be camelCase, e.g. bankDetails.", True))
            if g["api_name"] in top:
                issues.append(_issue("error", e["id"], wg, "'" + g["api_name"] + "' is both a group and a column or link.", True))
            members = [a for a in e["attributes"] if a.get("group_id") == g["id"]]
            if not members:
                issues.append(_issue("warning", e["id"], wg, "Group " + g["api_name"] + " has no columns; it is left out."))
            for a in members:
                if a["key"]:
                    issues.append(_issue("error", a["id"], e["business_name"] + " › " + a["business_name"],
                                         "A key column stays at the top level; take it out of group " + g["api_name"] + "."))
        for nm, c in gnames.items():
            if c > 1:
                issues.append(_issue("error", e["id"], e["business_name"], "Two column groups are both named '" + nm + "'.", True))
        # operation overrides
        for op, ov in (e.get("op_overrides") or {}).items():
            w = e["business_name"] + " › " + op
            if op not in OPERATIONS:
                issues.append(_issue("error", e["id"], w, "Unknown operation " + op + ".", True))
            elif op not in e["operations"]:
                issues.append(_issue("warning", e["id"], w, "Settings for an operation that is not ticked are ignored."))
            issues.extend(_override_issues(e["id"], w, ov, ents, e))
        # get one by query parameter
        gb = e.get("get_by") or "path"
        if gb not in GET_BY:
            issues.append(_issue("error", e["id"], e["business_name"], "Get one by must be path or query.", True))
        elif gb == "query" and "get" in e["operations"]:
            if "list" in e["operations"] and (e.get("op_overrides") or {}).get("get"):
                issues.append(_issue("warning", e["id"], e["business_name"] + " › get", "One record is read through the list "
                                     "(GET /" + e["plural"] + "?…), so the get operation's own settings are not used. "
                                     "Untick List for a separate single-record GET."))
            if not any(a["key"] for a in e["attributes"]):
                issues.append(_issue("error", e["id"], e["business_name"], "Getting one by query needs a key column."))
        keys = [a for a in e["attributes"] if a["key"]]
        get_ov = (e.get("op_overrides") or {}).get("get") or {}
        if gb == "path" and "get" in e["operations"] and keys and any(
                q.get("attr_id") == keys[0]["id"] for q in (get_ov.get("query_params") or [])):
            issues.append(_issue("warning", e["id"], e["business_name"] + " › get", keys[0]["api_name"]
                                 + " is in the path and also a query parameter. To use only the query, set Get one by to Query."))
        for op, ov in (e.get("op_overrides") or {}).items():
            issues.extend(_query_issues(e["id"], e["business_name"] + " › " + op, ov.get("query_params") or [], m))
        # actions
        has_key = any(a["key"] for a in e["attributes"])
        seen_names: Dict[str, int] = {}
        for act in e.get("actions", []):
            w = e["business_name"] + " › action " + act["name"]
            seen_names[act["name"]] = seen_names.get(act["name"], 0) + 1
            if not naming.CAMEL_RE.match(act["name"]):
                issues.append(_issue("error", act["id"], w, "Action name must be camelCase, e.g. decision.", True))
            if not naming.KEBAB_RE.match(act["path"]):
                issues.append(_issue("error", act["id"], w, "Path segment '" + act["path"] + "' must be kebab-case.", True))
            if act["method"] not in METHODS:
                issues.append(_issue("error", act["id"], w, "Unknown method " + act["method"] + ".", True))
            if act["scope"] not in ("item", "collection"):
                issues.append(_issue("error", act["id"], w, "Scope must be item or collection.", True))
            if act["status"] not in STATUSES:
                issues.append(_issue("error", act["id"], w, "Status must be one of " + ", ".join(STATUSES) + ".", True))
            if act["request_variant"] not in REQUEST_VARIANTS:
                issues.append(_issue("error", act["id"], w, "Request form must be create, update, full or fields.", True))
            elif act["request_variant"] == "fields" and act["request_entity_id"]:
                issues.extend(_request_field_issues(act["id"], w, ents.get(act["request_entity_id"]),
                                                    act.get("request_fields") or [], act.get("request_required") or []))
            if act["request_entity_id"] and act["request_entity_id"] not in ents:
                issues.append(_issue("error", act["id"], w, "Request entity not found.", True))
            if act["response_entity_id"] not in ("", "self") and act["response_entity_id"] not in ents:
                issues.append(_issue("error", act["id"], w, "Response entity not found.", True))
            if act.get("operation_id") and not naming.CAMEL_RE.match(act["operation_id"]):
                issues.append(_issue("error", act["id"], w, "Operation ID '" + act["operation_id"] + "' must be camelCase.", True))
            amode = act.get("response_mode") or ""
            if amode in ("page", "array", "wrapped"):
                if not act["response_entity_id"]:
                    issues.append(_issue("warning", act["id"], w, "A list response needs a response entity."))
                lp = act.get("list_property") or ""
                if lp and not re.match(r"^[a-z][A-Za-z0-9]*$", lp):
                    issues.append(_issue("error", act["id"], w, "The list's property name '" + lp + "' must be camelCase.", True))
            else:
                issues.extend(_mode_issues(act["id"], w, amode, act.get("response_fields") or [],
                                           e if act["response_entity_id"] == "self" else ents.get(act["response_entity_id"])))
            issues.extend(_query_issues(act["id"], w, act.get("query_params") or [], m))
            if act["request_entity_id"] and act["method"] in ("GET", "DELETE"):
                issues.append(_issue("warning", act["id"], w, act["method"] + " requests should not have a body."))
            bad = [x for x in act.get("errors") or [] if x not in ERROR_NAMES]
            if bad:
                issues.append(_issue("error", act["id"], w, "Unknown error responses: " + ", ".join(bad) + ".", True))
            if act["scope"] == "item" and not has_key:
                issues.append(_issue("error", act["id"], w, e["business_name"] + " needs a key column for an item action."))
            if act["scope"] == "item" and act["path"] in segs:
                issues.append(_issue("error", act["id"], w, "Path '" + act["path"] + "' is already used by "
                                     + segs[act["path"]] + ".", True))
            if act["scope"] == "item":
                segs.setdefault(act["path"], "action " + act["name"])
        for nm, c in seen_names.items():
            if c > 1:
                issues.append(_issue("error", e["id"], e["business_name"], "Two actions are both named '" + nm + "'.", True))
    issues.extend(name_check.issues(m))   # one business name or DD attribute, several API names
    return issues


def _match_issues(obj_id: str, where: str, match: str, a: Dict[str, Any]) -> List[Dict[str, Any]]:
    t = a["type"]
    if match == "range" and t not in ("integer", "number", "date", "date-time"):
        return [_issue("warning", obj_id, where, "A from/to range filter needs a number or date column.")]
    if match == "contains" and t not in ("string",):
        return [_issue("warning", obj_id, where, "A 'contains' filter needs a text column.")]
    if match == "in" and t == "boolean":
        return [_issue("warning", obj_id, where, "'One of several' makes little sense for a yes/no column.")]
    return []


def _query_issues(obj_id: str, where: str, specs: List[Dict[str, Any]], m: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    attrs = {a["id"]: a for e in m["entities"] for a in e["attributes"]}
    names: Dict[str, int] = {}
    for sp in specs:
        a = attrs.get(sp.get("attr_id", ""))
        if (sp.get("match") or "equals") not in FILTER_MATCHES:
            out.append(_issue("error", obj_id, where, "Query match must be equals, in, range or contains.", True))
            continue
        if not a:
            out.append(_issue("warning", obj_id, where, "A query parameter's column no longer exists; it is left out."))
            continue
        nm = sp.get("name") or a["api_name"]
        loc = sp.get("location") or "query"
        if loc not in ("query", "header"):
            out.append(_issue("error", obj_id, where, "A parameter goes in the query or a header.", True))
            continue
        if loc == "header":
            if sp.get("name") and not re.match(r"^[A-Za-z][A-Za-z0-9-]*$", sp["name"]):
                out.append(_issue("error", obj_id, where, "Header name '" + sp["name"] + "' may use letters, digits and hyphens.", True))
            if (sp.get("match") or "equals") != "equals":
                out.append(_issue("warning", obj_id, where, nm + " is a header, so it is matched as equals."))
            names["h:" + nm.lower()] = names.get("h:" + nm.lower(), 0) + 1
            continue
        if sp.get("name") and not naming.CAMEL_RE.match(sp["name"]):
            out.append(_issue("error", obj_id, where, "Query parameter name '" + sp["name"] + "' must be camelCase.", True))
        for x in ([nm + "From", nm + "To"] if sp.get("match") == "range" else [nm]):
            names[x] = names.get(x, 0) + 1
        out.extend(_match_issues(obj_id, where + " › " + nm, sp.get("match") or "equals", a))
    for nm, c in names.items():
        if c > 1:
            out.append(_issue("error", obj_id, where, ("Header " + nm[2:] if nm.startswith("h:") else "Query parameter " + nm)
                              + " appears twice.", True))
        if nm in ("limit", "cursor"):
            out.append(_issue("error", obj_id, where, "'" + nm + "' is used for paging.", True))
    return out


def _mode_issues(obj_id: str, where: str, mode: str, fields: List[str], ent: Any) -> List[Dict[str, Any]]:
    out = []
    is_list = where.endswith("› list")
    if is_list and mode and mode not in ("page", "array", "wrapped"):
        out.append(_issue("error", obj_id, where, "A GET list returns a page, an array, or an object holding an array.", True))
        return out
    if not is_list and mode and mode not in RESPONSE_MODES:
        out.append(_issue("error", obj_id, where, "Response content must be full, key, fields or none"
                          + (" (page, array and wrapped are for GET lists)." if mode in ("page", "array", "wrapped") else "."), True))
    if mode == "fields" and not fields:
        out.append(_issue("warning", obj_id, where, "Response content is 'selected fields' but none are selected; "
                          "the whole record is returned."))
    if ent and fields:
        ids = {a["id"] for a in ent["attributes"]}
        if any(f not in ids for f in fields):
            out.append(_issue("warning", obj_id, where, "Some selected response fields are not columns of "
                              + ent["business_name"] + " any more; they are left out."))
    if ent and mode == "key" and not any(a["key"] for a in ent["attributes"]):
        out.append(_issue("warning", obj_id, where, ent["business_name"] + " has no key column, so the whole record "
                          "is returned instead of its row id."))
    return out


def _request_field_issues(obj_id: str, where: str, ent: Any, fields: List[str], required: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not fields:
        out.append(_issue("warning", obj_id, where, "The request is set to selected fields but none are ticked; "
                          "the body is empty."))
        return out
    if not ent:
        return out
    by_id = {a["id"]: a for a in ent["attributes"]}
    if any(f not in by_id for f in fields):
        out.append(_issue("warning", obj_id, where, "Some request fields are not columns of " + ent["business_name"]
                          + " any more; they are left out."))
    ro = [by_id[f]["api_name"] for f in fields if f in by_id and by_id[f]["read_only"]]
    if ro:
        out.append(_issue("warning", obj_id, where, "Read-only column(s) " + ", ".join(ro) + " are in the request body."))
    if any(r not in fields for r in required):
        out.append(_issue("warning", obj_id, where, "A required request field is not ticked as a field; it is ignored."))
    return out


def _override_issues(obj_id: str, where: str, ov: Dict[str, Any], ents: Dict[str, Any],
                     default_ent: Any = None) -> List[Dict[str, Any]]:
    out = []
    resp = ents.get(ov.get("response_entity_id") or "") or default_ent
    out.extend(_mode_issues(obj_id, where, ov.get("response_mode") or "", ov.get("response_fields") or [], resp))
    rm = ov.get("request_mode") or ""
    if rm not in ("", "fields"):
        out.append(_issue("error", obj_id, where, "Request content must be the default form or fields.", True))
    elif rm == "fields":
        req_ent = ents.get(ov.get("request_entity_id") or "") or default_ent
        out.extend(_request_field_issues(obj_id, where, req_ent, ov.get("request_fields") or [], ov.get("request_required") or []))
    oid = ov.get("operation_id") or ""
    if oid and not naming.CAMEL_RE.match(oid):
        out.append(_issue("error", obj_id, where, "Operation ID '" + oid + "' must be camelCase, e.g. findAgreementByPerson.", True))
    lp = ov.get("list_property") or ""
    if lp and not re.match(r"^[a-z][A-Za-z0-9]*$", lp):
        out.append(_issue("error", obj_id, where, "The list's property name '" + lp + "' must be camelCase, e.g. agreements.", True))
    if ov.get("status") and ov["status"] not in STATUSES:
        out.append(_issue("error", obj_id, where, "Status must be one of " + ", ".join(STATUSES) + ".", True))
    if ov.get("request_entity_id") and ov["request_entity_id"] not in ents:
        out.append(_issue("error", obj_id, where, "Request entity not found.", True))
    rid = ov.get("response_entity_id")
    if rid and rid != "none" and rid not in ents:
        out.append(_issue("error", obj_id, where, "Response entity not found.", True))
    return out


def new_blocking(before: List[Dict[str, Any]], after: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Blocking errors present after an edit that were not there before it."""
    seen = {(i["object_id"], i["message"]) for i in before if i["blocking"]}
    return [i for i in after if i["blocking"] and (i["object_id"], i["message"]) not in seen]
