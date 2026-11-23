# Ontology Engine

用一份本地 JSONL 日志记录主体（人或机构）、项目和行动。从日志回放现状，按名字或问题检索，并沿引用返回相关条目。需要 Python 3.12 和支持文件锁的系统（例如 macOS 或 Linux）。依赖均为标准库。

## 命令行

```sh
python3.12 -m cli query --kind project
python3.12 -m cli query --name '示例项目'
python3.12 -m cli query --question '示例项目还有什么事' --kind action
python3.12 -m cli correct '示例项目' --fields '{"status":"paused"}' --quote '示例项目暂停。'
python3.12 -m cli correct '示例项目' --rename '交付项目' --quote '示例项目正式改名为交付项目。'
```

默认日志为当前目录下的 `.local/ontology.jsonl`。可在子命令前用 `--store PATH` 指定路径。首次使用可以运行下方的文件夹抽取或迁移，先建立条目。查询输出 `matches`（直接命中）与 `related`（按字段算出的关系）。`--kind` 同时筛选这两组结果。问题文本以名字和别称匹配为主，辅以词片段匹配，返回可检查的条目。旧名和旧编号仍可查询。

## 写入与回放

每条记录有 `op`、`id`、`kind`、`actor`、Unix 秒 `at`、`fields` 和一条或多条 `evidence`。证据包含引用 `ref` 和摘录 `quote`。缺少原话的旧证据用 `null` 标记。

```json
{"op":"create","id":"示例项目","kind":"project","actor":"source","at":1,"fields":{"name":"示例项目"},"evidence":[{"ref":"notes.md#L1","quote":"示例项目启动。"}]}
```

- 每个字段以 `owner > source > model` 为准。同级取较晚的记录，同一时间按日志顺序。低优先级的后续异议可在 `disagreements` 查到。source 的「未归属」是缺值，允许有证据的模型归属补上。模型可重判自己的归属，owner 明确填写的「未归属」仍以 owner 为准。
- 来源追加 `aliases` 和 `misrecognitions` 时合并列表。主人的纠正替换所写的整个列表，包括空列表。
- 以名字分配标识，重名时加后缀，如 `示例项目 (2)`。创建同名对象时可用 `allocate` 生成标识。纠正可用带后缀的标识选中条目。
- `redirect` 记录旧名或旧编号的去向。用 `correct --rename` 写入一条改名记录。迁移也保留合并记录和待核线索。
- 在 `project`、`people`、`organization`、`client` 等字段写目标标识即可连接。每件新行动都需填写 `project` 和 `people`。未知时分别用 `"未归属"` 和 `["未归属"]`。
- 项目可用 `parent` 指向所属的大项目（例如各业务线属于集团）。查大项目时，下属项目和它们的行动都会出现在 `related` 里。
- `append` 在锁内读取并校验新记录。单条直接写入 JSONL，多条批量写在同一行的 `records` 数组中。日志中的损坏行会报错，便于从备份核对。

## 接入文件夹

```python
from ingest import extract, folder_changes

changed, fragments, next_signatures = folder_changes(folder, previous_signatures)
records = extract(fragments, model)
```

`folder_changes` 返回变化的相对路径、UTF-8 文本片段和每份文件的内容签名。删除会列出路径。片段带原文件 URI 和行号。扫描跳过隐藏文件、隐藏目录、符号链接和二进制内容。调用者在成功处理后保存签名，下次扫描传回。

`model(prompt, items, fragments)` 由调用者提供，返回按整理说明组织的记录列表。抽取会核对摘录是否逐字来自所引片段，再以 `model` 身份追加。同一次抽取的记录共同通过校验后入库。

## 迁移与测试

```sh
python3.12 -m migrate SEED.jsonl SNAPSHOT.json .local/imported.jsonl
python3.12 -m unittest -v
```

迁移器读取旧工作台格式的事件种子和行动快照。可加 `--ledger LEDGER.json` 补读快照引用的待办原记录，或加 `--read-evidence` 回取哈希吻合的来源行。输出路径须为新文件。运行数据建议放在已被 Git 忽略的 `.local/`。单元测试通过注入桩验证模型抽取。

## 补归属

```sh
python3.12 -m backfill --store .local/imported.jsonl --review .local/待确认.md
```

默认预览，指定 `--apply` 后将唯一匹配追加为 `source` 记录。脚本用待办引用的路径匹配项目的 `home`，用人员的机构文字匹配机构的名字或别名，并用项目 `home` 的直接上级目录名匹配客户。现有归属和主人填写的字段优先保留，待确认清单须输出到新文件。

迁移后的日志可通过 `--ledger LEDGER.json` 补读其引用的台账字段。`--context` 接受私有 JSON。可提供 `organizations`（`name`、`path`，文本出处另填 `line`，可选 `quote`、`aliases`、`existing`）、`background_paths`（只供核对的台账引用路径）和 `directory_notes`（按导航目录绝对路径给出核对说明）。

## 登记来源与更新

用 `python3.12 -m cli` 运行下列子命令。自定义 `--store` 和 `--registry` 放在子命令前。登记表默认位于 `$XDG_STATE_HOME/ontology-engine/sources.json`，未设置时使用 `~/.local/state/ontology-engine/sources.json`。每张表绑定一份日志；使用时为同一用户固定这两条路径。

- `source-add ID PATH` 登记已授权文件夹。邮箱、日历等由调用方接入，使用 `source-add ID PATH --type fragments` 登记用户数据目录里的逐字摘录清单。清单是由 `ref` 和 `quote` 对象组成的 JSON 数组，每个 `ref` 在本来源内唯一且刷新时保持稳定。
- `scan` 交出变化、片段、整理说明和提交用的 `token`。增量扫描只交出变化的片段；新增来源时 `recheck` 为 `true`，交出所有来源的片段供调用方重判当前条目。
- `apply TOKEN PROPOSALS.json` 或 `apply TOKEN -` 接收按整理说明生成的 JSON 数组。模型输出强制按 `model` 入库。引擎复查引用及逐字摘录，日志写入成功后保存各来源的内容签名。空数组表示本轮处理完毕且没有追加判断。来源有改动时需要重新扫描。日志已写而游标保存失败时，同一批重试会沿用已追加的记录。
- `review` 列出新识别的主体或项目、不确定项、分歧和拆合建议。用 `review-answer ID confirm --quote '用户原话'` 确认身份，`dismiss` 关闭该条问题，`merge --target ID` 合并同种条目。答案带用户原话，以 `owner` 追加。字段分歧可用 `correct` 纠正，或关闭待核问题。
- `split ID --fields '{"name":"独立项目"}' --move-ref REF --quote '用户原话'` 保留原标识，并用现有 `create` 建立新条目。可重复指定 `--move-ref`，迁移所选引用下的证据。可用 `--keep-fields` 调整原条目字段，用 `--redirect-id` 把指定旧编号指向新条目。单个 `split` 操作记录证据去了哪里，回放时移动证据，整批原子追加。直接按原标识的关系继续指向原条目，需要改归属的用 `correct`。

查询条目带 `certainty`。名称由 `owner` 给出或身份获 owner 确认，判 `certain`。名称由 `source` 给出且条目有逐字证据，或条目拥有至少两个不同原件的逐字证据，也判 `certain`。其余判 `uncertain`。同一文件的多个行号算一个原件，旧记录中缺少原话的引用不计数。这是条目级标记，字段分歧可从 `disagreements` 查看。

Claude Code 和 Codex 共用的流程见 [skill/SKILL.md](../skill/SKILL.md)。

## 提醒：一个出口，一个入口

给守在用户身边、负责挑时机的那一端用（桌面助手、手机等）。账仍然只有这一份日志：用户交代的一件事是一条行动，回应写回同一条。

```sh
python3.12 -m cli reminders
python3.12 -m cli entrust '把样稿发给示例联系人' --due 2030-03-04T15:00:00+08:00 --person '示例联系人' --request-id r-1
python3.12 -m cli respond '把样稿发给示例联系人' done --request-id a-1
```

- `reminders` 列出未结且有时机的行动。时机用通用说法：`time`（到点，`at` 为日期或带时区的时间，`due` 表示已到）、`person`（见到某人，带本体里这个人的全部名字）、`state`（`stalled`，停了多少天）。哪个窗口算「见到」由调用端判断。条目归属到项目时带 `project`（名字和别称），供调用端判断某个群是不是这个项目的群。
- 一件事值不值得主动说出口由中枢判断：值得的条目带 `speak: true`。调用端只主动说带这个标记的，以及用户亲口交代的（`origin` 为 `entrusted`）；其余只在中枢自己的清单里。引擎本身不设这个标记，留给接入方的判断或学习结果。
- `entrust` 当场记下一条新交代，可带时间和人。同一个 `--request-id` 重复提交仍是同一条，离线暂存后补交不会重复。
- `respond` 记录用户的回应：`done`、`dropped`、`another_day`、`later`、`ignored`。前两种同时结束这条行动；`another_day` 可用 `--until` 指定回来的日子。每次回应连同提醒原因（`occasion`）和草稿差别（`draft`，由调用端填）追加在日志里，供以后学习。
- Python 中对应 `reminders.outlet(state, now)`、`reminders.entrust(...)`、`reminders.respond(...)`。
