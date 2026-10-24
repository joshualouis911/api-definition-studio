"""API style checks with Spectral (https://github.com/stoplightio/spectral).

Spectral is a Node.js tool, so it is not bundled: the studio runs whichever is
available, in this order:
  1. the command in $ADS_SPECTRAL (e.g. "/opt/spectral/bin/spectral");
  2. `spectral` on the PATH (npm install -g @stoplight/spectral-cli);
  3. `npx --yes @stoplight/spectral-cli@6` when Node.js is installed (downloads it once).

The ruleset is the studio's starter ruleset (rulesets/studio.spectral.yaml), or
the one uploaded for this data folder (spectral.yaml beside model.json). An
uploaded ruleset can build on the starter one with extends: ["./studio.spectral.yaml"]."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

STARTER = Path(__file__).parent / "rulesets" / "studio.spectral.yaml"
CUSTOM_NAME = "spectral.yaml"
SEVERITY = {0: "error", 1: "warning", 2: "info", 3: "hint"}


class LintUnavailable(Exception):
    pass


def command() -> Optional[List[str]]:
    """The Spectral command to run, or None when neither Spectral nor Node.js is installed."""
    env = os.environ.get("ADS_SPECTRAL", "").strip()
    if env:
        return shlex.split(env)
    found = shutil.which("spectral")
    if found:
        return [found]
    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", "@stoplight/spectral-cli@6"]
    return None


def ruleset_text(data_dir: Path) -> Tuple[str, bool]:
    """(ruleset YAML, is it an uploaded one)."""
    custom = Path(data_dir) / CUSTOM_NAME
    if custom.exists():
        return custom.read_text(encoding="utf-8"), True
    return STARTER.read_text(encoding="utf-8"), False


def check_ruleset(text: str) -> Dict[str, Any]:
    """Refuse text that is not a YAML mapping with rules or extends."""
    try:
        d = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise ValueError("Not valid YAML: " + str(err).splitlines()[0])
    if not isinstance(d, dict) or not (d.get("rules") or d.get("extends")):
        raise ValueError("A Spectral ruleset needs 'extends' or 'rules'.")
    return d


def save_ruleset(data_dir: Path, text: str) -> None:
    check_ruleset(text)
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    (Path(data_dir) / CUSTOM_NAME).write_text(text, encoding="utf-8")


def reset_ruleset(data_dir: Path) -> bool:
    custom = Path(data_dir) / CUSTOM_NAME
    if custom.exists():
        custom.unlink()
        return True
    return False


def run(openapi_yaml: str, data_dir: Optional[Path], timeout: int = 240, ruleset_file: Optional[Path] = None) -> Dict[str, Any]:
    """Lint one OpenAPI document. Returns {command, ruleset, findings}; raises
    LintUnavailable when Spectral can't be run."""
    cmd = command()
    if not cmd:
        raise LintUnavailable("Spectral isn't installed here. Install Node.js and run "
                              "'npm install -g @stoplight/spectral-cli', or set ADS_SPECTRAL to the spectral command.")
    if ruleset_file:
        text, custom = Path(ruleset_file).read_text(encoding="utf-8"), True
    elif data_dir:
        text, custom = ruleset_text(data_dir)
    else:
        text, custom = STARTER.read_text(encoding="utf-8"), False
    with tempfile.TemporaryDirectory(prefix="ads-lint-") as tmp:
        t = Path(tmp)
        shutil.copyfile(STARTER, t / "studio.spectral.yaml")      # an uploaded ruleset can extend it
        rules = t / ("ruleset.yaml" if custom else "studio.spectral.yaml")
        if custom:
            rules.write_text(text, encoding="utf-8")
        doc = t / "openapi.yaml"
        doc.write_text(openapi_yaml, encoding="utf-8")
        out = t / "result.json"
        try:
            p = subprocess.run(cmd + ["lint", "-r", str(rules), "-f", "json", "-o", str(out), "--fail-severity", "error",
                                      str(doc)], capture_output=True, text=True, timeout=timeout, cwd=tmp)
        except FileNotFoundError:
            raise LintUnavailable("Could not start Spectral (" + " ".join(cmd) + ").")
        except subprocess.TimeoutExpired:
            raise LintUnavailable("Spectral took longer than " + str(timeout) + " seconds.")
        if not out.exists():
            msg = (p.stderr or p.stdout or "").strip().splitlines()
            raise LintUnavailable("Spectral failed: " + (" ".join(msg[-3:]) if msg else "no output") )
        raw = json.loads(out.read_text(encoding="utf-8") or "[]")
    return {"command": " ".join(cmd[:3]), "ruleset": "uploaded" if custom else "starter", "findings": normalise(raw)}


def normalise(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Spectral's JSON results as rows, one per rule and place (Spectral can report a
    schema rule more than once at the same place)."""
    rows, seen = [], set()
    for r in raw:
        path = [str(x) for x in r.get("path") or []]
        key = (r.get("code"), tuple(path))
        if key in seen:
            continue
        seen.add(key)
        rows.append({"severity": SEVERITY.get(r.get("severity", 1), "warning"), "rule": str(r.get("code", "")),
                     "message": r.get("message", ""), "path": path,
                     "line": ((r.get("range") or {}).get("start") or {}).get("line", -1) + 1})
    order = {"error": 0, "warning": 1, "info": 2, "hint": 3}
    rows.sort(key=lambda x: (order[x["severity"]], x["rule"], x["path"]))
    return rows


def locate(m: Dict[str, Any], ns_id: str, path: List[str]) -> Dict[str, str]:
    """Where a finding is in the model: {entity_id, attr_id, where} for a schema or property,
    {where} for an operation."""
    if len(path) >= 3 and path[0] == "components" and path[1] == "schemas":
        sch = path[2]
        base = re.sub(r"(Create|Update|Page|List)$", "", sch)
        ent = next((e for e in m["entities"] if e["schema_name"] in (sch, base)), None)
        if not ent:
            return {"where": "Schema " + sch}
        out = {"entity_id": ent["id"], "where": ent["business_name"]}
        props = [path[i + 1] for i in range(3, len(path) - 1) if path[i] == "properties"]
        if props:
            name = props[-1]
            grp = props[0] if len(props) > 1 else ""
            g = next((g for g in ent.get("column_groups") or [] if g["api_name"] == grp), None)
            a = next((a for a in ent["attributes"] if a["api_name"] == name
                      and (not g or a.get("group_id") == g["id"])), None)
            if a:
                out.update(attr_id=a["id"], where=ent["business_name"] + " › " + a["business_name"])
        return out
    if len(path) >= 3 and path[0] == "paths":
        return {"where": path[2].upper() + " " + path[1]}
    return {"where": ".".join(path[:3])}
