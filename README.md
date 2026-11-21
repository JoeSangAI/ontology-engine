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

- 每个字段以 `owner > source > model` 为准。同级取较晚的记录，同一时间按日志顺序。低优先级的后续异议可在 `disagreements` 查到。
- 来源追加 `aliases` 和 `misrecognitions` 时合并列表。主人的纠正替换所写的整个列表，包括空列表。
- 以名字分配标识，重名时加后缀，如 `示例项目 (2)`。创建同名对象时可用 `allocate` 生成标识。纠正可用带后缀的标识选中条目。
- `redirect` 记录旧名或旧编号的去向。用 `correct --rename` 写入一条改名记录。迁移也保留合并记录和待核线索。
- 在 `project`、`people`、`organization`、`client` 等字段写目标标识即可连接。每件新行动都需填写 `project` 和 `people`。未知时分别用 `"未归属"` 和 `["未归属"]`。
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
