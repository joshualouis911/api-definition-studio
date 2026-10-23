"""JSON-file store for the design model.

The model lives in one file (default ./studio-data/model.json, override with
ADS_DATA_DIR). Every write is atomic, and imports keep a timestamped backup."""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import os
import secrets
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import naming
from .model import SUB_OPS, Model, clean_tags
from . import variants
from .source_kinds import KINDS, default_tokens
from .sample import sample_model


SCHEMA_VERSION = 4  # 2: source systems (1.2.0); 3: standard headers (1.5.0); 4: money is a column flag (1.15.0)


def standard_headers() -> list:
    """The standard headers, added to every model from 1.5.0. Authorization
    is not here: it comes from each namespace's security setting."""
    def h(id_, name, desc, example, fmt="", direction="request", required=True):
        return {"id": id_, "name": name, "description": desc, "direction": direction, "required": required,
                "type": "string", "format": fmt, "pattern": "", "example": example, "methods": [], "namespace_ids": []}
    return [
        h("hd_request_id", "request-id", "Unique ID of this request, set by the caller. Returned on the response.",
          "6f1c2d1e-9b7a-4c55-8f0e-3b2a1d4c5e6f", "uuid", "both"),
        h("hd_date_time", "date-time", "Date and time the request was sent (RFC 3339, UTC).",
          "2026-10-20T16:49:03Z", "date-time"),
        h("hd_correlation_id", "correlation-id",
          "ID shared by every request in one business transaction, across systems. Returned on the response.",
          "c0a8012e-5f3d-4b8b-9e21-77d4a6b2f001", "uuid", "both"),
        h("hd_journey_instance_id", "journey-instance-id",
          "ID of the customer or agent journey instance the request belongs to.", "JRN-2026-000184213"),
        h("hd_instigating_system", "instigating-system", "The system that started the request.", "SIEBEL"),
    ]


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


def new_id(prefix: str) -> str:
    return prefix + "_" + secrets.token_hex(4)


def fill_defaults(m: Dict[str, Any]) -> Dict[str, Any]:
    """Derive any blank technical names from business names and give every
    object an id, so a model typed quickly (or imported) is always complete."""
    # 1.14 had a 'money' type; from 1.15 money is a flag on an integer (pence) or number column
    dec = ((m.get("settings") or {}).get("money_amount") == "decimal")
    for e_ in m.get("entities", []):
        for a_ in e_.get("attributes", []):
            if a_.get("type") == "money":
                a_["type"], a_["money"] = ("number" if dec else "integer"), True
    m = Model.model_validate(m).model_dump()
    if m["schema_version"] < 3 and not m["headers"]:
        m["headers"] = standard_headers()
    if m["schema_version"] < 2:
        # 1.x models had one implied source system (settings.source_system, normally Siebel)
        if not m["source_systems"] and m["entities"]:
            name = (m["settings"].get("source_system") or "Siebel CRM").strip()
            kind = "siebel" if "siebel" in name.lower() else "other"
            m["source_systems"].append({"id": "ss_" + (naming.kebab(name).replace("-", "_") or "source"),
                                        "key": naming.kebab(name) or "source", "name": name, "kind": kind,
                                        "description": "", "owner": "", "internal_tokens": None})
            for e in m["entities"]:
                if not e.get("source_system_id"):
                    e["source_system_id"] = m["source_systems"][0]["id"]
    m["schema_version"] = max(m["schema_version"], SCHEMA_VERSION)
    for hd in m["headers"]:
        hd["id"] = hd["id"] or new_id("hd")
        hd["name"] = hd["name"].strip()
        hd["methods"] = [x.upper() for x in hd["methods"] if x]
    for s in m["source_systems"]:
        s["id"] = s["id"] or new_id("ss")
        s["key"] = s["key"].strip()
        s["name"] = (s["name"] or s["key"]).strip()
        if s["kind"] not in KINDS and s["kind"].lower() in KINDS:
            s["kind"] = s["kind"].lower()
        if s["internal_tokens"] is None:
            s["internal_tokens"] = default_tokens(s["kind"])
        s["internal_tokens"] = [t.strip().lower() for t in s["internal_tokens"] if t and t.strip()]
    for ns in m["namespaces"]:
        ns["id"] = ns["id"] or new_id("ns")
        ns["key"] = ns["key"].strip()
        if not ns["base_path"]:
            ns["base_path"] = "/" + ns["key"] + "/v1"
        ns["base_path"] = "/" + ns["base_path"].strip().strip("/") if ns["base_path"].strip("/") else ""
        ns["title"] = ns["title"] or (ns["key"].upper() + " API")
        ns["enum_style"] = ns.get("enum_style") or "UPPER_SNAKE"
        ns["source_in_openapi"] = ns.get("source_in_openapi", True) is not False
    ns_style = {ns["id"]: ns["enum_style"] for ns in m["namespaces"]}
    for e in m["entities"]:
        e["id"] = e["id"] or new_id("en")
        e["business_name"] = e["business_name"].strip()
        e["api_name"] = (e["api_name"] or naming.camel(e["business_name"])).strip()
        e["plural"] = re.sub(r"/+", "/", (e["plural"] or naming.kebab(naming.plural(e["api_name"]))).strip()).strip("/")
        e["schema_name"] = (e["schema_name"] or naming.pascal(e["api_name"])).strip()
        e["operations"] = [o for o in e["operations"] if o]
        e["tags"] = clean_tags(e.get("tags") or [])
        e["group"] = " ".join((e.get("group") or "").split())
        for g in e.get("column_groups") or []:
            g["id"] = g["id"] or new_id("cg")
            g["business_name"] = (g["business_name"] or " ".join(naming.words(g["api_name"])).capitalize()).strip()
            g["api_name"] = (g["api_name"] or naming.camel(g["business_name"])).strip()
        variants.clean(e, new_id)
        gids = {g["id"] for g in e.get("column_groups") or []}
        for a in e["attributes"]:
            a["id"] = a["id"] or new_id("at")
            if a.get("group_id") and a["group_id"] not in gids:
                a["group_id"] = ""
            a["business_name"] = a["business_name"].strip()
            a["api_name"] = (a["api_name"] or naming.camel(a["business_name"])).strip()
            if a["type"] in ("date", "date-time") and a.get("example"):
                a["example"] = naming.iso_example(a["type"], a["example"])   # examples in ISO 8601
            if a["type"] != "enum":
                a["enum"] = []
            for v in a["enum"]:
                v["code"] = v["code"].strip() or naming.enum_code(v["label"], ns_style.get(e["namespace_id"], "UPPER_SNAKE"))
                v["label"] = v["label"] or v["code"]
    cards = {"one-to-many": True, "many-to-many": True}
    inverse_many = {"many-to-one": True, "many-to-many": True}
    for r in m["relationships"]:
        r["id"] = r["id"] or new_id("rl")
        # a side exposed as a sub-resource always has at least its read operation
        if r["expose"] == "subresource" and not r["sub_ops"]:
            r["sub_ops"] = ["list"] if cards.get(r["cardinality"]) else ["get"]
        if r["inverse_expose"] == "subresource" and not r["inverse_sub_ops"]:
            r["inverse_sub_ops"] = ["list"] if inverse_many.get(r["cardinality"]) else ["get"]
        r["sub_ops"] = [o for o in SUB_OPS if o in r["sub_ops"]]
        r["inverse_sub_ops"] = [o for o in SUB_OPS if o in r["inverse_sub_ops"]]
    for e in m["entities"]:
        for act in e["actions"]:
            act["id"] = act["id"] or new_id("ac")
            act["name"] = act["name"].strip()
            act["path"] = (act["path"] or naming.kebab(act["name"])).strip()
            act["method"] = act["method"].upper()
        e["op_overrides"] = {k: v for k, v in e["op_overrides"].items()
                             if any(v.get(x) for x in ("operation_id", "status", "request_entity_id", "request_mode", "response_entity_id",
                                                        "response_mode", "response_fields", "summary", "description"))
                             or v.get("query_params") is not None}
        for a in e["attributes"]:
            if not a.get("filterable"):
                a["filter_required"] = False
    return m


class Store:
    def __init__(self, data_dir: Optional[str] = None):
        self.dir = Path(data_dir or os.environ.get("ADS_DATA_DIR") or "studio-data")
        self.path = self.dir / "model.json"
        self.lock = threading.RLock()
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(fill_defaults(sample_model()))
        else:
            self.load()  # upgrades an older model file now, at startup

    # -- raw file access ---------------------------------------------------
    def load(self) -> Dict[str, Any]:
        """Read the model. A file saved by an older version is upgraded and
        written back, after copying the original to a backup."""
        with self.lock:
            m = json.loads(self.path.read_text(encoding="utf-8"))
            if m.get("schema_version", 1) < SCHEMA_VERSION or "source_systems" not in m:
                self.backup("before-upgrade")
                m = fill_defaults(m)
                self._write(m)
            return m

    def _write(self, m: Dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def save(self, m: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            m = fill_defaults(m)
            self._write(m)
            return m

    def backup(self, label: str = "") -> str:
        with self.lock:
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = self.dir / ("model-" + stamp + ("-" + label if label else "") + ".bak.json")
            shutil.copyfile(self.path, dest)
            return dest.name

    def replace(self, m: Dict[str, Any], keep_positions: bool = True) -> Dict[str, Any]:
        """Replace the whole model (import). Keeps diagram positions of entities
        whose schema name still exists, and backs up the old model first."""
        with self.lock:
            old = self.load()
            self.backup()
            m = fill_defaults(m)
            if keep_positions:
                pos = {e["schema_name"]: (e.get("x"), e.get("y")) for e in old["entities"]}
                for e in m["entities"]:
                    if e.get("x") is None and e["schema_name"] in pos:
                        e["x"], e["y"] = pos[e["schema_name"]]
            self._write(m)
            return m

    def reset_sample(self) -> Dict[str, Any]:
        with self.lock:
            self.backup()
            m = fill_defaults(sample_model())
            self._write(m)
            return m

    # -- lookups -------------------------------------------------------------
    @staticmethod
    def find(items: List[Dict[str, Any]], id_: str, what: str) -> Dict[str, Any]:
        for it in items:
            if it["id"] == id_:
                return it
        raise NotFound(what + " " + id_ + " not found")

    def entity_and_attr(self, m: Dict[str, Any], attr_id: str):
        for e in m["entities"]:
            for a in e["attributes"]:
                if a["id"] == attr_id:
                    return e, a
        raise NotFound("column " + attr_id + " not found")

    # -- edits ---------------------------------------------------------------
    def mutate(self, fn) -> Any:
        """Run fn(model) under the lock, then save. fn returns the result."""
        with self.lock:
            m = self.load()
            work = copy.deepcopy(m)
            result_ref = fn(work)
            saved = self.save(work)
            return result_ref(saved) if callable(result_ref) else result_ref
