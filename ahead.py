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


# ── 계획서 · 계획 모드 계획 ───────────────────────────────────────

TASK_HEAD = re.compile(r"^#{2,3}\s*Task\s*(\d+[A-Za-z]?)(?!\d)(?!\.\d)\s*[:：—–\-.]?\s*(.*)$")
# 흔한 구역 이름은 단계가 아니다(계획 모드 계획의 Context · Verification 등)
SECTION_NAMES = {"context", "background", "goal", "goals", "overview", "summary", "approach", "verification",
                 "testing", "tests", "notes", "risks", "open questions", "references", "배경", "목표", "개요",
                 "요약", "확인", "검증", "시험", "메모", "참고", "위험", "질문"}
ANY_HEAD = re.compile(r"^(#{1,3})\s+(.+)$")
CHECK = re.compile(r"^\s*[-*]\s*\[( |x|X)\]")
NUMBERED = re.compile(r"^(\d+)[.)]\s+(.+)$")


def _clean_title(s):
    s = re.sub(r"\*\*|__|`", "", s or "").strip()
    s = re.sub(r"[:：]\s*$", "", s).strip()
    return s[:80]


def plan_steps(md_text):
    """계획 글에서 단계를 뽑는다: Task 제목 → 번호 목록 → ## 제목 순."""
    lines = (md_text or "").splitlines()
    steps, cur, fence = [], None, False
    for ln in lines:
        if ln.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:  # 코드 펜스 안 예시는 단계 · 체크 칸이 아니다
            continue
        m = TASK_HEAD.match(ln)
        if m:
            cur = {"key": "T" + m.group(1), "title": _clean_title(m.group(2)) or "Task " + m.group(1), "checks": [0, 0]}
            steps.append(cur)
            continue
        if cur is not None:
            if ANY_HEAD.match(ln) and len(ANY_HEAD.match(ln).group(1)) <= 3:
                cur = None
                continue
            c = CHECK.match(ln)
            if c:
                cur["checks"][1] += 1
                if c.group(1) in "xX":
                    cur["checks"][0] += 1
    if steps:
        return steps
    nums = [t for t in (_clean_title(m.group(2)) for m in (NUMBERED.match(ln) for ln in lines) if m) if t]
    if len(nums) >= 2:
        return [{"key": None, "title": t, "checks": [0, 0]} for t in nums]
    heads = [t for t in (_clean_title(m.group(2)) for m in (ANY_HEAD.match(ln) for ln in lines) if m and len(m.group(1)) == 2)
             if t and t.lower() not in SECTION_NAMES]
    return [{"key": None, "title": t, "checks": [0, 0]} for t in heads] if len(heads) >= 2 else []


# ── 끝남 짐작과 고르기 ─────────────────────────────────────────────

def _num(key):
    return key[1:] if key and key.startswith("T") else None


def _task_re(n, bare_t=False):
    """Task N(또는 TN) — 앞뒤가 영숫자가 아니면 된다(한글 조사가 바로 붙어도 됨, T10 은 T1 이 아님)."""
    head = r"(?:Task\s*|T)" if bare_t else r"Task\s*"
    return re.compile(r"(?<![0-9A-Za-z])" + head + re.escape(n) + r"(?![0-9A-Za-z])")


def _mentions(text, n):
    return bool(_task_re(n, bare_t=True).search(text or ""))


def guess_states(steps, helpers, commits, tasks):
    """계획 단계마다 상태를 짐작한다(설계 순서: 할 일 목록 → 체크 칸 → 도우미 → 커밋 → 뒤 단계)."""
    out = []
    for s in steps:
        n = _num(s.get("key"))
        state, exact = None, False
        if n:
            hit = next((t for t in tasks if _task_re(n).search(t.get("title") or "")), None)
            if hit:
                state, exact = hit["state"], True
        checks = s.get("checks") or [0, 0]
        if state is None and checks[1] > 0 and checks[0] >= checks[1]:
            state = "done"
        if state is None and n:
            mine = [h for h in helpers if (h.get("key") or "").lower() == s["key"].lower()]
            if mine:
                last = mine[-1]
                if last.get("running"):
                    state = "working"
                elif last.get("ended") and last["ended"] != "completed":
                    state = "stopped"
                else:
                    state = "done"
        if state is None and n and any(_mentions(c, n) for c in commits):
            state = "done"
        out.append(dict(s, state=state or "todo", exact=exact))
    later = False
    for s in reversed(out):
        if s["state"] == "todo" and later:
            s["state"], s["exact"] = "done", False
        if s["state"] in ("working", "done", "stopped"):
            later = True
    return out


def _pack(source, guess, items):
    left = [x for x in items if x["state"] != "done"]
    if not left:
        return None
    first = next((x for x in left if x["state"] == "working"), None) or next((x for x in left if x["state"] == "todo"), None) or left[0]
    return {"source": source, "guess": guess, "items": items, "left": len(left),
            "next": (first["key"] + " " + first["title"]) if first.get("key") else first["title"],
            "next_exact": bool(first.get("exact"))}


def build_ahead(plan, tasks, helpers, commits):
    """계획(계획서 · 계획 모드)에 남은 단계가 있으면 그것, 아니면 할 일 목록, 둘 다 없으면 None."""
    if plan and plan.get("steps") and tasks and not any(s.get("key") for s in plan["steps"]):
        # 번호 없는 계획은 끝을 짐작할 신호가 없다 → 정확한 할 일 목록이 있으면 그것만 본다
        return _pack("할 일 목록", False, [dict(t, exact=True) for t in tasks])
    if plan and plan.get("steps"):
        packed = _pack(plan.get("source") or "계획서", True, guess_states(plan["steps"], helpers, commits, tasks))
        if packed:
            return packed
    if tasks:
        return _pack("할 일 목록", False, [dict(t, exact=True) for t in tasks])
    return None
