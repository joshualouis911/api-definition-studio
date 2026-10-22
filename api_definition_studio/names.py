"""Name consistency: find business and API names already used in the model that
are like the one being typed, and flag the same business name (or the same DD
attribute) exposed under different API names.

Similarity works on words: camelCase and punctuation are split, common
abbreviations are expanded (num -> number, cust -> customer,
dob -> date of birth, …), and the score is the larger of the word overlap and
a character-level match of the normalised names."""
from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

ABBREV: Dict[str, List[str]] = {
    "no": ["number"], "num": ["number"], "nbr": ["number"], "nr": ["number"],
    "ref": ["reference"], "refs": ["reference"],
    "identifier": ["id"], "ident": ["id"],
    "dob": ["date", "birth"], "cust": ["customer"], "acct": ["account"],
    "addr": ["address"], "amt": ["amount"], "qty": ["quantity"], "dt": ["date"], "desc": ["description"],
    "cd": ["code"], "flg": ["flag"], "ind": ["indicator"], "zip": ["postcode"], "postal": ["post"],
    "tel": ["telephone"], "phone": ["telephone"], "ph": ["telephone"], "mob": ["mobile"], "cell": ["mobile"], "cellular": ["mobile"],
    "sr": ["service", "request"],
    "upd": ["updated"], "stat": ["status"], "sts": ["status"], "eff": ["effective"], "yr": ["year"], "yrs": ["year"],
    "dte": ["date"], "fst": ["first"], "lst": ["last"], "mid": ["middle"], "nm": ["name"],
}
STOP = {"the", "of", "a", "an"}   # "by", "at" carry meaning: Decided By is not Decided At
SAME = 0.999


def words(s: str, fold_plurals: bool = True) -> List[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s or "")
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    out: List[str] = []
    for w in re.split(r"[^A-Za-z0-9]+", s.lower()):
        if not w or w in STOP:
            continue
        out.extend(ABBREV.get(w, [w]))
    if not fold_plurals:
        return out
    # plural to singular, lightly (addresses -> address, years -> year)
    return [w[:-2] if w.endswith("sses") else w[:-1] if (w.endswith("s") and len(w) > 3 and not w.endswith("ss")) else w
            for w in out]


def norm(s: str) -> str:
    return " ".join(words(s))


def strict_norm(s: str) -> str:
    """For 'is this the same name': abbreviations expanded, plurals kept (Reason is not Reasons)."""
    return " ".join(words(s, fold_plurals=False))


def score(a: str, b: str) -> float:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    jac = len(ta & tb) / len(ta | tb)
    small, large = sorted((ta, tb), key=len)
    contained = (0.6 + 0.3 * len(small) / len(large)) if small and small <= large else 0.0   # Status ~ Case Status
    seq = SequenceMatcher(None, na, nb).ratio()                                             # typos, Hours/Hour
    return round(max(jac, contained, seq * 0.85), 3)


TABLE_COLUMN_RE = re.compile(r"^[A-Za-z_][\w$#]*\.[A-Za-z_][\w$#]*$")


def table_column(a: Dict[str, Any]) -> str:
    """The column's physical TABLE.COLUMN, upper case, when its source location is one (Siebel style)."""
    v = (a.get("source_column") or "").strip()
    return v.upper() if TABLE_COLUMN_RE.match(v) else ""


def similar(m: Dict[str, Any], business: str = "", api: str = "", dd: str = "", exclude: str = "",
            threshold: float = 0.65, limit: int = 12, location: str = "") -> List[Dict[str, Any]]:
    """Columns whose names are like the ones given, grouped by (business name, API name)
    with where each pair is used. Best first."""
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in m["entities"]:
        for a in e["attributes"]:
            if a["id"] == exclude:
                continue
            reasons: List[str] = []
            best = 0.0
            if business:
                s = score(business, a["business_name"])
                if s >= SAME:
                    reasons.append("same business name")
                elif s >= threshold:
                    reasons.append("similar business name")
                best = max(best, s)
                s2 = score(business, a["api_name"])
                if s2 >= threshold and s2 > s:
                    reasons.append("business name like its API name")
                best = max(best, s2)
            if api:
                s = score(api, a["api_name"])
                if s >= SAME:
                    reasons.append("same API name")
                elif s >= threshold:
                    reasons.append("similar API name")
                best = max(best, s)
            if dd and a.get("dd_reference") and dd.strip().lower() == a["dd_reference"].strip().lower():
                reasons.append("same DD attribute")
                best = 1.0
            if location and table_column(a) and location.strip().upper() == table_column(a):
                reasons.append("same table.column " + table_column(a))
                best = 1.0
            if best < threshold or not reasons:
                continue
            k = (a["business_name"], a["api_name"])
            g = groups.setdefault(k, {"business_name": a["business_name"], "api_name": a["api_name"], "type": a["type"],
                                      "score": 0.0, "reasons": [], "used_in": [], "dd_reference": a.get("dd_reference", ""),
                                      "source_column": a.get("source_column", "")})
            g["score"] = max(g["score"], best)
            g["reasons"] = list(dict.fromkeys(g["reasons"] + reasons))
            g["used_in"].append({"entity_id": e["id"], "entity": e["business_name"], "schema_name": e["schema_name"],
                                 "attr_id": a["id"]})
    out = sorted(groups.values(), key=lambda g: (-g["score"], -len(g["used_in"]), g["business_name"].lower()))
    return out[:limit]


def _majority(items: List[Tuple[str, Dict[str, Any], Dict[str, Any]]]) -> Tuple[str, List[str]]:
    c = Counter(a["api_name"] for _e, a, _x in items)
    top = max(c.items(), key=lambda kv: (kv[1], -[a["api_name"] for _e, a, _x in items].index(kv[0])))[0]
    where = [e["schema_name"] for e, a, _x in items if a["api_name"] == top]
    return top, where


def issues(m: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Warnings where one physical table.column, one business name, or one DD attribute is exposed under
    different names."""
    out: List[Dict[str, Any]] = []
    flagged = set()

    def warn(e: Dict[str, Any], a: Dict[str, Any], msg: str) -> None:
        if a["id"] in flagged:
            return
        flagged.add(a["id"])
        out.append({"level": "warning", "object_id": a["id"], "where": e["business_name"] + " › " + a["business_name"],
                    "message": msg, "blocking": False})

    # one physical TABLE.COLUMN mapped by columns with different names: name the most used pair and how close it is
    by_loc: Dict[Tuple[str, str], List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    for e in m["entities"]:
        for a in e["attributes"]:
            tc = table_column(a)
            if tc:
                by_loc.setdefault((e.get("source_system_id") or "", tc), []).append((e, a))
    for (_sid, tc), items in by_loc.items():
        pairs = Counter((a["business_name"], a["api_name"]) for _e, a in items)
        if len(pairs) < 2:
            continue
        order = [(a["business_name"], a["api_name"]) for _e, a in items]
        # the pair to follow: most used; on a tie a key column's, then the first
        keyed = {(a["business_name"], a["api_name"]) for _e, a in items if a["key"]}
        top = max(pairs, key=lambda p: (pairs[p], p in keyed, -order.index(p)))
        where = sorted({e["schema_name"] for e, a in items if (a["business_name"], a["api_name"]) == top})
        for e, a in items:
            if (a["business_name"], a["api_name"]) == top or a["key"]:
                continue
            sb, sa = score(a["business_name"], top[0]), score(a["api_name"], top[1])
            close = ("business names " + str(round(sb * 100)) + "% alike, API names " + str(round(sa * 100)) + "% alike")
            warn(e, a, "Table.column " + tc + " is also mapped as '" + top[0] + "' (" + top[1] + ") in " + ", ".join(where)
                 + "; here it is '" + a["business_name"] + "' (" + a["api_name"] + "): " + close
                 + ". Use the same names unless the meaning differs (e.g. a role such as account holder).")

    by_name: Dict[str, List[Tuple[str, Dict[str, Any], Dict[str, Any]]]] = {}
    by_dd: Dict[str, List[Tuple[str, Dict[str, Any], Dict[str, Any]]]] = {}
    for e in m["entities"]:
        for a in e["attributes"]:
            if a["key"]:
                continue   # keys are named after their entity on purpose (caseId, customerId)
            n = strict_norm(a["business_name"])
            if n:
                by_name.setdefault(n, []).append((e, a, {}))
            if (a.get("dd_reference") or "").strip():
                by_dd.setdefault(a["dd_reference"].strip().lower(), []).append((e, a, {}))
    for key, items in list(by_dd.items()) + list(by_name.items()):
        if len({a["api_name"] for _e, a, _x in items}) < 2:
            continue
        top, where = _majority(items)
        is_dd = items in by_dd.values()
        for e, a, _x in items:
            if a["api_name"] == top:
                continue
            what = ("DD attribute " + a["dd_reference"]) if is_dd else ("'" + a["business_name"] + "'")
            warn(e, a, what + " is " + top + " in " + ", ".join(sorted(set(where))) + "; here it is " + a["api_name"]
                 + ". Use the same API name for consistency, unless the meaning differs.")
    return out
