"""Data model diagram exports: Mermaid erDiagram and draw.io (diagrams.net).

`labels` chooses what the boxes show: "business" (business names), "api"
(API names) or "both"."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

from .model import derived_from

DERIVED_COLOUR = "#6B46C1"
GROUP_COLOURS = [("#EEF5FC", "#2F6DB0"), ("#EEF8F1", "#2E7D4F"), ("#FDF3EA", "#B0601C"), ("#F4F0FB", "#6B46C1"),
                 ("#FCEFF3", "#A63A62"), ("#ECF7F7", "#1F7373")]

MERMAID_CARD = {
    "one-to-one": "||--||",
    "one-to-many": "||--o{",
    "many-to-one": "}o--||",
    "many-to-many": "}o--o{",
}
MANY_FROM = {"many-to-one", "many-to-many"}
MANY_TO = {"one-to-many", "many-to-many"}


def _label(business: str, api: str, labels: str) -> str:
    if labels == "api":
        return api
    if labels == "both":
        return business + " (" + api + ")" if business != api else business
    return business


def _entities(m: Dict[str, Any], ns_id: Optional[str] = None, tags: Optional[List[str]] = None,
              src: Optional[str] = None, linked: Optional[bool] = None) -> List[Dict[str, Any]]:
    """The entities a diagram shows. Each filter keeps the entities that match it
    (tags: any of them; src: a source system id, "-" for not sourced). With
    `linked`, whatever the kept entities link to stays too, so links are not
    cut off. `linked` defaults to on when filtering by namespace only (as before)."""
    if linked is None:
        linked = not tags and not src
    ids = {e["id"] for e in m["entities"]}

    def keep(pred: Any) -> None:
        nonlocal ids
        base = {e["id"] for e in m["entities"] if e["id"] in ids and pred(e)}
        out = set(base)
        if linked:
            for r in m["relationships"]:
                if r["from_entity_id"] in base and r["to_entity_id"] in ids:
                    out.add(r["to_entity_id"])
                if r["to_entity_id"] in base and r["from_entity_id"] in ids:
                    out.add(r["from_entity_id"])
        ids = out

    if ns_id:
        keep(lambda e: e["namespace_id"] == ns_id)
    if src:
        keep(lambda e: (e.get("source_system_id") or "-") == src)
    if tags:
        want = {t.lower() for t in tags}
        keep(lambda e: any(t.lower() in want for t in e.get("tags") or []))
    return [e for e in m["entities"] if e["id"] in ids]


def positions(ents: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    out = {}
    for i, e in enumerate(ents):
        if e.get("x") is not None and e.get("y") is not None:
            out[e["id"]] = {"x": float(e["x"]), "y": float(e["y"])}
        else:
            out[e["id"]] = {"x": 60.0 + (i % 3) * 420, "y": 60.0 + (i // 3) * 420}
    return out


def mermaid(m: Dict[str, Any], labels: str = "business", ns_id: Optional[str] = None, **filters: Any) -> str:
    ents = _entities(m, ns_id, **filters)
    ids = {e["id"]: e for e in ents}
    lines = ["erDiagram"]
    for e in ents:
        title = _label(e["business_name"], e["api_name"], labels).replace('"', "'")
        lines.append("    " + e["schema_name"] + '["' + title + '"] {')
        for a in e["attributes"]:
            t = {"date-time": "datetime"}.get(a["type"], a["type"])
            name = a["api_name"]
            comment = a["business_name"] if labels != "api" else ""
            drv, other = derived_from(e, a)
            if drv:   # Mermaid has no styling for one attribute: say it in the comment
                comment = (comment + " " if comment else "") + ("(derived from " + other + ")" if other else "(derived)")
            gp = group_path(e, a)
            if gp:    # nor nesting: say which object or list it is in
                comment = (comment + " " if comment else "") + "(in " + gp + ")"
            row = "        " + t + " " + name + (" PK" if a["key"] else "")
            if comment:
                row += ' "' + comment.replace('"', "'") + '"'
            lines.append(row)
        lines.append("    }")
    for r in m["relationships"]:
        if r["from_entity_id"] in ids and r["to_entity_id"] in ids:
            f, t = ids[r["from_entity_id"]], ids[r["to_entity_id"]]
            lab = _label(r["business_name"] or r["api_name"], r["api_name"] or r["business_name"], labels) or "links"
            lines.append("    " + f["schema_name"] + " " + MERMAID_CARD.get(r["cardinality"], "||--o{") + " "
                         + t["schema_name"] + ' : "' + lab.replace('"', "'") + '"')
    # Mermaid erDiagram cannot draw groups: record them as comments
    for g, ms in groups_of(ents).items():
        lines.append("    %% group " + g + ": " + ", ".join(x["schema_name"] for x in ms))
    return "\n".join(lines) + "\n"


def group_path(e: Dict[str, Any], a: Dict[str, Any]) -> str:
    """bankDetails or bankDetails[] for a column in a column group, else ''."""
    for g in e.get("column_groups") or []:
        if g["id"] == a.get("group_id"):
            return g["api_name"] + ("[]" if g.get("many") else "")
    return ""


def groups_of(ents: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for e in ents:
        if e.get("group"):
            out.setdefault(e["group"], []).append(e)
    return out


def drawio(m: Dict[str, Any], labels: str = "business", ns_id: Optional[str] = None, **filters: Any) -> str:
    ents = _entities(m, ns_id, **filters)
    ids = {e["id"]: e for e in ents}
    pos = positions(ents)
    sss = {x["id"]: x for x in m.get("source_systems", [])}
    W, HEAD, ROW = 280, 34, 24
    cells: List[str] = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']

    def q(s: str) -> str:
        return escape(s, {'"': "&quot;", "\n": "&#xa;"})

    # logical groups: a dashed, labelled frame behind each group's boxes
    frames: List[str] = []
    for gi, (g, ms) in enumerate(groups_of(ents).items()):
        xs = [pos[x["id"]]["x"] for x in ms]
        ys = [pos[x["id"]]["y"] for x in ms]
        x2 = [pos[x["id"]]["x"] + W for x in ms]
        y2 = [pos[x["id"]]["y"] + HEAD + 16 * 3 + ROW * max(1, len(x["attributes"])) for x in ms]
        fx, fy = min(xs) - 24, min(ys) - 44
        fw, fh = max(x2) - min(xs) + 48, max(y2) - min(ys) + 68
        colour = GROUP_COLOURS[gi % len(GROUP_COLOURS)]
        frames.append('<mxCell id="' + q("g_" + str(gi)) + '" value="' + q(g) + '" style="rounded=1;arcSize=4;dashed=1;'
                      'fillColor=' + colour[0] + ';strokeColor=' + colour[1] + ';fontColor=' + colour[1] + ';fontStyle=1;'
                      'verticalAlign=top;align=left;spacingLeft=10;spacingTop=4;fontSize=14;html=0;" vertex="1" parent="1">'
                      '<mxGeometry x="' + str(int(fx)) + '" y="' + str(int(fy)) + '" width="' + str(int(fw)) + '" height="'
                      + str(int(fh)) + '" as="geometry"/></mxCell>')
    cells.extend(frames)

    for e in ents:
        p = pos[e["id"]]
        title = _label(e["business_name"], e["api_name"], labels)
        if labels == "business" and e["business_name"].lower() != e["api_name"].lower():
            title += "\n" + "API: " + e["api_name"]
        src = sss.get(e.get("source_system_id") or "")
        title += "\n" + "Source: " + (src["name"] if src else "not sourced")
        if e.get("tags"):
            title += "\n" + "Tags: " + ", ".join(e["tags"])
        h = HEAD + 16 * title.count("\n")
        height = h + ROW * max(1, len(e["attributes"]))
        style = ("swimlane;fontStyle=1;childLayout=stackLayout;horizontal=1;startSize=" + str(h) + ";"
                 "horizontalStack=0;resizeParent=1;resizeParentMax=0;resizeLast=0;collapsible=1;marginBottom=0;"
                 "fillColor=#1F3A5F;fontColor=#FFFFFF;strokeColor=#1F3A5F;swimlaneFillColor=#FFFFFF;"
                 "rounded=1;arcSize=6;whiteSpace=wrap;html=0;")
        cid = "e_" + e["id"]
        cells.append('<mxCell id="' + q(cid) + '" value="' + q(title) + '" style="' + style + '" vertex="1" parent="1">'
                     '<mxGeometry x="' + str(int(p["x"])) + '" y="' + str(int(p["y"])) + '" width="' + str(W)
                     + '" height="' + str(height) + '" as="geometry"/></mxCell>')
        for i, a in enumerate(e["attributes"]):
            name = (group_path(e, a) + "." if group_path(e, a) else "") + _label(a["business_name"], a["api_name"], labels)
            drv, other = derived_from(e, a)
            text = ("PK  " if a["key"] else "DRV " if drv else "      ") + name + " : " + a["type"] + (" *" if a["required"] else "") \
                + ((" \u2190 " + other) if other else "")
            st = ("text;strokeColor=none;align=left;verticalAlign=middle;spacingLeft=6;spacingRight=4;"
                  "overflow=hidden;rotatable=0;points=[[0,0.5],[1,0.5]];portConstraint=eastwest;html=0;"
                  + ("fontStyle=1;" if a["key"] else "")
                  + ("fillColor=#F3EEFC;fontColor=" + DERIVED_COLOUR + ";fontStyle=2;" if drv else "fillColor=none;"))
            cells.append('<mxCell id="' + q(cid + "_" + a["id"]) + '" value="' + q(text) + '" style="' + st
                         + '" vertex="1" parent="' + q(cid) + '"><mxGeometry y="' + str(h + i * ROW)
                         + '" width="' + str(W) + '" height="' + str(ROW) + '" as="geometry"/></mxCell>')
    for r in m["relationships"]:
        if r["from_entity_id"] not in ids or r["to_entity_id"] not in ids:
            continue
        start = "ERzeroToMany" if r["cardinality"] in MANY_FROM else "ERmandOne"
        end = "ERzeroToMany" if r["cardinality"] in MANY_TO else "ERmandOne"
        lab = _label(r["business_name"] or r["api_name"], r["api_name"] or r["business_name"], labels)
        st = ("edgeStyle=orthogonalEdgeStyle;rounded=1;html=0;startArrow=" + start + ";endArrow=" + end
              + ";startFill=0;endFill=0;strokeColor=#5B6B7F;fontColor=#1F3A5F;labelBackgroundColor=#FFFFFF;")
        cells.append('<mxCell id="' + q("r_" + r["id"]) + '" value="' + q(lab) + '" style="' + st
                     + '" edge="1" parent="1" source="' + q("e_" + r["from_entity_id"]) + '" target="'
                     + q("e_" + r["to_entity_id"]) + '"><mxGeometry relative="1" as="geometry"/></mxCell>')
    title = escape(m["settings"].get("title") or "Data model")
    return ('<mxfile host="api-definition-studio"><diagram id="data-model" name="' + title + '">'
            '<mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" '
            'arrows="1" fold="1" page="1" pageScale="1" pageWidth="1654" pageHeight="1169" math="0" shadow="0">'
            '<root>' + "".join(cells) + '</root></mxGraphModel></diagram></mxfile>\n')
