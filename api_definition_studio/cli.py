"""Command line: run the studio, or generate every output without the UI
(useful in a pipeline that publishes definitions from model.json)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from io import BytesIO
from pathlib import Path


def running_on(port: int) -> str:
    """'' if the port is free; the version of an API Definition Studio already
    listening on it; or 'another program'."""
    import socket
    import urllib.request
    s = socket.socket()
    s.settimeout(0.5)
    try:
        if s.connect_ex(("127.0.0.1", port)) != 0:
            return ""
    finally:
        s.close()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy
        with opener.open("http://127.0.0.1:" + str(port) + "/api/model", timeout=3) as r:
            return "API Definition Studio " + str(json.load(r).get("version") or "(unknown version)")
    except Exception:  # noqa: BLE001 - anything else on that port
        return "another program"


def main(argv=None) -> int:
    from . import __version__
    p = argparse.ArgumentParser(prog="api-definition-studio", description="API Definition Studio")
    p.add_argument("--version", action="version", version="api-definition-studio " + __version__)
    sub = p.add_subparsers(dest="cmd")
    run = sub.add_parser("run", help="Start the studio (default)")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8100)
    run.add_argument("--data", default=None, help="Data folder (default ./studio-data or $ADS_DATA_DIR)")
    imp = sub.add_parser("import-erd", help="Add an ERD (Mermaid erDiagram, Markdown or HTML page) to a model")
    imp.add_argument("file", help="ERD file: .mmd, .md, .txt or an .html page with Mermaid blocks")
    imp.add_argument("--data", default=None, help="Data folder holding model.json")
    imp.add_argument("--mode", default="merge", choices=["merge", "replace"],
                     help="merge: add to the model (default); replace: replace namespaces, entities and links")
    imp.add_argument("--namespaces", default="per-area", choices=["per-area", "single"],
                     help="per-area: one namespace per subject area on the page (default); single: one namespace")
    imp.add_argument("--namespace", default="", help="With --namespaces single: key of an existing namespace")
    imp.add_argument("--operations", default="none", choices=["none", "read", "crud"],
                     help="Operations for imported entities (default none: schema only)")
    imp.add_argument("--clash", default="link", choices=["link", "separate"],
                     help="When a name already exists: link to it (default) or import separately")
    imp.add_argument("--dry-run", action="store_true", help="Show what would be imported, change nothing")
    exa = sub.add_parser("add-example", help="Add a worked example to a model: agreements or notes")
    exa.add_argument("name", help="Example name: agreements or notes")
    exa.add_argument("--data", default=None, help="Data folder holding model.json")
    gen = sub.add_parser("generate", help="Write OpenAPI, mapping sheet and diagram files from a model")
    gen.add_argument("--data", default=None, help="Data folder holding model.json")
    gen.add_argument("--model", default=None, help="Path to a model.json (instead of --data)")
    gen.add_argument("--out", default="generated", help="Output folder")
    gen.add_argument("--labels", default="business", choices=["business", "api", "both"])
    gen.add_argument("--strict", action="store_true", help="Exit 1 if the model has errors")
    lnt = sub.add_parser("lint", help="Check the OpenAPI definitions with Spectral (needs Spectral or Node.js)")
    lnt.add_argument("--data", default=None, help="Data folder holding model.json (and an uploaded spectral.yaml)")
    lnt.add_argument("--model", default=None, help="Path to a model.json (instead of --data)")
    lnt.add_argument("--ns", action="append", default=[], help="Namespace key to check (repeat; default: all)")
    lnt.add_argument("--view", default="", choices=["", "full", "contract"],
                     help="blank: as published (default); full: with source mapping; contract: without")
    lnt.add_argument("--ruleset", default=None, help="A Spectral ruleset file to use instead")
    lnt.add_argument("--format", default="text", choices=["text", "json"])
    lnt.add_argument("--fail-on", default="error", choices=["error", "warning", "never"],
                     help="Exit 1 when there are findings this severe (default error)")
    args = p.parse_args(argv)

    if args.cmd in (None, "run"):
        import uvicorn
        if getattr(args, "data", None):
            os.environ["ADS_DATA_DIR"] = args.data
        host = getattr(args, "host", "127.0.0.1")
        port = getattr(args, "port", 8100)
        other = running_on(port)
        if other:
            print("Port " + str(port) + " is already in use by " + other + ".", file=sys.stderr)
            if other.startswith("API Definition Studio"):
                print("Stop it first (Ctrl+C in its terminal, or on a Mac: lsof -ti tcp:" + str(port)
                      + " | xargs kill), then start this one again.", file=sys.stderr)
            print("Or start this one on another port: api-definition-studio run --port " + str(port + 1),
                  file=sys.stderr)
            return 1
        from pathlib import Path as _P
        data = _P(os.environ.get("ADS_DATA_DIR") or "studio-data").resolve()
        print("API Definition Studio " + __version__ + " on http://" + host + ":" + str(port) + "/")
        print("  code: " + str(_P(__file__).resolve().parent))
        print("  data: " + str(data / "model.json"))
        uvicorn.run("api_definition_studio.main:app", host=host, port=port)
        return 0

    if args.cmd == "add-example":
        from . import examples
        from .store import Store
        if args.name not in examples.EXAMPLES:
            print("Unknown example. Available: " + ", ".join(examples.EXAMPLES), file=sys.stderr)
            return 1
        st = Store(args.data)
        merged, notes = examples.add_example(st.load(), args.name)
        st.replace(merged, keep_positions=False)
        for n in notes:
            print("  - " + n)
        print("Added " + args.name + " to " + str(st.path.resolve()) + " (previous model backed up alongside).")
        return 0

    if args.cmd == "import-erd":
        from . import erd_import
        from .main import OPERATION_SETS
        from .store import Invalid, Store
        from .validate import validate
        st = Store(args.data)
        current = st.load()
        ns_id = ""
        if args.namespaces == "single" and args.namespace:
            hit = [n for n in current["namespaces"] if n["key"] == args.namespace]
            if not hit:
                print("No namespace with key " + args.namespace, file=sys.stderr)
                return 1
            ns_id = hit[0]["id"]
        try:
            text = Path(args.file).read_text(encoding="utf-8", errors="replace")
            frag = erd_import.build(text, namespace_mode=args.namespaces, namespace_id=ns_id,
                                    operations=OPERATION_SETS[args.operations],
                                    base_model=current if args.mode == "merge" else None,
                                    servers=current["namespaces"][0]["servers"] if current["namespaces"] else [])
        except Invalid as exc:
            print(str(exc), file=sys.stderr)
            return 1
        merged, notes = erd_import.merge(current, frag, args.mode, args.clash)
        sm = frag["summary"]
        print("Read " + str(sm["diagrams"]) + " diagram(s): " + str(sm["entities"]) + " entities, "
              + str(sm["columns"]) + " columns, " + str(sm["links"]) + " links, "
              + str(sm["namespaces"]) + " new namespace(s).")
        for n in frag["notes"] + notes:
            print("  - " + n)
        issues = validate(merged)
        errs = [i for i in issues if i["level"] == "error"]
        print(str(len(errs)) + " errors, " + str(len(issues) - len(errs)) + " warnings after import.")
        if args.dry_run:
            print("Dry run: nothing changed.")
            return 0
        st.replace(merged, keep_positions=False)
        print("Saved " + str(st.path.resolve()) + " (previous model backed up alongside).")
        return 0

    if args.cmd == "lint":
        return _lint(args)

    from .main import bundle
    from .store import Store, fill_defaults
    from .validate import validate
    if args.model:
        m = fill_defaults(json.loads(Path(args.model).read_text(encoding="utf-8")))
    else:
        m = Store(args.data).load()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(bundle(m, args.labels, None if args.model else Store(args.data).dir))) as z:
        z.extractall(out)
    issues = validate(m)
    errs = [i for i in issues if i["level"] == "error"]
    for i in issues:
        print(i["level"].upper().ljust(8) + i["where"] + ": " + i["message"])
    print("Wrote " + str(out.resolve()) + " (" + str(len(errs)) + " errors, "
          + str(len(issues) - len(errs)) + " warnings)")
    return 1 if (args.strict and errs) else 0


def _lint(args) -> int:
    from . import lint
    from .main import to_yaml
    from .openapi_gen import published
    from .store import Store, fill_defaults
    data_dir = None if args.model else Store(args.data).dir
    m = fill_defaults(json.loads(Path(args.model).read_text(encoding="utf-8"))) if args.model else Store(args.data).load()
    nss = [n for n in m["namespaces"] if not args.ns or n["key"] in args.ns]
    if not nss:
        print("No namespace " + ", ".join(args.ns) + " in the model.", file=sys.stderr)
        return 2
    results, worst = {}, set()
    for n in nss:
        try:
            res = lint.run(to_yaml(published(m, n["id"], args.view)), data_dir,
                           ruleset_file=Path(args.ruleset) if args.ruleset else None)
        except lint.LintUnavailable as err:
            print(str(err), file=sys.stderr)
            return 2
        for f in res["findings"]:
            f.update(lint.locate(m, n["id"], f["path"]))
            worst.add(f["severity"])
        results[n["key"]] = res["findings"]
    if args.format == "json":
        print(json.dumps(results, indent=2))
    else:
        for key, rows in results.items():
            print(key + ": " + (str(len(rows)) + " finding(s)" if rows else "no findings"))
            for f in rows:
                print("  " + f["severity"].upper().ljust(8) + f["rule"] + "  " + f["where"] + ": " + f["message"]
                      + (" (line " + str(f["line"]) + ")" if f["line"] > 0 else ""))
    if args.fail_on == "never":
        return 0
    bad = {"error"} if args.fail_on == "error" else {"error", "warning"}
    return 1 if worst & bad else 0


if __name__ == "__main__":
    sys.exit(main())
