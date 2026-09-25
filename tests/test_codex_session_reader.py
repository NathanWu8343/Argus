"""以最小合成資料重現實測 Schema；測試不需要 Codex 或私人 Session。"""

import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from codex_session_reader import ReaderError, main, read_raw_session, read_session

ROOT = Path(__file__).resolve().parents[1]
A = "00000000-0000-4000-8000-000000000001"
B = "00000000-0000-4000-8000-000000000002"
C = "00000000-0000-4000-8000-000000000003"
T = "turn-1"


def record(kind, payload, timestamp="2026-09-23T00:00:00Z"):
    return {"timestamp": timestamp, "type": kind, "payload": payload}


def meta(tid=A, **kwargs):
    return record("session_meta", {"id": tid, "session_id": tid, "history_mode": "paginated", **kwargs})


def event(kind, **kwargs):
    return record("event_msg", {"type": kind, **kwargs})


def message(text, role="user", mid="m1", tid=A, turn=T, **kwargs):
    item = {"type": "UserMessage" if role == "user" else "AgentMessage", "id": mid,
            "content": [{"type": "text" if role == "user" else "Text", "text": text}], **kwargs}
    return event("item_completed", thread_id=tid, turn_id=turn, item=item)


def response(kind, **kwargs):
    return record("response_item", {"type": kind, **kwargs})


def write_rollout(home, records, tid=A, segment=None, archive=False):
    folder = Path(home) / ("archived_sessions" if archive else "sessions/2026/09/23")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-date-{tid}{'_' + segment if segment else ''}.jsonl"
    path.write_bytes(b"".join((json.dumps(r, ensure_ascii=False) + "\n").encode() for r in records))
    return path


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def write(self, records, tid=A, segment=None, archive=False):
        return write_rollout(self.home, records, tid, segment, archive)

    def index(self, tid, path):
        with contextlib.closing(sqlite3.connect(self.home / "state_5.sqlite")) as db:
            db.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, rollout_path TEXT)")
            db.execute("INSERT OR REPLACE INTO threads VALUES (?,?)", (tid, str(path)))
            db.commit()

    def read(self, tid=A):
        return read_raw_session(tid, self.home)

    def text(self, result):
        return [b["text"] for e in result["conversation"] for b in e["content"] if b["type"] == "text"]

    def test_order_uses_file_order_not_time_and_keeps_identical_messages(self):
        rows = [meta(), message("一樣"), message("一樣", mid="m2"), message("回答\n", "assistant", "m3")]
        rows[1]["timestamp"] = "2027-01-01T00:00:00Z"
        self.write(rows)
        result = self.read()
        self.assertEqual(self.text(result), ["一樣", "一樣", "回答\n"])
        self.assertEqual([e["role"] for e in result["conversation"]], ["user", "user", "assistant"])
        self.assertEqual([e["sequence"] for e in result["conversation"]], [1, 2, 3])

    def test_structural_mirrors_and_injections_are_not_conversation(self):
        self.write([meta(), response("message", role="user", content=[{"type": "input_text", "text": "注入"}]),
                    message("真正輸入"), response("message", role="user", content=[{"type": "input_text", "text": "真正輸入"}]),
                    event("token_count", info={"tokens": 999}), record("world_state", {"ui": True}),
                    event("item_completed", thread_id=A, turn_id=T, item={"type": "Reasoning", "id": "r", "raw_content": ["思考"]}),
                    message("回答", "assistant", "m2", phase="final")])
        result = self.read()
        self.assertEqual(self.text(result), ["真正輸入", "回答"])
        self.assertNotIn("999", json.dumps(result["conversation"]))

    def test_item_update_uses_latest_content_at_first_position(self):
        self.write([meta(), message("初稿", "assistant"), message("後續", mid="m2"), message("更新", "assistant")])
        self.assertEqual(self.text(self.read()), ["更新", "後續"])

    def test_same_id_in_different_turns_is_not_deduplicated(self):
        self.write([meta(), message("甲", turn="t1"), message("乙", turn="t2")])
        self.assertEqual(self.text(self.read()), ["甲", "乙"])

    def test_message_and_tool_output_can_share_an_id_without_overwriting(self):
        self.write([meta(), event("task_started", turn_id=T), message("可見回覆", "assistant", "call-1"),
                    response("function_call_output", call_id="call-1", output="工具結果")])
        result = self.read()
        self.assertEqual(self.text(result), ["可見回覆", "工具結果"])
        self.assertEqual([e["role"] for e in result["conversation"]], ["assistant", "tool"])

    def test_index_selects_head_and_history_base_discards_abandoned_tail(self):
        parent_rows = [meta(), message("繼承"), message("退出的分支", mid="m2")]
        parent = self.write(parent_rows)
        boundary = sum(len(x) for x in parent.read_bytes().splitlines(keepends=True)[:2])
        head = self.write([meta(history_base={"thread_id": A, "end_ordinal_exclusive": 2, "end_byte_offset": boundary}),
                           message("新分支", mid="m3")], segment=B)
        self.index(A, head)
        result = self.read()
        self.assertEqual(self.text(result), ["繼承", "新分支"])
        self.assertEqual(len(result["sources"]), 2)
        self.assertEqual(result["conversation"][-1]["source"]["ordinal"], 3)

    def test_nested_history_uses_global_ordinals_and_physical_byte_offsets(self):
        parent = self.write([meta(), message("根")])
        second = self.write([meta(history_base={"thread_id": A, "end_ordinal_exclusive": 2, "end_byte_offset": parent.stat().st_size}),
                             message("第二段", mid="m2")], segment=B)
        third = self.write([meta(history_base={"thread_id": B, "end_ordinal_exclusive": 4, "end_byte_offset": second.stat().st_size}),
                            message("第三段", mid="m3")], segment=C)
        self.index(A, third)
        self.assertEqual(self.text(self.read()), ["根", "第二段", "第三段"])

    def test_fork_copied_history_does_not_read_parent_later_messages(self):
        self.write([meta(A), message("父後續")])
        self.write([meta(B, forked_from_id=A), message("已複製歷史", tid=B)], tid=B)
        self.assertEqual(self.text(self.read(B)), ["已複製歷史"])

    def test_subagent_session_id_can_point_to_parent_but_thread_id_must_match(self):
        self.write([meta(B, session_id=A, parent_thread_id=A), message("子任務", tid=B)], tid=B)
        self.assertEqual(self.text(self.read(B)), ["子任務"])

    def test_subagent_visible_start(self):
        self.write([meta(subagent_history_start_ordinal=2), message("繼承的模型背景"), message("子任務", mid="m2")])
        self.assertEqual(self.text(self.read()), ["子任務"])

    def test_v2_subagent_state_ignores_copied_parent_meta_turns_and_compaction(self):
        # 實測子 Agent 檔的第 2 行是複製進來的父 session_meta；邊界要取自己的第 1 行
        self.write([meta(subagent_history_start_ordinal=5), meta(B), event("task_started", turn_id="parent"),
                    record("compacted", {"message": "", "replacement_history": []}),
                    event("task_complete", turn_id="parent"), event("task_started", turn_id=T),
                    message("子任務"), event("task_complete", turn_id=T)])
        result = read_session(A, self.home)
        self.assertEqual([t["id"] for t in result["state"]["turns"]], [T])
        self.assertEqual(result["compact"]["count"], 0)
        self.assertEqual(self.text(result), ["子任務"])

    def test_compaction_keeps_original_messages_and_ignores_replacement_summary(self):
        self.write([meta(), message("壓縮前原文"), record("compacted", {"message": "模型摘要", "replacement_history": []}),
                    message("壓縮後", mid="m2")])
        self.assertEqual(self.text(self.read()), ["壓縮前原文", "壓縮後"])

    def test_question_options_and_answers_survive_without_protocol(self):
        questions = {"questions": [{"id": "q", "question": "選哪個？", "options": [{"label": "甲"}]}]}
        self.write([meta(), event("task_started", turn_id=T), message("開始"),
                    response("function_call", call_id="c", name="request_user_input", arguments=json.dumps(questions)),
                    response("function_call_output", call_id="c", output='{"answers":{"q":{"answers":["甲"]}}}')])
        entries = self.read()["conversation"]
        self.assertEqual([e["kind"] for e in entries], ["message", "interaction", "tool_result"])
        self.assertEqual(entries[1]["content"][0]["value"], questions)
        self.assertEqual(entries[2]["role"], "tool")
        self.assertNotIn('"arguments"', json.dumps(entries))

    def test_code_mode_nested_question_and_computed_result_survive(self):
        self.write([meta(), message("開始"), response("custom_tool_call", call_id="c", name="exec", input="protocol"),
                    response("custom_tool_call_output", call_id="c", output="運算結果",
                             internal_chat_message_metadata_passthrough={"executed_tool_calls": [
                                 {"name": "request_user_input_async", "arguments": {"questions": [{"title": "問題"}]}}]})])
        result = self.read()
        self.assertEqual(self.text(result), ["開始", "運算結果"])
        self.assertEqual(result["conversation"][1]["kind"], "interaction")

    def test_late_tool_output_keeps_its_original_turn(self):
        self.write([meta(), event("task_started", turn_id="t1"), message("開始", turn="t1"),
                    response("function_call", call_id="c", name="work", arguments="{}"),
                    event("task_started", turn_id="t2"), message("後續", mid="m2", turn="t2"),
                    response("function_call_output", call_id="c", output="較晚完成")])
        self.assertEqual(self.read()["conversation"][-1]["turn_id"], "t1")

    def test_tools_preserve_result_errors_and_file_diffs(self):
        self.write([meta(), message("開始"), event("item_completed", thread_id=A, turn_id=T,
                    item={"type": "CommandExecution", "id": "c", "command": "pytest", "stdout": "結果", "stderr": "錯誤", "exit_code": 1, "duration": 100}),
                    event("item_completed", thread_id=A, turn_id=T,
                    item={"type": "FileChange", "id": "f", "changes": {"x.py": {"diff": "+程式"}}, "status": "completed"})])
        entries = self.read()["conversation"]
        self.assertEqual(entries[1]["content"][0]["value"], {"command": "pytest", "stdout": "結果", "stderr": "錯誤", "exit_code": 1})
        self.assertIn("+程式", json.dumps(entries, ensure_ascii=False))
        self.assertNotIn("protocol", json.dumps(entries))

    def test_code_images_annotations_and_references_are_preserved(self):
        text = "```python\nprint('繁體')\n```\n[檔案](D:/檔案.py)\n"
        row = message(text)
        row["payload"]["item"]["content"] += [{"type": "image", "url": "data:image/png;base64,AAAA"},
            {"type": "localImage", "path": "D:/不存在.png"}]
        row["payload"]["item"]["content"][0]["text_elements"] = [{"placeholder": "@file"}]
        self.write([meta(), row])
        content = self.read()["conversation"][0]["content"]
        self.assertEqual(content[0]["text"], text)
        self.assertEqual(content[0]["annotations"], [{"placeholder": "@file"}])
        self.assertEqual(content[1]["url"], "data:image/png;base64,AAAA")
        self.assertEqual(content[2]["path"], "D:/不存在.png")

    def test_raw_user_image_bytes_supplement_local_image_reference(self):
        ui = message("圖片")
        ui["payload"]["item"]["content"].append({"type": "local_image", "path": "D:/deleted.png"})
        self.write([meta(), event("task_started", turn_id=T), response("message", role="user", id="raw-id",
                    content=[{"type": "input_text", "text": "圖片"}, {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}],
                    internal_chat_message_metadata_passthrough={"content_item_kinds": ["user.text", "user.image"], "turn_id": T}), ui])
        image = self.read()["conversation"][0]["content"][1]
        self.assertEqual(image, {"type": "image", "path": "D:/deleted.png", "url": "data:image/png;base64,AAAA"})

    def test_missing_display_event_is_an_error_even_with_earlier_messages(self):
        self.write([meta(), event("task_started", turn_id=T), message("第一則"), response("message", role="user",
                    content=[{"type": "input_text", "text": "不可遺失"}],
                    internal_chat_message_metadata_passthrough={"content_item_kinds": ["user.text"], "turn_id": T})])
        with self.assertRaises(ReaderError) as caught:
            self.read()
        self.assertEqual(caught.exception.code, "missing_history")

    def test_compaction_at_start_cannot_claim_full_history_even_with_later_messages(self):
        self.write([meta(), record("compacted", {"message": "先前內容摘要"}), message("之後")])
        with self.assertRaises(ReaderError) as caught:
            self.read()
        self.assertEqual(caught.exception.code, "missing_history")

    def test_aborted_turn_is_retained(self):
        self.write([meta(), event("task_started", turn_id=T), message("輸入"), message("已發生的回應", "assistant", "m2"),
                    event("turn_aborted", turn_id=T)])
        self.assertEqual(len(self.read()["conversation"]), 2)

    def test_empty_session_is_valid_but_summary_without_history_is_not(self):
        self.write([meta(), event("task_started", turn_id=T), event("turn_aborted", turn_id=T)])
        self.assertEqual(self.read()["conversation"], [])
        self.write([meta(), record("compacted", {"message": "摘要", "replacement_history": []})])
        with self.assertRaises(ReaderError) as caught:
            self.read()
        self.assertEqual(caught.exception.code, "missing_history")

    def test_voice_transcripts_keep_roles_and_order(self):
        self.write([meta(), record("realtime_item", {"type": "realtime_session_started", "id": "v0"}),
                    record("realtime_item", {"type": "transcript_segment", "id": "v1", "realtime_session_id": "voice",
                                             "role": "user", "text": "問題"}),
                    record("realtime_item", {"type": "transcript_segment", "id": "v2", "realtime_session_id": "voice",
                                             "role": "assistant", "text": "回答"})])
        result = self.read()
        self.assertEqual(self.text(result), ["問題", "回答"])
        self.assertEqual([e["role"] for e in result["conversation"]], ["user", "assistant"])

    def test_encrypted_agent_context_reports_limit_instead_of_silent_loss(self):
        self.write([meta(), message("開始"), response("agent_message", author="/root/worker", recipient="/root",
                    content=[{"type": "encrypted_content", "encrypted_content": "opaque"}])])
        self.assertEqual(self.read()["filtered"]["encrypted_agent_message"], 1)
        self.assertEqual(read_session(A, self.home)["context_completeness"], "encrypted_agent_context")

    def test_image_generation_payload_and_path_are_preserved(self):
        self.write([meta(), event("item_completed", thread_id=A, turn_id=T,
                    item={"type": "Extension", "kind": "image_gen.generation", "id": "image", "result": "AAAA", "savedPath": "D:/image.png"})])
        self.assertEqual(self.read()["conversation"][0]["content"][0]["value"], {"result": "AAAA", "savedPath": "D:/image.png"})

    def test_rollback_removes_only_explicitly_removed_turns(self):
        self.write([meta(), event("task_started", turn_id="t1"), message("保留", turn="t1"),
                    event("task_started", turn_id="t2"), message("刪除", turn="t2"),
                    event("thread_rolled_back", num_turns=1), event("task_started", turn_id="t3"), message("新回合", turn="t3")])
        self.assertEqual(self.text(self.read()), ["保留", "新回合"])

    def test_archived_session_and_environment_home(self):
        self.write([meta(), message("封存")], archive=True)
        with patch.dict(os.environ, {"CODEX_HOME": str(self.home)}):
            self.assertEqual(self.text(read_raw_session(A)), ["封存"])

    def test_invalid_id_not_found_and_index_identity_mismatch(self):
        for tid, code in [("../escape", "invalid_session_id"), (A, "session_not_found")]:
            with self.assertRaises(ReaderError) as caught:
                self.read(tid)
            self.assertEqual(caught.exception.code, code)
        path = self.write([meta(B), message("其他", tid=B)], tid=B)
        self.index(A, path)
        with self.assertRaisesRegex(ReaderError, "索引與檔案"):
            self.read()

    def test_foreign_item_rejected(self):
        self.write([meta(), message("別人的訊息", tid=B)])
        with self.assertRaisesRegex(ReaderError, "混入其他"):
            self.read()

    def test_multiple_unindexed_heads_are_ambiguous(self):
        self.write([meta(), message("一")])
        self.write([meta(), message("二")], segment=B)
        with self.assertRaises(ReaderError) as caught:
            self.read()
        self.assertEqual(caught.exception.code, "ambiguous_session")

    def test_missing_parent_or_bad_boundary_never_returns_partial(self):
        self.write([meta(history_base={"thread_id": B, "end_ordinal_exclusive": 2, "end_byte_offset": 100}), message("尾段")])
        with self.assertRaises(ReaderError) as caught:
            self.read()
        self.assertEqual(caught.exception.code, "missing_history")
        parent = self.write([meta(B), message("前段", tid=B)], tid=B)
        head = self.write([meta(history_base={"thread_id": B, "end_ordinal_exclusive": 99, "end_byte_offset": parent.stat().st_size}), message("尾段")])
        self.index(A, head)
        with self.assertRaisesRegex(ReaderError, "ordinal"):
            self.read()

    def test_corrupt_line_errors_and_partial_tail_is_explicit(self):
        path = self.write([meta(), message("完整")])
        original = path.read_bytes()
        path.write_bytes(original + b'{"type":')
        result = read_session(A, self.home)
        self.assertEqual(result["state"]["source_completeness"], "partial")
        self.assertEqual(self.text(result), ["完整"])
        path.write_bytes(original + b"not-json\n")
        with self.assertRaises(ReaderError) as caught:
            self.read()
        self.assertEqual(caught.exception.code, "invalid_json")

    def test_unknown_types_and_legacy_are_not_silently_dropped(self):
        rows = [record("future_record", {}), event("future_event"), response("future_response"),
                event("item_completed", thread_id=A, item={"type": "FutureItem", "id": "x"}),
                message("x", content=[{"type": "future_content"}])]
        for row in rows:
            self.write([meta(), message("正常"), row])
            with self.assertRaises(ReaderError) as caught:
                self.read()
            self.assertEqual(caught.exception.code, "unsupported_schema")
        self.write([meta(history_mode="legacy"), message("舊格式")])
        with self.assertRaisesRegex(ReaderError, "非 paginated"):
            self.read()

    def test_missing_message_content_is_a_structured_schema_error(self):
        self.write([meta(), event("item_completed", thread_id=A, turn_id=T,
                                 item={"type": "UserMessage", "id": "broken"})])
        with self.assertRaises(ReaderError) as caught:
            self.read()
        self.assertEqual(caught.exception.code, "unsupported_schema")

    def test_cli_utf8_json_and_machine_readable_error(self):
        self.write([meta(), message("繁體\n")])
        command = [sys.executable, "-B", str(ROOT / "scripts/codex_session_reader.py"), A, "--codex-home", str(self.home)]
        done = subprocess.run(command, capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.text(json.loads(done.stdout)), ["繁體\n"])
        self.assertEqual(done.stderr, b"")
        failed = subprocess.run([*command[:3], B, *command[4:]], capture_output=True)
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(failed.stdout, b"")
        self.assertEqual(json.loads(failed.stderr)["error"]["code"], "session_not_found")

    def test_output_cannot_overwrite_source(self):
        path = self.write([meta(), message("資料")])
        original = path.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            code = main([A, "--codex-home", str(self.home), "--output", str(path)])
        self.assertEqual(code, 1)
        self.assertEqual(path.read_bytes(), original)

    def test_cli_output_replaces_external_file_only_after_success(self):
        self.write([meta(), message("匯出")])
        with tempfile.TemporaryDirectory() as destination:
            path = Path(destination) / "conversation.json"
            path.write_text("existing", encoding="utf8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([B, "--codex-home", str(self.home), "--output", str(path)]), 1)
            self.assertEqual(path.read_text(encoding="utf8"), "existing")
            self.assertEqual(main([A, "--codex-home", str(self.home), "--output", str(path)]), 0)
            self.assertEqual(self.text(json.loads(path.read_text(encoding="utf8"))), ["匯出"])
            self.assertEqual(list(Path(destination).glob(".codex-reader-*")), [])

    def test_midline_byte_boundary_is_rejected(self):
        parent = self.write([meta(), message("根")])
        head = self.write([meta(history_base={"thread_id": A, "end_ordinal_exclusive": 2,
                           "end_byte_offset": parent.stat().st_size - 1}), message("尾")], segment=B)
        self.index(A, head)
        with self.assertRaises(ReaderError) as caught:
            self.read()
        self.assertEqual(caught.exception.code, "incomplete_record")

    def test_snapshot_rewrite_is_rejected(self):
        from codex_session_reader import Store
        path = self.write([meta(), message("根")])
        original = Store.verify_snapshot

        def rewrite(store):
            path.write_bytes(path.read_bytes().replace("根".encode(), "改".encode()))
            original(store)

        with patch.object(Store, "verify_snapshot", rewrite):
            with self.assertRaises(ReaderError) as caught:
                self.read()
        self.assertEqual(caught.exception.code, "source_changed")

    def test_sqlite_wal_index_is_read_without_codex_process(self):
        path = self.write([meta(), message("WAL")])
        with contextlib.closing(sqlite3.connect(self.home / "state_5.sqlite")) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT)")
            db.execute("INSERT INTO threads VALUES (?,?)", (A, str(path)))
            db.commit()
            self.assertTrue((self.home / "state_5.sqlite-wal").exists())
            self.assertEqual(self.text(self.read()), ["WAL"])

    def test_v2_normal_session_keeps_conversation_and_recorded_artifacts(self):
        self.write([meta(), event("task_started", turn_id=T), message("請修正"),
                    event("item_completed", thread_id=A, turn_id=T, item={"type": "CommandExecution", "id": "run",
                        "command": "pytest", "stdout": "ok", "exit_code": 0, "status": "completed"}),
                    event("item_completed", thread_id=A, turn_id=T, item={"type": "FileChange", "id": "edit",
                        "changes": {"D:/repo/a.py": {"diff": "+修正"}}, "status": "completed"}),
                    message("已修正", "assistant", "reply", phase="final"), event("task_complete", turn_id=T)])
        result = read_session(A, self.home)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(self.text(result), ["請修正", "已修正"])
        self.assertEqual([x["role"] for x in result["conversation"]], ["user", "assistant"])
        self.assertEqual(result["state"]["status"], "completed")
        self.assertEqual(result["execution_evidence"], [])
        self.assertEqual([x["path"] for x in result["artifacts"]], ["D:/repo/a.py"])
        self.assertNotIn("CommandExecution", json.dumps(result, ensure_ascii=False))

    def test_v2_interrupted_turn_keeps_results_without_final_reply(self):
        self.write([meta(), event("task_started", turn_id=T), message("執行測試"),
                    event("item_completed", thread_id=A, turn_id=T, item={"type": "FileChange", "id": "edit",
                        "changes": {"D:/repo/a.py": {"diff": "+變更"}}, "status": "completed"}),
                    event("item_completed", thread_id=A, turn_id=T, item={"type": "CommandExecution", "id": "run",
                        "command": "pytest", "stdout": "FAILED", "stderr": "AssertionError", "exit_code": 1,
                        "status": "failed"}), event("turn_aborted", turn_id=T, reason="interrupted")])
        result = read_session(A, self.home)
        self.assertEqual(result["state"]["status"], "interrupted")
        self.assertEqual(self.text(result), ["執行測試"])
        self.assertEqual([e["kind"] for e in result["execution_evidence"]], ["file_change", "process_result"])
        self.assertIn("AssertionError", json.dumps(result["execution_evidence"]))

    def test_v2_multiple_plaintext_compacts_use_only_latest_context(self):
        self.write([meta(), event("task_started", turn_id="t1"), message("最早"),
                    record("compacted", {"message": "", "replacement_history":
                                         [{"type": "compaction", "content": "第一份摘要"}]}),
                    message("中段", mid="m2", turn="t2"),
                    record("compacted", {"message": "", "replacement_history":
                                         [{"type": "compaction", "content": "最新摘要"}]}),
                    message("後段", mid="m3", turn="t3"), message("完成", "assistant", "m4", turn="t3")])
        result = read_session(A, self.home)
        self.assertEqual(result["context_basis"], "latest_compact")
        self.assertEqual(result["compact"]["count"], 2)
        self.assertEqual(result["compact"]["latest"]["summary"]["content"][0]["text"], "最新摘要")
        self.assertEqual(self.text(result), ["後段", "完成"])
        self.assertNotIn("最早", json.dumps(result["conversation"], ensure_ascii=False))

    def test_v2_readable_summary_can_stand_when_pre_compact_raw_is_missing(self):
        self.write([meta(), record("compacted", {"message": "", "replacement_history":
                                    [{"type": "compaction", "content": "可讀摘要"}]}),
                    message("後續")])
        result = read_session(A, self.home)
        self.assertEqual(result["context_basis"], "latest_compact")
        self.assertEqual(self.text(result), ["後續"])
        self.assertEqual(result["compact"]["latest"]["summary"]["content"][0]["text"], "可讀摘要")

    def test_v2_encrypted_replacement_takes_precedence_over_message_field(self):
        self.write([meta(), message("原文"), record("compacted", {"message": "可能是顯示文字",
                    "replacement_history": [{"type": "compaction", "encrypted_content": "opaque"}]}),
                    message("後續", mid="m2")])
        result = read_session(A, self.home)
        self.assertEqual(result["context_basis"], "raw_fallback")
        self.assertEqual(result["compact"]["latest"]["summary"]["availability"], "encrypted")

    def test_v2_open_turn_is_incomplete_and_source_partial_is_separate(self):
        path = self.write([meta(), event("task_started", turn_id=T), message("仍在工作")])
        result = read_session(A, self.home)
        self.assertEqual(result["state"]["status"], "incomplete")
        self.assertEqual(result["state"]["source_completeness"], "complete")
        path.write_bytes(path.read_bytes() + b'{"unfinished":')
        result = read_session(A, self.home)
        self.assertEqual(result["state"]["source_completeness"], "partial")

    def test_v2_encrypted_compact_then_interrupted_execution_is_agent_neutral(self):
        self.write([meta(), event("task_started", turn_id="t1"), message("先前需求", turn="t1"),
                    event("task_complete", turn_id="t1"),
                    record("compacted", {"message": "", "replacement_history": [
                        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "保留"}]},
                        {"type": "compaction", "encrypted_content": "opaque"}]}),
                    event("task_started", turn_id="t2"), message("繼續", mid="m2", turn="t2"),
                    response("custom_tool_call", call_id="call", name="exec", input="internal invocation",
                             internal_chat_message_metadata_passthrough={"turn_id": "t2"}),
                    event("item_completed", thread_id=A, turn_id="t2", item={"type": "CommandExecution",
                        "id": "run", "command": "pytest", "stdout": "1 failed", "exit_code": 1, "status": "failed"}),
                    response("custom_tool_call_output", call_id="call", output="result",
                             internal_chat_message_metadata_passthrough={"turn_id": "t2"}),
                    event("turn_aborted", turn_id="t2", reason="interrupted")])
        result = read_session(A, self.home)
        self.assertEqual(result["context_basis"], "raw_fallback")
        self.assertEqual(result["context_completeness"], "summary_unavailable")
        self.assertEqual(result["compact"]["latest"]["summary"]["availability"], "encrypted")
        self.assertEqual(self.text(result), ["先前需求", "繼續"])
        self.assertEqual(result["state"]["status"], "interrupted")
        self.assertEqual(len(result["execution_evidence"]), 2)
        # 操作只附在 evidence 上，用來判讀結果；對話與協定外殼仍不輸出
        self.assertEqual(result["execution_evidence"][1]["operation"], {"name": "exec", "input": "internal invocation"})
        self.assertNotIn("internal invocation", json.dumps(result["conversation"], ensure_ascii=False))
        self.assertNotIn('"tool"', json.dumps(result, ensure_ascii=False))
        self.assertNotIn("call_id", json.dumps(result, ensure_ascii=False))

    def test_v2_quota_failure_and_pending_operation(self):
        self.write([meta(), event("task_started", turn_id=T), message("做事"),
                    response("function_call", call_id="pending", name="work", arguments="{}",
                             internal_chat_message_metadata_passthrough={"turn_id": T}),
                    event("task_complete", turn_id=T, error={"message": "usage exhausted",
                                                          "codex_error_info": "usage_limit_exceeded"})])
        result = read_session(A, self.home)
        self.assertEqual(result["state"]["status"], "failed")
        self.assertEqual(result["state"]["last_turn"]["error"]["category"], "usage_limit_exceeded")
        self.assertEqual(result["execution_evidence"][0]["kind"], "operation_without_recorded_result")

    def test_v2_evidence_names_the_operation_behind_each_result(self):
        self.write([meta(), event("task_started", turn_id=T), message("做事"),
                    response("function_call", call_id="sh", name="shell_command",
                             arguments='{"command":"Get-Content a.py","workdir":"D:/repo"}'),
                    response("function_call_output", call_id="sh", output="Exit code: 0\nWall time: 0.1 seconds\nOutput:\n內容"),
                    event("item_completed", thread_id=A, turn_id=T, item={"type": "DynamicToolCall", "id": "d",
                        "namespace": "browser", "tool": "js", "arguments": {"code": "page.title()"},
                        "content_items": [{"type": "inputText", "text": "標題"}], "status": "completed"}),
                    event("item_completed", thread_id=A, turn_id=T, item={"type": "McpToolCall", "id": "m",
                        "server": "docs", "tool": "search", "arguments": {"q": "RQ"}, "result": "找到",
                        "status": "completed"}),
                    event("item_completed", thread_id=A, turn_id=T, item={"type": "CommandExecution", "id": "c",
                        "command": "pytest -q", "stdout": "1 failed", "exit_code": 1, "status": "failed"}),
                    response("custom_tool_call", call_id="p", name="apply_patch", input="*** Begin Patch",
                             internal_chat_message_metadata_passthrough={"turn_id": T}),
                    event("turn_aborted", turn_id=T, reason="interrupted")])
        evidence = read_session(A, self.home)["execution_evidence"]
        self.assertEqual([e.get("operation") for e in evidence], [
            {"name": "shell_command", "input": {"command": "Get-Content a.py", "workdir": "D:/repo"}},
            {"name": "browser.js", "input": {"code": "page.title()"}},
            {"name": "docs.search", "input": {"q": "RQ"}},
            {"name": "command", "input": "pytest -q"},
            {"name": "apply_patch", "input": "*** Begin Patch"}])
        self.assertEqual(evidence[-1]["kind"], "operation_without_recorded_result")

    def test_v2_turns_list_every_turn_status_after_rollback(self):
        self.write([meta(), event("task_started", turn_id="t1"), message("一", turn="t1"), event("task_complete", turn_id="t1"),
                    event("task_started", turn_id="t2"), message("二", mid="m2", turn="t2"),
                    event("turn_aborted", turn_id="t2", reason="interrupted"),
                    event("task_started", turn_id="t3"), message("三", mid="m3", turn="t3"), event("task_complete", turn_id="t3"),
                    event("thread_rolled_back", num_turns=1),
                    event("task_started", turn_id="t4"), message("四", mid="m4", turn="t4")])
        state = read_session(A, self.home)["state"]
        self.assertEqual(state["turns"], [{"id": "t1", "status": "completed"}, {"id": "t2", "status": "interrupted"},
                                          {"id": "t4", "status": "incomplete"}])
        self.assertEqual(state["last_turn"]["id"], "t4")

    def test_v2_interaction_keeps_answers_but_not_async_acknowledgement(self):
        self.write([meta(), event("task_started", turn_id=T), message("請選"),
                    response("function_call", call_id="q1", name="request_user_input",
                             arguments='{"questions":[{"id":"x","question":"選？"}]}'),
                    response("function_call_output", call_id="q1", output='{"answers":{"x":{"answers":["甲"]}}}'),
                    response("function_call", call_id="q2", name="request_user_input_async",
                             arguments='{"questions":[{"title":"再選？"}]}'),
                    response("function_call_output", call_id="q2", output='{"accepted":true}'),
                    event("task_complete", turn_id=T)])
        result = read_session(A, self.home)
        self.assertEqual([x["kind"] for x in result["conversation"]],
                         ["message", "interaction_question", "interaction_response", "interaction_question"])
        self.assertNotIn("accepted", json.dumps(result["conversation"]))

    def test_v2_cli_inspect_and_raw_modes(self):
        self.write([meta(), event("task_started", turn_id=T), message("內容"), event("task_complete", turn_id=T)])
        base = [sys.executable, "-B", str(ROOT / "scripts/codex_session_reader.py"), A,
                "--codex-home", str(self.home)]
        inspect = subprocess.run([*base, "--inspect"], capture_output=True)
        self.assertEqual(inspect.returncode, 0, inspect.stderr)
        view = json.loads(inspect.stdout)
        self.assertEqual(view["counts"]["conversation"], 1)
        self.assertNotIn("conversation", view)
        raw = subprocess.run([*base, "--raw"], capture_output=True)
        self.assertEqual(raw.returncode, 0, raw.stderr)
        self.assertEqual(len(json.loads(raw.stdout)["records"]), 4)


if __name__ == "__main__":
    unittest.main()
