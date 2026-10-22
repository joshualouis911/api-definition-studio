"""Naming helpers shared by the generators and the validator."""
from __future__ import annotations

import re
from typing import List, Optional

CAMEL_RE = re.compile(r"^[a-z][a-zA-Z0-9]*$")
PASCAL_RE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
KEBAB_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
# an entity's path: one or more kebab-case segments, e.g. agreements or agreements/changes
PATH_SEGMENTS_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*(/[a-z][a-z0-9]*(-[a-z0-9]+)*)*$")
UPPER_SNAKE_RE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")
NS_KEY_RE = re.compile(r"^[a-z][a-z0-9-]*$")
BASE_PATH_RE = re.compile(r"^(/[a-zA-Z0-9._~-]+)+$")

# Words that suggest source-system internals are leaking into the contract.
LEAKY_TOKENS = ["siebel", "attrib", "x_", "bc_", "s_", "row_id", "lov", "integrationobject"]


def words(text: str) -> List[str]:
    """Split business text or any identifier into lower-case words."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", text) if w]


def camel(text: str) -> str:
    w = words(text)
    if not w:
        return ""
    out = w[0] + "".join(x[:1].upper() + x[1:] for x in w[1:])
    if out[0].isdigit():
        out = "n" + out
    return out


def pascal(text: str) -> str:
    c = camel(text)
    return c[:1].upper() + c[1:]


def kebab(text: str) -> str:
    return "-".join(words(text))


def upper_snake(text: str) -> str:
    out = "_".join(w.upper() for w in words(text))
    if out and out[0].isdigit():
        out = "V_" + out
    return out


def plural(word: str) -> str:
    """English plural good enough for resource names (parent -> parents)."""
    if not word:
        return word
    lower = word.lower()
    irregular = {"person": "people", "child": "children", "case": "cases", "status": "statuses",
                 "address": "addresses", "analysis": "analyses"}
    if lower in irregular:
        return word[: len(word) - len(lower)] + irregular[lower] if word.islower() else irregular[lower]
    if re.search(r"[^aeiou]y$", lower):
        return word[:-1] + "ies"
    if re.search(r"(s|x|z|ch|sh)$", lower):
        return word + "es"
    return word + "s"


def leaky(name: str, tokens: Optional[List[str]] = None) -> str:
    """Return the internal-name token found in an API name, or '' if clean.
    A token ending in '_' must be a prefix; others match anywhere."""
    low = (name or "").lower()
    for tok in (LEAKY_TOKENS if tokens is None else tokens):
        if tok.endswith("_"):
            if low.startswith(tok):
                return tok
        elif tok in low:
            return tok
    return ""


# -- ISO 8601 for date and date-time examples ------------------------------------------
_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%d %b %Y", "%d-%b-%Y", "%d %B %Y",
                 "%d-%b-%y", "%d/%m/%y", "%Y%m%d"]
_TIME_FORMATS = ["%H:%M:%S", "%H:%M", "%H:%M:%S.%f"]


def iso_example(kind: str, text: str) -> str:
    """An example date as ISO 8601: date -> 2026-01-31, date-time -> 2026-01-31T09:30:00Z.
    Day-first forms (31/01/2026, 31-Jan-2026) and Excel's '2026-01-31 00:00:00' are understood.
    Text that is not a date comes back unchanged (validation then reports it)."""
    import datetime as _dt
    t = (text or "").strip()
    if not t or kind not in ("date", "date-time"):
        return text
    parsed = None
    try:
        parsed = _dt.datetime.fromisoformat(t.replace("Z", "+00:00").replace(" ", "T", 1) if len(t) > 10 else t)
    except ValueError:
        pass
    if parsed is None:
        date_part, _, time_part = t.partition(" ")
        if "T" in t and not time_part:
            date_part, _, time_part = t.partition("T")
        for df in _DATE_FORMATS:
            try:
                d = _dt.datetime.strptime(date_part if time_part else t, df)
            except ValueError:
                continue
            if time_part:
                for tf in _TIME_FORMATS:
                    try:
                        tt = _dt.datetime.strptime(time_part.rstrip("Z"), tf).time()
                        d = _dt.datetime.combine(d.date(), tt)
                        break
                    except ValueError:
                        continue
            parsed = d
            break
    if parsed is None:
        return text
    if kind == "date":
        return parsed.date().isoformat()
    if parsed.tzinfo is None or parsed.utcoffset() == _dt.timedelta(0):
        return parsed.replace(tzinfo=None, microsecond=0).isoformat() + "Z"
    return parsed.replace(microsecond=0).isoformat()
