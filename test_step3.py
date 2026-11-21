import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import review
import sources
from ingest import extract
from ontology import UNASSIGNED, append, certainty, find, load, replay
from test_ontology import REF, event


class RobustnessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = self.root / "ontology.jsonl"
        self.registry = self.root / "sources.json"

    def command(self, *args, **options):
        result = subprocess.run(
            [sys.executable, "-m", "cli", "--store", str(self.store),
             "--registry", str(self.registry), *args], check=True, text=True,
            capture_output=True, **options,
        )
        return json.loads(result.stdout)

    def test_certainty_is_pure_and_counts_origins(self):
        refs = [{"ref": "notes.md#L1", "quote": "项目"}, {"ref": "notes.md#L5-L8", "quote": "项目启动"}]
        self.assertEqual(certainty("model", refs), "uncertain")
        self.assertEqual(certainty("source", refs), "certain")
        self.assertEqual(certainty("source", [{"ref": "notes.md", "quote": None}]), "uncertain")
        self.assertEqual(certainty("model", [*refs, REF]), "certain")
        self.assertEqual(certainty("owner", []), "certain")
        self.assertEqual(len(refs), 2)

    def test_new_source_rechecks_unchanged_evidence_and_tracks_own_cursor(self):
        first, second = self.root / "first", self.root / "second"
        for folder, text in ((first, "交付项目进入核对。"), (second, "交付项目已结束。")):
            folder.mkdir()
            (folder / "record.md").write_text(text)
        self.command("source-add", "first", str(first))
        start = self.command("scan")
        ref = start["fragments"][0]
        record = {"op": "create", "kind": "project", "fields": {"name": "交付项目", "status": "open"},
                  "evidence": [{"ref": ref["ref"], "quote": ref["quote"]}]}
        self.command("apply", start["token"], "-", input=json.dumps([record]))
        self.assertIsNone(self.command("scan")["token"])
        self.command("source-add", "second", str(second))
        plan = self.command("scan")
        self.assertEqual(plan["new_sources"], ["second"])
        self.assertEqual(plan["changed"], {"first": [], "second": ["record.md"]})
        self.assertEqual({ref["source_id"] for ref in plan["fragments"]}, {"first", "second"})
        update = {**record, "op": "update", "id": "交付项目", "fields": {"status": "completed"},
                  "evidence": [{k: ref[k] for k in ("ref", "quote")} for ref in plan["fragments"]]}
        self.command("apply", plan["token"], "-", input=json.dumps([update]))
        item = load(self.store)["items"]["交付项目"]
        self.assertEqual((item["status"], item["certainty"]), ("completed", "certain"))
        (first / "record.md").write_text("交付项目收尾归档。")
        later = self.command("scan")
        self.assertFalse(later["recheck"])
        self.assertEqual([ref["source_id"] for ref in later["fragments"]], ["first"])

    def test_failed_cursor_commit_is_retryable_and_quotes_are_checked(self):
        material = self.root / "source.json"
        material.write_text(json.dumps([REF]))
        sources.register(self.registry, self.store, "connector", material, "fragments")
        token = sources.prepare(self.registry, self.store)["token"]
        create = {"op": "create", "kind": "project", "fields": {"name": "发布项目"}, "evidence": [REF]}
        invalid = {**create, "evidence": [{**REF, "quote": "不存在的摘录"}]}
        with self.assertRaisesRegex(ValueError, "verbatim"):
            sources.apply(self.registry, self.store, token, [invalid])
        self.assertEqual(sources.prepare(self.registry, self.store)["token"], token)
        with patch("sources.save", side_effect=OSError("checkpoint interrupted")), self.assertRaises(OSError):
            sources.apply(self.registry, self.store, token, [create])
        material.write_text(json.dumps([{**REF, "quote": "发布项目暂停，等待通知。"}]))
        with self.assertRaisesRegex(ValueError, "scan changed"):
            sources.apply(self.registry, self.store, token, [create])
        material.write_text(json.dumps([REF]))
        self.assertEqual(sources.apply(self.registry, self.store, token, [create])["appended"], 0)
        self.assertEqual(list(load(self.store)["items"]), ["发布项目"])
        self.assertIsNone(sources.prepare(self.registry, self.store)["token"])

    def test_split_keeps_original_log_and_partitions_evidence_and_legacy_id(self):
        moved = {"ref": "handoff:2", "quote": "独立验收项目。"}
        append(lambda _: [event("发布项目"), event("旧编号", op="redirect", target="发布项目", fields={}),
                          event("发布项目", op="update", fields={"next": "验收"}, evidence=[moved])], self.store)
        previous = self.store.read_bytes()
        self.command("split", "发布项目", "--fields", '{"name":"验收项目","next":"验收"}',
                     "--move-ref", moved["ref"], "--redirect-id", "旧编号",
                     "--keep-fields", '{"next":null}', "--quote", "验收另开项目。")
        self.assertTrue(self.store.read_bytes().startswith(previous))
        state = load(self.store)
        self.assertEqual(find(state, "旧编号"), ["验收项目"])
        self.assertEqual(find(state, "发布项目"), ["发布项目"])
        self.assertNotIn(moved, state["items"]["发布项目"]["evidence"])
        self.assertIn(moved, state["items"]["验收项目"]["evidence"])
        self.assertIsNone(state["items"]["发布项目"]["next"])

    def test_model_can_fill_source_placeholder_but_owner_still_wins(self):
        create = event("核对稿件", kind="action", fields={"name": "核对稿件",
                       "project": UNASSIGNED, "people": [UNASSIGNED]})
        model = event("核对稿件", kind="action", actor="model", op="update", at=2,
                      fields={"project": "交付项目"})
        later = event("核对稿件", kind="action", op="update", at=3, fields={"project": UNASSIGNED})
        owner = {**later, "actor": "owner", "at": 4}
        state = replay([create, model, later], with_field_records=True)
        self.assertEqual(state["items"]["核对稿件"]["project"], "交付项目")
        self.assertEqual(state["field_records"]["核对稿件"]["project"], model)
        revised = {**model, "at": 4, "fields": {"project": UNASSIGNED}}
        self.assertEqual(replay([create, model, revised])["items"]["核对稿件"]["project"], UNASSIGNED)
        state = replay([create, model, owner, {**model, "at": 5}])
        item = state["items"]["核对稿件"]
        self.assertEqual(item["project"], UNASSIGNED)
        self.assertEqual(item["disagreements"]["project"]["value"], "交付项目")

    def test_owner_review_conflict_and_decisions(self):
        append(lambda _: [event("发布项目", actor="owner", fields={"name": "发布项目", "status": "paused"}),
                          event("核对项目", actor="model")], self.store)
        proposals = [
            {"op": "update", "id": "发布项目", "kind": "project", "fields": {"status": "open"}, "evidence": [REF]},
            {"op": "review", "kind": "project", "fields": {}, "reason": "merge-suggestion",
             "target": ["发布项目", "核对项目"], "evidence": [REF]},
            {"op": "review", "kind": "project", "fields": {}, "reason": "split-suggestion",
             "target": ["发布项目"], "evidence": [REF]},
        ]
        extract([REF], lambda *_: proposals, path=self.store)
        queue = {entry["reason"]: entry for entry in self.command("review")}
        self.assertEqual(set(queue), {"new-identity", "uncertain", "disagreement",
                                      "merge-suggestion", "split-suggestion"})
        self.assertEqual(queue["disagreement"]["current"], "paused")
        self.assertEqual(queue["disagreement"]["proposed"], "open")
        self.command("review-answer", queue["new-identity"]["id"], "confirm", "--quote", "核对项目确实存在。")
        self.assertEqual(load(self.store)["items"]["核对项目"]["certainty"], "certain")
        review.answer(self.store, queue["split-suggestion"]["id"], "dismiss", REF)
        result = self.command("review-answer", queue["merge-suggestion"]["id"], "merge",
                              "--target", "发布项目", "--quote", "两者是同一项目。")
        self.assertTrue(all(row["actor"] == "owner" for row in result))
        self.assertEqual(find(load(self.store), "核对项目"), ["发布项目"])
        self.assertEqual({row["reason"] for row in self.command("review")}, {"disagreement"})
