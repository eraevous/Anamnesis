"""Command line: `anamnesis init <path> [--name NAME] [--demo]`, `check <path>`,
`ingest <path> [EXPORT ...]` and `ask <path> QUESTION [--limit N] [--json]`
(also `python3 -m anamnesis ...`).

Exit codes: 0 done, 1 the instance has problems (listed on stderr), 2 usage error,
3 `ask` found nothing in the instance to cite (so it gave no answer).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .instance import InstanceError, init_instance, load_manifest
from .workflow import ask_instance, ingest_into_instance

NO_ANSWER = 3


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="anamnesis", description="Anamnesis memory engine")
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init", help="create a memory instance, or add the missing parts in place")
    p_init.add_argument("path")
    p_init.add_argument("--name", help="instance name (default: the folder name)")
    p_init.add_argument("--demo", action="store_true", help="fill a new instance with the synthetic fixtures")
    p_check = sub.add_parser("check", help="validate an instance's manifest")
    p_check.add_argument("path")
    p_ingest = sub.add_parser("ingest", help="import exports into an instance and (re)build its index")
    p_ingest.add_argument("path")
    p_ingest.add_argument("exports", nargs="*", help="ChatGPT export files (ZIP or conversations.json)")
    p_ask = sub.add_parser("ask", help="answer from an instance's sources, quoting and citing them")
    p_ask.add_argument("path")
    p_ask.add_argument("question", nargs="+")
    p_ask.add_argument("--limit", type=int, default=3, help="most sources to quote (default 3)")
    p_ask.add_argument("--json", action="store_true", help="print the answer as JSON")
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
        if args.command == "ingest":
            res = ingest_into_instance(args.path, args.exports)
            for item in res.imported:
                print(f"  imported {item}")
            for item in res.already:
                print(f"  kept     {item} (already imported)")
            state = "built" if res.built else "up to date"
            print(f"index {state}: {res.sources} source(s), {res.conversations} conversation(s), "
                  f"{res.units_indexed} message unit(s); sources digest {res.digest[:12]}")
            for note in res.notes:
                print(f"anamnesis: note: {note}", file=sys.stderr)
            return 0
        if args.command == "ask":
            if args.limit < 1:
                parser.error("--limit must be at least 1")
            answer, notes = ask_instance(args.path, " ".join(args.question), limit=args.limit)
            for note in notes:
                print(f"anamnesis: note: {note}", file=sys.stderr)
            print(json.dumps(answer.to_dict(), indent=2, ensure_ascii=False) if args.json else answer.render())
            return 0 if answer.found else NO_ANSWER
        manifest = load_manifest(args.path)
        print(f"ok: {manifest['name']} ({len(manifest['sources'])} sources)")
        return 0
    except InstanceError as exc:
        print(f"anamnesis: {exc}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError) as exc:  # permissions, full disk, undecodable files: reported, no traceback
        print(f"anamnesis: {exc}", file=sys.stderr)
        return 1
