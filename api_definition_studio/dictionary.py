"""A source system's data dictionary, e.g. the Siebel dictionary export:

    Business Component (BC) Name | BC Field Name | Physical Table Name |
    Physical Column Name | DD Attribute Reference | Data Item Full Name

Uploaded as Excel (.xlsx) or CSV and kept beside the model in
dictionary.json, one list of rows per source system. The studio uses it to
suggest business components, BC fields and DD attributes, to fill a column's
table.column, DD reference and data item, to add columns from a business
component, and to check columns against it."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .store import Invalid

FIELDS = ["bc", "field", "table", "column", "dd", "item"]
LABELS = {"bc": "Business Component (BC) Name", "field": "BC Field Name", "table": "Physical Table Name",
          "column": "Physical Column Name", "dd": "DD Attribute Reference", "item": "Data Item Full Name"}
# header spellings accepted for each column (compared after normalising case, spaces and punctuation)
HEADERS = {
    "bc": ["business component (bc) name", "business component bc name", "business component name",
           "business component", "bc name", "bc"],
    "field": ["bc field name", "business component field name", "field name", "bc field", "field"],
    "table": ["physical table name", "physical table", "table name", "base table", "table"],
    "column": ["physical column name", "physical column", "column name", "column"],
    "dd": ["dd attribute reference", "dd attribute ref", "dd attribute", "dd reference", "dd ref",
           "data dictionary attribute reference", "data dictionary attribute", "dd attribute id"],
    "item": ["data item full name", "data item name", "data item", "full name", "data item full"],
}


def _norm(h: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9()]+", " ", str(h or "").lower()).split())


_WANTED = {_norm(v): k for k, vs in HEADERS.items() for v in vs}


def _header_map(cells: List[Any]) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for i, c in enumerate(cells):
        k = _WANTED.get(_norm(c))
        if k and k not in out.values():
            out[i] = k
    return out


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def parse(data: bytes, filename: str = "") -> Tuple[List[Dict[str, str]], List[str]]:
    """Rows from an .xlsx or .csv dictionary. Finds the header row (first 20 rows of
    each sheet) by the column names; needs at least the BC and BC field columns."""
    notes: List[str] = []
    if data[:4] == b"\xd0\xcf\x11\xe0":
        raise Invalid("That looks like an old .xls workbook: save it as .xlsx (or CSV) and upload again.")
    tables: List[Tuple[str, List[List[Any]]]] = []
    if data[:2] == b"PK":
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - report any unreadable workbook the same way
            raise Invalid("Could not read the workbook: " + str(exc))
        for ws in wb.worksheets:
            tables.append((ws.title, [list(r) for r in ws.iter_rows(values_only=True)]))
        wb.close()
    else:
        text = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        sample = (text or "")[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        tables.append((filename or "CSV", [r for r in csv.reader(io.StringIO(text or ""), dialect)]))

    for title, rows in tables:
        for hi, hdr in enumerate(rows[:20]):
            cols = _header_map(hdr)
            if "bc" in cols.values() and "field" in cols.values() and len(cols) >= 3:
                missing = [LABELS[k] for k in FIELDS if k not in cols.values()]
                if missing:
                    notes.append("Not found, left blank: " + ", ".join(missing) + ".")
                out: List[Dict[str, str]] = []
                seen = set()
                skipped = 0
                for r in rows[hi + 1:]:
                    rec = {k: "" for k in FIELDS}
                    for i, k in cols.items():
                        if i < len(r):
                            rec[k] = _s(r[i])
                    if not any(rec.values()):
                        continue
                    if not rec["bc"] or not rec["field"]:
                        skipped += 1
                        continue
                    key = tuple(rec[k].lower() for k in FIELDS)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(rec)
                if skipped:
                    notes.append(str(skipped) + " row(s) without a business component or BC field were skipped.")
                if len(tables) > 1:
                    notes.append("Read sheet '" + title + "'.")
                if not out:
                    raise Invalid("The dictionary has its headers but no rows.")
                return out, notes
    raise Invalid("Could not find the dictionary headers. Expected: " + ", ".join(LABELS[k] for k in FIELDS)
                  + " (at least the business component and BC field columns).")


class Dictionaries:
    """dictionary.json beside model.json: {system_id: {file, loaded, rows: [[bc, field, table, column, dd, item]]}}."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "dictionary.json"
        self.lock = threading.RLock()
        self._cache: Optional[Tuple[float, Dict[str, Any]]] = None
        self._index: Dict[str, Dict[str, Any]] = {}

    # -- storage -------------------------------------------------------------
    def _raw(self) -> Dict[str, Any]:
        with self.lock:
            if not self.path.exists():
                return {}
            mt = self.path.stat().st_mtime
            if self._cache and self._cache[0] == mt:
                return self._cache[1]
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self._cache = (mt, d)
            self._index = {}
            return d

    def _write(self, d: Dict[str, Any]) -> None:
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
            self._cache = None
            self._index = {}

    def put(self, system_id: str, rows: List[Dict[str, str]], file: str = "", mode: str = "replace") -> Dict[str, Any]:
        with self.lock:
            d = dict(self._raw())
            cur = d.get(system_id, {}).get("rows", []) if mode == "merge" else []
            seen = {tuple(x.lower() for x in r) for r in cur}
            merged = list(cur)
            for r in rows:
                t = [r.get(k, "") for k in FIELDS]
                key = tuple(x.lower() for x in t)
                if key not in seen:
                    seen.add(key)
                    merged.append(t)
            d[system_id] = {"file": file, "loaded": dt.datetime.now().isoformat(timespec="seconds"), "rows": merged}
            self._write(d)
            return self.summary(system_id)

    def delete(self, system_id: str) -> bool:
        with self.lock:
            d = dict(self._raw())
            if system_id not in d:
                return False
            d.pop(system_id)
            self._write(d)
            return True

    def has(self, system_id: str) -> bool:
        return bool(system_id) and system_id in self._raw()

    # -- reading -------------------------------------------------------------
    def rows(self, system_id: str) -> List[Dict[str, str]]:
        return self._idx(system_id)["rows"]

    def _idx(self, system_id: str) -> Dict[str, Any]:
        with self.lock:
            raw = self._raw()
            if system_id in self._index:
                return self._index[system_id]
            rows = [dict(zip(FIELDS, r)) for r in raw.get(system_id, {}).get("rows", [])]
            by_bc: Dict[str, List[Dict[str, str]]] = {}
            by_dd: Dict[str, List[Dict[str, str]]] = {}
            bc_names: Dict[str, str] = {}
            for r in rows:
                by_bc.setdefault(r["bc"].lower(), []).append(r)
                bc_names.setdefault(r["bc"].lower(), r["bc"])
                if r["dd"]:
                    by_dd.setdefault(r["dd"].lower(), []).append(r)
            ix = {"rows": rows, "by_bc": by_bc, "by_dd": by_dd, "bc_names": bc_names}
            self._index[system_id] = ix
            return ix

    def summary(self, system_id: str) -> Dict[str, Any]:
        raw = self._raw().get(system_id)
        if not raw:
            return {"system_id": system_id, "rows": 0, "bcs": 0, "tables": 0, "file": "", "loaded": ""}
        ix = self._idx(system_id)
        return {"system_id": system_id, "rows": len(ix["rows"]), "bcs": len(ix["by_bc"]),
                "tables": len({r["table"].lower() for r in ix["rows"] if r["table"]}),
                "file": raw.get("file", ""), "loaded": raw.get("loaded", "")}

    def all_summaries(self) -> List[Dict[str, Any]]:
        return [self.summary(k) for k in self._raw()]

    def bc_exists(self, system_id: str, bc: str) -> bool:
        return bc.lower() in self._idx(system_id)["by_bc"]

    def field(self, system_id: str, bc: str, field: str) -> Optional[Dict[str, str]]:
        for r in self._idx(system_id)["by_bc"].get(bc.lower(), []):
            if r["field"].lower() == field.lower():
                return r
        return None

    def bcs(self, system_id: str, q: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        """Business components whose name, or one of whose tables, matches q."""
        ix = self._idx(system_id)
        ql = q.strip().lower()
        out = []
        for key, rows in ix["by_bc"].items():
            name = ix["bc_names"][key]
            tables = sorted({r["table"] for r in rows if r["table"]})
            if ql:
                if ql in key:
                    score = 0 if key == ql else 1 if key.startswith(ql) else 2
                elif any(ql in t.lower() for t in tables):
                    score = 3
                else:
                    continue
            else:
                score = 0
            out.append((score, name.lower(), {"bc": name, "fields": len(rows), "tables": tables}))
        out.sort(key=lambda x: (x[0], x[1]))
        return [x[2] for x in out[:limit]]

    def search(self, system_id: str, bc: str = "", q: str = "", by: str = "any", limit: int = 50
               ) -> List[Dict[str, str]]:
        """Dictionary rows, optionally within one business component, matching q in the BC field,
        DD attribute, physical table or column, or data item (by = any, field, dd, table, column, item)."""
        ix = self._idx(system_id)
        rows = ix["by_bc"].get(bc.lower(), []) if bc else ix["rows"]
        ql = q.strip().lower()
        keys = [by] if by in FIELDS else ["field", "dd", "table", "column", "item"]
        out = []
        for r in rows:
            if ql:
                best = None
                for k in keys:
                    v = r[k].lower()
                    if k == "table" and r["column"]:
                        v2 = (r["table"] + "." + r["column"]).lower()
                        if ql in v2 and ql not in v:
                            v = v2
                    if ql in v:
                        s = 0 if v == ql else 1 if v.startswith(ql) else 2
                        best = s if best is None else min(best, s)
                if best is None:
                    continue
            else:
                best = 0
            out.append((best, r["bc"].lower(), r["field"].lower(), r))
        out.sort(key=lambda x: (x[0], x[1], x[2]))
        return [x[3] for x in out[:limit]]


# -- applying the dictionary to the model ---------------------------------------

def column_bc(e: Dict[str, Any], a: Dict[str, Any]) -> str:
    """The business component a column comes from: its own, or else its entity's."""
    return (a.get("source_object") or "").strip() or (e.get("source_object") or "").strip()


def location(r: Dict[str, str]) -> str:
    return (r["table"] + "." + r["column"]) if r["table"] and r["column"] else (r["table"] or r["column"])


def fill_attribute(a: Dict[str, Any], r: Dict[str, str], overwrite: bool = False) -> List[str]:
    """Copy a dictionary row's details into a column. Returns the fields changed."""
    changed = []
    for key, val in (("source_field", r["field"]), ("source_column", location(r)),
                     ("dd_reference", r["dd"]), ("data_item", r["item"])):
        if val and (overwrite or not (a.get(key) or "").strip()) and a.get(key) != val:
            a[key] = val
            changed.append(key)
    return changed


def issues(m: Dict[str, Any], dic: "Dictionaries") -> List[Dict[str, Any]]:
    """Warnings for columns that do not match their source system's dictionary."""
    out: List[Dict[str, Any]] = []

    def warn(obj: str, where: str, msg: str) -> None:
        out.append({"level": "warning", "object_id": obj, "where": where, "message": msg, "blocking": False})

    for e in m["entities"]:
        sid = e.get("source_system_id") or ""
        if not dic.has(sid):
            continue
        if e.get("source_object") and not dic.bc_exists(sid, e["source_object"]):
            warn(e["id"], e["business_name"], "Business component '" + e["source_object"] + "' is not in the dictionary.")
        for a in e["attributes"]:
            bc = column_bc(e, a)
            w = e["business_name"] + " › " + a["business_name"]
            if a.get("source_object") and not dic.bc_exists(sid, a["source_object"]):
                warn(a["id"], w, "Business component '" + a["source_object"] + "' is not in the dictionary.")
                continue
            if not bc or not a.get("source_field"):
                continue
            r = dic.field(sid, bc, a["source_field"])
            if not r:
                if dic.bc_exists(sid, bc):
                    warn(a["id"], w, "BC field '" + a["source_field"] + "' is not on business component '" + bc
                         + "' in the dictionary.")
                continue
            if r["dd"] and a.get("dd_reference") and a["dd_reference"].lower() != r["dd"].lower():
                warn(a["id"], w, "DD attribute reference is " + a["dd_reference"] + "; the dictionary says " + r["dd"] + ".")
            loc = location(r)
            if loc and a.get("source_column") and a["source_column"].lower() != loc.lower():
                warn(a["id"], w, "Table.column is " + a["source_column"] + "; the dictionary says " + loc + ".")
    return out
