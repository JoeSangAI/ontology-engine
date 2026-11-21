"""Append source-backed ownership links and list unresolved cases."""

import argparse
import json
import os
import re
from collections import defaultdict
from functools import cache
from pathlib import Path
from time import time
from urllib.parse import unquote, urlparse

from ontology import DEFAULT_STORE, UNASSIGNED, allocate, append, load, normalize

GROUPS = ("待办 → 项目", "人员 → 机构", "项目 → 客户", "错听线索")


@cache
def local_path(value):
    if not isinstance(value, str):
        return None
    if value.startswith("file://"):
        uri = urlparse(value)
        if uri.netloc not in ("", "localhost"):
            return None
        value = unquote(uri.path)
    else:
        value = re.sub(r"#(?:L[0-9]+(?:-L[0-9]+)?|[0-9]+)$", "", value)
    path = Path(value)
    return path.resolve() if path.is_absolute() else None


def row(item, fields, evidence, at, op="update"):
    return dict(op=op, id=item["id"], kind=item["kind"], actor="source",
                at=at, fields=fields, evidence=evidence)


def field_evidence(record, key, value):
    return [dict(ref=entry["ref"], quote=value, field=key)
            for entry in record["evidence"]]


def field_candidate(records, key):
    record = records.get(key)
    return record if record and record["actor"] in ("source", "owner") else None


def labels_index(items):
    index = defaultdict(set)
    for item in items:
        for label in (item["name"], *item.get("aliases", [])):
            index[normalize(label)].add(item["id"])
    return index


def read_ledger(path):
    if path is None:
        return {}
    entries = json.loads(path.read_bytes())["task_followups"]
    records = defaultdict(list)
    for entry in entries:
        records[entry.get("followup_id") or entry.get("thread_id")].append(entry)
    return records


def action_evidence(entries, ledger, ledger_records, background_paths):
    paths, background, labels = [], [], []
    for entry in entries:
        path = entry.get("path", entry["ref"])
        if entry.get("source") != "workbench-ledger":
            if local_path(path):
                paths.append(dict(ref=entry["ref"], quote=path))
            continue
        if ledger is None or local_path(path) != ledger:
            continue
        records = ledger_records.get(entry.get("record"), [])
        if len(records) != 1:
            background.append(dict(ref=entry["ref"], quote=None))
            continue
        record, = records
        for key in ("state_owner", "project", "due_evidence", "evidence_links"):
            values = record.get(key) or []
            if isinstance(values, str):
                values = [values]
            for value in values:
                resolved = local_path(value)
                if resolved:
                    evidence = dict(ref=entry["ref"], quote=value, field=key)
                    if key in ("due_evidence", "evidence_links") and any(
                        resolved.is_relative_to(root) for root in background_paths
                    ):
                        background.append(evidence)
                    else:
                        paths.append(evidence)
                elif key == "project" and value:
                    labels.append(value)
    return paths, background, labels


def verified_organization(spec):
    """Read a curated name from an actual directory or a verbatim source line."""
    path = Path(spec["path"])
    labels = [spec["name"], *spec.get("aliases", [])]
    if "line" in spec:
        number = spec["line"]
        lines = path.read_text(encoding="utf-8").splitlines()
        if type(number) is not int or not 0 < number <= len(lines):
            raise ValueError(f"invalid source line in {path}")
        quote = lines[number - 1]
        ref = f"{path}#L{number}"
    elif path.is_dir():
        quote, ref = path.name, str(path)
    else:
        raise ValueError(f"source is not a directory: {path}")
    excerpt = spec.get("quote", quote)
    if (excerpt not in quote
            or not all(normalize(label) and normalize(label) in normalize(excerpt) for label in labels)):
        raise ValueError(f"organization name missing from cited source: {ref}")
    return {"ref": ref, "quote": excerpt}


def plan(state, *, ledger=None, context=None, at=None):
    """Return records and review entries against one replayed state."""
    if at is None:
        at = time()
    context = context or {}
    items = state["items"]
    fields = state["field_records"]
    projects = [item for item in items.values() if item["kind"] == "project"]
    organizations = [item for item in items.values()
                     if item["kind"] == "subject" and item.get("type") == "organization"]
    org_labels = labels_index(organizations)
    project_labels = labels_index(projects)
    occupied = set(items) | set(state["redirects"])
    records, reviews = [], {group: [] for group in GROUPS}
    for spec in context.get("organizations", []):
        label = spec["name"]
        aliases = spec.get("aliases", [])
        names = [label, *aliases]
        existing = set().union(*(org_labels[normalize(value)] for value in names))
        if "existing" in spec:
            identity = spec["existing"]
            item = items.get(identity)
            if (not item or item not in organizations or normalize(item["name"]) != normalize(label)
                    or existing - {identity}):
                raise ValueError(f"conflicting organization alias: {label}")
            new_aliases = [alias for alias in aliases if identity not in org_labels[normalize(alias)]]
            if not new_aliases or fields[identity].get("aliases", {}).get("actor") == "owner":
                continue
            records.append(row(item, {"aliases": new_aliases}, [verified_organization(spec)], at))
        else:
            if existing:
                continue
            identity = allocate(label, occupied)
            occupied.add(identity)
            item = {"id": identity, "kind": "subject"}
            records.append(row(item, {"name": label, "type": "organization", "aliases": aliases},
                               [verified_organization(spec)], at, op="create"))
        for value in names:
            org_labels[normalize(value)].add(identity)

    homes = defaultdict(set)
    for project in projects:
        if field_candidate(fields[project["id"]], "home"):
            for home in project.get("home", []):
                if (path := local_path(home)) is not None and path != path.parent:
                    homes[path].add(project["id"])

    ledger = ledger.resolve() if ledger else None
    ledger_records = read_ledger(ledger)
    background_paths = tuple(Path(value).resolve() for value in context.get("background_paths", []))
    directory_notes = context.get("directory_notes", {})

    for item in items.values():
        current = fields[item["id"]]
        kind = item["kind"]
        candidates, options, evidence, notes = set(), set(), [], []
        if kind == "action":
            group, key = GROUPS[0], "project"
            if item.get(key) not in (None, UNASSIGNED):
                continue
            source = field_candidate(current, key)
            entries = source["evidence"] if source else []
            paths, background, labels = action_evidence(entries, ledger, ledger_records, background_paths)
            supporting = []
            for ref in paths:
                path = local_path(ref["quote"])
                matches = set().union(*(homes[parent] for parent in (path, *path.parents)
                                       if parent in homes))
                if matches:
                    candidates.update(matches)
                    supporting.append(ref)
            evidence = supporting if len(candidates) == 1 else paths + background
            if background:
                notes.append("档案路径或背景材料只供核对")
            labels = list(dict.fromkeys(filter(None, [*labels, item.get("project_label")])))
            for label in labels:
                options.update(project_labels[normalize(label)] or [label])
            if labels:
                notes.append(f"台账／迁移项目文字：{'、'.join(labels)}")
            title_prefix, separator, _ = item["name"].partition("｜")
            if separator:
                title_key = normalize(title_prefix)
                title_matches = set().union(*(identities for label, identities in project_labels.items()
                                              if label and (label == title_key or
                                                            (not label.isascii() and title_key.startswith(label)))))
                if title_matches:
                    options.update(title_matches)
                    notes.append(f"待办标题提示：{title_prefix}")
            if not evidence:
                evidence = item["evidence"]
        elif item.get("type") == "person":
            group, key = GROUPS[1], "organization_id"
            if item.get(key) not in (None, UNASSIGNED):
                continue
            label = item.get("organization")
            source = field_candidate(current, "organization")
            if isinstance(label, str) and label != UNASSIGNED:
                options.update(org_labels[normalize(label)] or [label])
                evidence = field_evidence(source, "organization", label) if source else item["evidence"]
                if source:
                    candidates.update(org_labels[normalize(label)])
            else:
                notes.append("来源未填写机构文字")
                evidence = item["evidence"][:1]
        elif kind == "project":
            group, key = GROUPS[2], "client"
            if item.get(key) not in (None, UNASSIGNED):
                continue
            source = field_candidate(current, "home")
            matches_by_home = []
            for value in item.get("home", []):
                path = local_path(value)
                if not path or not path.parent.name:
                    continue
                notes.extend(directory_notes.get(str(path.parent), []))
                matches = org_labels[normalize(path.parent.name)]
                directory_only = str(path.parent) in directory_notes
                matches_by_home.append(set() if directory_only else matches)
                if not directory_only:
                    options.update(matches or [path.parent.name])
                options.update(org_labels[normalize(path.name)])
                options.update(org_labels[normalize(item["name"])])
                if source:
                    evidence.extend(field_evidence(source, "home", value))
            if source and matches_by_home and len(matches_by_home) == len(item["home"]) and all(matches_by_home):
                candidates.update(*matches_by_home)
            if not evidence:
                evidence = item["evidence"][:1]
        else:
            continue

        locked = current.get(key, {}).get("actor") == "owner"
        if len(candidates) == 1 and not locked:
            identity, = candidates
            at_record = max(at, current.get(key, {}).get("at", at))
            records.append(row(item, {key: identity}, evidence, at_record))
        else:
            if locked:
                notes.append("保留现有 owner 字段")
            if len(candidates) > 1:
                notes.append("规则匹配到多个对象")
            elif not candidates:
                notes.append("按规则未找到唯一归属")
            reviews[group].append((item["id"], sorted(candidates | options), evidence, notes))

    by_name = labels_index(items.values())
    for review in state["reviews"]:
        names = review["fields"].get("misrecognitions", [])
        if not names and review["reason"] not in {"voice-unverified", "voice-unmatched"}:
            continue
        label = review["fields"].get("label", review["id"])
        candidates = sorted(set(review["target"]) | by_name[normalize(label)])
        notes = [f"原待核编号 {review['id']}"]
        if names:
            notes.insert(0, f"转写：{'、'.join(names)}")
        if review.get("question"):
            notes.append(review["question"])
        reviews[GROUPS[3]].append((label, candidates, review["evidence"], notes))
    return records, reviews


def review_markdown(reviews, prompt="确认结果："):
    lines = ["# 补归属待确认清单", "", "可勾选条目并填写确认结果。", ""]
    for group, entries in reviews.items():
        lines.extend([f"## {group}（{len(entries)} 条）", ""])
        for label, candidates, evidence, notes in entries:
            lines.append(f"- [ ] **{label}**")
            lines.append(f"  - 候选 {'、'.join(candidates) or '当前无候选'}")
            for note in dict.fromkeys(notes):
                lines.append(f"  - {note}")
            for ref, quote, field in dict.fromkeys(
                (entry["ref"], entry["quote"], entry.get("field")) for entry in evidence
            ):
                text = " ".join(quote.split()) if quote else ""
                lines.append(f"  - 依据 `{ref}`"
                             + (f" · `{field}`" if field else "")
                             + (f" · {text}" if text else ""))
            lines.extend([f"  - {prompt}", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--ledger", type=Path, help="read cited task paths from the migration ledger")
    parser.add_argument("--context", type=Path, help="private JSON with verified names and directory context")
    parser.add_argument("--review", type=Path, required=True, help="write unresolved cases to Markdown")
    parser.add_argument("--apply", action="store_true", help="append records to the journal")
    args = parser.parse_args()
    try:
        context = json.loads(args.context.read_bytes()) if args.context else {}
        if not args.store.is_file():
            raise FileNotFoundError(args.store)
        if args.review.resolve() in {p.resolve() for p in (args.store, args.ledger, args.context) if p}:
            raise ValueError("the review must be written to a separate file")
        if args.review.exists() or not args.review.parent.is_dir():
            raise ValueError("the review needs a new path in an existing directory")
        reviews = {}

        def build(state):
            records, pending = plan(state, ledger=args.ledger, context=context)
            reviews.update(pending)
            return records

        records = (append(build, args.store, with_field_records=True) if args.apply
                   else build(load(args.store, with_field_records=True)))
        descriptor = os.open(args.review, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(review_markdown(reviews, context.get("review_prompt", "确认结果：")))
        counts = {group: len(entries) for group, entries in reviews.items()}
        print(json.dumps({"applied": args.apply, "records": len(records), "pending": counts}, ensure_ascii=False))
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
