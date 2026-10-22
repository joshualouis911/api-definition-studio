"""FastAPI app: the studio's own REST API plus the single-page UI."""
from __future__ import annotations

import copy
import io
import json
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, diagram, dictionary, erd_import, examples, mapping_sheet, names, naming, source_kinds
from .model import (Action, ActionPatch, AddFromDictionary, Attribute, AttributeIds, GroupMembers, AttributePatch, Entity, EntityPatch, Header,
                    HeaderPatch, Model,
                    Namespace, NamespacePatch,
                    Relationship, RelationshipPatch, Settings, SourceSystem, SourceSystemPatch, TagMembers, TagRename,
                    clean_tags)
from .openapi_gen import build_openapi, endpoints, path_conflicts
from .store import Invalid, NotFound, Store, fill_defaults, new_id
from .validate import new_blocking, validate

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="API Definition Studio", version=__version__,
              description="Design API resources over source-system entities; generate OpenAPI, "
                          "a mapping sheet and a data model diagram.")
class _NoCacheStatic(StaticFiles):
    """Static files the browser must revalidate, so an upgrade is picked up
    straight away instead of running a cached older script."""

    async def get_response(self, path, scope):  # type: ignore[override]
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", _NoCacheStatic(directory=str(STATIC)), name="static")

_store: Optional[Store] = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def reset_store(data_dir: Optional[str] = None) -> Store:
    """Used by tests and the CLI to point the app at a data directory."""
    global _store, _dics
    _store = Store(data_dir)
    _dics = None
    return _store


_dics: Optional[dictionary.Dictionaries] = None


def dics() -> dictionary.Dictionaries:
    global _dics
    if _dics is None or _dics.path.parent != store().dir:
        _dics = dictionary.Dictionaries(store().dir)
    return _dics


def all_issues(m: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validation issues plus warnings from the source systems' data dictionaries."""
    return validate(m) + path_conflicts(m) + dictionary.issues(m, dics())


@app.exception_handler(NotFound)
def _nf(_req: Request, exc: NotFound) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(Invalid)
def _inv(_req: Request, exc: Invalid) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=422)


def edit(fn: Callable[[Dict[str, Any]], Any]) -> Any:
    """Apply fn to a copy of the model, refuse it if it introduces a naming
    error (duplicate or badly formed name), otherwise save. fn returns a
    callable that picks the result out of the saved model."""
    s = store()
    with s.lock:
        m = s.load()
        before = validate(m)
        work = copy.deepcopy(m)
        pick = fn(work)
        work = fill_defaults(work)
        bad = new_blocking(before, validate(work))
        if bad:
            raise HTTPException(422, {"message": bad[0]["where"] + ": " + bad[0]["message"], "errors": bad})
        saved = s.save(work)
        return pick(saved) if callable(pick) else pick


def _ns_by_key(m: Dict[str, Any], key: str) -> Dict[str, Any]:
    for n in m["namespaces"]:
        if n["key"] == key or n["id"] == key:
            return n
    raise NotFound("namespace " + key + " not found")


# -- UI ------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    # version the script and stylesheet URLs so a browser never mixes an old
    # cached script with a newer server (or the other way round)
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for asset in ("static/app.js", "static/app.css"):
        html = html.replace('"' + asset + '"', '"' + asset + "?v=" + __version__ + '"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/preview/{ns_key}", include_in_schema=False)
def preview(ns_key: str) -> HTMLResponse:
    n = _ns_by_key(store().load(), ns_key)
    html = """<!doctype html><html><head><meta charset="utf-8"><title>%s</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"></head>
<body><div id="ui"></div><script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>window.onload=function(){SwaggerUIBundle({url:'../api/openapi/%s.json',dom_id:'#ui',deepLinking:true,
showExtensions:true,defaultModelsExpandDepth:2});};</script></body></html>""" % (n["title"], n["key"])
    return HTMLResponse(html)


# -- whole model -----------------------------------------------------------------

@app.get("/api/model", tags=["Model"], summary="The whole design model with its validation issues")
def get_model() -> Dict[str, Any]:
    m = store().load()
    return {"model": m, "issues": all_issues(m), "version": __version__, "source_kinds": source_kinds.meta(),
            "dictionaries": dics().all_summaries()}


@app.put("/api/model", tags=["Model"], summary="Replace the whole model (JSON import)")
def put_model(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        Model.model_validate(body)
    except Exception as exc:  # noqa: BLE001
        raise Invalid("Not a valid model file: " + str(exc).splitlines()[0])
    m = store().replace(body)
    return {"model": m, "issues": all_issues(m)}


@app.get("/api/model/download", tags=["Model"], summary="Download model.json")
def download_model() -> Response:
    return Response(json.dumps(store().load(), indent=2, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="model.json"'})


@app.post("/api/reset-sample", tags=["Model"], summary="Replace the model with the starter example")
def reset_sample() -> Dict[str, Any]:
    m = store().reset_sample()
    return {"model": m, "issues": all_issues(m)}


@app.patch("/api/settings", tags=["Model"])
def patch_settings(body: Settings) -> Dict[str, Any]:
    def fn(m):
        m["settings"].update(body.model_dump())
        return lambda saved: saved["settings"]
    return edit(fn)


@app.get("/api/validate", tags=["Model"], summary="Validation issues (errors and warnings)")
def get_validate() -> List[Dict[str, Any]]:
    return all_issues(store().load())


# -- standard headers ------------------------------------------------------------

@app.post("/api/headers", tags=["Headers"], status_code=201)
def create_header(body: Header) -> Dict[str, Any]:
    hid = new_id("hd")

    def fn(m):
        d = body.model_dump()
        d["id"] = hid
        m["headers"].append(d)
        return lambda saved: Store.find(saved["headers"], hid, "header")
    return edit(fn)


@app.patch("/api/headers/{header_id}", tags=["Headers"])
def patch_header(header_id: str, body: HeaderPatch) -> Dict[str, Any]:
    def fn(m):
        hd = Store.find(m["headers"], header_id, "header")
        hd.update(body.model_dump(exclude_unset=True))
        return lambda saved: Store.find(saved["headers"], header_id, "header")
    return edit(fn)


@app.delete("/api/headers/{header_id}", tags=["Headers"], status_code=204)
def delete_header(header_id: str) -> Response:
    def fn(m):
        Store.find(m["headers"], header_id, "header")
        m["headers"] = [x for x in m["headers"] if x["id"] != header_id]
    edit(fn)
    return Response(status_code=204)


@app.post("/api/headers/standard", tags=["Headers"], summary="Add any standard headers that are missing")
def add_standard_headers() -> List[Dict[str, Any]]:
    from .store import standard_headers

    def fn(m):
        have = {x["name"].lower() for x in m["headers"]}
        for hd in standard_headers():
            if hd["name"].lower() not in have:
                hd["id"] = new_id("hd")
                m["headers"].append(hd)
        return lambda saved: saved["headers"]
    return edit(fn)


# -- namespaces ----------------------------------------------------------------

@app.post("/api/namespaces", tags=["Namespaces"], status_code=201)
def create_namespace(body: Namespace) -> Dict[str, Any]:
    nid = new_id("ns")

    def fn(m):
        d = body.model_dump()
        d["id"] = nid
        m["namespaces"].append(d)
        return lambda saved: Store.find(saved["namespaces"], nid, "namespace")
    return edit(fn)


@app.patch("/api/namespaces/{ns_id}", tags=["Namespaces"])
def patch_namespace(ns_id: str, body: NamespacePatch) -> Dict[str, Any]:
    def fn(m):
        n = Store.find(m["namespaces"], ns_id, "namespace")
        n.update(body.model_dump(exclude_unset=True))
        return lambda saved: Store.find(saved["namespaces"], ns_id, "namespace")
    return edit(fn)


@app.delete("/api/namespaces/{ns_id}", tags=["Namespaces"], status_code=204)
def delete_namespace(ns_id: str) -> Response:
    def fn(m):
        Store.find(m["namespaces"], ns_id, "namespace")
        used = [e["business_name"] for e in m["entities"] if e["namespace_id"] == ns_id]
        if used:
            raise HTTPException(409, {"message": "Move or delete its entities first: " + ", ".join(used)})
        m["namespaces"] = [n for n in m["namespaces"] if n["id"] != ns_id]
    edit(fn)
    return Response(status_code=204)


# -- source systems ------------------------------------------------------------

@app.post("/api/source-systems", tags=["Source systems"], status_code=201)
def create_source_system(body: SourceSystem) -> Dict[str, Any]:
    sid = new_id("ss")

    def fn(m):
        d = body.model_dump()
        d["id"] = sid
        m["source_systems"].append(d)
        return lambda saved: Store.find(saved["source_systems"], sid, "source system")
    return edit(fn)


@app.patch("/api/source-systems/{ss_id}", tags=["Source systems"])
def patch_source_system(ss_id: str, body: SourceSystemPatch) -> Dict[str, Any]:
    def fn(m):
        s = Store.find(m["source_systems"], ss_id, "source system")
        changes = body.model_dump(exclude_unset=True)
        # changing type without giving tokens: switch to the new type's default list
        if "kind" in changes and changes["kind"] != s["kind"] and "internal_tokens" not in changes:
            if s["internal_tokens"] == source_kinds.default_tokens(s["kind"]):
                changes["internal_tokens"] = None
        s.update(changes)
        return lambda saved: Store.find(saved["source_systems"], ss_id, "source system")
    return edit(fn)


@app.delete("/api/source-systems/{ss_id}", tags=["Source systems"], status_code=204)
def delete_source_system(ss_id: str, detach: bool = Query(False, description="Mark its entities as not sourced")) -> Response:
    def fn(m):
        Store.find(m["source_systems"], ss_id, "source system")
        used = [e for e in m["entities"] if e.get("source_system_id") == ss_id]
        if used and not detach:
            raise HTTPException(409, {"message": "Used by " + ", ".join(e["business_name"] for e in used)
                                      + ". Move those entities to another source system first, or delete with detach."})
        for e in used:
            e["source_system_id"] = ""
        m["source_systems"] = [x for x in m["source_systems"] if x["id"] != ss_id]
    edit(fn)
    return Response(status_code=204)


# -- entities ------------------------------------------------------------------

@app.post("/api/entities", tags=["Entities"], status_code=201)
def create_entity(body: Entity, key_column: bool = Query(True, description="Add a key column")) -> Dict[str, Any]:
    eid = new_id("en")

    def fn(m):
        Store.find(m["namespaces"], body.namespace_id, "namespace")
        if body.source_system_id:
            Store.find(m["source_systems"], body.source_system_id, "source system")
        d = body.model_dump()
        d["id"] = eid
        if d["x"] is None:
            # place the new box under the lowest existing one on the diagram
            placed = [e for e in m["entities"] if e.get("y") is not None]
            d["x"] = 60
            d["y"] = max([e["y"] + 70 + 24 * len(e["attributes"]) for e in placed] or [20]) + 40
        if key_column and not any(a["key"] for a in d["attributes"]):
            api = d["api_name"] or naming.camel(d["business_name"])
            d["attributes"].insert(0, Attribute(business_name=d["business_name"] + " Id", api_name=api + "Id",
                                                key=True, read_only=True, source_field="Id").model_dump())
        m["entities"].append(d)
        return lambda saved: Store.find(saved["entities"], eid, "entity")
    return edit(fn)


@app.patch("/api/entities/{entity_id}", tags=["Entities"])
def patch_entity(entity_id: str, body: EntityPatch) -> Dict[str, Any]:
    def fn(m):
        e = Store.find(m["entities"], entity_id, "entity")
        changes = body.model_dump(exclude_unset=True)
        if "namespace_id" in changes:
            Store.find(m["namespaces"], changes["namespace_id"], "namespace")
        if changes.get("source_system_id"):
            Store.find(m["source_systems"], changes["source_system_id"], "source system")
        e.update(changes)
        return lambda saved: Store.find(saved["entities"], entity_id, "entity")
    return edit(fn)


@app.delete("/api/entities/{entity_id}", tags=["Entities"], status_code=204)
def delete_entity(entity_id: str) -> Response:
    def fn(m):
        Store.find(m["entities"], entity_id, "entity")
        m["entities"] = [e for e in m["entities"] if e["id"] != entity_id]
        m["relationships"] = [r for r in m["relationships"]
                              if entity_id not in (r["from_entity_id"], r["to_entity_id"])]
    edit(fn)
    return Response(status_code=204)


# -- source-system data dictionary (e.g. the Siebel dictionary) ---------------------------

def _system(m: Dict[str, Any], system_id: str) -> Dict[str, Any]:
    return Store.find(m.get("source_systems", []), system_id, "source system")


@app.get("/api/dictionary", tags=["Dictionary"], summary="Data dictionaries loaded, per source system")
def list_dictionaries() -> List[Dict[str, Any]]:
    return dics().all_summaries()


@app.post("/api/dictionary/{system_id}", tags=["Dictionary"],
          summary="Upload a dictionary (.xlsx or .csv, raw body): BC name, BC field, physical table, "
                  "physical column, DD attribute reference, data item full name")
async def upload_dictionary(system_id: str, request: Request, apply: bool = False,
                            mode: str = Query("replace", pattern="^(replace|merge)$"),
                            filename: str = "") -> Dict[str, Any]:
    _system(store().load(), system_id)
    raw = await request.body()
    if not raw:
        raise Invalid("Send the dictionary workbook (or CSV) as the request body.")
    rows, notes = dictionary.parse(raw, filename)
    bcs = sorted({r["bc"] for r in rows}, key=str.lower)
    result = {"rows": len(rows), "bcs": len(bcs), "notes": notes, "sample": rows[:8],
              "bc_sample": bcs[:12], "replaces": dics().summary(system_id)["rows"] if mode == "replace" else 0}
    if not apply:
        return dict(result, applied=False)
    summary = dics().put(system_id, rows, filename, mode)
    return dict(result, applied=True, summary=summary, issues=all_issues(store().load()))


@app.delete("/api/dictionary/{system_id}", tags=["Dictionary"], status_code=204, summary="Remove a dictionary")
def delete_dictionary(system_id: str) -> Response:
    if not dics().delete(system_id):
        raise NotFound("No dictionary for source system " + system_id)
    return Response(status_code=204)


@app.get("/api/dictionary/{system_id}/bcs", tags=["Dictionary"],
         summary="Business components matching q (by name, or by one of their physical tables)")
def dictionary_bcs(system_id: str, q: str = "", limit: int = Query(50, ge=1, le=500)) -> List[Dict[str, Any]]:
    return dics().bcs(system_id, q, limit)


@app.get("/api/dictionary/{system_id}/fields", tags=["Dictionary"],
         summary="Dictionary rows: optionally one business component; q matches BC field, DD attribute, "
                 "table, column or data item (by narrows it)")
def dictionary_fields(system_id: str, bc: str = "", q: str = "",
                      by: str = Query("any", pattern="^(any|field|dd|table|column|item)$"),
                      limit: int = Query(50, ge=1, le=2000)) -> List[Dict[str, Any]]:
    return dics().search(system_id, bc, q, by, limit)


@app.post("/api/entities/{entity_id}/attributes/from-dictionary", tags=["Dictionary"], status_code=201,
          summary="Add columns for dictionary fields (BC field, table.column, DD reference and data item filled in)")
def add_from_dictionary(entity_id: str, body: AddFromDictionary) -> Dict[str, Any]:
    m0 = store().load()
    e0 = Store.find(m0["entities"], entity_id, "entity")
    sid = e0.get("source_system_id") or ""
    if not dics().has(sid):
        raise Invalid("The entity's source system has no dictionary loaded.")
    added: List[str] = []
    skipped: List[str] = []

    def fn(m):
        e = Store.find(m["entities"], entity_id, "entity")
        names = {a["api_name"] for a in e["attributes"]}
        have = {(dictionary.column_bc(e, a).lower(), (a.get("source_field") or "").lower()) for a in e["attributes"]}
        for f_ in body.fields:
            r = dics().field(sid, f_.bc, f_.field)
            if not r:
                raise Invalid("'" + f_.field + "' is not on business component '" + f_.bc + "' in the dictionary.")
            if (r["bc"].lower(), r["field"].lower()) in have:
                skipped.append(r["field"])
                continue
            bn = (r["item"] if body.business_name_from == "item" and r["item"] else r["field"]).strip()
            api = naming.camel(bn) or naming.camel(r["field"])
            base, i = api, 2
            while api in names:
                api, i = base + str(i), i + 1
            names.add(api)
            a = Attribute(business_name=bn, api_name=api).model_dump()
            a["id"] = new_id("at")
            if r["bc"].lower() != (e.get("source_object") or "").lower():
                a["source_object"] = r["bc"]
            dictionary.fill_attribute(a, r, overwrite=True)
            e["attributes"].append(a)
            added.append(a["id"])
            have.add((r["bc"].lower(), r["field"].lower()))
        return lambda saved: {"added": [x for x in Store.find(saved["entities"], entity_id, "entity")["attributes"]
                                        if x["id"] in added], "skipped": skipped}
    return edit(fn)


@app.post("/api/entities/{entity_id}/fill-from-dictionary", tags=["Dictionary"],
          summary="Fill table.column, DD reference and data item for columns whose BC field is in the dictionary")
def fill_from_dictionary(entity_id: str, overwrite: bool = False) -> Dict[str, Any]:
    changed: Dict[str, List[str]] = {}
    missing: List[str] = []

    def fn(m):
        e = Store.find(m["entities"], entity_id, "entity")
        sid = e.get("source_system_id") or ""
        if not dics().has(sid):
            raise Invalid("The entity's source system has no dictionary loaded.")
        for a in e["attributes"]:
            bc = dictionary.column_bc(e, a)
            if not bc or not a.get("source_field"):
                continue
            r = dics().field(sid, bc, a["source_field"])
            if not r:
                missing.append(a["api_name"])
                continue
            ch = dictionary.fill_attribute(a, r, overwrite)
            if ch:
                changed[a["api_name"]] = ch
        return lambda saved: {"changed": changed, "not_found": missing}
    return edit(fn)


# -- name consistency ------------------------------------------------------------------

@app.get("/api/names/similar", tags=["Columns"],
         summary="Columns with names like these (business name, API name, DD attribute), with where they are used")
def similar_names(business: str = "", api: str = "", dd: str = "", exclude: str = "",
                  location: str = Query("", description="TABLE.COLUMN: columns mapped to the same physical column"),
                  threshold: float = Query(0.65, ge=0.3, le=1.0), limit: int = Query(12, ge=1, le=50)) -> List[Dict[str, Any]]:
    if not (business.strip() or api.strip() or dd.strip() or location.strip()):
        return []
    return names.similar(store().load(), business.strip(), api.strip(), dd.strip(), exclude, threshold, limit,
                         location.strip())


# -- money columns ---------------------------------------------------------------------

@app.get("/api/money-candidates", tags=["Columns"],
         summary="Number columns whose names suggest an amount of money (amount, price, liability, balance, …)")
def money_candidates() -> List[Dict[str, Any]]:
    import re as _re
    from .openapi_gen import MONEY_RE
    out = []
    for e in store().load()["entities"]:
        for a in e["attributes"]:
            if a["key"] or a.get("money"):
                continue
            named = _re.search(MONEY_RE, (a["business_name"] + " " + a["api_name"]).lower())
            if a["type"] in ("number", "integer") and named or (a["type"] == "string" and named and
                                                                 _re.search(r"(amount|price)", a["api_name"].lower())):
                out.append({"entity_id": e["id"], "entity": e["business_name"], "schema_name": e["schema_name"],
                            "attr_id": a["id"], "api_name": a["api_name"], "business_name": a["business_name"],
                            "type": a["type"], "format": a["format"], "example": a["example"],
                            "transform": a["transform"]})
    return out


@app.post("/api/attributes/to-money", tags=["Columns"],
          summary="Flag columns as money: { amount, currency }. With amount_type integer (default) the amount becomes "
                  "an integer in pence, and a column that was a decimal gets a pounds-to-pence transformation note")
def attributes_to_money(body: AttributeIds) -> Dict[str, Any]:
    done: List[str] = []

    def fn(m):
        to_int = body.amount_type == "integer"
        want = set(body.ids)
        for e in m["entities"]:
            for a in e["attributes"]:
                if a["id"] not in want:
                    continue
                was = a["type"]
                a["money"] = True
                if to_int or was not in ("integer", "number"):
                    a["type"], a["format"], a["max_length"], a["enum"] = "integer", "", None, []
                if a["type"] == "integer" and was != "integer" and not (a.get("transform") or "").strip():
                    a["transform"] = "Source amount in pounds × 100 = amount in pence; currency " + \
                                     ((m.get("settings") or {}).get("money_currency") or "GBP") + "."
                done.append(a["id"])
        missing = want - set(done)
        if missing:
            raise NotFound("column " + sorted(missing)[0] + " not found")
        return lambda saved: {"converted": done}
    return edit(fn)


# -- logical groups (one per entity; drawn as frames on the diagram) ----------------------

@app.get("/api/groups", tags=["Entities"], summary="Logical groups with their entities")
def list_groups() -> List[Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for e in store().load()["entities"]:
        if e.get("group"):
            out.setdefault(e["group"].lower(), {"name": e["group"], "entity_ids": []})["entity_ids"].append(e["id"])
    return sorted(out.values(), key=lambda g: g["name"].lower())


@app.put("/api/groups/{name}", tags=["Entities"],
         summary="Set exactly which entities are in a group (an entity is in one group; empty list removes the group)")
def set_group(name: str, body: GroupMembers) -> Dict[str, Any]:
    nm = " ".join(name.split())
    if not nm:
        raise Invalid("A group needs a name.")
    want = set(body.entity_ids)

    def fn(m):
        for i in want:
            Store.find(m["entities"], i, "entity")
        for e in m["entities"]:
            if e["id"] in want:
                e["group"] = nm
            elif (e.get("group") or "").lower() == nm.lower():
                e["group"] = ""
        return lambda saved: {"name": nm, "entity_ids": [e["id"] for e in saved["entities"] if e.get("group") == nm]}
    return edit(fn)


@app.post("/api/groups/{name}/rename", tags=["Entities"], summary="Rename a group (merges into an existing one)")
def rename_group(name: str, body: TagRename) -> Dict[str, Any]:
    new = " ".join(body.name.split())
    if not new:
        raise Invalid("A group needs a name.")

    def fn(m):
        hit = [e for e in m["entities"] if (e.get("group") or "").lower() == name.lower()]
        if not hit:
            raise NotFound("No entity is in the group '" + name + "'.")
        for e in hit:
            e["group"] = new
        return lambda saved: {"name": new}
    return edit(fn)


@app.delete("/api/groups/{name}", tags=["Entities"], status_code=204, summary="Remove a group (entities stay)")
def delete_group(name: str) -> Response:
    def fn(m):
        for e in m["entities"]:
            if (e.get("group") or "").lower() == name.lower():
                e["group"] = ""
    edit(fn)
    return Response(status_code=204)


@app.get("/api/tags", tags=["Entities"], summary="Tags in use, with the entities that have each")
def list_tags() -> List[Dict[str, Any]]:
    m = store().load()
    out: Dict[str, Dict[str, Any]] = {}
    for e in m["entities"]:
        for t in e.get("tags") or []:
            out.setdefault(t.lower(), {"name": t, "entity_ids": []})["entity_ids"].append(e["id"])
    return sorted(out.values(), key=lambda x: x["name"].lower())


@app.put("/api/tags/{tag}", tags=["Entities"], summary="Set exactly which entities have a tag (empty list removes it)")
def set_tag(tag: str, body: TagMembers) -> Dict[str, Any]:
    name = (clean_tags([tag]) or [""])[0]
    if not name:
        raise Invalid("A tag needs a name.")
    want = set(body.entity_ids)

    def fn(m):
        for i in want:
            Store.find(m["entities"], i, "entity")
        for e in m["entities"]:
            rest = [t for t in e.get("tags") or [] if t.lower() != name.lower()]
            e["tags"] = rest + [name] if e["id"] in want else rest
        return lambda saved: {"name": name, "entity_ids": [e["id"] for e in saved["entities"]
                                                           if any(t.lower() == name.lower() for t in e["tags"])]}
    return edit(fn)


@app.post("/api/tags/{tag}/rename", tags=["Entities"], summary="Rename a tag on every entity (merges into an existing tag)")
def rename_tag(tag: str, body: TagRename) -> Dict[str, Any]:
    new = (clean_tags([body.name]) or [""])[0]
    if not new:
        raise Invalid("A tag needs a name.")

    def fn(m):
        hit = False
        for e in m["entities"]:
            ts = e.get("tags") or []
            if any(t.lower() == tag.lower() for t in ts):
                hit = True
                e["tags"] = clean_tags([new if t.lower() == tag.lower() else t for t in ts])
        if not hit:
            raise NotFound("No entity has the tag '" + tag + "'.")
        return lambda saved: {"name": new}
    return edit(fn)


@app.delete("/api/tags/{tag}", tags=["Entities"], status_code=204, summary="Remove a tag from every entity")
def delete_tag(tag: str) -> Response:
    def fn(m):
        for e in m["entities"]:
            e["tags"] = [t for t in e.get("tags") or [] if t.lower() != tag.lower()]
    edit(fn)
    return Response(status_code=204)


@app.post("/api/positions", tags=["Entities"], summary="Save diagram positions {entityId: {x, y}}")
def save_positions(body: Dict[str, Dict[str, float]] = Body(...)) -> Dict[str, str]:
    s = store()
    with s.lock:
        m = s.load()
        for e in m["entities"]:
            if e["id"] in body:
                e["x"] = round(float(body[e["id"]]["x"]))
                e["y"] = round(float(body[e["id"]]["y"]))
        s.save(m)
    return {"status": "saved"}


# -- attributes (columns) ----------------------------------------------------------

@app.post("/api/entities/{entity_id}/attributes", tags=["Columns"], status_code=201)
def create_attribute(entity_id: str, body: Attribute,
                     after: Optional[str] = Query(None, description="Insert after this column id")) -> Dict[str, Any]:
    aid = new_id("at")

    def fn(m):
        e = Store.find(m["entities"], entity_id, "entity")
        d = body.model_dump()
        d["id"] = aid
        pos = len(e["attributes"])
        if after:
            for i, a in enumerate(e["attributes"]):
                if a["id"] == after:
                    pos = i + 1
        e["attributes"].insert(pos, d)
        return lambda saved: store().entity_and_attr(saved, aid)[1]
    return edit(fn)


@app.patch("/api/attributes/{attr_id}", tags=["Columns"])
def patch_attribute(attr_id: str, body: AttributePatch) -> Dict[str, Any]:
    def fn(m):
        e, a = store().entity_and_attr(m, attr_id)
        changes = body.model_dump(exclude_unset=True)
        if changes.get("key"):
            for other in e["attributes"]:
                other["key"] = False
        a.update(changes)
        return lambda saved: store().entity_and_attr(saved, attr_id)[1]
    return edit(fn)


@app.delete("/api/attributes/{attr_id}", tags=["Columns"], status_code=204)
def delete_attribute(attr_id: str) -> Response:
    def fn(m):
        e, _a = store().entity_and_attr(m, attr_id)
        e["attributes"] = [a for a in e["attributes"] if a["id"] != attr_id]
    edit(fn)
    return Response(status_code=204)


@app.post("/api/entities/{entity_id}/attributes/order", tags=["Columns"], summary="Reorder columns")
def order_attributes(entity_id: str, ids: List[str] = Body(..., embed=True)) -> Dict[str, Any]:
    def fn(m):
        e = Store.find(m["entities"], entity_id, "entity")
        by_id = {a["id"]: a for a in e["attributes"]}
        if sorted(ids) != sorted(by_id):
            raise Invalid("Send every column id of the entity exactly once.")
        e["attributes"] = [by_id[i] for i in ids]
        return lambda saved: Store.find(saved["entities"], entity_id, "entity")
    return edit(fn)


# -- actions (operations beyond create/read/update/delete) ----------------------------

def _entity_and_action(m: Dict[str, Any], action_id: str):
    for e in m["entities"]:
        for a in e.get("actions", []):
            if a["id"] == action_id:
                return e, a
    raise NotFound("action " + action_id + " not found")


@app.post("/api/entities/{entity_id}/actions", tags=["Actions"], status_code=201)
def create_action(entity_id: str, body: Action) -> Dict[str, Any]:
    aid = new_id("ac")

    def fn(m):
        e = Store.find(m["entities"], entity_id, "entity")
        d = body.model_dump()
        d["id"] = aid
        e.setdefault("actions", []).append(d)
        return lambda saved: _entity_and_action(saved, aid)[1]
    return edit(fn)


@app.patch("/api/actions/{action_id}", tags=["Actions"])
def patch_action(action_id: str, body: ActionPatch) -> Dict[str, Any]:
    def fn(m):
        _e, a = _entity_and_action(m, action_id)
        changes = body.model_dump(exclude_unset=True)
        if "name" in changes and "path" not in changes and a["path"] == naming.kebab(a["name"]):
            changes["path"] = ""  # path followed the name: keep following it
        a.update(changes)
        return lambda saved: _entity_and_action(saved, action_id)[1]
    return edit(fn)


@app.delete("/api/actions/{action_id}", tags=["Actions"], status_code=204)
def delete_action(action_id: str) -> Response:
    def fn(m):
        e, _a = _entity_and_action(m, action_id)
        e["actions"] = [a for a in e["actions"] if a["id"] != action_id]
    edit(fn)
    return Response(status_code=204)


# -- worked examples ------------------------------------------------------------------

@app.get("/api/examples", tags=["Model"], summary="Examples that can be added to the model")
def list_examples() -> Dict[str, str]:
    return examples.EXAMPLES


@app.post("/api/examples/{name}", tags=["Model"], summary="Add a worked example to the model (apply=false previews)")
def add_example(name: str, apply: bool = False) -> Dict[str, Any]:
    if name not in examples.EXAMPLES:
        raise NotFound("example " + name + " not found")
    s = store()
    current = s.load()
    merged, notes = examples.add_example(current, name)
    added = [e["business_name"] for e in merged["entities"] if e["id"] not in {x["id"] for x in current["entities"]}]
    issues = validate(merged)
    result = {"added": added, "notes": notes, "errors": sum(1 for i in issues if i["level"] == "error"),
              "warnings": sum(1 for i in issues if i["level"] == "warning")}
    if not apply:
        return dict(result, applied=False)
    s.replace(merged, keep_positions=False)
    return dict(result, applied=True)


# -- relationships (linked entities) -------------------------------------------------

@app.post("/api/relationships", tags=["Linked entities"], status_code=201)
def create_relationship(body: Relationship) -> Dict[str, Any]:
    rid = new_id("rl")

    def fn(m):
        Store.find(m["entities"], body.from_entity_id, "entity")
        Store.find(m["entities"], body.to_entity_id, "entity")
        d = body.model_dump()
        d["id"] = rid
        m["relationships"].append(d)
        return lambda saved: Store.find(saved["relationships"], rid, "link")
    return edit(fn)


@app.patch("/api/relationships/{rel_id}", tags=["Linked entities"])
def patch_relationship(rel_id: str, body: RelationshipPatch) -> Dict[str, Any]:
    def fn(m):
        r = Store.find(m["relationships"], rel_id, "link")
        r.update(body.model_dump(exclude_unset=True))
        Store.find(m["entities"], r["from_entity_id"], "entity")
        Store.find(m["entities"], r["to_entity_id"], "entity")
        return lambda saved: Store.find(saved["relationships"], rel_id, "link")
    return edit(fn)


@app.delete("/api/relationships/{rel_id}", tags=["Linked entities"], status_code=204)
def delete_relationship(rel_id: str) -> Response:
    def fn(m):
        Store.find(m["relationships"], rel_id, "link")
        m["relationships"] = [r for r in m["relationships"] if r["id"] != rel_id]
    edit(fn)
    return Response(status_code=204)


# -- generated outputs -------------------------------------------------------------

@app.get("/api/openapi/{name}", tags=["Outputs"], summary="OpenAPI for a namespace: /api/openapi/cm.yaml or cm.json")
def get_openapi(name: str, download: bool = False) -> Response:
    key, _, ext = name.rpartition(".")
    if ext not in ("json", "yaml", "yml") or not key:
        key, ext = name, "json"
    m = store().load()
    n = _ns_by_key(m, key)
    doc = build_openapi(m, n["id"])
    headers = {}
    if download:
        headers["Content-Disposition"] = 'attachment; filename="' + n["key"] + "-openapi." + ("json" if ext == "json" else "yaml") + '"'
    if ext == "json":
        return Response(json.dumps(doc, indent=2, ensure_ascii=False), media_type="application/json", headers=headers)
    return Response(to_yaml(doc), media_type="application/yaml", headers=headers)


def to_yaml(doc: Dict[str, Any]) -> str:
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110)


@app.get("/api/endpoints", tags=["Outputs"], summary="Every generated operation")
def get_endpoints() -> List[Dict[str, str]]:
    return endpoints(store().load())


@app.get("/api/mapping", tags=["Outputs"], summary="Mapping rows as JSON")
def get_mapping() -> Dict[str, Any]:
    m = store().load()
    return {"columns": mapping_sheet.mapping_rows(m), "links": mapping_sheet.relationship_rows(m)}


@app.get("/api/export/mapping.xlsx", tags=["Outputs"], summary="Excel mapping sheet")
def export_mapping_xlsx() -> Response:
    m = store().load()
    name = naming.kebab(m["settings"].get("title") or "api") + "-mapping.xlsx"
    return Response(mapping_sheet.export_xlsx(m),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="' + name + '"'})


@app.get("/api/export/mapping.csv", tags=["Outputs"], summary="Column mapping as CSV")
def export_mapping_csv() -> Response:
    return Response(mapping_sheet.export_csv(store().load()), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="mapping.csv"'})


@app.post("/api/import/mapping", tags=["Outputs"],
          summary="Import an Excel mapping sheet (raw .xlsx bytes as the body). apply=false previews only.")
async def import_mapping(request: Request, apply: bool = False) -> Dict[str, Any]:
    data = await request.body()
    if not data:
        raise Invalid("Send the .xlsx file as the request body.")
    s = store()
    current = s.load()
    m, notes = mapping_sheet.import_xlsx(data, current)
    issues = validate(m)
    summary = {"namespaces": len(m["namespaces"]), "entities": len(m["entities"]),
               "columns": sum(len(e["attributes"]) for e in m["entities"]), "links": len(m["relationships"])}
    if apply:
        backup = s.backup()
        m = s.replace(m)
        return {"applied": True, "backup": backup, "summary": summary, "notes": notes, "issues": validate(m)}
    return {"applied": False, "summary": summary, "notes": notes, "issues": issues}


OPERATION_SETS = {"none": [], "read": ["list", "get"], "crud": ["list", "get", "create", "patch", "delete"]}


@app.post("/api/import/erd", tags=["Model"],
          summary="Import an ERD: Mermaid erDiagram text, Markdown, or an HTML page with Mermaid blocks (raw body)")
async def import_erd(request: Request, apply: bool = False,
                     mode: str = Query("merge", pattern="^(merge|replace)$"),
                     namespaces: str = Query("per-area", pattern="^(per-area|single)$"),
                     namespace_id: str = "",
                     operations: str = Query("none", pattern="^(none|read|crud)$"),
                     clash: str = Query("link", pattern="^(link|separate)$"),
                     tags: str = Query("", description="Comma separated tags for every imported entity"),
                     area_tags: bool = Query(False, description="Also tag each entity with its subject area")) -> Dict[str, Any]:
    raw = await request.body()
    if not raw.strip():
        raise Invalid("Send the ERD (Mermaid text or the page) as the request body.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    s = store()
    current = s.load()
    if namespaces == "single" and namespace_id:
        Store.find(current["namespaces"], namespace_id, "namespace")
    servers = current["namespaces"][0]["servers"] if current["namespaces"] else []
    frag = erd_import.build(text, namespace_mode=namespaces, namespace_id=namespace_id,
                            operations=OPERATION_SETS[operations], base_model=current if mode == "merge" else None,
                            servers=servers, tags=clean_tags(tags), area_tags=area_tags)
    tag_by_name = {}
    for fe in frag["entities"]:
        if fe.get("tags"):
            tag_by_name[fe["schema_name"].lower()] = tag_by_name[fe["business_name"].lower()] = fe["tags"]
    frag_ids = {fe["id"] for fe in frag["entities"]}
    merged, merge_notes = erd_import.merge(current, frag, mode, clash)
    if clash == "link" and tag_by_name:
        # an entity the import linked to (same name) joins the import's tags too
        for e in merged["entities"]:
            if e["id"] not in frag_ids:
                extra = tag_by_name.get(e["schema_name"].lower()) or tag_by_name.get(e["business_name"].lower())
                if extra:
                    e["tags"] = clean_tags((e.get("tags") or []) + extra)
    issues = validate(merged)
    new_ns = [{"key": n["key"], "title": n["title"]} for n in merged["namespaces"]
              if n["key"] not in {x["key"] for x in current["namespaces"]}]
    result = {"summary": frag["summary"], "notes": frag["notes"] + merge_notes, "new_namespaces": new_ns,
              "errors": sum(1 for i in issues if i["level"] == "error"),
              "warnings": sum(1 for i in issues if i["level"] == "warning"),
              "error_samples": [i["where"] + ": " + i["message"] for i in issues if i["level"] == "error"][:8],
              "title": frag["title"]}
    if not apply:
        return dict(result, applied=False)
    m = s.replace(merged, keep_positions=False)
    return dict(result, applied=True, entities=len(m["entities"]))


def _diagram_filters(m: Dict[str, Any], ns: Optional[str], tags: str, src: str,
                     linked: Optional[bool]) -> Dict[str, Any]:
    return {"ns_id": _ns_by_key(m, ns)["id"] if ns else None, "tags": clean_tags(tags) or None,
            "src": src or None, "linked": linked}


@app.get("/api/export/diagram.mmd", tags=["Outputs"], summary="Mermaid erDiagram")
def export_mermaid(labels: str = "business", ns: Optional[str] = None, download: bool = False,
                   tags: str = Query("", description="Comma separated: entities with any of these tags"),
                   src: str = Query("", description="Source system id, or - for not sourced"),
                   linked: Optional[bool] = Query(None, description="Also show what the kept entities link to")) -> Response:
    m = store().load()
    headers = {"Content-Disposition": 'attachment; filename="data-model.mmd"'} if download else {}
    return PlainTextResponse(diagram.mermaid(m, labels, **_diagram_filters(m, ns, tags, src, linked)), headers=headers)


@app.get("/api/export/diagram.drawio", tags=["Outputs"], summary="draw.io (diagrams.net) file")
def export_drawio(labels: str = "business", ns: Optional[str] = None,
                  tags: str = Query("", description="Comma separated: entities with any of these tags"),
                  src: str = Query("", description="Source system id, or - for not sourced"),
                  linked: Optional[bool] = Query(None, description="Also show what the kept entities link to")) -> Response:
    m = store().load()
    return Response(diagram.drawio(m, labels, **_diagram_filters(m, ns, tags, src, linked)), media_type="application/xml",
                    headers={"Content-Disposition": 'attachment; filename="data-model.drawio"'})


def bundle(m: Dict[str, Any], labels: str = "business") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n in m["namespaces"]:
            doc = build_openapi(m, n["id"])
            z.writestr("openapi/" + n["key"] + "-openapi.yaml", to_yaml(doc))
            z.writestr("openapi/" + n["key"] + "-openapi.json", json.dumps(doc, indent=2, ensure_ascii=False))
        z.writestr("mapping/mapping.xlsx", mapping_sheet.export_xlsx(m))
        z.writestr("mapping/mapping.csv", mapping_sheet.export_csv(m))
        z.writestr("diagram/data-model.mmd", diagram.mermaid(m, labels))
        z.writestr("diagram/data-model.drawio", diagram.drawio(m, labels))
        z.writestr("model.json", json.dumps(m, indent=2, ensure_ascii=False))
        issues = validate(m)
        z.writestr("validation.txt", "\n".join(i["level"].upper() + "  " + i["where"] + ": " + i["message"]
                                               for i in issues) or "No issues.\n")
    return buf.getvalue()


@app.get("/api/export/bundle.zip", tags=["Outputs"], summary="Everything: OpenAPI per namespace, mapping sheet, diagram, model")
def export_bundle(labels: str = "business") -> Response:
    m = store().load()
    name = naming.kebab(m["settings"].get("title") or "api") + "-definitions.zip"
    return Response(bundle(m, labels), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="' + name + '"'})
