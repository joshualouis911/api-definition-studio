"""Import an entity relationship model into the studio.

Accepts Mermaid `erDiagram` text, Markdown with ```mermaid blocks, or an HTML
page holding `<pre class="mermaid">` blocks (such as
tests/fixtures/sample-logical-data-model.html). From an HTML page it also reads:
  - subject areas: each <section> with a code tag (CUS, ORD, ...) and heading;
  - the entity catalogue: <td class="ent">NAME</td> rows giving what the
    entity records, the rules it carries and its source reference.

What it builds:
  - one entity per ERD entity, not sourced (logical model), with business
    names, API names, schema names and no operations (schema only);
  - one column per attribute: types mapped, PK as key, enum values from the
    attribute comment, comments kept as descriptions, FKs described;
  - one linked entity per relationship, with cardinality, label, join keys
    where the foreign key is unambiguous, and not exposed in the API until
    you choose to expose it.
"""
from __future__ import annotations

import html as htmllib
import re
from typing import Any, Dict, List, Optional, Tuple

from . import naming
from .model import clean_tags
from .store import Invalid, fill_defaults, new_id

# words kept in capitals in business names; add your organisation's own
ACRONYMS = {"uk", "us", "eu", "gbp", "usd", "eur", "vat", "sku", "url", "api", "crm", "erp", "hr",
            "sla", "kpi", "iban", "bic", "po", "sr", "org"}

TYPE_MAP = {
    "id": ("string", ""), "uuid": ("string", "uuid"), "string": ("string", ""), "text": ("string", ""),
    "varchar": ("string", ""), "char": ("string", ""), "date": ("date", ""), "datetime": ("date-time", ""),
    "timestamp": ("date-time", ""), "bool": ("boolean", ""), "boolean": ("boolean", ""), "int": ("integer", ""),
    "integer": ("integer", ""), "bigint": ("integer", "int64"), "long": ("integer", "int64"),
    "money": ("number", "decimal"), "decimal": ("number", "decimal"), "number": ("number", ""),
    "float": ("number", "double"), "double": ("number", "double"), "numeric": ("number", "decimal"),
    "enum": ("enum", ""),
}

CARD_WORDS = {"||": "exactly one", "|o": "zero or one", "o|": "zero or one",
              "}o": "zero or many", "o{": "zero or many", "}|": "one or many", "|{": "one or many"}

REL_RE = re.compile(r'^\s*([A-Za-z][\w-]*)\s+([|}][|o]|[o|][|])(--|\.\.)([|o][|{]|[o|][|])\s+([A-Za-z][\w-]*)\s*:\s*(.+?)\s*$')
ENT_OPEN_RE = re.compile(r'^\s*([A-Za-z][\w-]*)\s*(\[\s*"[^"]*"\s*\])?\s*\{\s*$')
ATTR_RE = re.compile(r'^\s*([A-Za-z][\w()]*)\s+([A-Za-z_][\w]*)\s*((?:PK|FK|UK)(?:\s*,\s*(?:PK|FK|UK))*)?\s*(?:"([^"]*)")?\s*$')


# -- reading the source ------------------------------------------------------------

def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def read_source(text: str) -> Dict[str, Any]:
    """Split the source into ERD blocks (with their subject area, if any) and
    catalogue rows."""
    blocks: List[Dict[str, Any]] = []
    catalogue: Dict[str, Dict[str, str]] = {}
    title = ""
    if re.search(r"<(html|body|section|pre)\b", text, re.I):
        m = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S | re.I) or re.search(r"<title>(.*?)</title>", text, re.S | re.I)
        title = _text(m.group(1)) if m else ""
        parts = re.split(r"(?=<section\b)", text, flags=re.I)
        for part in parts:
            code = re.search(r'class="code-tag"[^>]*>(.*?)<', part, re.S)
            head = re.search(r"<h2[^>]*>(.*?)</h2>", part, re.S | re.I)
            sub = re.search(r'<p class="muted"[^>]*>(.*?)</p>', part, re.S)
            for pre in re.findall(r'<pre class="mermaid"[^>]*>(.*?)</pre>', part, re.S | re.I):
                blocks.append({"text": htmllib.unescape(pre), "area_code": _text(code.group(1)) if code else "",
                               "area_title": _text(head.group(1)) if head else "",
                               "area_description": _text(sub.group(1)) if sub else ""})
            for row in re.findall(r"<tr>\s*<td class=\"ent\"[^>]*>(.*?)</tr>", part, re.S | re.I):
                cells = [_text(c) for c in re.split(r"</td>\s*<td[^>]*>", row)]
                if len(cells) >= 2:
                    catalogue[re.sub(r"</?td[^>]*>", "", cells[0]).strip()] = {
                        "what": cells[1] if len(cells) > 1 else "", "rules": cells[2] if len(cells) > 2 else "",
                        "ref": cells[3] if len(cells) > 3 else ""}
    else:
        fenced = re.findall(r"```mermaid\s*(.*?)```", text, re.S)
        for b in (fenced or [text]):
            blocks.append({"text": b, "area_code": "", "area_title": "", "area_description": ""})
    blocks = [b for b in blocks if re.search(r"^\s*erDiagram\b", b["text"], re.M)]
    if not blocks:
        raise Invalid("No Mermaid erDiagram found. Paste Mermaid erDiagram text, or a page containing it.")
    return {"blocks": blocks, "catalogue": catalogue, "title": title}


def parse_block(text: str) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Return ({entity: {label, attributes}}, relationships, problems) for one erDiagram."""
    entities: Dict[str, Dict[str, Any]] = {}
    rels: List[Dict[str, Any]] = []
    problems: List[str] = []
    current: Optional[str] = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("%%", 1)[0].rstrip()
        if not line.strip() or line.strip() == "erDiagram" or line.strip().startswith(("direction", "title")):
            continue
        if current:
            if line.strip() == "}":
                current = None
                continue
            m = ATTR_RE.match(line)
            if not m:
                problems.append("line " + str(n) + ": could not read attribute '" + line.strip() + "'")
                continue
            keys = {k.strip() for k in (m.group(3) or "").split(",") if k.strip()}
            entities[current]["attributes"].append({"type": m.group(1).lower(), "name": m.group(2),
                                                    "pk": "PK" in keys, "fk": "FK" in keys, "uk": "UK" in keys,
                                                    "comment": (m.group(4) or "").strip()})
            continue
        m = ENT_OPEN_RE.match(line)
        if m:
            current = m.group(1)
            ent = entities.setdefault(current, {"attributes": [], "label": ""})
            ent["attributes"] = []  # a later full definition wins
            if m.group(2):
                ent["label"] = re.search(r'"([^"]*)"', m.group(2)).group(1)
            continue
        m = REL_RE.match(line)
        if m:
            a, left, _line, right, b, label = m.groups()
            for x in (a, b):
                entities.setdefault(x, {"attributes": [], "label": ""})
            rels.append({"from": a, "to": b, "left": left, "right": right,
                         "optional_line": _line == "..", "label": label.strip().strip('"')})
            continue
        if re.match(r"^\s*[A-Za-z][\w-]*\s*$", line):
            entities.setdefault(line.strip(), {"attributes": [], "label": ""})
            continue
        problems.append("line " + str(n) + ": not understood '" + line.strip() + "'")
    return entities, rels, problems


# -- naming --------------------------------------------------------------------------

def business_name(identifier: str) -> str:
    out = []
    for w in naming.words(identifier.replace("-", "_")):
        out.append(w.upper() if w in ACRONYMS else w.capitalize())
    return " ".join(out)


def enum_values(comment: str) -> List[Dict[str, str]]:
    if not comment:
        return []
    if "," in comment:
        parts = [p.strip() for p in comment.split(",")]
        # "a, b or c" -> a, b, c
        if " or " in parts[-1] and len(parts[-1].split(" or ")) == 2:
            parts = parts[:-1] + [x.strip() for x in parts[-1].split(" or ")]
    elif " or " in comment:
        parts = [p.strip() for p in comment.split(" or ")]
    else:
        return []
    vals, seen = [], set()
    for p in parts:
        if not p:
            continue
        code = naming.upper_snake(p) or "VALUE"
        base, i = code, 2
        while code in seen:
            code = base + "_" + str(i)
            i += 1
        seen.add(code)
        vals.append({"code": code, "label": p[:1].upper() + p[1:], "source_value": p})
    return vals if len(vals) >= 2 else []


def many(marker: str) -> bool:
    return "{" in marker or "}" in marker


def card_text(left: str, right: str) -> str:
    return CARD_WORDS.get(left, left) + " to " + CARD_WORDS.get(right, right)


# -- building the model fragment -------------------------------------------------------

def build(text: str, *, namespace_mode: str = "per-area", namespace_id: str = "",
          operations: Optional[List[str]] = None, skip_overview: bool = True,
          base_model: Optional[Dict[str, Any]] = None, servers: Optional[List[Dict[str, str]]] = None,
          tags: Optional[List[str]] = None, area_tags: bool = False) -> Dict[str, Any]:
    """Turn ERD source into namespaces, entities and relationships.
    Returns {namespaces, entities, relationships, notes, summary}."""
    src = read_source(text)
    blocks = src["blocks"]
    notes: List[str] = []
    operations = list(operations or [])

    parsed = []
    for b in blocks:
        ents, rels, problems = parse_block(b["text"])
        parsed.append((b, ents, rels))
        for p in problems[:5]:
            notes.append((b["area_code"] or "diagram") + " " + p)
    # an overview diagram defines no attributes at all; skip it when other diagrams do
    has_detail = any(any(e["attributes"] for e in ents.values()) for _b, ents, _r in parsed)
    if skip_overview and has_detail and len(parsed) > 1:
        kept = [p for p in parsed if any(e["attributes"] for e in p[1].values())]
        skipped = len(parsed) - len(kept)
        if skipped:
            notes.append("Skipped " + str(skipped) + " overview diagram(s) with no attribute lists "
                         "(their relationships are repeated in detail diagrams).")
        parsed = kept

    # entity definitions: the diagram that lists attributes is the entity's home
    defs: Dict[str, Dict[str, Any]] = {}
    home: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for b, ents, _rels in parsed:
        for name, e in ents.items():
            if name not in order:
                order.append(name)
            if e["attributes"] and (name not in defs or not defs[name]["attributes"]):
                defs[name] = e
                home[name] = b
            defs.setdefault(name, e)
            home.setdefault(name, b)
    undefined = [n for n in order if not defs[n]["attributes"]]
    if undefined:
        notes.append("No attributes given for: " + ", ".join(undefined) + ".")

    # namespaces
    namespaces: List[Dict[str, Any]] = []
    ns_for: Dict[str, str] = {}
    existing_ns = {n["key"]: n for n in (base_model or {}).get("namespaces", [])}
    if namespace_mode == "per-area" and any(b["area_code"] for b, _e, _r in parsed):
        for name in order:
            b = home[name]
            code = (b["area_code"] or "model").lower()
            key = naming.kebab(code) or "model"
            if key in existing_ns:
                ns_for[name] = existing_ns[key]["id"]
                continue
            if not any(n["key"] == key for n in namespaces):
                namespaces.append({"id": "ns_" + key.replace("-", "_"), "key": key,
                                   "title": b["area_title"] or key.upper(), "base_path": "/" + key + "/v1",
                                   "version": "0.1.0", "description": b["area_description"],
                                   "security": "bearer", "owner": "", "base_path_in": "paths",
                                   "servers": [dict(s) for s in (servers or [])]})
            ns_for[name] = "ns_" + key.replace("-", "_")
    else:
        if not namespace_id:
            key = "model"
            if key in existing_ns:
                namespace_id = existing_ns[key]["id"]
            else:
                namespaces.append({"id": "ns_model", "key": key, "title": src["title"] or "Logical model",
                                   "base_path": "/model/v1", "version": "0.1.0", "description": "",
                                   "security": "bearer", "owner": "", "base_path_in": "paths",
                                   "servers": [dict(s) for s in (servers or [])]})
                namespace_id = "ns_model"
        for name in order:
            ns_for[name] = namespace_id

    # PK lookup for resolving foreign keys
    pk_of: Dict[str, List[str]] = {n: [a["name"] for a in defs[n]["attributes"] if a["pk"]] for n in order}
    single_pk = {n: v[0] for n, v in pk_of.items() if len(v) == 1}

    def fk_target(col: str, owner: str) -> str:
        own_pk = single_pk.get(owner)

        def ok(n: str) -> bool:  # an entity may refer to itself, but its own key is not a foreign key
            return n != owner or col != own_pk
        exact = [n for n, pk in single_pk.items() if pk == col and ok(n)]
        if len(exact) == 1:
            return exact[0]
        suffix = [n for n, pk in single_pk.items() if col.endswith("_" + pk) and ok(n)]
        return suffix[0] if len(suffix) == 1 else ""

    entities: List[Dict[str, Any]] = []
    ent_id: Dict[str, str] = {}
    no_single_key = [n for n in order if len(pk_of[n]) != 1]
    if operations and no_single_key:
        notes.append(str(len(no_single_key)) + " entities have no single key (composite or none) and stay schema only: "
                     + ", ".join(business_name(n) for n in no_single_key) + ".")
    fk_cols: Dict[str, List[Tuple[str, str]]] = {}
    enum_unknown = 0
    for name in order:
        d = defs[name]
        cat = src["catalogue"].get(name, {})
        desc_parts = [cat.get("what", "")]
        if cat.get("rules"):
            desc_parts.append("Rules: " + cat["rules"])
        if cat.get("ref"):
            desc_parts.append("Reference: " + cat["ref"])
        pks = pk_of[name]
        if len(pks) > 1:
            desc_parts.append("Composite key: " + ", ".join(pks) + ".")
        if d.get("label"):
            desc_parts.insert(0, d["label"])
        bname = business_name(name)
        api = naming.camel(bname)
        eid = new_id("en")
        ent_id[name] = eid
        attrs = []
        fk_cols[name] = []
        for a in d["attributes"]:
            typ, fmt = TYPE_MAP.get(a["type"], ("string", ""))
            vals: List[Dict[str, str]] = []
            desc = a["comment"]
            if typ == "enum":
                vals = enum_values(a["comment"])
                if vals:
                    desc = ""
                else:
                    typ = "string"
                    enum_unknown += 1
                    desc = ((a["comment"] + ". ") if a["comment"] else "") + "Enumerated values to be defined."
            if a["fk"]:
                tgt = fk_target(a["name"], name)
                if tgt:
                    fk_cols[name].append((a["name"], tgt))
                    desc = ("Foreign key to " + business_name(tgt) + ". " + desc).strip()
                else:
                    desc = ("Foreign key. " + desc).strip()
            if a["pk"] and len(pks) > 1:
                desc = ("Part of the composite key. " + desc).strip()
            attrs.append({
                "id": new_id("at"), "business_name": business_name(a["name"]),
                "api_name": naming.camel(a["name"]), "type": typ, "format": fmt, "max_length": None,
                "key": a["pk"] and len(pks) == 1, "required": a["pk"], "read_only": False, "system": False,
                "filterable": False, "pii": False, "enum": vals, "source_field": "", "source_column": "",
                "lov_type": "", "transform": "", "dd_reference": "", "example": "", "description": desc,
            })
        entities.append({
            "id": eid, "namespace_id": ns_for[name], "source_system_id": "", "business_name": bname,
            "api_name": api, "plural": naming.kebab(naming.plural(api)), "schema_name": naming.pascal(api),
            "description": " ".join(p for p in desc_parts if p).strip(), "source_object": "", "source_io": "",
            "operations": [] if name in no_single_key else list(operations), "x": None, "y": None, "attributes": attrs,
            "tags": clean_tags(list(tags or []) + ([re.sub(r"\s*,\s*", " / ", home[name]["area_title"] or home[name]["area_code"])]
                                                   if area_tags and name in home else [])),   # a tag has no commas
            "_erd_name": name,
        })
    if enum_unknown:
        notes.append(str(enum_unknown) + " enum attribute(s) had no listed values; imported as text, "
                     "described as 'Enumerated values to be defined'.")

    # relationships
    relationships: List[Dict[str, Any]] = []
    seen = set()
    used_names: Dict[str, set] = {n: {a["api_name"] for a in e["attributes"]}
                                  for n, e in zip(order, entities)}

    def pick(owner: str, candidates: List[str]) -> str:
        for c in candidates:
            if c and naming.CAMEL_RE.match(c) and c not in used_names[owner]:
                used_names[owner].add(c)
                return c
        base, i = candidates[0] or "link", 2
        while base + str(i) in used_names[owner]:
            i += 1
        used_names[owner].add(base + str(i))
        return base + str(i)

    for _b, _ents, rels in parsed:
        for r in rels:
            k = (r["from"], r["to"], r["label"].lower())
            if k in seen:
                continue
            seen.add(k)
            a_many, b_many = many(r["left"]), many(r["right"])
            card = ("many-to-many" if a_many and b_many else "one-to-many" if b_many
                    else "many-to-one" if a_many else "one-to-one")
            fb, tb = business_name(r["from"]), business_name(r["to"])
            label = r["label"][:1].upper() + r["label"][1:]
            to_api = naming.camel(naming.plural(tb) if b_many else tb)
            from_api = naming.camel(naming.plural(fb) if a_many else fb)
            api_name = pick(r["from"], [to_api, naming.camel(r["label"]), naming.camel(r["label"] + " " + tb)])
            inv_name = pick(r["to"], [from_api, naming.camel(from_api + " " + r["label"])])
            # join keys, when exactly one foreign key on the "many" side points at the "one" side
            join_from = join_to = ""
            if card != "many-to-many":
                if card == "one-to-many":
                    tries = [(r["from"], r["to"])]
                elif card == "many-to-one":
                    tries = [(r["to"], r["from"])]
                else:
                    tries = [(r["from"], r["to"]), (r["to"], r["from"])]
                for parent, child in tries:
                    cands = [c for c, t in fk_cols.get(child, []) if t == parent]
                    if len(cands) == 1 and single_pk.get(parent):
                        pk, fk = single_pk[parent], cands[0]
                        if parent == r["from"]:
                            join_from, join_to = r["from"] + "." + pk, r["to"] + "." + fk
                        else:
                            join_from, join_to = r["from"] + "." + fk, r["to"] + "." + pk
                        break
            relationships.append({
                "id": new_id("rl"), "from_entity_id": ent_id[r["from"]], "to_entity_id": ent_id[r["to"]],
                "cardinality": card, "business_name": label, "api_name": api_name, "expose": "none",
                "inverse_business_name": "", "inverse_api_name": inv_name, "inverse_expose": "none",
                "join_from": join_from, "join_to": join_to, "source_link": "",
                "description": "ERD: " + r["from"] + " " + r["left"] + ("..") * r["optional_line"]
                               + ("--" if not r["optional_line"] else "") + r["right"] + " " + r["to"]
                               + " (" + card_text(r["left"], r["right"]) + ")" + (", non-identifying" if r["optional_line"] else ""),
            })

    summary = {"diagrams": len(parsed), "entities": len(entities),
               "columns": sum(len(e["attributes"]) for e in entities), "links": len(relationships),
               "namespaces": len(namespaces)}
    return {"namespaces": namespaces, "entities": entities, "relationships": relationships,
            "notes": notes, "summary": summary, "title": src["title"]}


def layout(entities: List[Dict[str, Any]], start_y: float = 60) -> None:
    """Place entities grouped by namespace: one band per namespace, four columns."""
    y = start_y
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for e in entities:
        groups.setdefault(e["namespace_id"], []).append(e)
    for _ns, ents in groups.items():
        col_y = [y] * 4
        for i, e in enumerate(ents):
            c = col_y.index(min(col_y))
            e["x"], e["y"] = 60 + c * 340, col_y[c]
            col_y[c] += 46 + 22 * len(e["attributes"]) + 8 + 70
        y = max(col_y) + 120


def merge(model: Dict[str, Any], frag: Dict[str, Any], mode: str = "merge",
          clash: str = "link") -> Tuple[Dict[str, Any], List[str]]:
    """Add the fragment to the model (merge) or put it in place of the model's
    namespaces, entities and links (replace).

    An incoming entity whose schema or business name already exists is either
    linked to the existing entity (clash="link": its links attach there, its
    columns are not added) or imported separately with its subject area in
    its names (clash="separate", e.g. Payment -> Payment (COL), ColPayment)."""
    notes: List[str] = []
    if mode == "replace":
        base = {"schema_version": model.get("schema_version", 3), "settings": model.get("settings", {}),
                "headers": model.get("headers", []),
                "source_systems": model.get("source_systems", []),
                "namespaces": [], "entities": [], "relationships": []}
    else:
        base = {k: (list(v) if isinstance(v, list) else v) for k, v in model.items()}
    ns_ids = {n["id"] for n in base["namespaces"]}
    ns_keys = {n["key"] for n in base["namespaces"]}
    for n in frag["namespaces"]:
        if n["key"] in ns_keys:
            continue
        while n["id"] in ns_ids:
            n["id"] = new_id("ns")
        base["namespaces"].append(n)
        ns_ids.add(n["id"])
        ns_keys.add(n["key"])
    by_schema = {e["schema_name"].lower(): e for e in base["entities"]}
    by_name = {e["business_name"].lower(): e for e in base["entities"]}
    remap: Dict[str, str] = {}
    added = []
    for e in frag["entities"]:
        hit = by_schema.get(e["schema_name"].lower()) or by_name.get(e["business_name"].lower())
        if hit and clash == "separate":
            ns = next((n for n in base["namespaces"] if n["id"] == e["namespace_id"]), None)
            tag = (ns["key"] if ns else "l2")
            e = dict(e, business_name=e["business_name"] + " (" + tag.upper() + ")",
                     api_name=naming.camel(tag + " " + e["api_name"]))
            e["schema_name"] = naming.pascal(e["api_name"])
            e["plural"] = naming.kebab(naming.plural(e["api_name"]))
            notes.append(hit["business_name"] + " already exists: imported the new one separately as "
                         + e["business_name"] + " (" + e["schema_name"] + ").")
        elif hit:
            remap[e["id"]] = hit["id"]
            notes.append(hit["business_name"] + " already exists: linked to the existing entity; the imported "
                         "columns for it were not added.")
            continue
        e = {k: v for k, v in e.items() if not k.startswith("_")}
        base["entities"].append(e)
        added.append(e)
    start = max([(x.get("y") or 0) + 46 + 22 * len(x["attributes"]) for x in base["entities"]
                 if x not in added and x.get("y") is not None] or [0]) + 120
    layout(added, start if mode == "merge" and len(added) < len(base["entities"]) else 60)
    existing = {(r["from_entity_id"], r["to_entity_id"], r["business_name"].lower()) for r in base["relationships"]}
    for r in frag["relationships"]:
        r = dict(r, from_entity_id=remap.get(r["from_entity_id"], r["from_entity_id"]),
                 to_entity_id=remap.get(r["to_entity_id"], r["to_entity_id"]))
        k = (r["from_entity_id"], r["to_entity_id"], r["business_name"].lower())
        if k in existing:
            continue
        existing.add(k)
        base["relationships"].append(r)
    return fill_defaults(base), notes
