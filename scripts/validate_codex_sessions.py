#!/usr/bin/env python3
"""唯讀驗證真實 Session；報告只包含 ID、計數與雜湊，不輸出私人對話。"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

from codex_session_reader import ReaderError, build_context, read_raw_session


def validate(session_id, home):
    raw_output = read_raw_session(session_id, home)
    output = build_context(raw_output)
    expected = {}
    turns = []
    features = Counter()
    ordinal = 0
    with Path(raw_output["sources"][-1]["path"]).open("rb") as stream:
        boundary = json.loads(stream.readline())["payload"].get("subagent_history_start_ordinal", 0)
    # 獨立從已標示的來源 byte prefix 重讀，不呼叫 Reader 的解析／normalization 函式。
    for source in raw_output["sources"]:
        with Path(source["path"]).open("rb") as stream:
            prefix = stream.read(source["bytes"])
        assert hashlib.sha256(prefix).hexdigest() == source["sha256"], "來源 snapshot 已改變"
        rows = [json.loads(line) for line in prefix.splitlines()]
        features["segments"] += 1
        for row in rows:
            payload = row["payload"]
            kind = payload.get("type")
            if row["type"] == "compacted":
                features["compactions"] += 1
            if kind == "task_started" and payload["turn_id"] not in turns:
                turns.append(payload["turn_id"])
            if kind == "thread_rolled_back":
                keep = max(len(turns) - payload["num_turns"], 0)
                removed = set(turns[keep:])
                expected = {key: value for key, value in expected.items() if key[0] not in removed}
                turns = turns[:keep]
            if kind == "item_completed" and ordinal >= boundary:
                item = payload["item"]
                typ = item["type"]
                if typ in {"UserMessage", "AgentMessage", "Plan"}:
                    role = "user" if typ == "UserMessage" else "assistant"
                    texts = [item["text"]] if typ == "Plan" else [b["text"] for b in item["content"] if "text" in b]
                    key = (payload["turn_id"], item["id"])
                    first = expected[key][2] if key in expected else ordinal
                    expected[key] = (role, texts, first)
                if typ == "UserMessage":
                    features["user_attachments"] += sum(b["type"] not in {"text"} for b in item["content"])
            ordinal += 1
    actual = []
    positions = []
    cutoff = output["compact"]["latest"]["position"] if output["context_basis"] == "latest_compact" else -1
    for index, entry in enumerate(output["conversation"], 1):
        assert entry["sequence"] == index, "輸出 sequence 不連續"
        assert entry["role"] in {"user", "assistant"}, "角色不合法"
        assert entry["kind"] not in {"reasoning", "token_count", "task_started", "function_call", "tool_result"}, "協定雜訊混入正文"
        positions.append(entry["position"])
        if entry["kind"] in {"message", "plan"}:
            actual.append((entry["role"], [b["text"] for b in entry["content"] if b["type"] == "text"], entry["position"]))
        features[entry["kind"]] += 1
    assert positions == sorted(positions), "輸出位置不是歷史順序"
    expected_list = sorted((value for value in expected.values() if value[2] > cutoff), key=lambda x: x[2])
    assert actual == expected_list, "User / Assistant / Plan 的原文、角色或順序不一致"
    assert output["state"]["status"] in {"completed", "failed", "interrupted", "incomplete", "unknown"}
    return {"session_id": session_id, "status": "passed", "messages_checked": len(expected),
            "entries": len(output["conversation"]), "features": dict(features),
            "context_basis": output["context_basis"], "session_state": output["state"]["status"],
            "compact_count": output["compact"]["count"], "evidence_count": len(output["execution_evidence"]),
            "artifact_count": len(output["artifacts"]),
            "filtered_records": sum(raw_output["filtered"].values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_ids", nargs="+")
    parser.add_argument("--codex-home", type=Path, help="覆寫 CODEX_HOME / ~/.codex")
    args = parser.parse_args()
    results = []
    for session_id in args.session_ids:
        try:
            results.append(validate(session_id, args.codex_home))
        except (ReaderError, OSError, AssertionError) as exc:
            results.append({"session_id": session_id, "status": "failed", "error": str(exc),
                            "code": exc.code if isinstance(exc, ReaderError) else "validation_failed"})
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return int(any(result["status"] != "passed" for result in results))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
