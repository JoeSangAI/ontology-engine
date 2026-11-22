import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cli import correct
from ingest import extract, folder_changes
from migrate import migrate
from ontology import UNASSIGNED, allocate, append, find, load, query, replay

REF = {"ref": "notes:1", "quote": "发布项目暂停。"}


def event(identity, *, kind="project", at=1, actor="source", op="create", fields=None, **options):
    return {"id": identity, "kind": kind, "at": at, "actor": actor, "op": op,
            "fields": {"name": identity} if fields is None else fields, "evidence": [REF], **options}


class ReplayTests(unittest.TestCase):
    def test_field_authority_disagreement_and_time(self):
        rows = [
            event("发布项目", fields={"name": "发布项目", "status": "open"}, at=2),
            event("发布项目", op="update", actor="model", at=3, fields={"status": "done", "next": "排期"}),
            event("发布项目", op="update", actor="owner", at=4, fields={"status": "paused"},
                  evidence=[REF, {"ref": "notes:2", "quote": "暂停一周。"}]),
            event("发布项目", op="update", at=5, fields={"status": "open", "next": "复核"}),
            event("发布项目", op="update", actor="owner", at=3.5, fields={"status": "reviewing"}),
        ]
        item = replay(rows)["items"]["发布项目"]
        self.assertEqual((item["status"], item["next"]), ("paused", "复核"))
        self.assertEqual(item["disagreements"]["status"], {"value": "open", "actor": "source", "evidence": [REF]})
        self.assertEqual(len(item["evidence"]), 2)
        rows.append(event("发布项目", op="update", at=6, fields={"status": "paused"}))
        self.assertNotIn("disagreements", replay(rows)["items"]["发布项目"])

    def test_model_below_source_without_owner(self):
        state = replay([
            event("发布项目", fields={"name": "发布项目", "status": "open"}),
            event("发布项目", actor="model", op="update", fields={"status": "completed"}, at=2),
        ])
        item = state["items"]["发布项目"]
        self.assertEqual(item["status"], "open")
        self.assertEqual(item["disagreements"]["status"]["actor"], "model")

    def test_source_union_and_owner_replacement(self):
        rows = [event("发布项目", fields={"name": "发布项目", "aliases": ["上线"], "misrecognitions": ["发步"]}),
                event("发布项目", op="update", at=2, fields={"aliases": ["上线", "发布"],
                                                          "misrecognitions": ["发部"]})]
        self.assertEqual(find(replay(rows), "发步"), ["发布项目"])
        self.assertEqual(replay(rows)["items"]["发布项目"]["aliases"], ["上线", "发布"])
        rows.extend([event("发布项目", actor="owner", op="update", at=3,
                           fields={"aliases": [], "misrecognitions": ["发行"]}),
                     event("发布项目", op="update", at=4, fields={"misrecognitions": ["发布会"]})])
        state = replay(rows)
        self.assertEqual(state["items"]["发布项目"]["aliases"], [])
        self.assertEqual(find(state, "发行"), ["发布项目"])
        self.assertEqual(find(state, "发步"), [])

    def test_redirect_chain_and_kind_errors(self):
        rows = [event("项目"), event("编号", op="redirect", target="项目", fields={}),
                event("项目", actor="owner", at=2, op="redirect", target="新名", fields={"name": "新名"}),
                event("新名", actor="owner", at=3, op="redirect", target="新名二", fields={"name": "新名二"})]
        state = replay(rows)
        self.assertEqual(find(state, "编号"), ["新名二"])
        self.assertEqual(find(state, "项目"), ["新名二"])
        self.assertEqual(state["items"]["新名二"]["name"], "新名二")
        with self.assertRaisesRegex(ValueError, "cycle"):
            replay([*rows, event("新名二", op="redirect", target="项目", fields={})])
        with self.assertRaisesRegex(ValueError, "different kind"):
            replay([event("项目"), event("编号", kind="subject", op="redirect", target="项目", fields={})])

    def test_merge_cancel_and_lower_priority_dispute(self):
        rows = [event("项目一"), event("项目二"), event("项目三"),
                event("项目一", actor="owner", op="merge", target="项目二", fields={}, at=2),
                event("项目一", actor="model", op="merge", target="项目三", fields={}, at=3)]
        state = replay(rows)
        self.assertEqual(find(state, "项目一"), ["项目二"])
        self.assertEqual(state["items"]["项目二"]["disagreements"]["merge"]["项目一"]["value"], "项目三")
        rows.append(event("项目一", actor="owner", op="merge", target=None, fields={}, at=4))
        self.assertEqual(find(replay(rows), "项目一"), ["项目一"])

    def test_read_links_questions_and_collisions(self):
        rows = [event("客户机构", kind="subject", fields={"name": "客户机构", "type": "organization"}),
                event("评审人", kind="subject", fields={"name": "评审人", "organization": "客户机构"}),
                event("交付项目", fields={"name": "交付项目", "client": "客户机构",
                                        "misrecognitions": ["交付向目"]}),
                event("整理稿件", kind="action", fields={"name": "整理稿件", "project": "交付项目", "people": ["评审人"]})]
        other = allocate("评审人", [row["id"] for row in rows])
        rows.append(event(other, kind="subject", fields={"name": "评审人"}))
        state = replay(rows)
        self.assertEqual(find(state, "评审人"), ["评审人", "评审人 (2)"])
        self.assertEqual(query(state, question="交付向目还有几件事", kind="action")["related"][0]["id"], "整理稿件")
        self.assertEqual(query(state, identity="客户机构", kind="action")["related"][0]["id"], "整理稿件")
        self.assertEqual(query(state, identity="客户机构", kind="project")["related"][0]["id"], "交付项目")
        self.assertEqual(query(state, question="稿件怎么整理", kind="action")["matches"][0]["id"], "整理稿件")

    def test_parent_project_brings_its_children_and_their_actions(self):
        state = replay([
            event("大项目", fields={"name": "大项目"}),
            event("小项目", fields={"name": "小项目", "parent": "大项目"}),
            event("例会", fields={"name": "例会", "parent": "大项目"}),
            event("准备方案", kind="action", fields={"name": "准备方案", "project": "小项目", "people": ["未归属"]}),
        ])
        self.assertEqual([row["id"] for row in query(state, identity="大项目", kind="project")["related"]],
                         ["例会", "小项目"])
        self.assertEqual([row["id"] for row in query(state, identity="大项目", kind="action")["related"]], ["准备方案"])
        self.assertEqual([row["id"] for row in query(state, identity="例会", kind="project")["related"]], ["大项目"])

    def test_org_correction_does_not_keep_old_migrated_relationship(self):
        rows = [
            event("原机构", kind="subject", fields={"name": "原机构", "type": "organization"}),
            event("新机构", kind="subject", fields={"name": "新机构", "type": "organization"}),
            event("评审人", kind="subject", fields={"name": "评审人", "organization": "原机构",
                                                    "organization_id": "原机构"}),
            event("评审人", kind="subject", actor="owner", op="update", at=2,
                  fields={"organization": "新机构"}),
        ]
        state = replay(rows)
        self.assertEqual(query(state, identity="原机构")["related"], [])
        self.assertEqual([item["id"] for item in query(state, identity="新机构")["related"]], ["评审人"])

    def test_invalid_event_rejected(self):
        for row in (event("缺归属", kind="action"),
                    event("坏时间", at=float("inf")),
                    event("坏出处", evidence=[]),
                    event("坏出处", evidence=[{"ref": "notes:1"}]),
                    event("坏字段", op="update", fields={"at": 5})):
            with self.subTest(row=row), self.assertRaises(ValueError):
                replay([row])


class EntryPointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "private" / "ontology.jsonl"

    def test_append_and_cli_correction_do_not_rewrite(self):
        append(lambda _: [event("发布项目")], self.path)
        previous = self.path.read_bytes()
        found = subprocess.run([sys.executable, "-m", "cli", "--store", str(self.path), "correct",
                                "发布项目", "--fields", '{"status":"paused"}', "--quote", REF["quote"]],
                               capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(found.stdout)[0]["actor"], "owner")
        self.assertTrue(self.path.read_bytes().startswith(previous))
        self.assertEqual(len(correct(self.path, "发布项目", REF, rename="改版项目")), 1)
        found = subprocess.run([sys.executable, "-m", "cli", "--store", str(self.path), "query",
                                "--name", "发布项目"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(found.stdout)["matches"][0]["status"], "paused")
        previous = self.path.read_bytes()
        with self.assertRaises(ValueError):
            append(lambda _: [event("未知项目", op="update", fields={"status": "paused"})], self.path)
        self.assertEqual(self.path.read_bytes(), previous)

    def test_folder_changes_and_stub_model(self):
        folder = self.root / "source"
        folder.mkdir()
        original = folder / "notes.md"
        original.write_text("核对交付清单，项目还未确定。\n", encoding="utf-8")
        hidden = folder / ".hidden"
        hidden.mkdir()
        (hidden / "notes.md").write_text("忽略隐藏目录", encoding="utf-8")
        (folder / "shortcut.md").symlink_to(original)
        changed, fragments, cursor = folder_changes(folder)
        self.assertEqual(changed, ["notes.md"])
        self.assertTrue(fragments[0]["ref"].endswith("notes.md#L1-L1"))
        self.assertEqual(folder_changes(folder, cursor)[:2], ([], []))

        def model(prompt, state, traces):
            self.assertIn("未归属", prompt)
            self.assertEqual(state, {})
            return [{"op": "create", "kind": "action",
                     "fields": {"name": "核对清单", "project": UNASSIGNED, "people": [UNASSIGNED]},
                     "evidence": [{"ref": traces[0]["ref"], "quote": "核对交付清单"}]}]

        self.assertEqual(extract(fragments, model, path=self.path)[0]["actor"], "model")
        self.assertEqual(load(self.path)["items"]["核对清单"]["project"], UNASSIGNED)
        raw = self.path.read_bytes()
        bad = model("未归属", {}, fragments)
        bad[0]["evidence"][0]["quote"] = "编造的原话"
        with self.assertRaisesRegex(ValueError, "verbatim"):
            extract(fragments, lambda *_: bad, path=self.path)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(original.read_text(), fragments[0]["quote"])
        original.unlink()
        self.assertEqual(folder_changes(folder, cursor)[:2], (["notes.md"], []))

    def test_migration_keeps_old_ids_and_verified_evidence(self):
        source = self.root / "notes.md"
        source.write_text(REF["quote"] + "\n", encoding="utf-8")
        ref = {"source": "project-file", "path": str(source), "line": 1, "at": 1,
               "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        seed = self.root / "seed.jsonl"
        seed.write_text(json.dumps({"op": "create", "id": "project:001", "kind": "project",
                                    "actor": "source", "event_at": 1, "fields": {"name": "发布项目"},
                                    "evidence": [ref]}), encoding="utf-8")
        snapshot = self.root / "snapshot.json"
        snapshot.write_text(json.dumps({"items": [{"id": "action:001", "kind": "action", "at": 1,
                                                   "name": "核对清单", "project_label": "发布项目",
                                                   "provenance": [ref]}],
                                        "redirects": {"action:000": "action:001"}}), encoding="utf-8")
        self.assertEqual(migrate(seed, snapshot, self.path, read_evidence=True), 5)
        state = load(self.path)
        self.assertEqual(find(state, "project:001"), ["发布项目"])
        self.assertEqual(find(state, "action:000"), ["核对清单"])
        self.assertEqual(state["items"]["发布项目"]["evidence"][0]["quote"], REF["quote"])
        self.assertEqual(state["items"]["核对清单"]["people"], [UNASSIGNED])
        self.assertEqual(query(state, identity="发布项目", kind="action")["related"][0]["id"], "核对清单")
        with self.assertRaises(FileExistsError):
            migrate(seed, snapshot, self.path)


if __name__ == "__main__":
    unittest.main()
