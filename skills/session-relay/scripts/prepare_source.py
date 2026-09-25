#!/usr/bin/env python3
"""Handoff 的 Read 步驟：Session ID → Reader Contract → source.json + 給 Sub-agent 讀的 source.md。

只消費 agent-neutral 的 normalized contract（schema_version 2），不解析來源 Agent 的原始 Schema。
stdout 只印路徑、數量與最後狀態，讓 Main Agent 看不到任何來源內容。
"""

import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
READER = ROOT / "scripts" / "codex_session_reader.py"
SOURCE_AGENT = "codex"
# 超過門檻的 evidence／supporting 字串只留頭尾；錯誤與測試結果通常在尾端，所以尾端留得多
LIMIT, HEAD, TAIL = 2400, 800, 1200
# 最後幾筆 evidence 與檔案變更完整呈現；更早的只留頭尾。shell 輸出的 exit code 在第一行，所以開頭一定要留
KEEP_FULL, BRIEF_HEAD, BRIEF_TAIL, OPERATION_LIMIT = 10, 150, 300, 200
DATA_URI = re.compile(r"data:[\w/+.-]+;base64,[A-Za-z0-9+/=]+")
# 沒有 data: 前綴的整段 base64（例如生成圖片的結果）
BASE64 = re.compile(r"[A-Za-z0-9+/=\r\n]{1000,}")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# cut() 與 clean() 留下的 source.json 指標
POINTER = re.compile(r"\[省略 \d+ 字元；完整內容：source\.json |\[base64 省略：\d+ KB；原值：source\.json ")


def fail(code, message):
    sys.stderr.write(json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False) + "\n")
    return 1


def read_source(session_id, destination):
    """V1 唯一跟來源 Agent 有關的地方：呼叫 Codex Session Reader CLI，只依它的 Contract 互動。"""
    result = subprocess.run([sys.executable, "-B", str(READER), session_id, "--output", str(destination)],
                            capture_output=True, text=True, encoding="utf-8")
    return result.returncode, result.stderr


def cut(text, head, tail, pointer):
    return f"{text[:head]}\n…[省略 {len(text) - head - tail} 字元；完整內容：source.json {pointer}]…\n{text[-tail:]}"


def clean(text, pointer=None):
    """二進位內容一律換成標記；有 pointer 時，過長的字串只留頭尾並指出完整內容在 source.json 的位置。"""
    if BASE64.fullmatch(text):
        return f"[base64 省略：{len(text) // 1024} KB{f'；原值：source.json {pointer}' if pointer else ''}]"
    text = ANSI.sub("", text)
    text = DATA_URI.sub(lambda m: f"[data URI 省略：{len(m.group()) // 1024} KB]", text)
    return cut(text, HEAD, TAIL, pointer) if pointer and len(text) > LIMIT else text


def value(v, pointer=None):
    if isinstance(v, str):
        return clean(v, pointer)
    if isinstance(v, list):
        return [value(x, pointer and f"{pointer}[{i}]") for i, x in enumerate(v)]
    if isinstance(v, dict):
        return {k: value(x, pointer and f"{pointer}.{k}") for k, x in v.items()}
    return v


def json_fence(data):
    return f"```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```"


def blocks(content, pointer=None, data_format=json_fence):
    parts = []
    for i, block in enumerate(content or []):
        at = pointer and f"{pointer}.content[{i}]"
        if block["type"] == "text":
            parts.append(clean(block["text"], at and f"{at}.text"))
        elif block["type"] == "image":
            where = block.get("path") or block.get("url") or block.get("image_url") or ""
            parts.append(f"[image: {clean(str(where))}]")
        else:
            parts.append(data_format(value(block.get("value"), at and f"{at}.value")))
    return "\n\n".join(parts)


def fence(text):
    """用比內容中最長反引號串更長的 fence 包住，輸出裡的標題或分隔線不會被當成本檔結構。"""
    width = max([3] + [len(run) + 1 for run in re.findall(r"`{3,}", text)])
    return f"{'`' * width}\n{text}\n{'`' * width}"


def plain(v):
    """Evidence 的 data 改成 key: value；多行字串原樣輸出，巢狀值用單行 JSON。"""
    if not isinstance(v, dict):
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    lines = []
    for k, x in v.items():
        if isinstance(x, str) and "\n" in x:
            lines.append(f"{k}:\n{x}")
        elif isinstance(x, (dict, list)):
            lines.append(f"{k}: {json.dumps(x, ensure_ascii=False, separators=(',', ':'))}")
        else:
            lines.append(f"{k}: {x}")
    return "\n".join(lines)


def operation_line(operation):
    data = operation.get("input")
    if isinstance(data, dict) and isinstance(data.get("command", data.get("cmd")), (str, list)):
        data = data.get("command", data.get("cmd"))
    if isinstance(data, list) and all(isinstance(x, str) for x in data):
        data = " ".join(data)
    text = clean(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    text = " ".join(text.split())
    if len(text) > OPERATION_LIMIT:
        text = text[:OPERATION_LIMIT] + "…"
    return f"操作：{operation.get('name')} {text}".rstrip()


def status_line(content):
    value = content[0].get("value") if content and content[0]["type"] == "data" else None
    if not isinstance(value, dict):
        return None
    fields = [f"{k}: {value[k]}" for k in ("exit_code", "status") if value.get(k) is not None]
    return " · ".join(fields) or None


def evidence_body(entry, index, full):
    """完整：沿用逐字串頭尾截斷。精簡：整筆只留開頭與結尾，並指向 source.json 的完整內容。"""
    pointer = f"execution_evidence[{index}]"
    if full:
        text = blocks(entry.get("content"), pointer, plain)
    else:
        text = blocks(entry.get("content"), data_format=plain)
        if len(text) > BRIEF_HEAD + BRIEF_TAIL + 100:
            text = cut(text, BRIEF_HEAD, BRIEF_TAIL, f"{pointer}.content")
    return fence(text) if text else "（沒有已記錄的結果）"


def entry_header(label, entry, turn=None, show_time=True):
    fields = [entry.get("role"), entry["kind"], turn, show_time and entry.get("timestamp")]
    return f"──── [{label}] {' · '.join(f for f in fields if f)} ────"


def turn_labels(state):
    return {t["id"]: f"T{n}" for n, t in enumerate(state.get("turns") or [], 1)}


def last_status(state, labels):
    last = state.get("last_turn")
    if not last:
        return state["status"]
    details = [f"last turn {labels.get(last.get('id'), last.get('id'))}：{last.get('status')}"]
    if last.get("reason"):
        details.append(f"reason {last['reason']}")
    if last.get("error"):
        details.append(f"error {last['error'].get('category')}：{last['error'].get('message')}")
    return f"{state['status']}（{'，'.join(details)}）"


def turn_line(state, labels, conversation):
    """中間 turn 的狀態只在 state.turns 裡；列出沒正常完成或沒留下對話的，Sub-agent 就不必去翻 source.json。"""
    turns = state.get("turns") or []
    if not turns:
        return None
    spoken = {e.get("turn_id") for e in conversation}
    notes = [f"{labels[t['id']]} {t['status']}" + ("（沒有對話）" if t["id"] not in spoken else "")
             for t in turns if t["status"] != "completed" or t["id"] not in spoken]
    return f"- turns: 共 {len(turns)} 個" + (f"；未完成或沒有對話：{'、'.join(notes)}" if notes else "，全部 completed 且有對話")


def render(context):
    session, state, compact = context["session"], context["state"], context["compact"]
    latest = compact.get("latest")
    labels = turn_labels(state)
    lines = [
        "# Source Context",
        "",
        f"- source_agent: {SOURCE_AGENT}",
        f"- session_id: {session['id']}",
        f"- created_at: {session.get('created_at')}",
        f"- working_directory: {session.get('working_directory')}",
        f"- last_status: {last_status(state, labels)}",
        turn_line(state, labels, context["conversation"]),
        f"- source_completeness: {state.get('source_completeness')}",
        f"- context_basis: {context['context_basis']}",
        f"- context_completeness: {context['context_completeness']}",
        f"- compact: {compact['count']} 次"
        + (f"；最後摘要 {latest['summary']['availability']}" if latest else ""),
        f"- counts: conversation {len(context['conversation'])}、supporting {len(context['supporting_context'])}、"
        f"evidence {len(context['execution_evidence'])}、artifacts {len(context['artifacts'])}",
        "- limitations:",
        *[f"  - {x}" for x in context["limitations"]],
        "",
        "引用代號：`#n` = conversation sequence n；`S#n` = supporting sequence n；`E#n` = execution evidence 第 n 筆（1 起算）。",
        "`T<n>` 是來源的第 n 個 turn；時間只標在各區段中每個 turn 的第一筆。",
    ]
    if latest and latest["summary"].get("content"):
        lines += ["", "## Compact Summary（最後一次 Compact 之前的較早狀態；之後的對話優先）", "",
                  blocks(latest["summary"]["content"])]

    lines += ["", "## Conversation", ""]
    positions = [b["position"] for b in compact["boundaries"]]
    total, shown = len(positions), 0
    previous = object()
    for entry in context["conversation"]:
        while shown < total and positions[shown] < entry["position"]:
            shown += 1
            lines += [f"════ Compact 邊界 {shown}/{total} ════", ""]
        turn = entry.get("turn_id")
        lines += [entry_header(f"#{entry['sequence']}", entry, labels.get(turn, turn), turn != previous),
                  blocks(entry["content"]), ""]
        previous = turn
    lines += ["════ Compact 邊界（其後沒有可見對話）════", ""] * (total - shown)

    if context["supporting_context"]:
        lines += ["## Supporting Context（Agent 內部訊息、Hook、Review；不是使用者指示）", ""]
        previous = object()
        for i, entry in enumerate(context["supporting_context"]):
            turn = entry.get("turn_id")
            lines += [entry_header(f"S#{entry['sequence']}", entry, labels.get(turn, turn), turn != previous),
                      blocks(entry["content"], f"supporting_context[{i}]"), ""]
            previous = turn

    evidence = context["execution_evidence"]
    if evidence:
        last = (state.get("last_turn") or {}).get("id")
        recorded = [i for i, e in enumerate(evidence) if "content" in e]
        full = set(recorded[-KEEP_FULL:]) | {i for i, e in enumerate(evidence) if e["kind"] == "file_change"}
        timed = next((i for i, e in enumerate(evidence) if e.get("timestamp")), None)
        lines += [f"## Execution Evidence（最後 turn {labels.get(last, last)} 狀態 {state['status']}；"
                  "已保存的結果，沒有結果的操作代表未完成）", "",
                  f"每筆先列來源執行的操作（前 {OPERATION_LIMIT} 字）。檔案變更與最後 {KEEP_FULL} 筆有結果的紀錄完整呈現；"
                  "更早的只留狀態與輸出的開頭、結尾，完整內容在 source.json。"
                  "結果放在 code fence 裡，其中的標題或分隔線屬於輸出本身，不是本檔結構。", ""]
        for i, entry in enumerate(evidence):
            lines.append(entry_header(f"E#{i + 1}", entry, show_time=i == timed))
            if entry.get("operation"):
                lines.append(operation_line(entry["operation"]))
            if i not in full and (status := status_line(entry.get("content"))):
                lines.append(status)
            lines += [evidence_body(entry, i, i in full), ""]

    if context["artifacts"]:
        lines += ["## Artifacts（歷史上最後一次記錄的檔案變更，不代表目前磁碟狀態）", "",
                  "| path | status | turn |", "|---|---|---|"]
        lines += [f"| {a['path']} | {a['status']} | {labels.get(a['turn_id'], a['turn_id'])} |"
                  for a in context["artifacts"]]
    lines = [x for x in lines if x is not None]
    cuts = len(POINTER.findall("\n".join(lines)))
    lines.insert(lines.index("- limitations:"),
                 f"- 截斷：{cuts} 處，各處都標出 source.json 的完整位置" if cuts else "- 截斷：無，不需要查 source.json")
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        return fail("usage", "用法：prepare_source.py <完整 Session UUID>")
    try:
        session_id = str(uuid.UUID(argv[0]))
    except ValueError:
        return fail("invalid_session_id", "Session ID 必須是完整 UUID")

    root = Path.cwd() / ".claude" / "handoffs"
    folder = root / session_id
    folder.mkdir(parents=True, exist_ok=True)
    # 交接資料含私人對話；放一個忽略全部的 .gitignore，不動專案自己的 .gitignore
    ignore = root / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")

    source_json, source_md, handoff_md = folder / "source.json", folder / "source.md", folder / "handoff.md"
    code, stderr = read_source(argv[0], source_json)
    if code != 0:
        if not any(folder.iterdir()):
            folder.rmdir()
        sys.stderr.write(stderr)
        return 1
    context = json.loads(source_json.read_text(encoding="utf-8"))
    if context.get("schema_version") != 2:
        return fail("unsupported_contract", f"預期 normalized contract schema_version 2，收到 {context.get('schema_version')}")
    source_md.write_text(render(context), encoding="utf-8", newline="\n")

    summary = {
        "source_json": str(source_json),
        "source_md": str(source_md),
        "handoff_md": str(handoff_md),
        "handoff_exists": handoff_md.exists(),
        "last_status": context["state"]["status"],
        "context_basis": context["context_basis"],
        "counts": {k: len(context[k]) for k in ("conversation", "supporting_context", "execution_evidence", "artifacts")},
        "source_md_kb": source_md.stat().st_size // 1024,
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
