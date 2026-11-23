import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ahead  # noqa: E402


def create(t, tool_id, subject, ts, number):
    ahead.on_task_tool(t, tool_id, "TaskCreate", {"subject": subject, "description": "", "activeForm": ""}, ts)
    ahead.on_task_result(t, tool_id, f"Task #{number} created successfully: {subject}")


class TaskListTests(unittest.TestCase):
    def test_task_create_update_flow(self):
        t = ahead.new_tasks()
        create(t, "tu1", "시험 쓰기", "2026-11-23T01:00:00Z", 1)
        create(t, "tu2", "구현", "2026-11-23T01:01:00Z", 2)
        ahead.on_task_tool(t, "tu3", "TaskUpdate", {"taskId": "1", "status": "completed"}, "2026-11-23T01:02:00Z")
        ahead.on_task_tool(t, "tu4", "TaskUpdate", {"taskId": "2", "status": "in_progress", "subject": "구현하기"}, "2026-11-23T01:03:00Z")
        self.assertEqual(ahead.task_list(t, None), [
            {"key": None, "title": "시험 쓰기", "state": "done"},
            {"key": None, "title": "구현하기", "state": "working"},
        ])

    def test_deleted_task_is_removed(self):
        t = ahead.new_tasks()
        create(t, "tu1", "지울 것", "2026-11-23T01:00:00Z", 1)
        ahead.on_task_tool(t, "tu2", "TaskUpdate", {"taskId": "1", "status": "deleted"}, "2026-11-23T01:01:00Z")
        self.assertEqual(ahead.task_list(t, None), [])

    def test_unknown_or_unfinished_ids_are_ignored(self):
        t = ahead.new_tasks()
        ahead.on_task_tool(t, "tu1", "TaskCreate", {"subject": "결과 없음"}, "2026-11-23T01:00:00Z")
        ahead.on_task_tool(t, "tu2", "TaskUpdate", {"taskId": "9", "status": "completed"}, "2026-11-23T01:01:00Z")
        ahead.on_task_result(t, "nope", "Task #3 created successfully: 모르는 것")
        self.assertEqual(ahead.task_list(t, None), [])

    def test_since_filters_old_items(self):
        t = ahead.new_tasks()
        create(t, "tu1", "예전 일", "2026-11-23T01:00:00Z", 1)
        create(t, "tu2", "새 일", "2026-11-23T03:00:00Z", 2)
        self.assertEqual(ahead.task_list(t, "2026-11-23T02:00:00Z"), [{"key": None, "title": "새 일", "state": "todo"}])

    def test_todowrite_latest_snapshot(self):
        t = ahead.new_tasks()
        ahead.on_task_tool(t, "tw1", "TodoWrite", {"todos": [{"content": "시험", "status": "completed", "activeForm": ""}]}, "2026-11-23T01:00:00Z")
        ahead.on_task_tool(t, "tw2", "TodoWrite", {"todos": [{"content": "README", "status": "pending", "activeForm": ""}]}, "2026-11-23T01:05:00Z")
        self.assertEqual(ahead.task_list(t, None), [{"key": None, "title": "README", "state": "todo"}])


PLAN_MD = """# 추천 도우미 계획
### Task 1: 최신 main 따라잡기
- [x] **Step 1:** 합치기
- [ ] **Step 2:** 시험
### Task 2 — 칸 이름 바꾸기
- [ ] 바꾸기
"""


class PlanStepsTests(unittest.TestCase):
    def test_plan_task_headings_and_checks(self):
        self.assertEqual(ahead.plan_steps(PLAN_MD), [
            {"key": "T1", "title": "최신 main 따라잡기", "checks": [1, 2]},
            {"key": "T2", "title": "칸 이름 바꾸기", "checks": [0, 1]},
        ])

    def test_plan_mode_numbered_list(self):
        steps = ahead.plan_steps("# 계획\n\n1. 설정 읽기\n2. 칸 고치기\n   1. 세부\n")
        self.assertEqual([s["title"] for s in steps], ["설정 읽기", "칸 고치기"])
        self.assertTrue(all(s["key"] is None for s in steps))

    def test_plan_without_steps_gives_empty(self):
        self.assertEqual(ahead.plan_steps("그냥 설명 글입니다."), [])


def steps(*keys, checks=None):
    return [{"key": k, "title": f"{k} 일", "checks": list((checks or {}).get(k, [0, 0]))} for k in keys]


class GuessTests(unittest.TestCase):
    def test_guess_from_helpers_and_commits(self):
        helpers = [{"key": "T2", "running": False, "ended": "completed"}, {"key": "T3", "running": True, "ended": ""}]
        out = ahead.guess_states(steps("T1", "T2", "T3", "T4"), helpers, [], [])
        self.assertEqual([s["state"] for s in out], ["done", "done", "working", "todo"])

    def test_guess_commit_and_stopped(self):
        out = ahead.guess_states(steps("T1", "T2"), [{"key": "T2", "running": False, "ended": "failed"}], ["ADD: Task 1 끝"], [])
        self.assertEqual([s["state"] for s in out], ["done", "stopped"])

    def test_task_list_overrides_plan_guess(self):
        out = ahead.guess_states(steps("T1", checks={"T1": [0, 2]}), [], [],
                                 [{"key": None, "title": "Task 1 설정 읽기", "state": "working"}])
        self.assertEqual(out[0]["state"], "working")
        self.assertTrue(out[0]["exact"])

    def test_build_ahead_prefers_plan_then_tasks(self):
        tasks = [{"key": None, "title": "README", "state": "todo"}]
        plan = {"source": "계획서", "steps": steps("T1", "T2", "T3")}
        a = ahead.build_ahead(plan, tasks, [{"key": "T2", "running": False, "ended": "completed"}], [])
        self.assertEqual(a["source"], "계획서")
        self.assertTrue(a["guess"])
        self.assertEqual(a["next"], "T3 T3 일")
        self.assertEqual(a["left"], 1)
        done_plan = {"source": "계획서", "steps": steps("T1", checks={"T1": [2, 2]})}
        b = ahead.build_ahead(done_plan, tasks, [], [])
        self.assertEqual((b["source"], b["guess"], b["left"], b["next"]), ("할 일 목록", False, 1, "README"))
        self.assertTrue(b["items"][0]["exact"])
        self.assertIsNone(ahead.build_ahead(None, [], [], []))
        self.assertIsNone(ahead.build_ahead(None, [{"key": None, "title": "끝난 일", "state": "done"}], [], []))


class ReviewFixTests(unittest.TestCase):
    def test_keyless_plan_yields_to_task_list(self):
        plan = {"source": "계획 모드", "steps": [{"key": None, "title": "읽기", "checks": [0, 0]}, {"key": None, "title": "고치기", "checks": [0, 0]}]}
        done = [{"key": None, "title": "읽기", "state": "done"}]
        self.assertIsNone(ahead.build_ahead(plan, done, [], []))
        a = ahead.build_ahead(plan, [], [], [])
        self.assertEqual((a["source"], a["left"]), ("계획 모드", 2))

    def test_section_headings_are_not_steps(self):
        self.assertEqual(ahead.plan_steps("# 계획\n## Context\n설명\n## Verification\n확인\n"), [])
        self.assertEqual(ahead.plan_steps("# 계획\n\n1. 하나뿐\n"), [])

    def test_korean_particle_after_number(self):
        out = ahead.guess_states(steps("T1", "T2"), [], ["Task 1을 끝냄"], [{"key": None, "title": "Task 2를 하는 중", "state": "working"}])
        self.assertEqual([s["state"] for s in out], ["done", "working"])
        self.assertEqual([s["key"] for s in ahead.plan_steps("### Task 1.5: 중간\n### Task 2: 둘\n")], ["T2"])

    def test_inferred_done_is_not_exact(self):
        out = ahead.guess_states(steps("T1", "T2"), [{"key": "T2", "running": True, "ended": ""}], [],
                                 [{"key": None, "title": "Task 1 읽기", "state": "todo"}])
        self.assertEqual((out[0]["state"], out[0]["exact"]), ("done", False))


if __name__ == "__main__":
    unittest.main()
