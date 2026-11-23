import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import reminders
from ontology import append, load

ZONE = timezone(timedelta(hours=8))
NOW = datetime(2030, 3, 4, 10, 0, tzinfo=ZONE)


def seed(path, rows):
    append(lambda state: rows, path)


def record(op, identity, kind, fields, at=1, actor="source"):
    return {"op": op, "id": identity, "kind": kind, "actor": actor, "at": at, "fields": fields,
            "evidence": [{"ref": "notes.md#L1", "quote": "示例"}]}


class Reminders(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "ontology.jsonl"
        seed(self.path, [
            record("create", "林青", "subject", {"name": "林青", "type": "person", "aliases": ["青姐"]}),
            record("create", "旧事", "action", {"name": "旧事", "status": "open", "project": "未归属",
                                               "people": ["未归属"]}, at=NOW.timestamp() - 20 * 86400),
        ])

    def outlet(self):
        return {item["id"]: item for item in reminders.outlet(load(self.path, with_field_records=True), NOW)["items"]}

    def test_three_occasions_in_general_terms(self):
        item, created = reminders.entrust(self.path, "r-1", "把样稿发给林青", at=NOW.timestamp(),
                                          due="2030-03-04T15:00:00+08:00", people=["青姐", "没见过的人"])
        self.assertTrue(created)
        again, created = reminders.entrust(self.path, "r-1", "把样稿发给林青", at=NOW.timestamp())
        self.assertFalse(created)
        self.assertEqual(again["id"], item["id"])
        listed = self.outlet()
        self.assertEqual(listed[item["id"]]["occasions"], [
            {"type": "time", "at": "2030-03-04T15:00:00+08:00", "due": False, "kind": "appointment"},
            {"type": "person", "why": "concerns", "people": [
                {"id": "林青", "name": "林青", "aliases": ["青姐"]},
                {"id": None, "name": "没见过的人", "aliases": []}]}])
        self.assertEqual(listed["旧事"]["occasions"], [{"type": "state", "state": "stalled", "days": 20}])
        self.assertEqual(sum(item["kind"] == "action" for item in load(self.path)["items"].values()), 2)

    def test_a_response_changes_the_same_action(self):
        item, _ = reminders.entrust(self.path, "r-2", "回电话", at=NOW.timestamp(), due="2030-03-04")
        reminders.respond(self.path, "a-1", item["id"], "later", at=NOW.timestamp() + 1, occasion={"type": "time"})
        self.assertEqual(self.outlet()[item["id"]]["last_response"]["response"], "later")
        reminders.respond(self.path, "a-2", item["id"], "another_day", at=NOW.timestamp() + 2, until="2030-03-06")
        self.assertEqual(self.outlet()[item["id"]]["snoozed_until"], "2030-03-06")
        done = reminders.respond(self.path, "a-3", item["id"], "done", at=NOW.timestamp() + 3)
        self.assertEqual((done["created"], done["open"], done["state"]), (True, False, "completed"))
        self.assertFalse(reminders.respond(self.path, "a-3", item["id"], "done", at=NOW.timestamp() + 3)["created"])
        self.assertNotIn(item["id"], self.outlet())
        with self.assertRaises(ValueError):
            reminders.respond(self.path, "a-4", "没有这件事", "done")

    def test_a_clock_time_needs_its_zone(self):
        with self.assertRaises(ValueError):
            reminders.entrust(self.path, "r-3", "开会", due="2030-03-04T15:00:00")


if __name__ == "__main__":
    unittest.main()
