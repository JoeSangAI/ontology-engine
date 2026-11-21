import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backfill import plan
from ontology import UNASSIGNED, append, load, query, replay


def event(identity, kind, fields, *, evidence=None, actor="source", op="create", at=1):
    if op == "create":
        fields = {"name": identity, **fields}
    return dict(op=op, id=identity, kind=kind, actor=actor, fields=fields, at=at,
                evidence=evidence or [{"ref": "source.md#L1", "quote": identity}])


def project(identity, home):
    return event(identity, "project", {"home": [str(home)]})


def action(identity, *sources):
    return event(identity, "action", {"project": UNASSIGNED, "people": [UNASSIGNED]},
                 evidence=[{"ref": str(source), "quote": None} for source in sources])


def organization(identity, *aliases):
    return event(identity, "subject", {"type": "organization", "aliases": list(aliases)})


def person(identity, label, **options):
    return event(identity, "subject", {"type": "person", "organization": label}, **options)


class BackfillTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def build(self, rows, **options):
        return plan(replay(rows, with_field_records=True), at=10, **options)

    def test_action_matches_cited_ledger_path_inside_home(self):
        home = self.root / "launch"
        ledger = self.root / "ledger.json"
        source = home / "notes" / "brief.md"
        ledger.write_text(json.dumps({"task_followups": [
            {"followup_id": "first", "state_owner": str(source)},
        ]}))
        task = action("review")
        task["evidence"] = [{"ref": f"{ledger}#first", "quote": "review",
                             "path": str(ledger), "record": "first", "source": "workbench-ledger"}]
        records, _ = self.build([project("launch", home), task], ledger=ledger)
        self.assertEqual(records[0]["fields"], {"project": "launch"})
        self.assertEqual(records[0]["evidence"], [
            {"ref": f"{ledger}#first", "quote": str(source), "field": "state_owner"},
        ])

    def test_person_matches_registered_alias_and_uses_winning_field_evidence(self):
        rows = [organization("Example Company", "Example"), person("Reviewer", "EXAMPLE")]
        records, _ = self.build(rows)
        self.assertEqual(records[0]["fields"], {"organization_id": "Example Company"})
        self.assertEqual(records[0]["evidence"][0]["quote"], "EXAMPLE")
        self.assertEqual(query(replay(rows + records), identity="Example Company")["related"][0]["id"],
                         "Reviewer")

    def test_project_matches_immediate_parent_alias_and_is_idempotent(self):
        home = self.root / "Example" / "Phase"
        rows = [organization("Example Company", "Example"), project("Phase", home)]
        journal = self.root / "journal.jsonl"
        append(lambda _: rows, journal)

        def fill(state):
            return plan(state, at=10)[0]

        records = append(fill, journal, with_field_records=True)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["actor"], "source")
        self.assertEqual(records[0]["evidence"][0]["quote"], str(home))
        self.assertEqual(load(journal)["items"]["Phase"]["client"], "Example Company")
        self.assertEqual(append(fill, journal, with_field_records=True), [])

    def test_ambiguous_and_missing_links_do_not_write(self):
        home = self.root / "Shared" / "Launch"
        other = self.root / "Elsewhere"
        rows = [
            organization("One", "Shared"), organization("Two", "Shared"),
            project("Launch", home), project("Second", other),
            action("multi", home / "brief.md", other / "brief.md"),
            action("prefix collision", home.with_name("Launching") / "brief.md"),
            person("ambiguous", "Shared"), person("unknown", "Unknown"),
            person("unsupported", "One", actor="model"),
            event("Launch", "project", {"client": UNASSIGNED}, actor="owner", op="update", at=2),
        ]
        records, pending = self.build(rows)
        self.assertEqual(records, [])
        self.assertEqual([len(group) for group in pending.values()], [2, 3, 2, 0])
        self.assertEqual(next(iter(pending.values()))[0][1], ["Launch", "Second"])

    def test_owner_action_placeholder_has_precedence(self):
        home = self.root / "launch"
        rows = [project("launch", home), action("review", home / "brief.md"),
                event("review", "action", {"project": UNASSIGNED}, actor="owner", op="update", at=2,
                      evidence=[{"ref": str(home / "brief.md"), "quote": None}])]
        records, pending = self.build(rows)
        self.assertEqual(records, [])
        self.assertIn("保留现有 owner 字段", next(iter(pending.values()))[0][3])

    def test_registration_requires_an_explicit_source(self):
        source = self.root / "records.md"
        source.write_text("Official organization: Example Company\n")
        context = {"organizations": [{"name": "Example Company", "path": str(source), "line": 1}]}
        rows = [project("Example Company", self.root / "Example Company" / "Phase"),
                person("Reviewer", "Example Company")]
        records, _ = self.build(rows, context=context)
        self.assertEqual(records[0]["id"], "Example Company (2)")
        self.assertEqual(records[0]["evidence"][0]["quote"], "Official organization: Example Company")
        self.assertEqual(records[1]["fields"], {"client": "Example Company (2)"})
        self.assertEqual(records[2]["fields"], {"organization_id": "Example Company (2)"})
        context["organizations"][0]["name"] = "Different Company"
        with self.assertRaisesRegex(ValueError, "missing from cited source"):
            self.build(rows, context=context)


if __name__ == "__main__":
    unittest.main()
