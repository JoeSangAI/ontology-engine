"""Convert an event seed and an action snapshot into a fresh local journal."""

import argparse
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from functools import cache
from pathlib import Path

from ontology import UNASSIGNED, allocate, encode, normalize, replay, timestamp


def convert(seed, snapshot, *, ledger=None, read_evidence=False):
    old_events = [json.loads(line) for line in seed.read_text(encoding="utf-8").splitlines() if line.strip()]
    snapshot_data = json.loads(snapshot.read_bytes())
    actions = snapshot_data["items"]
    action_records = {}
    if ledger is not None:
        action_records = {r.get("followup_id") or r.get("thread_id"): r
                          for r in json.loads(ledger.read_bytes())["task_followups"]}
    identity, occupied = {}, set()
    creates = [(e["id"], e["fields"]["name"]) for e in
               sorted(old_events, key=lambda row: row["event_at"]) if e["op"] == "create"]
    for old_id, label in [*creates, *((item["id"], item["name"]) for item in actions)]:
        if old_id not in identity:
            identity[old_id] = allocate(label.strip(), occupied)
            occupied.add(identity[old_id])

    occupied.update(identity)
    old_targets = {row["id"]: row["target"] for row in old_events if row["op"] == "redirect"}

    def translate(old_id):
        return identity.get(old_id, old_id)

    @cache
    def source_file(path):
        try:
            raw = Path(path).read_bytes()
            return hashlib.sha256(raw).hexdigest(), raw.decode("utf-8").splitlines()
        except (OSError, UnicodeError):
            return "", []

    def evidence(ref):
        path = ref["path"]
        locator = ref.get("line", ref.get("record"))
        quote = ref.get("quote")
        if quote is None and read_evidence and ref.get("source") == "project-file":
            digest, lines = source_file(path)
            if digest == ref.get("sha256") and type(locator) is int and 0 < locator <= len(lines):
                quote = lines[locator - 1]
        if quote is None and ref.get("source") == "workbench-ledger":
            if ledger is not None and Path(path).resolve() == ledger.resolve():
                quote = action_records.get(locator, {}).get("title")
        return {**ref, "ref": f"{path}#{locator}", "quote": quote if quote and quote.strip() else None}

    rows = []
    for event in sorted(old_events, key=lambda row: row["event_at"]):
        op, old_id, kind = (event[key] for key in ("op", "id", "kind"))
        fields = dict(event["fields"])
        if "name" in fields:
            fields["name"] = fields["name"].strip()
        for key in ("project", "organization_id", "debtor", "creditor"):
            if isinstance(fields.get(key), str):
                fields[key] = translate(fields[key])
        if op == "review":
            new_id = event["card_id"]
        else:
            new_id = translate(old_id)
        row = {"op": op, "kind": kind, "actor": event["actor"], "id": new_id,
               "at": event["event_at"], "fields": fields,
               "evidence": [evidence(ref) for ref in event["evidence"]]}
        if "target" in event:
            target = event["target"]
            row["target"] = [translate(key) for key in target] if isinstance(target, list) else translate(target)
        for key in ("reason", "scope", "question", "basis", "compared_ids"):
            if key in event:
                row[key] = event[key]
        if op == "update" and "name" in fields:
            label = fields.pop("name")
            renamed = allocate(label, occupied)
            occupied.add(renamed)
            rows.append({**row, "op": "redirect", "target": renamed, "fields": {"name": label}})
        if op != "update" or fields:
            rows.append(row)
        if op == "create" and old_id not in old_targets and old_id != new_id:
            rows.append({**row, "op": "redirect", "id": old_id, "target": new_id, "fields": {}})

    state = replay(rows)
    subjects, organizations, projects = defaultdict(set), defaultdict(set), defaultdict(set)
    by_source, by_home = defaultdict(set), defaultdict(set)
    for item in state["items"].values():
        indexes = [subjects] if item["kind"] == "subject" else [projects]
        if item.get("type") == "organization":
            indexes.append(organizations)
        for label in [item["name"], *item.get("aliases", [])]:
            for index in indexes:
                index[normalize(label)].add(item["id"])
        if item["kind"] == "project":
            for home in item.get("home", []):
                by_home[home].add(item["id"])
            for ref in item["evidence"]:
                if ref.get("source") == "project-file":
                    by_source[ref["path"]].add(item["id"])

    for item in state["items"].values():
        ref = item.get("organization")
        matches = organizations.get(normalize(ref), set())
        if item["kind"] == "subject" and "organization_id" not in item and len(matches) == 1:
            refs = [ref for ref in item["evidence"] if ref.get("source") != "project-file"
                    or timestamp(ref.get("at")) <= item["source_updated_at"]]
            rows.append({"op": "update", "kind": "subject", "actor": "source", "id": item["id"],
                         "at": max(timestamp(ref.get("at")) for ref in refs),
                         "fields": {"organization_id": next(iter(matches))}, "evidence": refs})

    action_rows = {}
    for action in actions:
        if action["kind"] != "action":
            raise ValueError("snapshot must contain only actions")
        old_id = action["id"]
        fields = {key: value for key, value in action.items()
                  if key not in {"id", "kind", "at", "provenance", "sources", "state_owner"}}
        refs = [evidence(ref) for ref in action["provenance"]]
        source = action.get("state_owner") or action_records.get(old_id, {}).get("state_owner")
        matches = by_source.get(source, set())
        if not matches and source:
            folder = Path(source).parent
            if folder.name in {".local", "知识库"}:
                folder = folder.parent
            matches = by_home.get(str(folder), set())
        if not matches:
            matches = projects.get(normalize(fields.get("project_label")), set())
        fields.setdefault("project", next(iter(matches)) if len(matches) == 1 else UNASSIGNED)
        matches = subjects.get(normalize(fields.get("owner_label")), set())
        if len(matches) == 1:
            fields.setdefault("debtor", next(iter(matches)))
        fields.setdefault("people", [fields.get("debtor") or UNASSIGNED])
        fields["name"] = fields["name"].strip()
        for key in ("project", "debtor", "creditor", "superseded_by"):
            if fields.get(key):
                fields[key] = translate(fields[key])
        row = {"op": "create", "kind": "action", "actor": "source", "id": identity[old_id],
               "at": action["at"], "fields": fields, "evidence": refs}
        action_rows[old_id] = row
        rows.append(row)
        if old_id != row["id"]:
            rows.append({**row, "op": "redirect", "id": old_id, "target": row["id"], "fields": {}})
    for old_id, target in snapshot_data.get("redirects", {}).items():
        if old_id not in identity and target in action_rows:
            row = action_rows[target]
            rows.append({**row, "op": "redirect", "id": old_id, "target": row["id"], "fields": {},
                         "evidence": [{"ref": f"{snapshot}#redirects/{old_id}", "quote": target}]})
    return rows


def migrate(seed, snapshot, target, **options):
    rows = convert(seed, snapshot, **options)
    replay(rows)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent) as stream:
        for row in rows:
            stream.write(encode(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        os.link(stream.name, target)
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seed", type=Path)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--ledger", type=Path, help="optional ledger cited by the snapshot")
    parser.add_argument("--read-evidence", action="store_true", help="read hash-verified lines in cited files")
    args = parser.parse_args()
    try:
        print(migrate(args.seed, args.snapshot, args.output, ledger=args.ledger, read_evidence=args.read_evidence))
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"{exc}\n")
