"""Query the local ontology and append explicit owner corrections."""

import argparse
import json
from pathlib import Path
from time import time

import reminders
import review
import sources
from ontology import DEFAULT_STORE, KINDS, allocate, append, find, load, name, query


def correct(path, identity, evidence, *, fields=None, rename=None):
    if (fields is None) == (rename is None):
        raise ValueError("provide fields or a new name")
    review.require_quote(evidence)

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
    parser.add_argument("--registry", type=Path, default=sources.DEFAULT_REGISTRY)
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
    register = commands.add_parser("source-add")
    register.add_argument("identity")
    register.add_argument("path", type=Path)
    register.add_argument("--type", choices=("folder", "fragments"), default="folder")
    commands.add_parser("scan")
    apply = commands.add_parser("apply")
    apply.add_argument("token")
    apply.add_argument("proposals", type=argparse.FileType("r"), help="JSON array file or - for stdin")
    commands.add_parser("review")
    answer = commands.add_parser("review-answer")
    answer.add_argument("identity")
    answer.add_argument("decision", choices=("confirm", "dismiss", "merge"))
    answer.add_argument("--target")
    split = commands.add_parser("split")
    split.add_argument("identity")
    split.add_argument("--fields", type=json.loads, required=True)
    split.add_argument("--move-ref", action="append", required=True)
    split.add_argument("--keep-fields", type=json.loads)
    split.add_argument("--redirect-id", action="append", default=[])
    commands.add_parser("reminders")
    entrust = commands.add_parser("entrust")
    entrust.add_argument("text")
    entrust.add_argument("--due")
    entrust.add_argument("--person", action="append", default=[])
    respond = commands.add_parser("respond")
    respond.add_argument("identity")
    respond.add_argument("response", choices=reminders.RESPONSES)
    respond.add_argument("--until")
    for command in (entrust, respond):
        command.add_argument("--request-id")
        command.add_argument("--ref")
        command.add_argument("--quote")
    for command in (write, answer, split):
        command.add_argument("--ref", default="owner:cli")
        command.add_argument("--quote", required=True)
    args = parser.parse_args(argv)
    args.store = args.store.expanduser()
    if args.command in ("entrust", "respond"):
        request_id = args.request_id or f"cli-{time()}"
    elif hasattr(args, "quote"):
        evidence = {"ref": args.ref, "quote": args.quote}
    try:
        if args.command == "query":
            output = query(load(args.store), identity=args.name, kind=args.kind, question=args.question)
        elif args.command == "correct":
            output = correct(args.store, args.identity, evidence, fields=args.fields, rename=args.rename)
        elif args.command == "reminders":
            output = reminders.outlet(load(args.store, with_field_records=True))
        elif args.command == "entrust":
            item, created = reminders.entrust(args.store, request_id, args.text, due=args.due,
                                              people=args.person, ref=args.ref, quote=args.quote)
            output = {"schema": reminders.SCHEMA_VERSION, "item": item, "created": created}
        elif args.command == "respond":
            output = {"schema": reminders.SCHEMA_VERSION, **reminders.respond(
                args.store, request_id, args.identity, args.response, until=args.until,
                ref=args.ref, quote=args.quote)}
        elif args.command == "source-add":
            output = sources.register(args.registry, args.store, args.identity, args.path, args.type)
        elif args.command == "scan":
            output = sources.prepare(args.registry, args.store)
        elif args.command == "apply":
            output = sources.apply(args.registry, args.store, args.token, json.load(args.proposals))
        elif args.command == "review":
            output = review.queue(load(args.store, with_field_records=True))
        elif args.command == "review-answer":
            output = review.answer(args.store, args.identity, args.decision, evidence, target=args.target)
        elif args.command == "split":
            output = review.split(args.store, args.identity, args.fields, args.move_ref, evidence,
                                  keep_fields=args.keep_fields, aliases=args.redirect_id)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
