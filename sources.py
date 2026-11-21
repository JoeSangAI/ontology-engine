"""Register authorized local sources and advance their cursors after journal append."""

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from time import time

from ingest import PROMPT, folder_changes, proposal_rows
from ontology import append, encode, name

DEFAULT_REGISTRY = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "ontology-engine/sources.json"


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


@contextmanager
def locked_registry(path, store):
    path = Path(path).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    with os.fdopen(os.open(lock, os.O_NOFOLLOW | os.O_CREAT | os.O_RDWR, 0o600), "rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        store = str(Path(store).expanduser().resolve())
        data = json.loads(path.read_bytes()) if path.exists() else {"store": store, "sources": {}}
        if data["store"] != store:
            raise ValueError("source registry is bound to a different ontology journal")
        yield data


def save(path, data):
    path = Path(path).expanduser()
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write((encode(data) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def register(path, store, identity, location, kind):
    if not name(identity) or kind not in ("folder", "fragments"):
        raise ValueError("provide a source identifier and supported source type")
    location = Path(location).expanduser().resolve(strict=True)
    if not (location.is_dir() if kind == "folder" else location.is_file()):
        raise ValueError("source location does not match its type")
    with locked_registry(path, store) as data:
        sources = data["sources"]
        spec = {"type": kind, "path": str(location)}
        if identity in sources and any(sources[identity][key] != value for key, value in spec.items()):
            raise ValueError("use a different identifier for a different source")
        if identity not in sources:
            sources[identity] = spec
            save(path, data)
        return {"id": identity, **sources[identity]}


def fragment_changes(path, previous):
    """Read an authorized connector export with stable references and verbatim text."""
    fragments = json.loads(Path(path).read_bytes())
    if not isinstance(fragments, list):
        raise ValueError("fragment source needs a JSON array")
    by_ref = {}
    for entry in fragments:
        if (not isinstance(entry, dict) or not name(entry.get("ref"))
                or not isinstance(entry.get("quote"), str) or not entry["quote"].strip()
                or entry["ref"] in by_ref):
            raise ValueError("fragment source needs unique references and nonempty quotes")
        by_ref[entry["ref"]] = {"ref": entry["ref"], "quote": entry["quote"]}
    signatures = {key: digest(entry["quote"]) for key, entry in by_ref.items()}
    changed = sorted(key for key in previous.keys() | signatures.keys()
                     if previous.get(key) != signatures.get(key))
    return changed, [by_ref[key] for key in changed if key in by_ref], signatures


def scan(data):
    sources = data["sources"]
    fresh = sorted(key for key, spec in sources.items() if "cursor" not in spec)
    changed, fragments, cursors = {}, [], {}
    for key, spec in sorted(sources.items()):
        previous = spec.get("cursor", {})
        reader = folder_changes if spec["type"] == "folder" else fragment_changes
        paths, excerpts, cursor = reader(spec["path"], {} if fresh else previous)
        cursors[key] = cursor
        changed[key] = sorted(path for path in previous.keys() | cursor.keys()
                              if previous.get(path) != cursor.get(path)) if fresh else paths
        fragments.extend({**entry, "source_id": key} for entry in excerpts)
    token = digest([data, cursors]) if fresh or any(changed.values()) else None
    return {"token": token, "recheck": bool(fresh), "new_sources": fresh,
            "changed": changed, "fragments": fragments, "prompt": PROMPT}, cursors


def prepare(path, store):
    with locked_registry(path, store) as data:
        return scan(data)[0]


def apply(path, store, token, proposals):
    with locked_registry(path, store) as data:
        result, cursors = scan(data)
        if not token or result["token"] != token:
            raise ValueError("scan changed or is already applied; run scan again")
        records = append(
            lambda state: proposal_rows(proposals, state, result["fragments"], time()),
            store, batch=token,
        ) if proposals != [] else []
        for key, cursor in cursors.items():
            data["sources"][key]["cursor"] = cursor
        save(path, data)
        return {"appended": len(records), "recheck": result["recheck"]}
