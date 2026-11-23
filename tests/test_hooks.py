import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = str(REPO / "wwi_hook.py")
PMAP_SCRIPT = str(REPO / "pmap.py")
SETUP_SCRIPT = str(REPO / "scripts" / "herdr_setup.py")


class HookIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wwi-test-")
        self.home = Path(self.tmp) / "home"
        self.home.mkdir()
        self.env = {
            "HOME": str(self.home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HC_TERMINAL_ID": "t42",
            "WWI_SESSION_PID": str(os.getpid()),
            # 이 맥에 실제로 떠 있는 다른 에이전트 세션이 시험에 섞이지 않게
            "WWI_SKIP_LIVE_AGENTS": "1",
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_hook(self, payload, extra_env=None):
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, HOOK_SCRIPT],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
        )

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

    def test_hook_zero_stdout_and_no_secret_leak(self):
        sid = "sess-zero-token"
        r1 = self.run_hook({
            "session_id": sid,
            "hook_event_name": "SessionStart",
            "cwd": "/tmp/work",
            "transcript_path": str(self.home / "tr.jsonl"),
        })
        self.assertEqual(r1.returncode, 0)
        self.assertEqual(r1.stdout, "")
        self.assertEqual(r1.stderr, "")

        r2 = self.run_hook({
            "session_id": sid,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": "tu-1",
            "tool_input": {"command": "API_SECRET=super_secret_token_999 pytest tests/"},
        })
        self.assertEqual(r2.returncode, 0)
        self.assertEqual(r2.stdout, "")

        r3 = self.run_hook({
            "session_id": sid,
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_use_id": "tu-2",
            "tool_input": {"file_path": "/tmp/work/app.py", "content": "PRIVATE_BODY_DO_NOT_SAVE"},
        })
        self.assertEqual(r3.returncode, 0)
        self.assertEqual(r3.stdout, "")

        ev_file = self.home / ".cache" / "where-was-i" / "hooks" / "events" / f"{sid}.jsonl"
        raw = ev_file.read_text(encoding="utf-8")
        self.assertNotIn("super_secret_token_999", raw)
        self.assertNotIn("PRIVATE_BODY_DO_NOT_SAVE", raw)

    def test_old_claude_without_sessions_dir_found_via_hooks(self):
        """~/.claude/sessions 가 없어도 훅만으로 herdr 칸 ↔ 세션 연결과 지도 표시가 된다."""
        sid = "sess-old-claude"
        tr_dir = self.home / ".claude" / "projects" / "-tmp-proj"
        tr_dir.mkdir(parents=True)
        tr = tr_dir / f"{sid}.jsonl"
        tr.write_text("", encoding="utf-8")

        self.run_hook({
            "session_id": sid,
            "hook_event_name": "SessionStart",
            "cwd": "/tmp/proj",
            "transcript_path": str(tr),
        })
        self.run_hook({
            "session_id": sid,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "로그인 버그 고쳐줘",
        })
        self.run_hook({
            "session_id": sid,
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_use_id": "tu-e1",
            "tool_input": {"file_path": "/tmp/proj/auth.py"},
        })

        sessions = self.run_pmap("list")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session"], sid)
        self.assertEqual(sessions[0]["terminal"], "t42")
        self.assertEqual(sessions[0]["status"], "busy")
        self.assertTrue(sessions[0]["hooked"])

        mp = self.run_pmap("map", "--terminal", "t42")
        self.assertEqual(mp["session"], sid)
        self.assertEqual(mp["source"], "hook+log")
        self.assertEqual(mp["status"], "working")
        self.assertEqual(mp["now"][0]["now"], "auth.py 고치는 중")

        # 권한 확인 알림 → asking
        self.run_hook({
            "session_id": sid,
            "hook_event_name": "Notification",
            "notification_type": "permission_prompt",
            "message": "Claude needs permission",
        })
        mp_perm = self.run_pmap("map", "--terminal", "t42")
        self.assertEqual(mp_perm["status"], "asking")
        self.assertIn("권한 확인 기다리는 중", mp_perm["asked"])
        self.assertIn("auth.py", mp_perm["asked"])

        # 도구 끝나고 Stop → idle
        self.run_hook({
            "session_id": sid,
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_use_id": "tu-e1",
            "tool_input": {"file_path": "/tmp/proj/auth.py"},
            "tool_response": "ok",
        })
        self.run_hook({
            "session_id": sid,
            "hook_event_name": "Stop",
            "last_assistant_message": "로그인 버그를 고쳤습니다.",
        })
        mp_idle = self.run_pmap("map", "--terminal", "t42")
        self.assertEqual(mp_idle["status"], "idle")
        self.assertIsNone(mp_idle["asked"])
        self.assertIn("로그인 버그를 고쳤습니다", mp_idle["last_words"])

        # SessionEnd → list 에서 사라짐
        self.run_hook({"session_id": sid, "hook_event_name": "SessionEnd", "reason": "exit"})
        sessions_after = self.run_pmap("list")
        self.assertEqual(sessions_after, [])

    def test_hybrid_subagent_and_ask_user_question(self):
        """JSONL(중간 한글 설명) + 훅(도우미 시작/끝, 질문 상자, 시험 통과)이 자연스럽게 합쳐진다."""
        sid = "sess-hybrid"
        proj_dir = self.home / ".claude" / "projects" / "-tmp-proj"
        sub_dir = proj_dir / sid / "subagents"
        sub_dir.mkdir(parents=True)
        tr = proj_dir / f"{sid}.jsonl"
        sub_tr = sub_dir / "agent-sub123.jsonl"
        sub_meta = sub_dir / "agent-sub123.meta.json"

        # 메인 JSONL: 사용자 지시 + Agent 호출 + 한국어 설명
        ts0 = "2026-11-20T08:00:00.000Z"
        ts1 = "2026-11-20T08:00:05.000Z"
        main_lines = [
            {"type": "user", "timestamp": ts0, "message": {"content": "T1 결제 모듈 고쳐줘"}},
            {"type": "assistant", "timestamp": ts1, "message": {"content": [
                {"type": "text", "text": "네, 결제 모듈 오류를 확인하고 도우미를 띄워 고치겠습니다."},
                {"type": "tool_use", "id": "toolu_agent_1", "name": "Agent", "input": {
                    "description": "T1 결제 모듈 구현", "subagent_type": "general-purpose", "prompt": "결제 모듈을 고쳐라", "model": "sonnet"
                }},
            ]}},
        ]
        tr.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in main_lines) + "\n", encoding="utf-8")
        sub_meta.write_text(json.dumps({"toolUseId": "toolu_agent_1", "agentType": "general-purpose"}), encoding="utf-8")
        sub_tr.write_text("", encoding="utf-8")

        # 훅: 세션 시작 + 도우미 시작 + 도우미 안 시험/구현/통과
        self.run_hook({"session_id": sid, "hook_event_name": "SessionStart", "cwd": "/tmp/proj", "transcript_path": str(tr)})
        self.run_hook({"session_id": sid, "hook_event_name": "SubagentStart", "agent_id": "sub123", "agent_type": "general-purpose"})
        self.run_hook({
            "session_id": sid, "hook_event_name": "PreToolUse", "agent_id": "sub123",
            "tool_name": "Write", "tool_use_id": "s-1", "tool_input": {"file_path": "/tmp/proj/test_pay.py"},
        })
        self.run_hook({
            "session_id": sid, "hook_event_name": "PreToolUse", "agent_id": "sub123",
            "tool_name": "Bash", "tool_use_id": "s-2", "tool_input": {"command": "pytest test_pay.py"},
        })
        self.run_hook({
            "session_id": sid, "hook_event_name": "PostToolUse", "agent_id": "sub123",
            "tool_name": "Bash", "tool_use_id": "s-2",
            "tool_input": {"command": "pytest test_pay.py"}, "tool_response": {"stdout": "1 failed\nFAILED"},
        })
        self.run_hook({
            "session_id": sid, "hook_event_name": "PreToolUse", "agent_id": "sub123",
            "tool_name": "Edit", "tool_use_id": "s-3", "tool_input": {"file_path": "/tmp/proj/pay.py"},
        })

        mp1 = self.run_pmap("map", "--terminal", "t42")
        self.assertEqual(mp1["status"], "working")
        card = mp1["phases"][0]["cards"][0]
        self.assertEqual(card["state"], "working")
        self.assertEqual(card["helpers"][0]["now"], "pay.py 고치는 중")
        # JSONL 의 한국어 첫 문장도 살아 있어야 함
        self.assertIn("결제 모듈 오류를 확인", card["turns"][0]["first"])

        # 도우미 종료 훅 → 메인 JSONL 에 task-notification 이 아직 없어도 즉시 완료 반영
        self.run_hook({
            "session_id": sid, "hook_event_name": "SubagentStop", "agent_id": "sub123",
            "agent_type": "general-purpose", "agent_transcript_path": str(sub_tr),
            "last_assistant_message": "결제 모듈 수정과 시험 통과를 마쳤습니다.",
        })
        mp2 = self.run_pmap("map", "--terminal", "t42")
        card2 = mp2["phases"][0]["cards"][0]
        self.assertEqual(card2["state"], "done")
        self.assertIn("결제 모듈 수정", card2["done_text"])

        # AskUserQuestion 훅 → 즉시 asking + 질문/선택지 표시
        self.run_hook({
            "session_id": sid, "hook_event_name": "PreToolUse",
            "tool_name": "AskUserQuestion", "tool_use_id": "q-1",
            "tool_input": {"questions": [{"question": "배포할까요?", "options": [{"label": "지금 배포"}, {"label": "나중에"}]}]},
        })
        mp3 = self.run_pmap("map", "--terminal", "t42")
        self.assertEqual(mp3["status"], "asking")
        self.assertIn("배포할까요?", mp3["asked"])
        self.assertIn("지금 배포", mp3["asked"])

        # 질문 취소 후 Stop → asking 이 풀려야 함
        self.run_hook({"session_id": sid, "hook_event_name": "Stop", "last_assistant_message": "취소했습니다."})
        mp4 = self.run_pmap("map", "--terminal", "t42")
        self.assertEqual(mp4["status"], "idle")
        self.assertIsNone(mp4["asked"])

    def test_background_subagent_keeps_working_after_main_stop(self):
        """메인 세션이 Stop 되어도 백그라운드 도우미가 살아 있으면 working 으로 유지된다."""
        sid = "sess-bg-sub"
        self.run_hook({"session_id": sid, "hook_event_name": "SessionStart", "cwd": "/tmp/bg"})
        self.run_hook({"session_id": sid, "hook_event_name": "SubagentStart", "agent_id": "bg99", "agent_type": "Explore"})
        self.run_hook({"session_id": sid, "hook_event_name": "PreToolUse", "agent_id": "bg99", "tool_name": "Grep", "tool_use_id": "g1", "tool_input": {"pattern": "foo"}})
        self.run_hook({"session_id": sid, "hook_event_name": "Stop"})

        mp = self.run_pmap("map", "--terminal", "t42")
        self.assertEqual(mp["status"], "working")
        self.assertEqual(mp["phases"][0]["cards"][0]["state"], "working")
        self.assertEqual(mp["phases"][0]["cards"][0]["helpers"][0]["now"], "코드 읽는 중")

        # PermissionRequest 훅 직접 수신
        self.run_hook({
            "session_id": sid, "hook_event_name": "PermissionRequest",
            "tool_name": "Bash", "tool_input": {"command": "git push origin main"},
        })
        mp_p = self.run_pmap("map", "--terminal", "t42")
        self.assertEqual(mp_p["status"], "asking")
        self.assertIn("git push origin main", mp_p["asked"])

    def test_pre_install_jsonl_only_fallback(self):
        """훅 기록이 없는 기존 세션(설치 전 세션)도 기존대로 정확히 동작한다."""
        sid = "sess-legacy"
        sdir = self.home / ".claude" / "sessions"
        pdir = self.home / ".claude" / "projects" / "-tmp-old"
        sdir.mkdir(parents=True)
        pdir.mkdir(parents=True)
        tr = pdir / f"{sid}.jsonl"
        tr.write_text(json.dumps({
            "type": "user", "timestamp": "2026-11-20T07:00:00.000Z",
            "message": {"content": "예전 세션 테스트"},
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        (sdir / f"{os.getpid()}.json").write_text(json.dumps({
            "pid": os.getpid(), "sessionId": sid, "cwd": "/tmp/old", "status": "idle", "name": "레거시", "nameSource": "user",
        }), encoding="utf-8")

        mp = self.run_pmap("map", "--session", sid)
        self.assertEqual(mp["source"], "log")
        self.assertEqual(mp["status"], "idle")
        self.assertEqual(mp["name"], "레거시")

    def test_settings_json_install_idempotent_and_uninstall_preserves_buddy(self):
        """herdr_setup 의 훅 설치/재설치/제거가 버디 · 한국어 훅을 절대 건드리지 않는다."""
        sys.path.insert(0, str(REPO / "scripts"))
        import herdr_setup

        # 견본은 시험 안에만 둔다(개인 맥의 설정 파일에 기대면 그 파일이 바뀔 때 시험이 깨진다 — 11-23)
        orig = {
            "env": {"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"},
            "hooks": {
                "SessionStart": [{"hooks": [{"type": "command", "command": "bash buddy/session_start.sh", "timeout": 10}]}],
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 buddy/pre_tool.py", "timeout": 5}]},
                               {"hooks": [{"type": "command", "command": "bash ~/.claude/hooks/korean-only.sh", "timeout": 5}]}],
                "Stop": [{"hooks": [{"type": "command", "command": "bash ~/.claude/hooks/closing.sh"}]}],
            },
            "language": "korean",
        }
        # 테스트용 settings.json 준비
        spath = self.home / ".claude" / "settings.json"
        spath.parent.mkdir(parents=True, exist_ok=True)
        spath.write_text(json.dumps(orig, ensure_ascii=False, indent=2), encoding="utf-8")

        # 1) 첫 설치
        ok = herdr_setup.install_hooks(settings_path=str(spath), hook_script=HOOK_SCRIPT)
        self.assertTrue(ok)
        self.assertTrue(Path(str(spath) + ".bak-where-was-i").exists())
        after1 = json.loads(spath.read_text(encoding="utf-8"))
        for ev_name, _, _, _ in herdr_setup.HOOK_SPECS:
            ours = [h for g in after1["hooks"][ev_name] for h in g["hooks"] if "wwi_hook.py" in h.get("command", "")]
            self.assertEqual(len(ours), 1, msg=f"{ev_name} should have exactly 1 wwi hook")

        # 2) 다른 경로로 재설치(update 시나리오) — 중복 없어야 함
        ok2 = herdr_setup.install_hooks(settings_path=str(spath), hook_script=HOOK_SCRIPT)
        self.assertTrue(ok2)
        after2 = json.loads(spath.read_text(encoding="utf-8"))
        for ev_name, _, _, _ in herdr_setup.HOOK_SPECS:
            ours = [h for g in after2["hooks"][ev_name] for h in g["hooks"] if "wwi_hook.py" in h.get("command", "")]
            self.assertEqual(len(ours), 1, msg=f"{ev_name} duplicated on reinstall")

        # 3) 제거 — 원래 settings.json 과 완전히 같아야 함
        removed = herdr_setup.uninstall_hooks(settings_path=str(spath))
        self.assertEqual(removed, len(herdr_setup.HOOK_SPECS))
        self.assertTrue(Path(str(spath) + ".bak-where-was-i-uninstall").exists())
        after_un = json.loads(spath.read_text(encoding="utf-8"))
        self.assertEqual(after_un, orig)


if __name__ == "__main__":
    unittest.main()
