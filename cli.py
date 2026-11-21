"""Query the local ontology and append explicit owner corrections."""

import argparse
import json
from pathlib import Path
from time import time

from ontology import DEFAULT_STORE, KINDS, allocate, append, find, load, name, query


def correct(path, identity, evidence, *, fields=None, rename=None):
    if (fields is None) == (rename is None):
        raise ValueError("provide fields or a new name")
    if not isinstance(evidence, dict) or not isinstance(evidence.get("quote"), str) or not evidence["quote"].strip():
        raise ValueError("a correction needs the owner's words")

    def build(state):
        matches = find(state, identity)
        if identity in state["items"]:
            matches = [identity]
        if len(matches) != 1:
            raise ValueError("use a unique identifier for the correction")
        old = matches[0]
        item = state["items"][old]
        base = {"kind": item["kind"], "actor": "owner", "at": time(), "evidence": [evidence]}
        if rename is None:
            yield {**base, "op": "update", "id": old, "fields": fields}
        else:
            if not name(rename) or rename == item["name"]:
                raise ValueError("provide a different display name")
            new = allocate(rename, set(state["items"]) | set(state["redirects"]))
            yield {**base, "op": "redirect", "id": old, "target": new, "fields": {"name": rename}}

    return append(build, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    commands = parser.add_subparsers(dest="command", required=True)
    read = commands.add_parser("query")
    terms = read.add_mutually_exclusive_group()
    terms.add_argument("--name")
    terms.add_argument("--question")
    read.add_argument("--kind", choices=KINDS)
    write = commands.add_parser("correct")
    write.add_argument("identity")
    change = write.add_mutually_exclusive_group(required=True)
    change.add_argument("--fields", type=json.loads)
    change.add_argument("--rename")
    write.add_argument("--ref", default="owner:cli")
    write.add_argument("--quote", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "query":
            output = query(load(args.store), identity=args.name, kind=args.kind, question=args.question)
        else:
            output = correct(args.store, args.identity, {"ref": args.ref, "quote": args.quote},
                             fields=args.fields, rename=args.rename)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
