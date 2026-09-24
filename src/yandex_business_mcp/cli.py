"""ybiz: CLI over the same workspace operations as the MCP server.

    ybiz init                  create ybiz.yaml + data/branches in the workspace
    ybiz import <export.xml>   Yandex export -> data/branches/*.yaml (+ baseline copy)
    ybiz validate              XSD + Yandex content rules
    ybiz diff                  what the feed would add / close / change vs the baseline
    ybiz build                 write feed/feed.xml (refuses to close branches silently)

Workspace: --workspace, else $YBIZ_WORKSPACE, else the current directory.
"""

from __future__ import annotations

import argparse
import sys

from yandex_business_mcp.workspace import Workspace, WorkspaceError


def _report(issues) -> int:
    errors = [i for i in issues if i.level == "error"]
    for i in issues:
        print(i)
    print(f"{len(errors)} error(s), {len(issues) - len(errors)} warning(s)")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ybiz", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", help="workspace directory with ybiz.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create a workspace skeleton")
    p = sub.add_parser("import", help="import a Yandex Business XML export")
    p.add_argument("xml")
    p.add_argument("--force", action="store_true", help="overwrite existing branch files")
    sub.add_parser("validate", help="validate branches against the XSD and Yandex rules")
    sub.add_parser("diff", help="compare branches with the imported Yandex baseline")
    p = sub.add_parser("build", help="write the XML feed")
    p.add_argument("--allow-close", default="", help="comma-separated company-ids that may be closed")
    p.add_argument("--no-baseline", action="store_true", help="build without a Yandex baseline (first feed)")
    args = parser.parse_args(argv)

    ws = Workspace(args.workspace)
    try:
        if args.cmd == "init":
            ws.init()
            print(f"workspace ready: {ws.root}")
        elif args.cmd == "import":
            print(f"imported {ws.import_export(args.xml, args.force)} branches, baseline -> {ws.path('baseline')}")
        elif args.cmd == "validate":
            return _report(ws.validate())
        elif args.cmd == "diff":
            d = ws.diff()
            print(f"added ({len(d['added'])}): {', '.join(d['added']) or '-'}")
            print(f"WILL BE CLOSED ({len(d['removed'])}): {', '.join(d['removed']) or '-'}")
            print(f"changed ({len(d['changed'])}):")
            for cid, keys in d["changed"].items():
                print(f"  {cid}: {', '.join(keys)}")
        elif args.cmd == "build":
            r = ws.build([c for c in args.allow_close.split(",") if c], args.no_baseline)
            _report(r.issues)
            print(f"wrote {r.branches} branches -> {r.output} (updated: {', '.join(r.updated) or '-'})")
    except WorkspaceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
