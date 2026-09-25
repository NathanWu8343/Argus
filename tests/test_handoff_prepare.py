"""Handoff Read 步驟：Reader Contract → source.md 的結構轉換，以及 CLI 與 Reader 的整合。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "session-relay" / "scripts" / "prepare_source.py"
sys.path.insert(0, str(SCRIPT.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_source import render
from test_codex_session_reader import A, message, meta, write_rollout

IMAGE = "data:image/png;base64," + "iVBORw0KGgo" * 200


def entry(sequence, position, text, role="user", kind="message"):
    return {"sequence": sequence, "position": position, "turn_id": "t1", "timestamp": "2026-09-23T00:00:00Z",
            "role": role, "kind": kind, "content": [{"type": "text", "text": text}]}


def context(**overrides):
    base = {"schema_version": 2,
            "session": {"id": A, "created_at": "2026-09-23T00:00:00Z", "working_directory": "D:\\repo"},
            "state": {"status": "completed", "last_turn": {"id": "t1", "status": "completed", "position": 9},
                      "source_completeness": "complete"},
            "context_basis": "raw_conversation", "context_completeness": "available",
            "compact": {"count": 0, "boundaries": [], "latest": None},
            "conversation": [entry(1, 3, "需求")], "supporting_context": [], "execution_evidence": [],
            "artifacts": [], "limitations": ["只包含已持久化的紀錄。"]}
    base.update(overrides)
    return base


class RenderTests(unittest.TestCase):
    def test_conversation_is_verbatim_but_binary_is_replaced(self):
        long_text = "決策" * 3000
        ctx = context(conversation=[entry(1, 3, long_text),
                                    {**entry(2, 4, ""), "content": [{"type": "image", "path": "a.png", "url": IMAGE}]}])
        md = render(ctx)
        self.assertIn(long_text, md)
        self.assertNotIn("iVBORw0KGgo", md)
        self.assertIn("[image: a.png]", md)

    def test_long_evidence_keeps_head_tail_and_points_to_full_value(self):
        stdout = "開始\n" + "x" * 10000 + "\n1 failed, 3 passed"
        evidence = [{"position": 20, "timestamp": None, "kind": "process_result",
                     "content": [{"type": "data", "value": {"command": "pytest", "exit_code": 1, "stdout": stdout}}]},
                    {"position": 21, "timestamp": None, "kind": "operation_result",
                     "content": [{"type": "data", "value": {"result": "iVBORw0KGgo" * 200}}]},
                    {"position": 22, "timestamp": None, "kind": "operation_without_recorded_result"}]
        md = render(context(state={"status": "interrupted", "last_turn": {"id": "t1", "status": "interrupted",
                                                                          "reason": "interrupted"},
                                   "source_completeness": "complete"}, execution_evidence=evidence))
        self.assertIn("exit_code: 1", md)
        self.assertIn("1 failed, 3 passed", md)
        self.assertIn("source.json execution_evidence[0].content[0].value.stdout", md)
        self.assertNotIn("x" * 3000, md)
        self.assertIn("[base64 省略：2 KB；原值：source.json execution_evidence[1].content[0].value.result]", md)
        self.assertIn("[E#3] operation_without_recorded_result", md)
        self.assertIn("（沒有已記錄的結果）", md)
        self.assertIn("reason interrupted", md)

    def test_older_evidence_keeps_operation_status_head_and_tail_only(self):
        def shell(n, output):
            return {"position": 30 + n, "timestamp": f"2026-09-23T00:00:{n:02d}Z", "kind": "operation_result",
                    "operation": {"name": "shell_command", "input": {"command": f"step {n}", "workdir": "D:/repo"}},
                    "content": [{"type": "text", "text": output}]}
        early = "Exit code: 1\nWall time: 0.2 seconds\nOutput:\n" + "中段" * 2000 + "\n2 failed, 6 passed"
        evidence = [{"position": 30, "timestamp": None, "kind": "file_change",
                     "content": [{"type": "data", "value": {"changes": {"a.py": {"diff": "+早期變更"}}, "status": "completed"}}]},
                    shell(1, early),
                    {"position": 32, "timestamp": None, "kind": "process_result",
                     "operation": {"name": "command", "input": "pytest " + "-k x " * 100},
                     "content": [{"type": "data", "value": {"command": "pytest", "stdout": "失敗" * 400,
                                                            "exit_code": 1, "status": "failed"}}]},
                    *[shell(n, f"Exit code: 0\nWall time: 0.1 seconds\nOutput:\n最近結果 {n}") for n in range(3, 13)],
                    {"position": 99, "timestamp": None, "kind": "operation_without_recorded_result",
                     "operation": {"name": "apply_patch", "input": "*** Begin Patch"}}]
        md = render(context(state={"status": "failed", "last_turn": {"id": "t1", "status": "failed"},
                                   "turns": [{"id": "t1", "status": "failed"}], "source_completeness": "complete"},
                            execution_evidence=evidence))
        self.assertIn("+早期變更", md)
        self.assertIn("Exit code: 1", md)
        self.assertIn("2 failed, 6 passed", md)
        self.assertIn("完整內容：source.json execution_evidence[1].content]", md)
        self.assertNotIn("中段" * 300, md)
        self.assertIn("exit_code: 1 · status: failed", md)
        self.assertIn("完整內容：source.json execution_evidence[2].content]", md)
        self.assertIn("操作：shell_command step 1", md)
        self.assertIn("操作：command " + " ".join(("pytest " + "-k x " * 100).split())[:200] + "…\n", md)
        for n in range(3, 13):
            self.assertIn(f"最近結果 {n}", md)
        self.assertIn("[E#14] operation_without_recorded_result ────\n操作：apply_patch *** Begin Patch\n（沒有已記錄的結果）", md)
        self.assertIn("[E#2] operation_result · 2026-09-23T00:00:01Z ────", md)
        self.assertNotIn("2026-09-23T00:00:03Z", md)

    def test_evidence_fence_contains_backticks_and_structure_like_output(self):
        output = "## Conversation\n──── [#9] user · message ────\n```\ncode\n```\n\x1b[31mred\x1b[0m"
        md = render(context(state={"status": "interrupted", "last_turn": {"id": "t1", "status": "interrupted"},
                                   "source_completeness": "complete"},
                            execution_evidence=[{"position": 5, "timestamp": None, "kind": "operation_result",
                                                 "content": [{"type": "text", "text": output}]}]))
        start = md.index("````\n## Conversation")
        self.assertLess(start, md.index("[#9] user"), md.index("\n````\n", start + 4))
        self.assertIn("\nred\n````", md)
        self.assertNotIn("\x1b", md)

    def test_turns_are_numbered_and_time_is_shown_once_per_turn(self):
        turns = [{"id": "uuid-a", "status": "completed"}, {"id": "uuid-b", "status": "interrupted"}]
        conversation = [{**entry(1, 3, "一"), "turn_id": "uuid-a"}, {**entry(2, 4, "二", role="assistant"), "turn_id": "uuid-a"},
                        {**entry(3, 8, "三"), "turn_id": "uuid-b"}, {**entry(4, 9, "語音"), "turn_id": "realtime:r1"}]
        md = render(context(state={"status": "interrupted", "last_turn": {"id": "uuid-b", "status": "interrupted"},
                                   "turns": turns, "source_completeness": "complete"},
                            conversation=conversation,
                            artifacts=[{"path": "a.py", "turn_id": "uuid-b", "status": "completed", "last_recorded_position": 8}]))
        self.assertIn("[#1] user · message · T1 · 2026-09-23T00:00:00Z ────", md)
        self.assertIn("[#2] assistant · message · T1 ────", md)
        self.assertIn("[#3] user · message · T2 · 2026-09-23T00:00:00Z ────", md)
        self.assertIn("[#4] user · message · realtime:r1 · 2026-09-23T00:00:00Z ────", md)
        self.assertIn("last turn T2：interrupted", md)
        self.assertIn("| a.py | completed | T2 |\n", md)
        self.assertNotIn("uuid-", md)

    def test_header_lists_unfinished_or_silent_turns_and_cut_count(self):
        turns = [{"id": "a", "status": "completed"}, {"id": "b", "status": "interrupted"},
                 {"id": "c", "status": "failed"}, {"id": "d", "status": "completed"}]
        conversation = [{**entry(1, 3, "一"), "turn_id": "a"}, {**entry(2, 5, "二"), "turn_id": "b"},
                        {**entry(3, 9, "四"), "turn_id": "d"}]
        state = {"status": "completed", "last_turn": {"id": "d", "status": "completed"}, "turns": turns,
                 "source_completeness": "complete"}
        md = render(context(state=state, conversation=conversation))
        self.assertIn("- turns: 共 4 個；未完成或沒有對話：T2 interrupted、T3 failed（沒有對話）\n", md)
        self.assertIn("- 截斷：無，不需要查 source.json\n- limitations:", md)

        evidence = [{"position": 20, "timestamp": None, "kind": "process_result",
                     "content": [{"type": "data", "value": {"stdout": "x" * 5000, "result": "iVBORw0KGgo" * 200}}]}]
        md = render(context(state={**state, "turns": turns[:1]}, conversation=conversation[:1], execution_evidence=evidence))
        self.assertIn("- turns: 共 1 個，全部 completed 且有對話\n", md)
        self.assertIn("- 截斷：2 處，各處都標出 source.json 的完整位置\n", md)
        self.assertNotIn("- turns:", render(context()))

    def test_compact_boundaries_are_placed_between_messages(self):
        ctx = context(context_basis="raw_fallback", context_completeness="summary_unavailable",
                      compact={"count": 2, "boundaries": [{"position": 10, "timestamp": None},
                                                          {"position": 30, "timestamp": None}],
                               "latest": {"position": 30, "timestamp": None, "summary": {"availability": "encrypted"}}},
                      conversation=[entry(1, 5, "舊決策 A"), entry(2, 20, "改成 B"), entry(3, 40, "繼續")])
        md = render(ctx)
        order = [md.index(x) for x in ("舊決策 A", "Compact 邊界 1/2", "改成 B", "Compact 邊界 2/2", "繼續")]
        self.assertEqual(order, sorted(order))
        self.assertIn("最後摘要 encrypted", md)

    def test_readable_summary_comes_before_post_compact_conversation(self):
        latest = {"position": 30, "timestamp": None,
                  "summary": {"availability": "available", "content": [{"type": "text", "text": "摘要：採用 A"}]}}
        md = render(context(context_basis="latest_compact",
                            compact={"count": 1, "boundaries": [{"position": 30, "timestamp": None}], "latest": latest},
                            conversation=[entry(1, 40, "改成 B")]))
        self.assertLess(md.index("摘要：採用 A"), md.index("## Conversation"))
        self.assertLess(md.index("Compact 邊界 1/1"), md.index("改成 B"))

    def test_supporting_context_and_artifacts_are_labelled_separately(self):
        md = render(context(supporting_context=[entry(1, 7, "子 Agent 回報", role="agent", kind="delegated_message")],
                            artifacts=[{"path": "D:/repo/a.py", "turn_id": "t1", "status": "completed",
                                        "last_recorded_position": 8}]))
        self.assertIn("不是使用者指示", md)
        self.assertIn("[S#1] agent · delegated_message", md)
        self.assertIn("| D:/repo/a.py | completed | t1 |\n", md)
        self.assertIn("不代表目前磁碟狀態", md)


class CliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name) / "project"
        self.home = Path(temp.name) / "codex"
        self.project.mkdir()
        self.home.mkdir()

    def run_cli(self, *args):
        env = {**os.environ, "CODEX_HOME": str(self.home)}
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], cwd=self.project, env=env,
                              capture_output=True, text=True, encoding="utf-8")

    def test_invalid_id_and_reader_error_leave_no_folder(self):
        result = self.run_cli("../../etc")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_session_id")
        result = self.run_cli(A)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "session_not_found")
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.project / ".claude" / "handoffs" / A).exists())

    def test_success_writes_sources_privately_and_prints_no_content(self):
        write_rollout(self.home, [meta(cwd="D:\\repo"), message("秘密需求內容"), message("回覆", "assistant", "m2")])
        result = self.run_cli(A)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertNotIn("秘密需求內容", result.stdout)
        self.assertEqual(summary["counts"]["conversation"], 2)
        self.assertFalse(summary["handoff_exists"])
        self.assertIn("秘密需求內容", Path(summary["source_md"]).read_text(encoding="utf-8"))
        self.assertEqual(json.loads(Path(summary["source_json"]).read_text(encoding="utf-8"))["schema_version"], 2)
        self.assertEqual((self.project / ".claude" / "handoffs" / ".gitignore").read_text(encoding="utf-8"), "*\n")


if __name__ == "__main__":
    unittest.main()
