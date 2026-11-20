import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PMAP_SCRIPT = str(REPO / "pmap.py")


class MultiAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wwi-agents-")
        self.home = Path(self.tmp) / "home"
        self.home.mkdir()
        self.env = {
            "HOME": str(self.home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_pmap(self, *args):
        res = subprocess.run(
            [sys.executable, PMAP_SCRIPT, *args],
            text=True,
            capture_output=True,
            env=self.env,
            timeout=15,
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        return json.loads(res.stdout)

    def test_antigravity_transcript_parsing(self):
        conv_id = "11111111-2222-3333-4444-555555555555"
        tr = self.home / ".gemini" / "antigravity-cli" / "brain" / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
        tr.parent.mkdir(parents=True)
        lines = [
            {
                "step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT",
                "created_at": "2026-11-20T08:00:00Z",
                "content": "<USER_REQUEST>\n결제 테스트 추가하고 커밋해줘\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\ntime\n</ADDITIONAL_METADATA>",
            },
            {
                "step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE",
                "created_at": "2026-11-20T08:00:05Z", "model": "gemini-4-neon-high",
                "tool_calls": [
                    {"name": "read_file", "args": {"Path": "\"/tmp/app.py\""}},
                    {"name": "write_file", "args": {"Path": "\"/tmp/test_pay.py\""}},
                    {"name": "run_command", "args": {"CommandLine": "\"pytest test_pay.py\""}},
                ],
            },
            {"step_index": 2, "source": "MODEL", "type": "GENERIC", "created_at": "2026-11-20T08:00:06Z", "content": "ok read"},
            {"step_index": 3, "source": "MODEL", "type": "GENERIC", "created_at": "2026-11-20T08:00:07Z", "content": "Created file."},
            {"step_index": 4, "source": "MODEL", "type": "GENERIC", "created_at": "2026-11-20T08:00:08Z", "content": "Command exited with code 0.\n1 passed"},
            {
                "step_index": 5, "source": "MODEL", "type": "PLANNER_RESPONSE",
                "created_at": "2026-11-20T08:00:10Z", "model": "gemini-4-neon-high",
                "tool_calls": [
                    {"name": "run_command", "args": {"CommandLine": "\"git commit -m 'test'\""}},
                    {"name": "invoke_subagent", "args": {"Role": "\"Code Reviewer\""}},
                ],
            },
            {"step_index": 6, "source": "MODEL", "type": "GENERIC", "created_at": "2026-11-20T08:00:11Z", "content": "Command exited with code 0.\n[main abc123] test"},
            {"step_index": 7, "source": "MODEL", "type": "GENERIC", "created_at": "2026-11-20T08:00:12Z", "content": "Subagent launched"},
            {
                "step_index": 8, "source": "MODEL", "type": "PLANNER_RESPONSE",
                "created_at": "2026-11-20T08:00:15Z", "model": "gemini-4-neon-high",
                "content": "결제 테스트를 추가하고 커밋까지 마쳤습니다.",
            },
        ]
        tr.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")

        mp = self.run_pmap("map", "--session", f"agy:{conv_id}")
        self.assertEqual(mp["agent"], "antigravity")
        self.assertEqual(mp["status"], "idle")
        self.assertEqual(mp["helpers"], 1)
        self.assertIn("Gemini 4 Neon", mp["phases"][0]["name"])
        card = mp["phases"][0]["cards"][0]
        self.assertEqual(card["title"], "결제 테스트 추가하고 커밋해줘")
        self.assertIn("커밋까지 마쳤습니다", card["done_text"])
        tn = card["turns"][0]
        self.assertEqual(tn["commits"], 1)
        self.assertEqual(tn["tests"], "통과")
        self.assertIn("test_pay.py", tn["files"])
        self.assertIn("Code Reviewer", tn["helpers"])

    def test_opencode_sqlite_parsing_and_list(self):
        dbp = self.home / ".local" / "share" / "opencode" / "opencode.db"
        dbp.parent.mkdir(parents=True)
        now_ms = int(time.time() * 1000)
        con = sqlite3.connect(dbp)
        c = con.cursor()
        c.execute(
            "CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, title TEXT, agent TEXT, model TEXT, "
            "time_created INTEGER, time_updated INTEGER, time_busy INTEGER, time_idle INTEGER, idle_outcome TEXT)"
        )
        c.execute(
            "CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INTEGER, time_created INTEGER, data TEXT)"
        )
        c.execute(
            "INSERT INTO session_v2 VALUES (?, NULL, ?, ?, 'build', ?, ?, ?, ?, ?, 'succeeded')",
            ("ses_oc1", "/tmp/myrepo", "조건 카드 구현", json.dumps({"id": "deepseek-v4.1-flash-free"}),
             now_ms - 60000, now_ms - 1000, now_ms - 10000, now_ms - 1000),
        )
        c.execute(
            "INSERT INTO session_v2 VALUES (?, ?, ?, ?, 'sub', NULL, ?, ?, ?, ?, 'succeeded')",
            ("ses_oc_child", "ses_oc1", "/tmp/myrepo", "하위 검토", now_ms - 30000, now_ms - 20000, now_ms - 30000, now_ms - 20000),
        )
        msgs = [
            ("m1", "ses_oc1", "user", 1, now_ms - 50000, json.dumps({"text": "조건 카드 버그를 고쳐줘"})),
            ("m2", "ses_oc1", "assistant", 2, now_ms - 40000, json.dumps({
                "model": {"id": "deepseek-v4.1-flash-free"},
                "content": [
                    {"type": "tool", "id": "c1", "name": "edit", "state": {"status": "completed", "input": {"filePath": "/tmp/myrepo/card.tsx"}}},
                    {"type": "tool", "id": "c2", "name": "shell", "state": {
                        "status": "completed", "input": {"command": "vitest run"}, "content": [{"type": "text", "text": "5 passed"}]
                    }},
                    {"type": "tool", "id": "c3", "name": "shell", "state": {
                        "status": "completed", "input": {"command": "git commit -m 'fix'"}, "content": [{"type": "text", "text": "[main 123] fix"}]
                    }},
                    {"type": "text", "text": "조건 카드 버그를 고치고 시험 통과 및 커밋했습니다."},
                ],
            })),
            ("m3", "ses_oc1", "idle", 3, now_ms - 1000, json.dumps({"outcome": "succeeded"})),
        ]
        c.executemany("INSERT INTO session_message VALUES (?, ?, ?, ?, ?, ?)", msgs)
        con.commit()
        con.close()

        # list 에 잡혀야 함 (24시간 이내 활동)
        sessions = self.run_pmap("list")
        oc_list = [s for s in sessions if s.get("agent") == "opencode"]
        self.assertEqual(len(oc_list), 1)
        self.assertEqual(oc_list[0]["session"], "opencode:ses_oc1")
        self.assertEqual(oc_list[0]["name"], "조건 카드 구현")

        mp = self.run_pmap("map", "--session", "opencode:ses_oc1")
        self.assertEqual(mp["agent"], "opencode")
        self.assertEqual(mp["source"], "db")
        self.assertEqual(mp["helpers"], 1)
        self.assertEqual(mp["status"], "idle")
        card = mp["phases"][0]["cards"][0]
        self.assertEqual(card["turns"][0]["commits"], 1)
        self.assertEqual(card["turns"][0]["tests"], "통과")
        self.assertIn("card.tsx", card["turns"][0]["files"])
        self.assertIn("시험 통과 및 커밋했습니다", card["done_text"])

    def test_copilot_events_parsing_and_list(self):
        sid = "6b884e13-491c-419e-ac06-59d4ff729fd7"
        sdir = self.home / ".copilot" / "session-state" / sid
        sdir.mkdir(parents=True)
        (sdir / "workspace.yaml").write_text(f"id: {sid}\ncwd: /tmp/cp\nsummary: 코파일럿 테스트\n", encoding="utf-8")
        ev = sdir / "events.jsonl"
        events = [
            {"type": "session.start", "timestamp": "2026-11-20T08:10:00.000Z", "data": {"sessionId": sid, "context": {"cwd": "/tmp/cp"}}},
            {"type": "session.model_change", "timestamp": "2026-11-20T08:10:01.000Z", "data": {"newModel": "gpt-5.4"}},
            {"type": "user.message", "timestamp": "2026-11-20T08:10:05.000Z", "data": {"content": "README 오타 고쳐줘"}},
            {"type": "tool.execution_start", "timestamp": "2026-11-20T08:10:06.000Z", "data": {
                "toolCallId": "tc1", "toolName": "edit", "arguments": {"path": "/tmp/cp/README.md"}
            }},
            {"type": "tool.execution_complete", "timestamp": "2026-11-20T08:10:07.000Z", "data": {"toolCallId": "tc1", "success": True}},
            {"type": "tool.execution_start", "timestamp": "2026-11-20T08:10:08.000Z", "data": {
                "toolCallId": "tc2", "toolName": "bash", "arguments": {"command": "pytest"}
            }},
            {"type": "tool.execution_complete", "timestamp": "2026-11-20T08:10:09.000Z", "data": {
                "toolCallId": "tc2", "success": True, "result": "2 passed"
            }},
            {"type": "assistant.message", "timestamp": "2026-11-20T08:10:12.000Z", "data": {"content": "README 오타를 수정하고 시험 통과를 확인했습니다."}},
            {"type": "assistant.turn_end", "timestamp": "2026-11-20T08:10:12.500Z", "data": {}},
        ]
        ev.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in events) + "\n", encoding="utf-8")
        (self.home / ".copilot" / "open-sessions-state.json").write_text(json.dumps({
            sid: {"schemaVersion": 1, "working": False}
        }), encoding="utf-8")

        sessions = self.run_pmap("list")
        cp_list = [s for s in sessions if s.get("agent") == "copilot"]
        self.assertEqual(len(cp_list), 1)
        self.assertEqual(cp_list[0]["session"], f"copilot:{sid}")
        self.assertEqual(cp_list[0]["name"], "코파일럿 테스트")

        mp = self.run_pmap("map", "--session", f"copilot:{sid}")
        self.assertEqual(mp["agent"], "copilot")
        self.assertEqual(mp["status"], "idle")
        self.assertIn("gpt-5.4", mp["phases"][0]["name"])
        card = mp["phases"][0]["cards"][0]
        self.assertIn("README.md", card["turns"][0]["files"])
        self.assertEqual(card["turns"][0]["tests"], "통과")
        self.assertIn("오타를 수정하고", card["done_text"])


if __name__ == "__main__":
    unittest.main()
