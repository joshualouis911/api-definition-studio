"""Source variants: an entity whose records come from different sources depending on
one of its columns, e.g. a Note whose parent.type picks the business component
(service-request -> Service Request Note on S_NOTE_SR, contact -> Contact Note on
S_NOTE_CON). The API contract stays one resource; only the source mapping varies.

Each column has its own mapping (the default) and, per variant, an optional override.
A blank override field inherits the default, so fields that are named alike in every
source (Note, Created, ...) are mapped once. A default location without a table
(e.g. NOTE) takes the variant's table: S_NOTE_SR.NOTE, S_NOTE_CON.NOTE."""
from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Tuple

FIELDS = ("source_object", "source_field", "source_column", "dd_reference", "data_item", "transform")


def has_variants(e: Dict[str, Any]) -> bool:
    return bool(e.get("source_variants"))


def discriminator(e: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The column the source varies by."""
    vid = e.get("varies_by") or ""
    return next((a for a in e.get("attributes", []) if a["id"] == vid), None)


def discriminator_path(e: Dict[str, Any]) -> str:
    """e.g. parent.type for a column in the parent group."""
    a = discriminator(e)
    return column_path(e, a) if a else ""


def column_path(e: Dict[str, Any], a: Dict[str, Any]) -> str:
    """A column as the mapping sheet names it: parent.type inside a group, else its API name."""
    g = next((g for g in e.get("column_groups") or [] if g["id"] == a.get("group_id")), None)
    return (g["api_name"] + "." if g else "") + a["api_name"]


def find_column(e: Dict[str, Any], path: str) -> Optional[Dict[str, Any]]:
    """The column named by column_path (or a plain API name when it is unambiguous)."""
    path = (path or "").strip()
    hit = next((a for a in e.get("attributes", []) if column_path(e, a) == path), None)
    if hit:
        return hit
    same = [a for a in e.get("attributes", []) if a["api_name"] == path.split(".")[-1]]
    return same[0] if len(same) == 1 else None


def effective(e: Dict[str, Any], a: Dict[str, Any], v: Dict[str, Any]) -> Dict[str, Any]:
    """A column's mapping in one variant: the override where given, else the default.
    Returns the FIELDS plus not_applicable and inherited (the fields taken from the default)."""
    vm = (a.get("variant_sources") or {}).get(v["id"]) or {}
    out: Dict[str, Any] = {"not_applicable": bool(vm.get("not_applicable")), "inherited": []}
    for f in FIELDS:
        own = (vm.get(f) or "").strip()
        if own:
            out[f] = own
            continue
        out["inherited"].append(f)
        if f == "source_object":
            out[f] = (a.get("source_object") or "").strip() or (v.get("source_object") or "").strip()
        elif f == "source_column":
            base = (a.get("source_column") or "").strip()
            prefix = (v.get("location_prefix") or "").strip()
            out[f] = prefix + "." + base if base and prefix and "." not in base and " " not in base else base
        else:
            out[f] = (a.get(f) or "").strip()
    return out


def mappings(e: Dict[str, Any], a: Dict[str, Any]) -> Iterator[Tuple[Optional[Dict[str, Any]], Dict[str, Any]]]:
    """(variant, mapping) for each source of the column: one pair with variant None for an
    entity without variants, else one per variant (not-applicable ones included)."""
    if not has_variants(e):
        yield None, {"source_object": (a.get("source_object") or "").strip() or (e.get("source_object") or "").strip(),
                     **{f: (a.get(f) or "").strip() for f in FIELDS if f != "source_object"},
                     "not_applicable": False, "inherited": []}
        return
    for v in e["source_variants"]:
        yield v, effective(e, a, v)


def extension(e: Dict[str, Any], a: Dict[str, Any], src: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """x-source-variants on a property: per variant value, the column's source in full."""
    if not has_variants(e):
        return None
    from .source_kinds import ext_name
    keys = {"source_object": "x-source-object", "source_field": "x-source-field",
            "source_column": ext_name(src, "location"), "dd_reference": "x-dd-attribute",
            "data_item": "x-data-item", "transform": "x-transform"}
    out: Dict[str, Any] = {}
    for v in e["source_variants"]:
        if not v["code"]:
            continue
        eff = effective(e, a, v)
        if eff["not_applicable"]:
            out[v["code"]] = {"x-not-applicable": True}
            continue
        d = {keys[f]: eff[f] for f in FIELDS
             if eff.get(f) and (f != "source_object" or eff[f] != (v.get("source_object") or ""))}
        if d:
            out[v["code"]] = d
    return out or None


def entity_extension(e: Dict[str, Any], src: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """x-source-variants on a schema: the column that picks the source, and each variant's
    object, container and table."""
    from .source_kinds import ext_name
    out: Dict[str, Any] = {"selected-by": discriminator_path(e), "variants": {}}
    for v in e["source_variants"]:
        if not v["code"]:
            continue
        d = {}
        for k, x in (("x-source-object", v.get("source_object")), (ext_name(src, "container"), v.get("source_io")),
                     ("x-source-location-prefix", v.get("location_prefix")), ("description", v.get("description"))):
            if x:
                d[k] = x
        out["variants"][v["code"]] = d
    return out


def clean(e: Dict[str, Any], new_id) -> None:
    """Normalise variants and drop overrides for variants that no longer exist."""
    vs = e.get("source_variants") or []
    for v in vs:
        v["id"] = v.get("id") or new_id("sv")
        for k in ("code", "source_object", "source_io", "location_prefix", "description"):
            v[k] = (v.get(k) or "").strip()
    e["source_variants"] = vs
    ids = {v["id"] for v in vs}
    attr_ids = {a["id"] for a in e.get("attributes", [])}
    if e.get("varies_by") and e["varies_by"] not in attr_ids:
        e["varies_by"] = ""
    for a in e.get("attributes", []):
        kept = {}
        for vid, vm in (a.get("variant_sources") or {}).items():
            if vid not in ids:
                continue
            vm = {k: (vm.get(k) or "").strip() if k != "not_applicable" else bool(vm.get(k))
                  for k in FIELDS + ("not_applicable",)}
            if any(vm[k] for k in FIELDS) or vm["not_applicable"]:
                kept[vid] = vm
        a["variant_sources"] = kept


def issues(e: Dict[str, Any], src: Optional[Dict[str, Any]], labels: Dict[str, str], issue) -> List[Dict[str, Any]]:
    """Issues for an entity with variants. `issue(level, obj_id, where, msg, blocking=False)`."""
    out: List[Dict[str, Any]] = []
    if not has_variants(e):
        return out
    w = e["business_name"]
    d = discriminator(e)
    if not d:
        out.append(issue("error", e["id"], w, "Source variants need the column they vary by (e.g. parent.type)."))
        return out
    if d["type"] != "enum":
        out.append(issue("error", e["id"], w, "The column the source varies by (" + d["business_name"] + ") must be an enum."))
    codes = [v["code"] for v in e["source_variants"]]
    if len(set(codes)) != len(codes):
        out.append(issue("error", e["id"], w, "Each source variant needs its own value of " + d["api_name"] + "."))
    enum_codes = [x["code"] for x in d.get("enum") or []]
    for c in codes:
        if not c:
            out.append(issue("error", e["id"], w, "A source variant has no " + d["api_name"] + " value."))
        elif d["type"] == "enum" and c not in enum_codes:
            out.append(issue("error", e["id"], w, "Source variant '" + c + "' is not a value of " + d["api_name"] + "."))
    for c in enum_codes:
        if c not in codes:
            out.append(issue("warning", e["id"], w, discriminator_path(e) + " '" + c + "' has no source variant: "
                             "where do its records come from?"))
    if src:
        for v in e["source_variants"]:
            if v["code"] and not v.get("source_object"):
                out.append(issue("warning", e["id"], w, "Source variant '" + v["code"] + "' has no "
                                 + labels["object"].lower() + "."))
    return out


def column_issues(e: Dict[str, Any], a: Dict[str, Any], labels: Dict[str, str], issue) -> List[Dict[str, Any]]:
    """Per variant, a column with neither a source field nor a transformation (and not marked
    not applicable) is unmapped there."""
    out = []
    missing = [v["code"] for v in e["source_variants"] if v["code"]
               and not (lambda eff: eff["not_applicable"] or eff["source_field"] or eff["transform"])(effective(e, a, v))]
    if missing:
        out.append(issue("warning", a["id"], e["business_name"] + " › " + a["business_name"],
                         "No " + labels["field"] + " mapped for " + ", ".join(missing) + "."))
    return out
