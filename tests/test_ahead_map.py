import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PMAP_SCRIPT = str(REPO / "pmap.py")

PLAN = """# 설치 계획
### Task 1: 설정 읽기
- [ ] 읽기
### Task 2: 칸 고치기
- [ ] 고치기
### Task 3: README
- [ ] 쓰기
"""


def tool_use(ts, tid, name, inp):
    return {"type": "assistant", "timestamp": ts, "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def tool_result(ts, tid, text):
    return {"type": "user", "timestamp": ts, "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}


def said(ts, text):
    return {"type": "user", "timestamp": ts, "message": {"content": text}}


class AheadMapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wwi-ahead-")
        self.home = Path(self.tmp) / "home"
        self.home.mkdir()
        self.env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "WWI_SKIP_LIVE_AGENTS": "1"}
        self.sid = "sess-ahead"
        self.pdir = self.home / ".claude" / "projects" / "-tmp-ahead"
        self.pdir.mkdir(parents=True)
        sdir = self.home / ".claude" / "sessions"
        sdir.mkdir(parents=True)
        (sdir / f"{os.getpid()}.json").write_text(json.dumps({
            "pid": os.getpid(), "sessionId": self.sid, "cwd": "/tmp/ahead", "status": "idle", "name": "앞으로", "nameSource": "user",
        }), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, lines):
        tr = self.pdir / f"{self.sid}.jsonl"
        tr.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
        return tr

    def run_map(self):
        res = subprocess.run([sys.executable, PMAP_SCRIPT, "map", "--session", self.sid],
                             text=True, capture_output=True, env=self.env, timeout=20)
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        return json.loads(res.stdout)

    def plan_lines(self, write_file):
        path = self.home / "proj" / "docs" / "plans" / "2026-11-23-x.md"
        if write_file:
            path.parent.mkdir(parents=True)
            path.write_text(PLAN, encoding="utf-8")
        return [
            said("2026-11-23T01:00:00.000Z", "계획 짜고 진행해"),
            tool_use("2026-11-23T01:01:00.000Z", "w1", "Write", {"file_path": str(path), "content": PLAN}),
            tool_result("2026-11-23T01:01:01.000Z", "w1", "File created"),
            tool_use("2026-11-23T01:02:00.000Z", "ag1", "Agent", {"description": "Task 1: 설정 읽기", "subagent_type": "general-purpose", "prompt": "읽어"}),
            tool_result("2026-11-23T01:05:00.000Z", "ag1", "설정을 다 읽었습니다."),
        ]

    def test_task_list_reaches_map(self):
        self.write([
            said("2026-11-23T01:00:00.000Z", "할 일 두 개"),
            tool_use("2026-11-23T01:01:00.000Z", "tc1", "TaskCreate", {"subject": "시험 쓰기", "description": ""}),
            tool_result("2026-11-23T01:01:01.000Z", "tc1", "Task #1 created successfully: 시험 쓰기"),
            tool_use("2026-11-23T01:02:00.000Z", "tc2", "TaskCreate", {"subject": "구현", "description": ""}),
            tool_result("2026-11-23T01:02:01.000Z", "tc2", "Task #2 created successfully: 구현"),
            tool_use("2026-11-23T01:03:00.000Z", "tu1", "TaskUpdate", {"taskId": "1", "status": "completed"}),
        ])
        a = self.run_map()["ahead"]
        self.assertEqual((a["source"], a["left"], a["next"]), ("할 일 목록", 1, "구현"))

    def test_plan_file_and_helper_task(self):
        self.write(self.plan_lines(write_file=True))
        a = self.run_map()["ahead"]
        self.assertEqual(a["source"], "계획서")
        self.assertEqual([x["state"] for x in a["items"]], ["done", "todo", "todo"])
        self.assertEqual(a["next"], "T2 칸 고치기")

    def test_plan_file_missing_uses_recorded_steps(self):
        self.write(self.plan_lines(write_file=False))
        a = self.run_map()["ahead"]
        self.assertEqual([x["state"] for x in a["items"]], ["done", "todo", "todo"])

    def test_plan_mode_plan(self):
        self.write([
            said("2026-11-23T01:00:00.000Z", "계획 모드로"),
            tool_use("2026-11-23T01:01:00.000Z", "pm1", "ExitPlanMode", {"plan": "# 계획\n\n1. 설정 읽기\n2. 칸 고치기\n"}),
        ])
        a = self.run_map()["ahead"]
        self.assertEqual((a["source"], a["left"]), ("계획 모드", 2))

    def test_no_material_gives_null(self):
        self.write([said("2026-11-23T01:00:00.000Z", "그냥 질문")])
        self.assertIsNone(self.run_map()["ahead"])

    def test_old_cache_is_reread(self):
        tr = self.write([
            said("2026-11-23T01:00:00.000Z", "할 일"),
            tool_use("2026-11-23T01:01:00.000Z", "tc1", "TaskCreate", {"subject": "남은 일", "description": ""}),
            tool_result("2026-11-23T01:01:01.000Z", "tc1", "Task #1 created successfully: 남은 일"),
        ])
        cache = self.home / ".cache" / "where-was-i"
        cache.mkdir(parents=True)
        (cache / f"{self.sid}.main.json").write_text(json.dumps({"offset": tr.stat().st_size, "events": []}), encoding="utf-8")
        a = self.run_map()["ahead"]
        self.assertEqual(a["next"], "남은 일")


if __name__ == "__main__":
    unittest.main()
