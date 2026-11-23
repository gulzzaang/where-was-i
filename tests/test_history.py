import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import history  # noqa: E402

PMAP_SCRIPT = str(REPO / "pmap.py")


def said(ts, text):
    return {"type": "user", "timestamp": ts, "message": {"content": text}}


def ai(ts, text):
    return {"type": "assistant", "timestamp": ts, "message": {"content": [{"type": "text", "text": text}]}}


def tool_use(ts, tid, name, inp):
    return {"type": "assistant", "timestamp": ts, "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def tool_result(ts, tid, text):
    return {"type": "user", "timestamp": ts, "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wwi-hist-")
        self.home = Path(self.tmp) / "home"
        self.proj = self.home / ".claude" / "projects" / "-tmp-x"
        self.proj.mkdir(parents=True)
        self.env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "WWI_SKIP_LIVE_AGENTS": "1"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, sid, rows):
        p = self.proj / f"{sid}.jsonl"
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        return p

    def run_pmap(self, *args):
        r = subprocess.run([sys.executable, PMAP_SCRIPT, *args], text=True, capture_output=True, env=self.env, timeout=30)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        return r.stdout

    def sample(self):
        return [
            said("2026-11-23T01:00:00.000Z", "포트폴리오 상세 페이지 만들어줘"),
            ai("2026-11-23T01:00:05.000Z", "상세 페이지를 만들겠습니다. 이대로 올릴까요?"),
            tool_use("2026-11-23T01:01:00.000Z", "b1", "Bash", {"command": "git commit -m \"ADD: 상세 페이지\""}),
            said("2026-11-23T01:02:00.000Z", "ㅇㅇ"),
            ai("2026-11-23T01:02:05.000Z", "올렸습니다."),
            tool_use("2026-11-23T01:03:00.000Z", "q1", "AskUserQuestion", {"questions": [{"question": "사진을 바꿀까요?", "options": [{"label": "바꾸기"}, {"label": "두기"}]}]}),
            tool_result("2026-11-23T01:04:00.000Z", "q1", 'User has answered your questions: "사진을 바꿀까요?"="바꾸기". You can now continue.'),
            said("2026-11-23T01:05:00.000Z", "아니 왜 확인 안 하고 올렸어"),
            ai("2026-11-23T01:05:05.000Z", "죄송합니다. 확인하지 않고 올렸습니다."),
        ]

    def test_scan_turns_kinds_and_commits(self):
        p = self.write("s1", self.sample())
        st = history.scan(str(p), {})
        kinds = [(t["said"], t["kind"]) for t in st["turns"]]
        self.assertEqual(kinds, [("포트폴리오 상세 페이지 만들어줘", "direct"), ("ㅇㅇ", "answer"), ("바꾸기", "answer"), ("아니 왜 확인 안 하고 올렸어", "direct")])
        self.assertEqual(st["turns"][1]["asked"], "이대로 올릴까요?")
        self.assertEqual(st["turns"][2]["asked"], "사진을 바꿀까요? — 바꾸기 / 두기")
        self.assertEqual(st["turns"][0]["commits"], ["ADD: 상세 페이지"])
        self.assertEqual(st["turns"][3]["first"], "죄송합니다.")
        # 이어 읽기: 새 줄만 더해진다
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(said("2026-11-23T02:00:00.000Z", "다음 일"), ensure_ascii=False) + "\n")
        st = history.scan(str(p), st)
        self.assertEqual(len(st["turns"]), 5)

    def test_nags_group_similar_corrections(self):
        turns = [
            {"ts": "2026-11-20T01:00:00Z", "said": "아니 왜 확인 안 하고 올렸어", "first": "죄송합니다.", "sid": "a", "name": "가"},
            {"ts": "2026-11-21T01:00:00Z", "said": "또 확인 안 하고 올렸네", "first": "죄송합니다. 다시 확인하겠습니다.", "sid": "b", "name": "나"},
            {"ts": "2026-11-22T01:00:00Z", "said": "포트폴리오 만들어줘", "first": "만들겠습니다.", "sid": "a", "name": "가"},
            {"ts": "2026-11-22T02:00:00Z", "said": "영어로 쓰지 마", "first": "죄송합니다. 한국어로 쓰겠습니다.", "sid": "c", "name": "다"},
        ]
        groups = history.nags(turns)
        self.assertEqual([g["count"] for g in groups], [2, 1])
        self.assertEqual(groups[0]["latest"]["said"], "또 확인 안 하고 올렸네")

    def test_instructions_with_conditions_are_not_corrections(self):
        turns = [
            {"ts": "2026-11-20T01:00:00Z", "said": "실제로 설치하지 말고 확인만 해줘", "first": "확인하겠습니다.", "sid": "a", "name": "가"},
            {"ts": "2026-11-20T02:00:00Z", "said": "버디 노트로 설치해줘. 시험이라 실제 설치 명령은 돌리지 말고 무엇을 할지만 말해 " * 8, "first": "알겠습니다.", "sid": "a", "name": "가"},
            {"ts": "2026-11-20T03:00:00Z", "said": "아니 4코어는 무료라고 몇번을 말하냐", "first": "맞습니다.", "sid": "a", "name": "가"},
        ]
        self.assertEqual([g["latest"]["said"] for g in history.nags(turns)], ["아니 4코어는 무료라고 몇번을 말하냐"])

    def test_journal_splits_direct_and_answers(self):
        self.write("s1", self.sample())
        out = self.run_pmap("journal", "--date", "2026-11-23")
        self.assertIn("# where was i 일지 — 2026-11-23", out)
        self.assertIn("### 직접 지시", out)
        self.assertIn("### AI 제안에 답함", out)
        self.assertIn("「ㅇㅇ」 ← 이대로 올릴까요?", out)
        self.assertIn("커밋: ADD: 상세 페이지", out)

    def test_journal_out_writes_file(self):
        self.write("s1", self.sample())
        outdir = Path(self.tmp) / "out"
        self.run_pmap("journal", "--date", "2026-11-23", "--out", str(outdir))
        self.assertTrue((outdir / "where was i 일지 2026-11-23.md").exists())

    def test_find_matches_all_words(self):
        self.write("s1", self.sample())
        self.write("s2", [said("2026-11-22T01:00:00.000Z", "캐스팅 상세 페이지 고쳐줘")])
        out = json.loads(self.run_pmap("find", "상세", "페이지", "--json"))
        self.assertEqual(sorted(x["said"] for x in out), ["캐스팅 상세 페이지 고쳐줘", "포트폴리오 상세 페이지 만들어줘"])
        out = json.loads(self.run_pmap("find", "캐스팅", "--json"))
        self.assertEqual([x["sid"] for x in out], ["s2"])

    def test_nags_command(self):
        self.write("s1", self.sample())
        out = json.loads(self.run_pmap("nags", "--days", "36500"))
        self.assertEqual(out["groups"][0]["latest"]["said"], "아니 왜 확인 안 하고 올렸어")


if __name__ == "__main__":
    unittest.main()
