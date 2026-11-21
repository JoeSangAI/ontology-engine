"""Present supported review candidates and append the owner's decisions."""

import hashlib
from time import time

from ontology import allocate, append, encode, name


def queue(state):
    answered = {row["id"] for row in state["reviews"] if row["actor"] == "owner"}
    confirmed = {key for row in state["reviews"] if row["actor"] == "owner"
                 and row["reason"] == "confirmed-identity" for key in row["target"]}
    candidates = []

    def offer(reason, kind, targets, evidence, **details):
        if reason == "merge-suggestion" and len(targets) < 2:
            return
        payload = dict(reason=reason, kind=kind, target=targets, evidence=evidence, **details)
        identity = "answer:" + hashlib.sha256(encode(payload).encode()).hexdigest()
        if identity not in answered:
            candidates.append({"id": identity, **payload})

    for key, item in sorted(state["items"].items()):
        name_record = state["field_records"][key].get("name", {})
        evidence = [next((ref for ref in item["evidence"] if ref["quote"]), item["evidence"][0])]
        if (item["kind"] in ("subject", "project") and name_record.get("actor") == "model"
                and key not in confirmed):
            offer("new-identity", item["kind"], [key], evidence, name=item["name"])
        if item["certainty"] == "uncertain":
            offer("uncertain", item["kind"], [key], evidence, name=item["name"])
        for field, dispute in sorted(item.get("disagreements", {}).items()):
            if field == "merge":
                for origin, choice in sorted(dispute.items()):
                    offer("disagreement", item["kind"], [key], choice["evidence"],
                          field=field, origin=origin, proposed=choice["value"])
            else:
                offer("disagreement", item["kind"], [key], dispute["evidence"],
                      field=field, current=item[field], proposed=dispute["value"])
    for row in state["reviews"]:
        if row["actor"] != "owner":
            offer(row["reason"], row["kind"], row["target"], row["evidence"], origin=row["id"])
    return candidates


def answer(path, identity, decision, evidence, *, target=None):
    def build(state):
        candidate = next((entry for entry in queue(state) if entry["id"] == identity), None)
        if candidate is None:
            raise ValueError("review entry has changed or was already answered")
        targets = candidate["target"]
        base = dict(actor="owner", at=time(), kind=candidate["kind"], evidence=[evidence], fields={})
        reason = "dismissed-review"
        if decision == "confirm":
            if candidate["reason"] not in ("new-identity", "uncertain"):
                raise ValueError("use confirm for an identity or correct its fields before dismissing")
            reason = "confirmed-identity"
        elif decision == "merge":
            if len(targets) < 2 or target not in targets or any(
                    state["items"].get(key, {}).get("kind") != candidate["kind"] for key in targets):
                raise ValueError("merge requires current candidates of the same kind and a retained target")
            for key in targets:
                if key != target:
                    yield {**base, "op": "merge", "id": key, "target": target}
        elif decision != "dismiss":
            raise ValueError("unknown review decision")
        yield {**base, "op": "review", "id": identity, "target": targets, "reason": reason}

    require_quote(evidence)
    return append(build, path, with_field_records=True)


def require_quote(evidence):
    if not isinstance(evidence.get("quote"), str) or not evidence["quote"].strip():
        raise ValueError("an owner decision needs the owner's words")


def split(path, identity, fields, refs, evidence, *, keep_fields=None, aliases=()):
    """Keep the original identity and move selected evidence to a new entry."""
    require_quote(evidence)

    def build(state):
        item = state["items"].get(identity)
        if item is None:
            raise ValueError("use the current identifier of the entry to split")
        if not refs or set(refs) - {ref["ref"] for ref in item["evidence"]}:
            raise ValueError("select existing evidence references from the original entry")
        if not isinstance(fields, dict) or not name(fields.get("name")):
            raise ValueError("provide the new entry's fields and display name")
        target = allocate(fields["name"], set(state["items"]) | set(state["redirects"]))
        base = dict(actor="owner", at=time(), kind=item["kind"], evidence=[evidence])
        yield {**base, "op": "create", "id": target, "fields": fields}
        if keep_fields is not None:
            yield {**base, "op": "update", "id": identity, "fields": keep_fields}
        yield {**base, "op": "split", "id": identity, "target": target, "refs": refs, "fields": {}}
        for alias in aliases:
            if state["redirects"].get(alias) != identity:
                raise ValueError("select a legacy alias pointing to the original entry")
            yield {**base, "op": "redirect", "id": alias, "target": target, "fields": {}}

    return append(build, path)
