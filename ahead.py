"""앞으로 할 일 — 남은 일을 뽑고 고르는 순수 함수.

파일 · 기록을 직접 읽지 않는다. pmap.py 가 세션 기록에서 재료를 넘겨주면
할 일 목록 · 계획서 · 계획 모드 계획에서 남은 일을 뽑아 지도에 붙일 꼴로 만든다.
"""
import re

STATE_OF = {"pending": "todo", "in_progress": "working", "completed": "done"}
CREATED = re.compile(r"Task #(\d+) created")


# ── 할 일 목록(TaskCreate · TaskUpdate · TodoWrite) ─────────────────────

def new_tasks():
    return {"items": {}, "pending": {}, "todo": None}


def on_task_tool(tasks, tool_id, name, inp, ts):
    """할 일 도구 호출 하나를 상태에 반영한다."""
    inp = inp or {}
    if name == "TaskCreate":
        tasks["pending"][tool_id] = {"title": str(inp.get("subject") or "").strip(), "ts": ts}
    elif name == "TaskUpdate":
        item = tasks["items"].get(str(inp.get("taskId")))
        if item is None:
            return
        status = inp.get("status")
        if status == "deleted":
            tasks["items"].pop(str(inp.get("taskId")), None)
            return
        if status in STATE_OF:
            item["state"] = STATE_OF[status]
        if inp.get("subject"):
            item["title"] = str(inp["subject"]).strip()
    elif name == "TodoWrite":
        tasks["todo"] = {"ts": ts, "list": [
            {"title": str(x.get("content") or "").strip(), "state": STATE_OF.get(x.get("status"), "todo")}
            for x in (inp.get("todos") or []) if isinstance(x, dict)
        ]}


def on_task_result(tasks, tool_id, text):
    """TaskCreate 결과 글에서 번호를 얻어 목록에 올린다."""
    pend = tasks["pending"].pop(tool_id, None)
    if pend is None:
        return
    m = CREATED.search(text or "")
    if m:
        tasks["items"][m.group(1)] = {"title": pend["title"], "state": "todo", "ts": pend["ts"]}


def task_list(tasks, since_ts):
    """지금 볼 할 일 목록. 옛 꼴(TodoWrite)이 더 최근이면 그것을 쓴다."""
    items = sorted(tasks.get("items", {}).items(), key=lambda kv: int(kv[0]))
    newest = max((v["ts"] or "" for _, v in items), default="")
    todo = tasks.get("todo")
    if todo and (todo.get("ts") or "") >= newest and (since_ts is None or (todo.get("ts") or "") >= since_ts):
        return [{"key": None, "title": x["title"], "state": x["state"]} for x in todo["list"]]
    return [{"key": None, "title": v["title"], "state": v["state"]}
            for _, v in items if since_ts is None or (v["ts"] or "") >= since_ts]
