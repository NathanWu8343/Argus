#!/usr/bin/env python3
"""本機 Codex Session ID → 無模型、無網路的 agent-neutral Session Context。"""

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import uuid


class ReaderError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, message, code="unsupported_schema"):
    if not condition:
        raise ReaderError(code, message)


def read_json(raw, location):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ReaderError("invalid_json", f"{location}：JSON 不完整或損壞") from exc
    require(isinstance(value, dict), f"{location}：預期 JSON object")
    return value


def canonical_id(value):
    try:
        result = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ReaderError("invalid_session_id", "Session ID 必須是完整 UUID") from exc
    require(value.lower() == result, "Session ID 必須是含連字號的完整 UUID", "invalid_session_id")
    return result


def codex_home(home=None):
    return Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()


def check_meta(record, path):
    require(record.get("type") == "session_meta", f"{path}：第一行不是 session_meta")
    meta = record.get("payload")
    require(isinstance(meta, dict) and isinstance(meta.get("id"), str), f"{path}：缺少 Session ID")
    require(meta.get("session_id", meta["id"]) in {meta["id"], meta.get("parent_thread_id")},
            f"{path}：session_id 不是本身或明確的父 Session")
    return meta


class Store:
    def __init__(self, home):
        self.home = codex_home(home)
        self.files = None
        self.sources = []
        self.partial_tail_bytes = 0

    def candidates(self, identifier):
        if self.files is None:
            self.files = [p for folder in ("sessions", "archived_sessions")
                          for p in (self.home / folder).rglob("*.jsonl")]
        return [p for p in self.files if identifier in p.name]

    def metadata(self, path):
        with path.open("rb") as stream:
            return check_meta(read_json(stream.readline(), f"{path}:1"), path)

    def indexed_path(self, session_id):
        databases = sorted(self.home.glob("state_*.sqlite"))
        if not databases:
            return None
        require(len(databases) == 1 and databases[0].name == "state_5.sqlite",
                "未確認的 state 資料庫版本；請更新 Reader")
        try:
            with closing(sqlite3.connect(databases[0].as_uri() + "?mode=ro", uri=True)) as connection:
                row = connection.execute("SELECT rollout_path FROM threads WHERE id = ?", (session_id,)).fetchone()
        except sqlite3.Error as exc:
            raise ReaderError("index_error", f"無法唯讀查詢 Session 索引：{exc}") from exc
        return Path(row[0]) if row else None

    def locate(self, session_id):
        """回傳 (路徑, session_meta)。"""
        indexed = self.indexed_path(session_id)
        if indexed is not None:
            require(indexed.is_file(), f"索引指向不存在的紀錄：{indexed}", "missing_history")
            meta = self.metadata(indexed)
            require(meta["id"] == session_id, "索引與檔案 Session ID 不一致", "identity_mismatch")
            return indexed, meta
        matches = [(p, m) for p in self.candidates(session_id) if (m := self.metadata(p))["id"] == session_id]
        require(matches, f"找不到 Session {session_id}", "session_not_found")
        require(len(matches) == 1, "有多個候選歷史段，缺少有效索引，不能推測目前分支", "ambiguous_session")
        return matches[0]

    def segment(self, identifier, exclude):
        canonical_id(identifier)
        matches = [p for p in self.candidates(identifier)
                   if p.stem.endswith(identifier) and p.resolve() != exclude.resolve()]
        require(matches, f"缺少被繼承的歷史段 {identifier}", "missing_history")
        require(len(matches) == 1, f"歷史段 {identifier} 有多個候選", "ambiguous_session")
        # 段 ID 來自 history_base；檔名只用來解析該實體段，不用來認定 Session。
        self.metadata(matches[0])
        return matches[0]

    def load(self, path, byte_limit=None, stack=()):
        identity = str(path.resolve())
        require(identity not in stack and len(stack) < 100, "歷史鏈循環或超過 100 段", "invalid_history")
        with path.open("rb") as stream:
            initial = os.fstat(stream.fileno())
            size = initial.st_size if byte_limit is None else byte_limit
            require(isinstance(size, int) and 0 < size <= initial.st_size,
                    f"{path}：歷史 byte 邊界超出檔案", "invalid_history")
            raw = stream.read(size)
        require(len(raw) == size, f"{path}：讀取期間檔案被截短", "source_changed")
        if not raw.endswith(b"\n"):
            require(byte_limit is None, f"{path}：繼承邊界不是換行", "incomplete_record")
            boundary = raw.rfind(b"\n") + 1
            require(boundary > 0, f"{path}：尚無完整紀錄", "incomplete_record")
            self.partial_tail_bytes = len(raw) - boundary
            raw = raw[:boundary]
            size = boundary
        # 遞迴讀父段前先釋放原始 bytes，整條歷史鏈不會同時留在記憶體
        digest = hashlib.sha256(raw).hexdigest()
        lines = raw.splitlines(keepends=True)
        del raw
        records = []
        for line_number, line in enumerate(lines, 1):
            record = read_json(line, f"{path}:{line_number}")
            require(isinstance(record.get("payload"), dict) and isinstance(record.get("type"), str),
                    f"{path}:{line_number}：缺少 type/payload")
            records.append(record)
        del lines
        meta = check_meta(records[0], path)
        require(meta.get("history_mode") == "paginated", f"{path}：尚未支援非 paginated 紀錄")
        inherited = []
        base = meta.get("history_base")
        if base is not None:
            require(isinstance(base, dict) and isinstance(base.get("thread_id"), str), "history_base 格式不符")
            require(type(base.get("end_ordinal_exclusive")) is int and type(base.get("end_byte_offset")) is int,
                    "history_base 缺少明確的 ordinal/byte 邊界")
            parent = self.segment(base["thread_id"], path)
            inherited = self.load(parent, base["end_byte_offset"], (*stack, identity))
            require(len(inherited) == base["end_ordinal_exclusive"],
                    f"{path}：歷史 ordinal 與 byte 邊界不一致", "invalid_history")
        self.sources.append({"path": str(path), "session_id": meta["id"], "bytes": size,
                             "sha256": digest, "cli_version": meta.get("cli_version")})
        for n, record in enumerate(records, 1):
            inherited.append({"record": record, "source": {"path": str(path), "line": n,
                              "ordinal": len(inherited)}, "owner": meta["id"]})
        return inherited

    def verify_snapshot(self):
        for source in self.sources:
            with Path(source["path"]).open("rb") as stream:
                raw = stream.read(source["bytes"])
            require(hashlib.sha256(raw).hexdigest() == source["sha256"],
                    "讀取期間既有歷史被改寫，請重試", "source_changed")


TEXT_TYPES = {"text", "Text", "input_text", "output_text", "inputText"}
IMAGE_TYPES = {"image", "Image", "input_image", "output_image", "inputImage"}
LOCAL_IMAGE_TYPES = {"localImage", "local_image"}
BLOCK_TYPES = {"audio", "input_audio", "output_audio", "video", "file", "input_file",
               "resource", "resource_link", "refusal"}
QUESTION_TOOLS = {"request_user_input", "request_user_input_async", "send_user_message_async"}
NOISE_RECORDS = {"session_meta", "turn_context", "world_state", "token_usage_record", "compacted",
                 "inter_agent_communication_metadata"}
NOISE_EVENTS = {"task_started", "task_complete", "turn_aborted", "token_count", "thread_settings_applied",
                "realtime_session_started", "realtime_session_closed"}
NOISE_ITEMS = {"Reasoning", "ContextCompaction", "SubAgentActivity"}
NOISE_RESPONSES = {"message", "reasoning", "compaction", "tool_search_call", "tool_search_output", "web_search_call"}
# 內部 (role, kind) → Supporting Context 的 kind
SUPPORTING_KINDS = {("context", "agent_message"): "delegated_message", ("context", "hook"): "context_note",
                    ("context", "review"): "review_context", ("assistant", "review"): "review_result"}


def blocks(value):
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    if isinstance(value, dict):
        return [{"type": "data", "value": value}]
    require(isinstance(value, list), "content/output 必須是字串、object 或 array")
    result = []
    for block in value:
        require(isinstance(block, dict), "content block 必須是 object")
        kind = block.get("type")
        if kind in TEXT_TYPES:
            require(isinstance(block.get("text"), str), "text block 缺少文字")
            item = {"type": "text", "text": block["text"]}
            for key in ("text_elements", "textElements", "annotations"):
                if block.get(key):
                    item["annotations"] = block[key]
            result.append(item)
        elif kind in IMAGE_TYPES:
            # 不下載或重新編碼圖片；保留既有 data URI、URL 或 MCP base64。
            result.append({**block, "type": "image"})
        elif kind in LOCAL_IMAGE_TYPES:
            require(isinstance(block.get("path"), str), "localImage 缺少 path")
            result.append({"type": "image", "path": block["path"]})
        elif kind in BLOCK_TYPES:
            result.append({"type": "data", "value": block})
        else:
            raise ReaderError("unsupported_schema", f"未知 content block：{kind}")
    return result


def selected(item, keys):
    return {k: item[k] for k in keys if k in item and item[k] is not None}


def match_messages(records, boundary):
    """只以 ID、turn 與精確文字配對鏡像；同時補回 UI 只存路徑的圖片 bytes。"""
    visible, raw_messages = [], []
    turn = None
    for row in records:
        record, payload = row["record"], row["record"]["payload"]
        if record["type"] == "turn_context" or payload.get("type") == "task_started":
            turn = payload.get("turn_id", turn)
        if row["source"]["ordinal"] < boundary:
            continue
        if payload.get("type") == "item_completed":
            item = payload.get("item", {})
            if item.get("type") in {"UserMessage", "AgentMessage", "Plan"}:
                texts = [item["text"]] if item["type"] == "Plan" else [b["text"] for b in item["content"] if "text" in b]
                visible.append((payload.get("turn_id", turn), "user" if item["type"] == "UserMessage" else "assistant", item, texts))
        if record["type"] == "response_item" and payload.get("type") == "message":
            metadata = payload.get("internal_chat_message_metadata_passthrough") or {}
            kinds = metadata.get("content_item_kinds", [])
            role = payload.get("role")
            required = (role == "assistant" and payload.get("phase") in {None, "commentary", "final", "final_answer"}
                        or role == "user" and any(k in {"user.text", "user.image"} for k in kinds))
            if required or role == "user" and not kinds:
                raw_messages.append((metadata.get("turn_id", turn), payload, required))
    images = {}
    for turn, payload, required in raw_messages:
        content = payload.get("content", [])
        texts = [b["text"] for b in content if "text" in b]
        joined = "".join(texts)
        stored_images = [b for b in content if b.get("type") in IMAGE_TYPES]
        match = None
        for index, (other_turn, role, item, visible_texts) in enumerate(visible):
            if turn != other_turn or payload["role"] != role:
                continue
            same_id = payload.get("id") is not None and payload["id"] == item["id"]
            plan = item["type"] == "Plan" and "<proposed_plan>" in joined and "".join(visible_texts).strip() in joined
            user_match = role == "user" and all(t in texts for t in visible_texts) and bool(visible_texts or stored_images)
            if same_id or texts == visible_texts or user_match or plan:
                match = index
                break
        require(match is not None or not required, "已儲存的對話訊息缺少相符 UI 事件；請於寫入完成後重試", "missing_history")
        if match is not None:
            _, role, item, _ = visible.pop(match)
            if role == "user":
                visible_images = [b for b in item["content"] if b.get("type") in IMAGE_TYPES | LOCAL_IMAGE_TYPES]
                if stored_images and len(stored_images) == len(visible_images):
                    images[(turn, item["id"])] = stored_images
    return images


def normalize_item(item):
    kind = item.get("type")
    if kind in NOISE_ITEMS:
        return None
    if kind == "UserMessage":
        return "user", "message", blocks(item.get("content"))
    if kind == "AgentMessage":
        return "assistant", "message", blocks(item.get("content"))
    if kind == "Plan":
        return "assistant", "plan", blocks(item.get("text"))
    if kind == "CommandExecution":
        output = selected(item, ("command", "cwd", "stdout", "stderr", "exit_code", "status"))
        if not output.get("stdout") and not output.get("stderr"):
            output.update(selected(item, ("aggregated_output",)))
        return "tool", "tool_result", blocks(output)
    if kind == "McpToolCall":
        return "tool", "tool_result", blocks(selected(item, ("result", "error", "status")))
    if kind == "DynamicToolCall":
        return "tool", "tool_result", blocks(item.get("content_items", []))
    if kind == "FunctionCallOutput":
        return "tool", "tool_result", blocks(item.get("output"))
    if kind == "FileChange":
        return "tool", "file_change", blocks(selected(item, ("changes", "status", "stdout", "stderr")))
    if kind == "ImageView":
        return "tool", "attachment", blocks([{"type": "localImage", "path": item.get("path")}])
    if kind == "CollabAgentToolCall":
        return "tool", "tool_result", blocks(selected(item, ("agents_states", "status")))
    if kind == "EnteredReviewMode":
        return "context", "review", blocks(selected(item, ("target", "user_facing_hint")))
    if kind == "ExitedReviewMode":
        return "assistant", "review", blocks(item.get("review_output", {}))
    if kind == "HookPrompt":
        return "context", "hook", blocks(selected(item, ("fragments",)))
    if kind in {"WebSearch", "ImageGeneration"}:
        return "tool", "tool_result", blocks(selected(item, ("results", "result", "saved_path", "savedPath", "failure", "status")))
    if kind == "Extension":
        extension = item.get("kind")
        if extension == "clock.sleep":
            return None
        if extension in {"web.search", "image_gen.generation"}:
            return "tool", "tool_result", blocks(selected(item, ("results", "result", "savedPath", "failure", "status")))
        raise ReaderError("unsupported_schema", f"未知 Extension：{extension}")
    raise ReaderError("unsupported_schema", f"未知 item_completed item：{kind}")


def normalize(records, start_ordinal=0):
    stored_images = match_messages(records, start_ordinal)
    entries = []
    slots = {}
    calls = {}
    answered = set()
    filtered = Counter()
    current_turn = None
    known_turns = []

    def emit(row, role, kind, content, item_id=None, turn=None, **extra):
        if row["source"]["ordinal"] < start_ordinal:
            return
        entry = {"role": role, "kind": kind, "turn_id": turn or current_turn,
                 "content": content, "timestamp": row["record"].get("timestamp"),
                 "source": row["source"], **extra}
        key = (role, kind, entry["turn_id"], item_id) if item_id else None
        if item_id:
            entry["source"] = {**entry["source"], "item_id": item_id}
        if key in slots:
            index = slots[key]
            first = entries[index]
            entry["source"]["first_ordinal"] = first["source"].get("first_ordinal", first["source"]["ordinal"])
            entries[index] = entry
        else:
            if key:
                slots[key] = len(entries)
            entries.append(entry)

    for row in records:
        record = row["record"]
        category, payload = record["type"], record["payload"]
        typ = payload.get("type")
        if category == "turn_context":
            current_turn = payload.get("turn_id", current_turn)
        if category in NOISE_RECORDS:
            filtered[category] += 1
            continue
        if category == "realtime_item":
            if typ in {"realtime_session_started", "realtime_session_closed"}:
                filtered[typ] += 1
            elif typ == "transcript_segment":
                require(payload.get("role") in {"user", "assistant"}, "語音逐字稿角色不合法")
                emit(row, payload["role"], "transcript", blocks(payload.get("text")), payload.get("id"),
                     "realtime:" + payload["realtime_session_id"])
            else:
                raise ReaderError("unsupported_schema", f"未知 realtime_item：{typ}")
            continue
        if category == "event_msg":
            if typ == "task_started":
                current_turn = payload.get("turn_id")
                if current_turn not in known_turns:
                    known_turns.append(current_turn)
            if typ in NOISE_EVENTS:
                filtered[typ] += 1
                continue
            if typ == "thread_rolled_back":
                count = payload.get("num_turns")
                require(type(count) is int and 0 <= count <= len(known_turns), "rollback 邊界不合法")
                keep = len(known_turns) - count
                removed = set(known_turns[keep:])
                entries[:] = [e for e in entries if e["turn_id"] not in removed]
                del known_turns[keep:]
                slots.clear()
                slots.update(((e["role"], e["kind"], e["turn_id"], e["source"]["item_id"]), i)
                             for i, e in enumerate(entries) if "item_id" in e["source"])
                current_turn = known_turns[-1] if known_turns else None
                continue
            if typ == "item_completed":
                require(payload.get("thread_id", row["owner"]) == row["owner"],
                        "item_completed 混入其他 Session", "identity_mismatch")
                item = payload.get("item")
                require(isinstance(item, dict) and isinstance(item.get("id"), str), "item_completed 缺少 item/id")
                result = normalize_item(item)
                if result:
                    role, kind, content = result
                    if role == "user":
                        images = stored_images.get((payload.get("turn_id", current_turn), item["id"]), [])
                        for block, raw_image in zip((b for b in content if b["type"] == "image"), images):
                            if "image_url" in raw_image:
                                block["url"] = raw_image["image_url"]
                    extra = {}
                    if item.get("phase") is not None:
                        extra["phase"] = item["phase"]
                    if role == "tool":
                        extra["tool"] = item.get("tool", item.get("name", item["type"]))
                    emit(row, role, kind, content, item["id"], payload.get("turn_id"), **extra)
                else:
                    filtered[item["type"]] += 1
                continue
            raise ReaderError("unsupported_schema", f"未知 event_msg：{typ}")
        if category == "response_item":
            if typ == "agent_message":
                content = payload.get("content", [])
                require(isinstance(content, list), "agent_message.content 格式不符")
                if any(b.get("type") == "encrypted_content" for b in content):
                    filtered["encrypted_agent_message"] += 1
                    continue
                emit(row, "context", "agent_message", blocks(content), payload.get("id"),
                     author=payload.get("author"), recipient=payload.get("recipient"))
                continue
            if typ in NOISE_RESPONSES:
                filtered[f"response_item.{typ}"] += 1
                continue
            if typ in {"function_call", "custom_tool_call"}:
                call_id = payload.get("call_id")
                require(isinstance(call_id, str), "Tool call 缺少 call_id")
                turn = (payload.get("internal_chat_message_metadata_passthrough") or {}).get("turn_id", current_turn)
                calls[call_id] = {"payload": payload, "turn": turn, "position": row["source"]["ordinal"],
                                  "timestamp": record.get("timestamp")}
                if payload.get("name") in QUESTION_TOOLS:
                    args = payload.get("arguments", payload.get("input"))
                    if isinstance(args, str):
                        args = read_json(args, "互動工具 arguments")
                    emit(row, "assistant", "interaction", blocks(args), "question:" + call_id, turn,
                         tool=payload["name"])
                filtered[typ] += 1
                continue
            if typ in {"function_call_output", "custom_tool_call_output"}:
                call_id = payload.get("call_id")
                require(isinstance(call_id, str), "Tool result 缺少 call_id")
                answered.add(call_id)
                call = calls.get(call_id, {})
                name = call.get("payload", {}).get("name", "unknown")
                # Code mode 可在 metadata 提供實際執行的互動工具；只保留已知互動的問題。
                metadata = payload.get("internal_chat_message_metadata_passthrough") or {}
                for number, nested in enumerate(metadata.get("executed_tool_calls", [])):
                    if nested.get("name") in QUESTION_TOOLS and name not in QUESTION_TOOLS:
                        emit(row, "assistant", "interaction", blocks(nested["arguments"]),
                             f"question:{call_id}:{number}", metadata.get("turn_id", call.get("turn")), tool=nested["name"])
                emit(row, "tool", "tool_result", blocks(payload.get("output")), call_id,
                     metadata.get("turn_id", call.get("turn")), tool=name)
                continue
            raise ReaderError("unsupported_schema", f"未知 response_item：{typ}")
        raise ReaderError("unsupported_schema", f"未知 rollout record：{category}")
    for sequence, entry in enumerate(entries, 1):
        entry["sequence"] = sequence
    for call_id, call in calls.items():
        call["answered"] = call_id in answered
    return entries, dict(sorted(filtered.items())), calls


def read_raw_session(session_id, home=None, allow_summary_only=False):
    session_id = canonical_id(session_id)
    store = Store(home)
    path, meta = store.locate(session_id)
    records = store.load(path)
    require(store.sources[-1]["session_id"] == session_id, "讀取期間來源 Session ID 改變", "source_changed")
    boundary = meta.get("subagent_history_start_ordinal", 0)
    require(type(boundary) is int and 0 <= boundary <= len(records), "子 Agent 歷史邊界不合法")
    try:
        conversation, filtered, calls = normalize(records, boundary)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ReaderError("unsupported_schema", "Session 欄位缺漏或型別不符，無法可靠還原") from exc
    visible_message_seen = boundary > 0
    summaries = [row["record"]["payload"] for row in records[boundary:]
                 if row["record"]["type"] == "compacted"]
    latest_summary_available = bool(summaries and compact_summary(summaries[-1])[0] == "available")
    for row in records[boundary:]:
        payload = row["record"]["payload"]
        if payload.get("type") == "item_completed" and payload.get("item", {}).get("type") in {"UserMessage", "AgentMessage"}:
            visible_message_seen = True
        if row["record"]["type"] == "compacted":
            # 條件只會由假變真，檢查第一個 Compact 就夠
            require(visible_message_seen or allow_summary_only and latest_summary_available,
                    "壓縮前缺少原始對話，且最後摘要不可讀", "missing_history")
            break
    store.verify_snapshot()
    after = store.indexed_path(session_id)
    require(after is None or after.resolve() == path.resolve(), "讀取期間目前分支改變，請重試", "source_changed")
    return {"session_id": session_id, "conversation": conversation, "sources": store.sources,
            "filtered": filtered, "partial_tail_bytes": store.partial_tail_bytes,
            "records": records, "meta": meta, "boundary": boundary, "calls": calls}


def neutral_entry(entry, kind=None, role=None, content=None):
    """移除來源協定欄位，但保留內容及在原始紀錄中的位置。"""
    source = entry["source"]
    return {"sequence": 0, "position": source.get("first_ordinal", source["ordinal"]),
            "turn_id": entry["turn_id"], "timestamp": entry["timestamp"],
            "role": role or entry["role"], "kind": kind or entry["kind"],
            "content": entry["content"] if content is None else content}


def compact_summary(payload):
    message = payload.get("message")
    require(message is None or isinstance(message, str), "Compact message 格式不符")
    history = payload.get("replacement_history", [])
    require(isinstance(history, list) and all(isinstance(part, dict) for part in history),
            "Compact replacement_history 格式不符")
    summaries = [part for part in history if part.get("type") == "compaction"]
    if not summaries:
        return "absent", None
    latest = summaries[-1]
    if latest.get("encrypted_content") is not None:
        return "encrypted", None
    content = latest.get("content", latest.get("summary"))
    if content:
        return "available", blocks(content)
    return "absent", None


def turn_states(records):
    turns = []
    by_id = {}
    for row in records:
        record = row["record"]
        if record["type"] != "event_msg":
            continue
        payload = record["payload"]
        typ = payload.get("type")
        if typ == "thread_rolled_back":
            keep = max(len(turns) - payload["num_turns"], 0)
            for turn in turns[keep:]:
                by_id.pop(turn["id"], None)
            del turns[keep:]
            continue
        if typ not in {"task_started", "task_complete", "turn_aborted"}:
            continue
        turn_id = payload.get("turn_id")
        require(isinstance(turn_id, str), "Turn lifecycle 缺少 turn_id")
        if turn_id not in by_id:
            state = {"id": turn_id, "status": "incomplete", "position": row["source"]["ordinal"]}
            turns.append(state)
            by_id[turn_id] = state
        state = by_id[turn_id]
        if typ == "task_complete":
            error = payload.get("error")
            state["status"] = "failed" if error else "completed"
            if error:
                require(isinstance(error, dict), "Turn error 格式不符")
                state["error"] = {"category": error.get("codex_error_info", "unknown"),
                                  "message": error.get("message", "")}
        elif typ == "turn_aborted":
            state["status"] = "interrupted"
            if payload.get("reason"):
                state["reason"] = payload["reason"]
    return turns


def interaction_answer(entry):
    """只有明確的 answers 結構能歸屬於使用者選擇；accepted 回執不是回答。"""
    content = entry["content"]
    if len(content) != 1 or content[0].get("type") != "text":
        return None
    value = parsed_input(content[0]["text"])
    if isinstance(value, dict) and isinstance(value.get("answers"), dict):
        return [{"type": "data", "value": value["answers"]}]
    return None


def parsed_input(value):
    """JSON 字串參數解析成 object／array；其他（shell 字串、程式碼）保留原樣。"""
    if isinstance(value, str):
        try:
            result = json.loads(value)
        except ValueError:
            return value
        return result if isinstance(result, (dict, list)) else value
    return value


def call_operation(payload):
    """Evidence 用的操作描述：只取名稱與參數內容，不含 call_id、metadata 等協定外殼。"""
    return {"name": payload.get("name", "unknown"), "input": parsed_input(payload.get("arguments", payload.get("input")))}


def item_operation(item):
    kind = item.get("type")
    if kind == "CommandExecution":
        return {"name": "command", "input": item.get("command")}
    if kind in {"McpToolCall", "DynamicToolCall"}:
        prefix, name = item.get("server") or item.get("namespace"), item.get("tool")
        return {"name": f"{prefix}.{name}" if prefix else name, "input": parsed_input(item.get("arguments"))}
    return None


def read_session(session_id, home=None):
    return build_context(read_raw_session(session_id, home, allow_summary_only=True))


def build_context(raw):
    records, meta, boundary = raw["records"], raw["meta"], raw["boundary"]
    compact_events = []
    for row in records[boundary:]:
        if row["record"]["type"] == "compacted":
            availability, content = compact_summary(row["record"]["payload"])
            compact_events.append({"position": row["source"]["ordinal"],
                                   "timestamp": row["record"].get("timestamp"),
                                   "summary": {"availability": availability, **({"content": content} if content else {})}})
    latest = compact_events[-1] if compact_events else None
    use_compact = latest is not None and latest["summary"]["availability"] == "available"
    cutoff = latest["position"] if use_compact else boundary - 1

    all_entries = raw["conversation"]
    visible_ids = {(e["turn_id"], e["source"].get("item_id")) for e in all_entries
                   if e["role"] == "assistant" and e["kind"] == "message"}
    conversation = []
    supporting_context = []
    for entry in all_entries:
        if entry["source"].get("first_ordinal", entry["source"]["ordinal"]) <= cutoff:
            continue
        role, kind = entry["role"], entry["kind"]
        if role in {"user", "assistant"} and kind in {"message", "plan", "transcript"}:
            conversation.append(neutral_entry(entry))
        elif role == "assistant" and kind == "interaction":
            item_id = entry["source"].get("item_id", "")
            if item_id.startswith("question:") and (entry["turn_id"], item_id[9:]) in visible_ids:
                continue
            conversation.append(neutral_entry(entry, kind="interaction_question"))
        elif role == "tool" and kind == "tool_result" and entry.get("tool") in QUESTION_TOOLS:
            answer = interaction_answer(entry)
            if answer is not None:
                conversation.append(neutral_entry(entry, kind="interaction_response", role="user", content=answer))
        elif (role, kind) in SUPPORTING_KINDS:
            supporting_context.append(neutral_entry(entry, kind=SUPPORTING_KINDS[role, kind], role="agent"))
    conversation.sort(key=lambda e: e["position"])
    for n, entry in enumerate(conversation, 1):
        entry["sequence"] = n
    for n, entry in enumerate(supporting_context, 1):
        entry["sequence"] = n

    turns = turn_states(records[boundary:])
    last_turn = turns[-1] if turns else None
    status = last_turn["status"] if last_turn else "unknown"
    if raw["partial_tail_bytes"]:
        status = "incomplete"

    unfinished = last_turn is not None and last_turn["status"] != "completed"
    calls = raw["calls"]
    pending = [c for c in calls.values() if unfinished and not c["answered"]
               and c["turn"] == last_turn["id"] and c["position"] >= boundary]

    evidence = []
    artifacts_by_path = {}
    for entry in all_entries:
        if entry["role"] != "tool":
            continue
        ordinal = entry["source"]["ordinal"]
        record = records[ordinal]["record"]
        item = record["payload"].get("item", {}) if record["type"] == "event_msg" else {}
        item_type = item.get("type")
        if entry["kind"] == "file_change":
            details = entry["content"][0].get("value", {}) if entry["content"] else {}
            for path in details.get("changes", {}) if isinstance(details.get("changes"), dict) else []:
                artifacts_by_path[path] = {"path": path, "turn_id": entry["turn_id"],
                                           "status": details.get("status", "recorded"),
                                           "last_recorded_position": ordinal}
        if not unfinished or entry["turn_id"] != last_turn["id"]:
            continue
        kind = ("file_change" if item_type == "FileChange" else
                "process_result" if item_type == "CommandExecution" else
                "attachment" if entry["kind"] == "attachment" else "operation_result")
        result = {"position": ordinal, "timestamp": entry["timestamp"], "kind": kind, "content": entry["content"]}
        call = calls.get(record["payload"].get("call_id")) if record["type"] == "response_item" else None
        op = item_operation(item) if item else call_operation(call["payload"]) if call else None
        if op:
            result["operation"] = op
        evidence.append(result)
    for call in pending:
        evidence.append({"position": call["position"], "timestamp": call["timestamp"],
                         "kind": "operation_without_recorded_result", "operation": call_operation(call["payload"])})
    evidence.sort(key=lambda e: e["position"])
    artifacts = sorted(artifacts_by_path.values(), key=lambda e: e["last_recorded_position"])

    summary_unavailable = latest is not None and not use_compact
    encrypted = raw["filtered"].get("encrypted_agent_message")
    limitations = ["只包含已持久化的紀錄；附件路徑及檔案變更不代表目前磁碟狀態。"]
    if summary_unavailable:
        limitations.append("最後的 Compact 摘要不可讀；原始對話是可驗證的替代資料，可能與原模型 context 不同。")
    if encrypted:
        limitations.append("有加密的 Agent 間訊息無法讀取；相關上下文可能不完整。")
    if raw["partial_tail_bytes"]:
        limitations.append("活動歷史段尾端有未完成 JSONL 紀錄；只讀取到最後完整的一行。")
    return {"schema_version": 2,
            "session": {"id": raw["session_id"], "created_at": records[0]["record"].get("timestamp"),
                        "working_directory": meta.get("cwd")},
            "state": {"status": status, "last_turn": last_turn,
                      "turns": [{"id": t["id"], "status": t["status"]} for t in turns],
                      "source_completeness": "partial" if raw["partial_tail_bytes"] else "complete"},
            "context_basis": "latest_compact" if use_compact else "raw_fallback" if latest else "raw_conversation",
            "context_completeness": "summary_unavailable" if summary_unavailable else
                                    "encrypted_agent_context" if encrypted else "available",
            "compact": {"count": len(compact_events),
                        "boundaries": [{"position": e["position"], "timestamp": e["timestamp"]} for e in compact_events],
                        "latest": latest},
            "conversation": conversation, "supporting_context": supporting_context,
            "execution_evidence": evidence, "artifacts": artifacts,
            "limitations": limitations}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Codex Session ID → Agent-neutral Session Context（無模型、無網路）")
    parser.add_argument("session_id", help="完整 Codex Session UUID")
    parser.add_argument("--codex-home", type=Path, help="覆寫 CODEX_HOME / ~/.codex")
    parser.add_argument("--output", type=Path, help="寫入 UTF-8 JSON；預設 stdout")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--raw", action="store_true", help="診斷：輸出原始 JSONL 紀錄及來源位置")
    mode.add_argument("--inspect", action="store_true", help="診斷：只輸出計數、Compact 與最後狀態")
    args = parser.parse_args(argv)
    try:
        if args.raw:
            raw = read_raw_session(args.session_id, args.codex_home, allow_summary_only=True)
            result = {"session_id": raw["session_id"], "sources": raw["sources"], "records": raw["records"],
                      "partial_tail_bytes": raw["partial_tail_bytes"]}
        else:
            result = read_session(args.session_id, args.codex_home)
            if args.inspect:
                result = {"session": result["session"], "state": result["state"],
                          "context_basis": result["context_basis"],
                          "compact": {"count": result["compact"]["count"],
                                      "boundaries": result["compact"]["boundaries"],
                                      "latest_summary_availability": result["compact"]["latest"]["summary"]["availability"]
                                      if result["compact"]["latest"] else None},
                          "counts": {key: len(result[key]) for key in
                                     ("conversation", "supporting_context", "execution_evidence", "artifacts")}}
        output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            destination = args.output.resolve()
            require(not destination.is_relative_to(codex_home(args.codex_home)), "輸出必須在 CODEX_HOME 外，避免覆寫 Session 或索引", "invalid_output")
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=destination.parent,
                                                 prefix=".codex-reader-", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(output)
                os.replace(temporary, destination)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
        else:
            sys.stdout.write(output)
        return 0
    except (ReaderError, OSError) as exc:
        error = {"error": {"code": exc.code if isinstance(exc, ReaderError) else "io_error", "message": str(exc)}}
        sys.stderr.write(json.dumps(error, ensure_ascii=False) + "\n")
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
