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


if __name__ == "__main__":
    unittest.main()
