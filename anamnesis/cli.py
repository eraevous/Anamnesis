"""Command line: `python3 -m anamnesis init <path> [--name NAME] [--demo]` and `check <path>`.

Exit codes: 0 done, 1 the instance has problems (listed on stderr), 2 usage error.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .instance import InstanceError, init_instance, load_manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="anamnesis", description="Anamnesis memory engine")
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init", help="create a memory instance, or add the missing parts in place")
    p_init.add_argument("path")
    p_init.add_argument("--name", help="instance name (default: the folder name)")
    p_init.add_argument("--demo", action="store_true", help="fill a new instance with the synthetic fixtures")
    p_check = sub.add_parser("check", help="validate an instance's manifest")
    p_check.add_argument("path")
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            report = init_instance(args.path, name=args.name, demo=args.demo)
            print(f"instance: {report.path}")
            for item in report.created:
                print(f"  created  {item}")
            for item in report.kept:
                print(f"  kept     {item}")
            for item in report.demo_sources:
                print(f"  demo     {item} (synthetic)")
            return 0
        manifest = load_manifest(args.path)
        print(f"ok: {manifest['name']} ({len(manifest['sources'])} sources)")
        return 0
    except InstanceError as exc:
        print(f"anamnesis: {exc}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError) as exc:  # permissions, full disk, undecodable files: reported, no traceback
        print(f"anamnesis: {exc}", file=sys.stderr)
        return 1
