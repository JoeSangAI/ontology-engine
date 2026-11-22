#!/bin/bash
# One run of the whole loop on fictional notes, in a throwaway folder:
# register a folder -> scan -> an agent's judgments (written here by hand) -> query -> owner correction.
set -euo pipefail
cd "$(dirname "$0")/.."
WORK="$(mktemp -d)"
cp -R examples/notes "$WORK/notes"
run() { python3.12 -m cli --store "$WORK/ontology.jsonl" --registry "$WORK/sources.json" "$@"; }

run source-add notes "$WORK/notes" > /dev/null
run scan > "$WORK/scan.json"

# The agent reads the fragments and answers with judgments that quote them word for word.
python3.12 - "$WORK" <<'PY'
import json, sys
from pathlib import Path
work = Path(sys.argv[1])
scan = json.loads((work / "scan.json").read_text())
ref = {Path(f["path"]).stem: f["ref"] for f in scan["fragments"]}
proposals = [
    {"op": "create", "kind": "subject",
     "fields": {"name": "青禾茶饮", "type": "organization", "misrecognitions": ["青河茶饮"]},
     "evidence": [{"ref": ref["周会"], "quote": "转写里把「青禾」听成了「青河」。"}]},
    {"op": "create", "kind": "subject",
     "fields": {"name": "林夏", "type": "person", "organization": "青禾茶饮"},
     "evidence": [{"ref": ref["通讯录"], "quote": "林夏：青禾茶饮市场部经理。"}]},
    {"op": "create", "kind": "project", "fields": {"name": "青禾春季新品发布", "client": "青禾茶饮"},
     "evidence": [{"ref": ref["周会"], "quote": "青禾茶饮的春季新品发布，林夏负责，月底前出方案。"}]},
    {"op": "create", "kind": "action",
     "fields": {"name": "出春季新品方案", "project": "青禾春季新品发布", "people": ["林夏"],
                "due": "月底前", "status": "open"},
     "evidence": [{"ref": ref["周会"], "quote": "林夏负责，月底前出方案。"}]},
]
(work / "proposals.json").write_text(json.dumps(proposals, ensure_ascii=False))
(work / "token").write_text(scan["token"])
PY
run apply "$(cat "$WORK/token")" "$WORK/proposals.json" > /dev/null

show() { python3.12 -c 'import json,sys; d=json.load(sys.stdin); print("  命中:", [m["id"] for m in d["matches"]], " 相关:", [m["id"] for m in d["related"]])'; }
echo '问「青河茶饮还有什么事」（转写听错的写法）：'
run query --question '青河茶饮还有什么事' --kind action | show
echo '问「林夏是哪家的」：'
run query --name 林夏 | python3.12 -c 'import json,sys; m=json.load(sys.stdin)["matches"][0]; print("  ", m["name"], "→", m["organization"])'
echo '主人说「方案提前到11月20日交。」：'
run correct 出春季新品方案 --fields '{"due":"11月20日"}' --quote '方案提前到11月20日交。' > /dev/null
run query --name 出春季新品方案 | python3.12 -c 'import json,sys; m=json.load(sys.stdin)["matches"][0]; print("  截止:", m["due"], "· 出处:", [e["quote"] for e in m["evidence"]][-1])'
echo "日志在 $WORK/ontology.jsonl，可以直接打开看。"
