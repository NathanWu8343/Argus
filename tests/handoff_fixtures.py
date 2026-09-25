"""Handoff 驗收用的合成 Codex Session（V1 Acceptance Case 1–4、7）與每個 Case 的預期事實。

依 Reader 測試的實測 Schema 產生原始 JSONL，讓驗收走完整管線：Reader → prepare_source → Sub-agent。
用法：python -B tests/handoff_fixtures.py <codex-home>
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_codex_session_reader import event, message, meta, record, response, write_rollout

PLANNING = "00000000-0000-4000-8000-0000000000c1"
INTERRUPTED = "00000000-0000-4000-8000-0000000000c2"
COMPACT = "00000000-0000-4000-8000-0000000000c3"
COMPACT_INTERRUPTED = "00000000-0000-4000-8000-0000000000c4"
LONG_EVIDENCE = "00000000-0000-4000-8000-0000000000c7"

# 來源 Agent 的操作方式；不論哪個 Case，handoff.md 都不應出現（Case 5）
SOURCE_METHOD_TERMS = ["apply_patch", "exec_command", "function_call", "turn_aborted", "task_complete",
                       "shell_command", "docs.search", "browser.js"]


class Session:
    def __init__(self, sid, cwd):
        self.sid, self.cwd, self.rows, self.n = sid, cwd, [meta(sid, cwd=cwd)], 0

    def turn(self, *items, end="complete", error=None):
        self.n += 1
        tid = f"turn-{self.n}"
        self.rows.append(event("task_started", turn_id=tid))
        for kind, value in items:
            self.n += 1
            mid = f"i{self.n}"

            def item(typ, **fields):
                self.rows.append(event("item_completed", thread_id=self.sid, turn_id=tid,
                                       item={"type": typ, "id": mid, **fields}))

            if kind in {"user", "assistant"}:
                self.rows.append(message(value, kind, mid, self.sid, tid))
            elif kind == "plan":
                item("Plan", text=value)
            elif kind == "command":
                item("CommandExecution", **value)
            elif kind == "file":
                path, diff = value
                item("FileChange", changes={path: {"diff": diff}}, status="completed")
            elif kind == "pending":
                self.rows.append(response("function_call", call_id=mid, name=value, arguments="{}",
                                          internal_chat_message_metadata_passthrough={"turn_id": tid}))
            elif kind == "shell":
                command, code, output = value
                passthrough = {"turn_id": tid}
                self.rows.append(response("function_call", call_id=mid, name="shell_command",
                                          arguments=json.dumps({"command": command, "workdir": self.cwd}),
                                          internal_chat_message_metadata_passthrough=passthrough))
                self.rows.append(response("function_call_output", call_id=mid,
                                          output=f"Exit code: {code}\nWall time: 0.4 seconds\nOutput:\n{output}",
                                          internal_chat_message_metadata_passthrough=passthrough))
            elif kind == "mcp":
                server, tool, arguments, result = value
                item("McpToolCall", server=server, tool=tool, arguments=arguments, result=result, status="completed")
            elif kind == "dynamic":
                namespace, tool, arguments, text = value
                item("DynamicToolCall", namespace=namespace, tool=tool, arguments=arguments, status="completed",
                     content_items=[{"type": "inputText", "text": text}])
            elif kind == "delegated":
                self.rows.append(response("agent_message", author="/root/reviewer", recipient="/root",
                                          content=[{"type": "output_text", "text": value}]))
        if end == "complete":
            self.rows.append(event("task_complete", turn_id=tid, **({"error": error} if error else {})))
        elif end == "interrupted":
            self.rows.append(event("turn_aborted", turn_id=tid, reason="interrupted"))

    def compact(self, summary=None):
        part = {"type": "compaction", "content": summary} if summary else {"type": "compaction", "encrypted_content": "opaque"}
        self.rows.append(record("compacted", {"message": "", "replacement_history": [part]}))

    def write(self, home):
        write_rollout(home, self.rows, self.sid)


def planning():
    s = Session(PLANNING, "D:\\repo\\prweekly")
    s.turn(("user", "我想做一個 CLI，把我們 GitHub org 每週合併的 PR 彙整成週報給主管看。先討論需求跟架構，還不要寫程式。"),
           ("assistant", "可以。先確認幾件事：1) 語言用 Node.js（Octokit）還是 Python（PyGithub）？"
                         "2) token 我建議存在 ~/.prweekly/config.json，比較方便。3) 輸出格式要什麼？"))
    s.turn(("user", "用 Python，團隊比較熟。token 不要存檔，資安不允許，用環境變數 GITHUB_TOKEN。輸出先要 Markdown。"),
           ("assistant", "了解。為了加速，我建議加一層 SQLite 快取 PR 資料，重跑時只抓增量。"
                         "另外 Slack 我也可以用 incoming webhook 在產生後直接發到頻道。"))
    s.turn(("user", "快取先不要，V1 每次即時抓就好，資料量不大。Slack 可以，輸出也要支援 Slack。"),
           ("assistant", "好的，V1 不做快取。Slack 部分我會用 webhook 在產生週報後直接 POST 到頻道。"))
    s.turn(("user", "等等，公司不允許我們自己設 webhook。Slack 只要輸出成 Slack mrkdwn 格式的文字，我自己貼上去，不要自動發送。"
                    "另外週期用 ISO week（週一到週日），要彙整的 repo 清單從 config.yaml 讀。"),
           ("assistant", "收到。還有兩個問題：draft PR 要不要算進去？被 revert 的 PR 要不要標註？"))
    s.turn(("user", "revert 的要標註。draft 要不要算我再問主管，先記下來。"),
           ("plan", "<proposed_plan>\n# prweekly V1 計畫\n- Python 3.11 CLI：`prweekly --week 2026-W38 --format md|slack`\n"
                    "- 認證：環境變數 GITHUB_TOKEN\n- 設定：config.yaml 列出 repos\n- 資料：每次即時呼叫 GitHub API，不做快取\n"
                    "- 輸出：Markdown、Slack mrkdwn 文字（只輸出，不發送）\n- 被 revert 的 PR 加註記\n"
                    "- 模組：cli.py / github_client.py / report.py / formatters/\n</proposed_plan>"),
           ("assistant", "計畫如上。draft PR 是否計入等你問完主管再補。你確認後我就開始建立專案骨架。"))
    return s


def interrupted():
    s = Session(INTERRUPTED, "D:\\repo\\inventory-api")
    s.turn(("user", "幫 service/ 這個 FastAPI 服務加上 API key 驗證：header 用 X-API-Key，合法 key 從環境變數 API_KEYS 讀"
                    "（逗號分隔）。/health 不需要驗證。要補測試。"),
           ("assistant", "目前有 service/routers/items.py 與 service/routers/health.py，測試用 pytest + TestClient。計畫："
                         "1) 新增 service/auth.py 的 require_api_key dependency 2) 只套用到 items router，/health 排除 "
                         "3) 新增 tests/test_auth.py 4) 跑 pytest 全綠。可以嗎？"))
    s.turn(("user", "可以。另外 key 比對要用 constant-time 比較，避免 timing attack。回傳 401 時 body 用 "
                    "{\"detail\": \"invalid api key\"}。開始做吧。"),
           ("assistant", "好，開始實作。我會用 apply_patch 新增 auth.py，再用 exec_command 跑 pytest。"),
           ("file", ("service/auth.py", "+import hmac, os\n+from fastapi import Header, HTTPException\n+\n"
                                        "+def require_api_key(x_api_key: str = Header(default=\"\")):\n"
                                        "+    keys = [k for k in os.environ.get(\"API_KEYS\", \"\").split(\",\") if k]\n"
                                        "+    if not any(hmac.compare_digest(x_api_key, k) for k in keys):\n"
                                        "+        raise HTTPException(status_code=401, detail=\"invalid api key\")\n")),
           ("file", ("service/main.py", "-app = FastAPI()\n+app = FastAPI(dependencies=[Depends(require_api_key)])\n")),
           ("file", ("tests/test_auth.py", "+def test_missing_key_is_401(client): ...\n+def test_valid_key(client): ...\n"
                                           "+def test_health_without_key(client): ...\n")),
           ("command", {"command": "pytest -q", "exit_code": 1, "status": "failed",
                        "stdout": "FAILED tests/test_auth.py::test_health_without_key - assert 401 == 200\n"
                                  "FAILED tests/test_items.py::test_list_items - assert 401 == 200\n"
                                  "2 failed, 6 passed in 0.41s\n"}),
           ("assistant", "兩個失敗：main.py 把驗證掛在全域，連 /health 也被擋；既有的 test_items.py 沒帶 key。"
                         "我改成只掛在 items router，並在 tests/conftest.py 加一個帶 key 的 client fixture。"),
           ("pending", "apply_patch"),
           error={"message": "You've hit your usage limit.", "codex_error_info": "usage_limit_exceeded"})
    return s


def compact():
    s = Session(COMPACT, "D:\\repo\\clinic-booking")
    s.turn(("user", "想做診所線上掛號系統的後端，先討論架構。"),
           ("assistant", "建議 REST API、JWT 認證、MongoDB 儲存。"))
    s.compact("## 先前討論摘要\n- 專案：診所線上掛號系統後端\n- API 風格：REST\n- 認證：JWT（access token 15 分鐘）\n"
              "- 資料庫：MongoDB\n- 需求：病患可預約、取消；醫師可查看當日名單\n- 限制：身分證字號需加密儲存\n"
              "- 尚未決定：通知管道（簡訊或 LINE）")
    s.turn(("user", "資料庫改用 PostgreSQL，因為之後要做跨表報表，Mongo 不適合。其他照舊。"),
           ("assistant", "了解，改為 PostgreSQL，schema 用 SQLAlchemy + Alembic 管理。身分證字號仍用欄位層級加密。"))
    s.turn(("user", "認證也改一下，前台是同網域的 web，用 HttpOnly session cookie 就好，不用 JWT。"),
           ("assistant", "好，改為 server-side session + HttpOnly cookie，session 存在 PostgreSQL。我先更新 docs/architecture.md。"),
           ("file", ("docs/architecture.md", "-資料庫：MongoDB\n+資料庫：PostgreSQL\n-認證：JWT\n+認證：server-side session + HttpOnly cookie\n")),
           ("assistant", "已更新 docs/architecture.md：資料庫 PostgreSQL、認證改為 session cookie、通知管道仍待決定。"))
    s.turn(("user", "通知管道用 LINE。下一步先把資料表設計出來給我看。"),
           ("assistant", "收到，通知用 LINE。我接著整理資料表設計（patients、doctors、appointments、sessions）給你確認。"))
    return s


def compact_interrupted():
    s = Session(COMPACT_INTERRUPTED, "D:\\repo\\shop-admin")
    s.turn(("user", "後台需要訂單匯出功能：管理員選日期區間，匯出 CSV。"),
           ("assistant", "可以直接在 API request 內同步產生 CSV 回傳。"))
    s.turn(("user", "訂單量可能到 50 萬筆，同步會 timeout，要改成背景工作，做完通知管理員。"),
           ("assistant", "建議用 Celery + Redis 做背景工作。"))
    s.turn(("user", "我們已經有 RQ 了，不要再引入 Celery。"),
           ("assistant", "了解，用現有 RQ。匯出檔先存在伺服器的 exports/ 目錄。"),
           ("delegated", "Review：RQ 缺少排程重試，建議改用 Celery。"))
    s.turn(("user", "檔案不要存本機，存 S3 bucket acme-exports，下載連結 24 小時過期。"),
           ("assistant", "好，上傳 S3 並產生 24 小時的 presigned URL。"))
    s.compact()
    s.turn(("user", "補充欄位：order_id、created_at、customer_email、total。customer_email 要遮罩，只留網域，例如 ***@gmail.com。"),
           ("assistant", "了解。"))
    s.turn(("user", "格式改成 XLSX，不要 CSV，財務要直接用 Excel 開。"),
           ("assistant", "改為 XLSX，使用 openpyxl 的 write_only 模式處理大量資料。"))
    s.turn(("user", "開始實作吧。這輪先做 model 和背景 job，API endpoint 下一輪再做。"),
           ("assistant", "先新增 ExportJob model 與 migration。"),
           ("file", ("app/models/export_job.py", "+class ExportJob(Model):\n+    status = CharField()  # queued/running/done/failed\n"
                                                 "+    date_from = DateField()\n+    date_to = DateField()\n+    s3_key = CharField(null=True)\n"
                                                 "+    expires_at = DateTimeField(null=True)\n")),
           ("file", ("migrations/0042_export_job.py", "+class Migration: ...  # create export_job table\n")),
           ("command", {"command": "python manage.py migrate", "exit_code": 0, "status": "completed",
                        "stdout": "Applying orders.0042_export_job... OK\n"}),
           ("assistant", "migration 已套用到本機開發資料庫。接著寫匯出 job（openpyxl write_only、email 遮罩、上傳 S3）。"),
           ("file", ("app/jobs/export_orders.py", "+def mask_email(email):\n+    raise NotImplementedError  # TODO\n+\n"
                                                  "+def export_orders(job_id):\n+    job = ExportJob.get(job_id)\n"
                                                  "+    wb = Workbook(write_only=True)\n+    ws = wb.create_sheet()\n"
                                                  "+    ws.append([\"order_id\", \"created_at\", \"customer_email\", \"total\"])\n"
                                                  "+    # TODO: 寫入資料列、上傳 S3、設定 presigned URL 與 expires_at\n")),
           ("pending", "exec_command"),
           end="interrupted")
    return s


def source_listing(name, lines):
    """合成的長檔案內容：讓較早的 evidence 超過精簡門檻。"""
    return "\n".join(f"{n:>4}  {name}: {text}" for n, text in enumerate(lines * 12, 1))


def long_evidence():
    s = Session(LONG_EVIDENCE, "D:\\repo\\billing-tool")
    s.turn(("user", "billing/ 套件裡很多 print 除錯訊息，改成標準 logging，等級用環境變數 LOG_LEVEL 控制，預設 WARNING。"
                    "CLI 印給使用者看的結果不能變。不要引入 loguru 之類的第三方套件。README 要補 LOG_LEVEL 的說明。"),
           ("assistant", "計畫：1) 新增 billing/log.py 集中設定 logging 2) invoice.py 與 cli.py 的除錯 print 改成 logger "
                         "3) 跑 pytest 與 ruff 4) README 補 LOG_LEVEL 說明。可以嗎？"))
    code = ["def compute_total(items):", "    print('debug: items', items)", "    return sum(i.price for i in items)",
            "def render_summary(total):", "    print(f'Total: {total}')"]
    failure = ("============================= test session starts =============================\n"
               + "\n".join(f"tests/test_cli.py::test_case_{n} PASSED" for n in range(40))
               + "\n________________________ test_summary_output ________________________\n"
               + "E   AssertionError: expected 'Total: 42' in stdout\n" * 20
               + "FAILED tests/test_cli.py::test_summary_output - AssertionError: expected 'Total: 42' in stdout\n"
               + "FAILED tests/test_cli.py::test_summary_currency - AssertionError\n"
               + "FAILED tests/test_cli.py::test_summary_empty - AssertionError\n3 failed, 9 passed in 0.52s")
    s.turn(("user", "可以，開始。"),
           ("shell", ("Get-Content billing/cli.py", 0, source_listing("cli.py", code))),
           ("shell", ("Get-Content billing/invoice.py", 0, source_listing("invoice.py", code))),
           ("shell", ("rg -n \"print\\(\" billing", 0, "\n".join(f"billing/invoice.py:{n}:    print('debug')" for n in range(1, 40)))),
           ("mcp", ("docs", "search", {"q": "logging.basicConfig force level from env"},
                    "logging.basicConfig(level=..., force=True) 會移除既有 handler 後重設；level 可用字串名稱。")),
           ("file", ("billing/log.py", "+import logging, os\n+\n+def configure_logging():\n"
                                       "+    level = os.environ.get(\"LOG_LEVEL\", \"WARNING\").upper()\n"
                                       "+    logging.basicConfig(level=level, force=True)\n")),
           ("file", ("billing/invoice.py", "-    print('debug: items', items)\n+    logger.debug('items: %s', items)\n")),
           ("file", ("billing/cli.py", "-    print(f'Total: {total}')\n+    logger.info(f'Total: {total}')\n")),
           ("shell", ("pytest -q", 1, failure)),
           ("assistant", "summary 是 CLI 給使用者看的輸出，不能改成 logger，否則預設 WARNING 下會消失。改回 print，只把除錯訊息換成 logger。"),
           ("file", ("billing/cli.py", "-    logger.info(f'Total: {total}')\n+    print(f'Total: {total}')\n")),
           ("shell", ("pytest -q", 0, "............                                                  [100%]\n12 passed in 0.48s")),
           ("shell", ("Get-Content tests/test_cli.py", 0, source_listing("test_cli.py", ["def test_summary_output(capsys): ..."]))),
           ("shell", ("Get-Content CHANGELOG.md", 0, source_listing("CHANGELOG.md", ["## 1.4.0", "- 發票匯出"]))),
           ("dynamic", ("browser", "js", {"code": "document.title"}, "Python logging HOWTO")),
           ("shell", ("ruff check billing", 1, "billing/log.py:4:89: E501 Line too long (96 > 88)\nFound 1 error.")),
           ("file", ("billing/log.py", "-    level = os.environ.get(\"LOG_LEVEL\", \"WARNING\").upper()\n"
                                       "+    level = os.environ.get(\"LOG_LEVEL\", \"WARNING\")\n+    level = level.upper()\n")),
           ("shell", ("ruff check billing", 0, "All checks passed!")),
           ("shell", ("git diff --stat", 0, " billing/cli.py     | 4 ++--\n billing/invoice.py | 6 +++---\n billing/log.py     | 7 +++++++\n")),
           ("shell", ("Get-Content README.md", 0, source_listing("README.md", ["## 安裝", "pip install -e ."]))),
           ("assistant", "程式與測試都已完成，接著在 README 補 LOG_LEVEL 的說明。"),
           ("pending", "apply_patch"),
           error={"message": "You've hit your usage limit.", "codex_error_info": "usage_limit_exceeded"})
    return s


CASES = {
    PLANNING: {
        "name": "case1-planning",
        "build": planning,
        "expectations": [
            "Requests & Intent 是用 Python 做一個彙整 GitHub org 每週合併 PR 的週報 CLI",
            "Requests & Intent 或 Decisions & Constraints 包含：Python、token 用環境變數 GITHUB_TOKEN、V1 不做快取、輸出 Markdown 與 Slack mrkdwn 文字、ISO week、repo 清單從 config.yaml 讀、revert 的 PR 要標註",
            "沒有把 SQLite 快取或 Slack webhook 自動發送寫成目前有效的決策",
            "Decisions & Constraints 的否決選項包含 SQLite 快取（V1）與 Slack webhook 自動發送（公司不允許）",
            "Files & Artifacts 為「無」或明確表示尚未實作",
            "Pending 包含 draft PR 是否計入（待問主管）",
            "Next 是在使用者確認計畫後建立專案骨架／開始實作，以目標描述而非工具步驟",
        ],
    },
    INTERRUPTED: {
        "name": "case2-interrupted-execution",
        "build": interrupted,
        "expectations": [
            "說明來源 Session 在執行中因額度耗盡停止，最後沒有完成回覆",
            "Files & Artifacts 記錄 service/auth.py 已建立（constant-time 比較、401 detail）與 tests/test_auth.py 已建立",
            "service/main.py 的變更被標為有問題／未完成：驗證掛在全域導致 /health 也被擋",
            "明確記錄測試未通過（2 failed, 6 passed），沒有宣稱測試通過或功能完成",
            "Pending 包含：驗證只套用到 items router 並排除 /health、讓既有 items 測試帶 key（如 conftest fixture）、重跑測試到全部通過",
            "tests/conftest.py 的修正只是開始或尚未做，沒有被寫成已完成",
            "Decisions & Constraints 包含 X-API-Key header、API_KEYS 逗號分隔、constant-time 比較、/health 不驗證、401 body 格式",
        ],
    },
    COMPACT: {
        "name": "case3-compact",
        "build": compact,
        "expectations": [
            "Decisions & Constraints 使用 PostgreSQL（不是 MongoDB），並記錄原因是跨表報表",
            "Decisions & Constraints 使用 server-side session + HttpOnly cookie（不是 JWT）",
            "保留 Compact 摘要中仍有效的內容：REST、病患預約／取消與醫師查看當日名單、身分證字號加密",
            "通知管道已決定為 LINE，沒有列為未決",
            "MongoDB 與 JWT 沒有被寫成目前有效的決策",
            "Files & Artifacts 記錄 docs/architecture.md 已更新",
            "Next 是整理資料表設計給使用者確認",
        ],
    },
    COMPACT_INTERRUPTED: {
        "name": "case4-compact-interrupted",
        "build": compact_interrupted,
        "expectations": [
            "Requests & Intent 是後台訂單匯出的背景工作功能",
            "最新決策：XLSX（不是 CSV）、用現有 RQ（不是 Celery）、存 S3 acme-exports 並產生 24 小時過期連結、欄位 order_id/created_at/customer_email/total、customer_email 只留網域遮罩",
            "Reviewer 建議改用 Celery 沒有被當成決策；Celery 若出現只在否決選項",
            "Decisions & Constraints 的否決選項包含同步匯出（會 timeout）與 Celery；CSV 與本機 exports/ 目錄沒有被寫成目前做法",
            "Files & Artifacts 記錄 ExportJob model 與 migration，且 migration 已套用到本機開發資料庫",
            "app/jobs/export_orders.py 被標為未完成：mask_email 未實作、資料列寫入與 S3 上傳仍是 TODO",
            "Pending 包含完成匯出 job（遮罩、寫入、S3 上傳、連結過期）與測試；API endpoint 明確延到下一輪",
            "Next 是完成匯出 job，不是做 API endpoint",
        ],
    },
    LONG_EVIDENCE: {
        "name": "case7-long-evidence",
        "build": long_evidence,
        "expectations": [
            "說明來源 Session 在執行中因額度耗盡停止，最後沒有完成回覆",
            "Files & Artifacts 記錄 billing/log.py（LOG_LEVEL，預設 WARNING）、invoice.py 除錯訊息改用 logger",
            "cli.py 的 summary 維持 print（使用者可見輸出不變），沒有寫成改用 logger",
            "目前測試狀態是最後一次的 12 passed 與 ruff 通過；較早的 3 failed 沒有被寫成目前狀態",
            "Pending 包含 README 補 LOG_LEVEL 說明，且標為未完成（寫入沒有結果）",
            "Decisions & Constraints 包含不引入第三方 logging 套件、CLI 使用者可見輸出不變",
            "handoff.md 沒有出現來源的工具名稱（shell_command、docs.search、browser.js、apply_patch）",
        ],
    },
}


def build(home):
    for case in CASES.values():
        case["build"]().write(home)


if __name__ == "__main__":
    build(sys.argv[1])
    print(json.dumps({sid: case["name"] for sid, case in CASES.items()}, ensure_ascii=False, indent=2))
