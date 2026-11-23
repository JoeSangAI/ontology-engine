"""What is worth bringing up now, and what the owner said about it.

One way out (`outlet`) and one way in (`entrust`, `respond`) for whatever sits
next to the owner and picks the moment: a desktop companion, a phone, a watch.
The journal stays the only book. A thing the owner entrusts is one action; a
response is an owner record on that same action. An occasion is said in general
terms (a time, a person, having stood still), never as an application or a
window: which window "seeing this person" means is the hand's business.
"""

from datetime import date, datetime, timedelta
from time import time

from ontology import TERMINAL, UNASSIGNED, allocate, append, find, load, name, normalize

SCHEMA_VERSION = 1
RESPONSES = ("done", "dropped", "another_day", "later", "ignored")
CLOSING = {"done": "completed", "dropped": "cancelled"}
STALLED_DAYS = 14
STEP_FIELDS = ("name", "next", "status", "due", "project", "people")


def moment(value):
    """A day (YYYY-MM-DD) or an instant with its zone. A clock time is never guessed from a day."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("a time is a day or an instant with its zone")
    if len(value) == 10:
        date.fromisoformat(value)
    elif datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("a time is a day or an instant with its zone")
    return value


def _reached(value, now):
    if len(value) == 10:
        return date.fromisoformat(value) <= now.date()
    return datetime.fromisoformat(value.replace("Z", "+00:00")) <= now


def _person(state, label):
    """A person as the ontology knows them: every name another app may show for them."""
    matches = [key for key in ([label] if label in state["items"] else find(state, label))
               if state["items"][key]["kind"] == "subject" and state["items"][key].get("type") != "organization"]
    if len(matches) != 1:
        return {"id": None, "name": label, "aliases": []}
    subject = state["items"][matches[0]]
    names = list(dict.fromkeys([subject["name"], *subject.get("aliases", []), label]))
    return {"id": subject["id"], "name": names[0], "aliases": names[1:]}


def outlet(state, now=None):
    """Open actions with an occasion to bring them up. `state` comes from `load(..., with_field_records=True)`."""
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        raise ValueError("now needs a time zone")
    records = state.get("field_records", {})
    items = []
    for key, item in state["items"].items():
        if item["kind"] != "action" or item.get("status") in TERMINAL:
            continue
        occasions = []
        due = item.get("due")
        if isinstance(due, str) and due:
            try:
                occasions.append({"type": "time", "at": moment(due), "due": _reached(due, now),
                                  "kind": item.get("due_kind") or ("appointment" if len(due) > 10 else "target")})
            except ValueError:
                due = None
        people = [_person(state, label) for label in item.get("people", []) if label != UNASSIGNED]
        if people:
            occasions.append({"type": "person", "people": people, "why": "concerns"})
        changed = max((records.get(key, {}).get(field, {}).get("at", 0) for field in STEP_FIELDS), default=0) or item["at"]
        stalled = int((now.timestamp() - changed) // 86400)
        if not due and stalled >= STALLED_DAYS:
            occasions.append({"type": "state", "state": "stalled", "days": stalled})
        entrusted = bool(item.get("request"))
        if not occasions and not entrusted:
            continue
        entry = {"id": key, "title": item["name"], "origin": "entrusted" if entrusted else "source",
                 "open": True, "weight": "normal", "reason": "", "occasions": occasions}
        if item.get("next"):
            entry["next"] = item["next"]
        project = state["items"].get(state["redirects"].get(item.get("project"), item.get("project")))
        if project and project["kind"] == "project":
            entry["project"] = {"id": project["id"], "name": project["name"], "aliases": list(project.get("aliases", []))}
        last = item.get("response")
        if isinstance(last, dict) and last.get("response") in RESPONSES:
            entry["last_response"] = {"response": last["response"],
                                      "at": datetime.fromtimestamp(last["at"], now.tzinfo).isoformat(timespec="seconds")}
            until = last.get("until")
            if last["response"] == "another_day" and until and not _reached(until, now):
                entry["snoozed_until"] = until
        items.append(entry)
    return {"schema": SCHEMA_VERSION, "now": now.isoformat(timespec="seconds"),
            "timezone": str(now.tzinfo), "items": items}


def _batch(request_id):
    if not name(request_id) or len(request_id) > 160:
        raise ValueError("a request needs an identifier")
    return f"reminder:{request_id}"


def entrust(path, request_id, text, *, at=None, due=None, people=(), ref=None, quote=None):
    """The owner's own words become one action at once. The same request twice is still one action."""
    if not name(text):
        raise ValueError("an entrusted thing needs its words")
    due, at = moment(due), time() if at is None else at
    evidence = {"ref": ref or f"entrusted:{request_id}", "quote": quote or text}

    def build(state):
        if any(item.get("request") == request_id for item in state["items"].values()):
            return
        fields = {"name": text, "status": "open", "project": UNASSIGNED, "request": request_id,
                  "people": [(_person(state, label)["id"] or label) for label in people] or [UNASSIGNED]}
        if due:
            fields["due"] = due
        yield {"op": "create", "kind": "action", "actor": "owner", "at": at, "evidence": [evidence],
               "id": allocate(text, set(state["items"]) | set(state["redirects"])), "fields": fields}

    created = append(build, path, batch=_batch(request_id))
    state = load(path, with_field_records=True)
    identity = next(key for key, item in state["items"].items() if item.get("request") == request_id)
    item = next((entry for entry in outlet(state)["items"] if entry["id"] == identity), None)
    return item or {"id": identity, "title": state["items"][identity]["name"], "origin": "entrusted",
                    "open": state["items"][identity].get("status") not in TERMINAL, "occasions": []}, bool(created)


def respond(path, request_id, identity, response, *, at=None, occasion=None, until=None, draft=None,
            ref=None, quote=None):
    """What the owner did when it was brought up. done and dropped close the action itself."""
    if response not in RESPONSES:
        raise ValueError("response is one of " + ", ".join(RESPONSES))
    at = time() if at is None else at
    answer = {"response": response, "at": at, "request": request_id}
    if occasion is not None:
        answer["occasion"] = occasion
    if draft is not None:
        answer["draft"] = draft
    if response == "another_day":
        answer["until"] = moment(until) or (datetime.fromtimestamp(at).astimezone().date() + timedelta(days=1)).isoformat()

    def build(state):
        matches = [identity] if identity in state["items"] else find(state, identity)
        matches = [key for key in matches if state["items"][key]["kind"] == "action"]
        if len(matches) != 1:
            raise ValueError("no such action")
        fields = {"response": answer}
        if response in CLOSING and state["items"][matches[0]].get("status") not in TERMINAL:
            fields["status"] = CLOSING[response]
        yield {"op": "update", "kind": "action", "actor": "owner", "at": at, "id": matches[0], "fields": fields,
               "evidence": [{"ref": ref or f"reminder-response:{request_id}", "quote": quote or response}]}

    created = append(build, path, batch=_batch(request_id))
    state = load(path)
    key = identity if identity in state["items"] else find(state, identity)[0]
    status = state["items"][key].get("status")
    return {"id": key, "response": response, "created": bool(created), "open": status not in TERMINAL, "state": status}
