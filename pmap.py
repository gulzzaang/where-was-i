#!/usr/bin/env python3
"""where was i 프로토타입 — Claude Code 훅 기록과 대화 기록 파일을 함께 읽어 세션 하나의 지도 자료(JSON)를 만든다.

훅 기록(토큰 0)으로 herdr 칸 ↔ 세션 짝짓기 · 도우미 시작/끝 · 도구/질문/권한 대기 상태를 정확히 잡고,
AI 중간 글과 설치 전 세션은 대화 기록 파일(JSONL)에서 이어 채운다.
쓰는 법:
  python3 pmap.py list                     떠 있는 Claude 세션 목록(JSON)
  python3 pmap.py map --terminal t29       herdr 칸 번호로 세션 지도
  python3 pmap.py map --session <id>       세션 id 로 지도
"""

import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ahead  # noqa: E402  남은 일을 뽑고 고르는 순수 함수

HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
KST = timezone(timedelta(hours=9))
CACHE = os.path.join(HOME, ".cache", "where-was-i")
HOOK_DIR = os.path.join(CACHE, "hooks")
HOOK_SESSIONS = os.path.join(HOOK_DIR, "sessions")
HOOK_EVENTS = os.path.join(HOOK_DIR, "events")
RUNNING_SEC = 120
SESSION_ALIVE = True  # main() 이 정한다: 세션이 아직 떠 있는지  # 도우미 기록이 이 안에 바뀌었으면 「일하는 중」

TEST_FILE = re.compile(r"(_test\.|\.test\.|\.spec\.|/tests?/|test_[^/]*\.py$)")
TEST_CMD = re.compile(r"(go test|pytest|unittest|vitest|jest|pnpm (run )?test|npm (run )?test|gradlew? .*test|make test)")
FAIL_OUT = re.compile(r"(\bFAIL\b|\bfailed\b|FAILED|Error Trace|panic:|AssertionError|✗)")
PASS_OUT = re.compile(r"((^|\n)ok\s|\bPASS\b|\bpassed\b|BUILD SUCCESSFUL|Ran \d+ tests?[\s\S]*\nOK\b)")
TNUM = re.compile(r"(?:^|[\s(])(?:\d+[A-Z]?\s+)?(?:Task\s*|T)(\d+(?:[-~][^\s]*)?[a-d가-힣]?)(?=[\s:·,)]|$)")
REVIEW = re.compile(r"(검토|review|리뷰)", re.I)
FIX = re.compile(r"(고침|고치기|고쳐|반영|fix)", re.I)
RESEARCH = re.compile(r"(조사|파악|설계|대조|Explore|찾기)", re.I)
PLAN_DESC = re.compile(r"계획서")
PLAN_PATH = re.compile(r"(plans?/|plan-|계획)[^/]*\.md$")
ST_VERSION = 3  # 세션 캐시 꼴 판 — 바뀌면 처음부터 한 번 다시 읽는다


def hangul(s):
    s = str(s or "")
    h = len(re.findall(r"[가-힣]", s))
    return h >= 8 and h >= len(re.findall(r"[A-Za-z]", s)) * 0.4


def clean_md(s, n=420):
    """표 · 머리표 · 굵게를 걷고 읽기 쉬운 줄글로."""
    out = []
    for line in str(s or "").splitlines():
        L = line.strip()
        if not L or L.startswith("|") or re.match(r"^[-:| ]+$", L) or L.startswith("```"):
            continue
        L = re.sub(r"^#+\s*", "", L)
        L = re.sub(r"^[-*]\s+", "· ", L)
        L = re.sub(r"\*\*|`", "", L)
        out.append(L)
    t = " ".join(out)
    return t if len(t) <= n else t[: n - 1] + "…"


def past(now):
    return (str(now).replace("돌리는 중", "돌림").replace("고치는 중", "고침").replace("쓰는 중", "씀")
            .replace("읽는 중", "읽음").replace("커밋하는 중", "커밋").replace("보고하고 끝냄", "보고"))


SHORT_OK = re.compile(r"^(ㅇㅇ|ㄱㄱ|ㅇㅋ|ㄱ|응|ㅇ|네|예|그래|좋아|좋다|진행|고고|ok|오케이|넵|ㄴㄴ|아니|ㅇㅇ\s*ㄱㄱ)", re.I)


def is_short(said):
    t = str(said or "").strip()
    return len(t) <= 15 or (len(t) <= 30 and bool(SHORT_OK.match(t)))


def ask_context(txt):
    """AI 글에서 마지막 물음과 그 둘레의 선택지를 꺼낸다."""
    lines = [l.strip() for l in str(txt or "").splitlines() if l.strip()]
    qi = None
    for i in range(len(lines) - 1, -1, -1):
        if re.search(r"(\?|까요|여쭤|골라 주|정해 주)", lines[i]):
            qi = i
            break
    if qi is None:
        return ""
    def opt(l):
        return re.match(r"^(\*\*|- \*\*|[①-⑨]|[A-D][.)]|\d+[.)]|- )", l) and not re.match(r"^[│┌└├|]", l)
    opts = [l for l in lines[qi + 1: qi + 7] if opt(l)] or [l for l in lines[max(0, qi - 6): qi] if opt(l)]
    def lab(l):
        l = re.sub(r"^(- |\d+[.)]\s*)", "", l)
        l = re.sub(r"\*\*|`", "", l)
        return short(re.split(r"[:：—]| \(", l)[0], 40)
    q = re.sub(r"\*\*|`|^#+\s*|^여쭤볼 것[:：]?\s*", "", lines[qi])
    return short(q + (" — " + " / ".join(lab(o) for o in opts[:4]) if opts else ""), 300)


def kst(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST)
    except Exception:
        return None


def hm(ts):
    d = kst(ts)
    return d.strftime("%H:%M") if d else ""


def md(ts):
    d = kst(ts)
    return d.strftime("%m-%d") if d else ""


def short(s, n=140):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def first_sentence(s, n=140):
    s = re.sub(r"[#*`>|]", "", str(s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    m = re.match(r"(.+?[.!?。]|.+?(니다|어요|해요|했다|한다)\.?)(\s|$)", s)
    return short(m.group(1) if m else s, n)


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def iter_jsonl(path):
    try:
        with open(path, errors="ignore") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except FileNotFoundError:
        return


# ── 훅 기록 읽기 ──────────────────────────────────────────

def safe_sid(sid):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(sid or ""))[:120]


def _apply_hook_tool(ev, ts, tool, info):
    """훅 PreToolUse 한 건을 ev(scan_lines 와 같은 모양)에 반영한다."""
    info = info if isinstance(info, dict) else {}
    if ts:
        ev["first"] = ev["first"] or ts
        ev["last"] = ts
    ev["tools"] = ev.get("tools", 0) + 1
    before = ev.get("now", "")
    act = info.get("act") or ""
    if tool in ("Edit", "Write", "MultiEdit"):
        base = info.get("file") or ""
        fl = ev.setdefault("files", [])
        if base and base not in fl and len(fl) < 40:
            fl.append(base)
        if info.get("test"):
            ev["test_write"] = ev.get("test_write") or ts
        elif ev.get("fail") or ev.get("test_write"):
            ev["impl"] = ev.get("impl") or ts
    elif tool == "Bash":
        step = info.get("step")
        if isinstance(step, list) and len(step) == 2:
            ev["steps"] = ev.get("steps") or {"plan": [], "now": 0}
            if step[0] == "plan" and isinstance(step[1], list):
                ev["steps"]["plan"] = step[1]
            elif step[0] == "now":
                ev["steps"]["now"] = step[1]
            else:
                ev["steps"]["now"] = 999
        if info.get("edit") and (ev.get("fail") or ev.get("test_write")):
            ev["impl"] = ev.get("impl") or ts
    if act:
        ev["now"] = act
        if act != before or not ev.get("acts"):
            acts = ev.setdefault("acts", [])
            if not acts or acts[-1][1] != act:
                acts.append([ts, act])
                del acts[:-12]


def _apply_hook_result(ev, ts, res):
    if ts:
        ev["last"] = ts
    if res == "commit":
        ev["commit"] = ts or ev.get("commit")
        ev["commits"] = ev.get("commits", 0) + 1
    elif res == "fail":
        if not ev.get("pass"):
            ev["fail"] = ev.get("fail") or ts
        ev["tests"] = "실패"
    elif res == "pass":
        ev["pass"] = ts
        ev["tests"] = "통과"


def read_hook_state(sid):
    """세션의 훅 사건 파일(~/.cache/where-was-i/hooks/events/<sid>.jsonl)을 이어 읽어 상태를 만든다."""
    if not sid:
        return None
    ssid = safe_sid(sid)
    ev_path = os.path.join(HOOK_EVENTS, ssid + ".jsonl")
    if not os.path.exists(ev_path):
        return None
    os.makedirs(CACHE, exist_ok=True)
    cpath = os.path.join(CACHE, ssid + ".hookstate.json")
    hst = load_state(cpath, {})
    lines, off, reset = read_new_lines(ev_path, hst.get("offset", 0))
    if reset:
        hst = {}
    if not lines and hst.get("has_hooks"):
        return hst
    hst["has_hooks"] = True
    hst["offset"] = off
    hst.setdefault("subagents", {})
    hst.setdefault("agent_tools", {})
    hst.setdefault("main_ev", new_ev())

    for d in lines:
        ts = d.get("ts") or ""
        ev = d.get("ev") or ""
        if ts:
            hst["first_ts"] = hst.get("first_ts") or ts
            hst["last_ts"] = ts
        if ev == "session_start":
            if d.get("term"):
                hst["terminal"] = d["term"]
            if d.get("cwd"):
                hst["cwd"] = d["cwd"]
            if d.get("tr"):
                hst["transcript"] = d["tr"]
            hst["ended"] = False
            hst["status"] = "idle"
            hst["status_ts"] = ts
            hst["permission"] = None
            hst["asked"] = None
        elif ev == "session_end":
            hst["ended"] = True
            hst["status"] = "ended"
            hst["status_ts"] = ts
            hst["permission"] = None
            for sub in hst["subagents"].values():
                if sub.get("running"):
                    sub["running"] = False
                    sub["ended_at"] = sub.get("ended_at") or ts
        elif ev == "prompt":
            hst["ended"] = False
            hst["status"] = "busy"
            hst["status_ts"] = ts
            hst["asked"] = None
            hst["permission"] = None
            hst["pending_tool"] = None
            hst["last_prompt"] = d.get("said") or ""
            hst["last_prompt_ts"] = ts
            hst["now"] = "생각하는 중"
            hst["now_ts"] = ts
            hst["main_ev"] = new_ev()
        elif ev == "pre_tool":
            tool = d.get("tool") or ""
            info = d.get("info") or {}
            aid = d.get("aid") or ""
            tid = d.get("id") or ""
            if aid:
                sub = hst["subagents"].setdefault(aid, {
                    "agent_id": aid, "agent_type": "", "started_at": ts, "ended_at": None,
                    "running": True, "transcript": "", "last_msg": "", "now": "", "now_ts": ts,
                    "ev": new_ev(),
                })
                if not sub.get("ended_at"):
                    sub["running"] = True
                _apply_hook_tool(sub["ev"], ts, tool, info)
                if info.get("act"):
                    sub["now"] = info["act"]
                    sub["now_ts"] = ts
            else:
                hst["permission"] = None
                if tool == "AskUserQuestion":
                    hst["status"] = "asking"
                    hst["status_ts"] = ts
                    hst["asked"] = info.get("q") or "질문"
                else:
                    hst["status"] = "busy"
                    hst["status_ts"] = ts
                    hst["asked"] = None
                    _apply_hook_tool(hst["main_ev"], ts, tool, info)
                    if info.get("act"):
                        hst["now"] = info["act"]
                        hst["now_ts"] = ts
                    detail = info.get("brief") or info.get("file") or tool
                    hst["pending_tool"] = {"id": tid, "tool": tool, "desc": f"{tool}({detail})" if detail and detail != tool else tool}
                    if tool == "Agent" and tid:
                        hst["agent_tools"][tid] = {
                            "ts": ts, "desc": info.get("desc") or "", "atype": info.get("atype") or "",
                            "model": info.get("model") or "", "done_ts": None,
                        }
        elif ev == "post_tool":
            tool = d.get("tool") or ""
            res = d.get("res") or ""
            aid = d.get("aid") or ""
            tid = d.get("id") or ""
            if aid:
                sub = hst["subagents"].get(aid)
                if sub:
                    _apply_hook_result(sub["ev"], ts, res)
            else:
                hst["permission"] = None
                if tool == "AskUserQuestion":
                    hst["asked"] = None
                    hst["status"] = "busy"
                    hst["status_ts"] = ts
                if tool == "Agent" and tid in hst["agent_tools"] and res != "async":
                    hst["agent_tools"][tid]["done_ts"] = ts
                _apply_hook_result(hst["main_ev"], ts, res)
        elif ev == "tool_fail":
            hst["permission"] = None
            if (d.get("tool") or "") == "AskUserQuestion":
                hst["asked"] = None
        elif ev == "sub_start":
            aid = d.get("aid") or ""
            if aid:
                sub = hst["subagents"].setdefault(aid, {
                    "agent_id": aid, "agent_type": "", "started_at": ts, "ended_at": None,
                    "running": True, "transcript": "", "last_msg": "", "now": "시작함", "now_ts": ts,
                    "ev": new_ev(),
                })
                sub["agent_type"] = d.get("atype") or sub.get("agent_type") or ""
                sub["started_at"] = sub.get("started_at") or ts
                sub["ended_at"] = None
                sub["running"] = True
                sub["now_ts"] = ts
                sub["ev"]["first"] = sub["ev"].get("first") or ts
                sub["ev"]["last"] = ts
        elif ev == "sub_stop":
            aid = d.get("aid") or ""
            if aid:
                sub = hst["subagents"].setdefault(aid, {
                    "agent_id": aid, "agent_type": "", "started_at": ts, "ended_at": ts,
                    "running": False, "transcript": "", "last_msg": "", "now": "끝", "now_ts": ts,
                    "ev": new_ev(),
                })
                sub["agent_type"] = d.get("atype") or sub.get("agent_type") or ""
                sub["ended_at"] = ts
                sub["running"] = False
                if d.get("tr"):
                    sub["transcript"] = d["tr"]
                if d.get("last"):
                    sub["last_msg"] = d["last"]
                sub["now"] = "끝"
                sub["now_ts"] = ts
                sub["ev"]["last"] = ts
        elif ev == "permission":
            hst["status"] = "asking"
            hst["status_ts"] = ts
            hst["permission"] = d.get("msg") or "권한 확인 기다리는 중"
        elif ev == "notify":
            kind = d.get("kind") or ""
            if kind == "permission_prompt":
                hst["status"] = "asking"
                hst["status_ts"] = ts
                pt = hst.get("pending_tool") or {}
                desc = pt.get("desc") or ""
                msg = d.get("msg") or ""
                if desc:
                    hst["permission"] = short(f"권한 확인 기다리는 중 · {desc}", 160)
                else:
                    hst["permission"] = short(msg or "권한 확인 기다리는 중", 160)
            elif kind == "elicitation_dialog":
                hst["status"] = "asking"
                hst["status_ts"] = ts
                hst["asked"] = hst.get("asked") or short(d.get("msg") or "입력을 기다리는 중", 160)
            elif kind == "idle_prompt":
                if not hst.get("asked") and not hst.get("permission"):
                    hst["status"] = "idle"
                    hst["status_ts"] = ts
        elif ev == "stop":
            hst["status"] = "idle"
            hst["status_ts"] = ts
            hst["permission"] = None
            hst["asked"] = None
            if d.get("last"):
                hst["last_stop_msg"] = d["last"]
                hst["last_stop_ts"] = ts

    save_state(cpath, hst)
    return hst


def hook_session_status(hst):
    if not hst:
        return ""
    if hst.get("ended"):
        return "ended"
    if hst.get("permission") or hst.get("asked") or hst.get("status") == "asking":
        return "waiting"
    if hst.get("status") == "busy" or any(x.get("running") for x in (hst.get("subagents") or {}).values()):
        return "busy"
    if hst.get("status") == "idle":
        return "idle"
    return ""


# ── 세션 찾기 ─────────────────────────────────────────────

def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def terminal_of(pid):
    try:
        out = subprocess.run(["ps", "eww", "-p", str(pid)], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"\bHC_TERMINAL_ID=(\S+)", out)
        return m.group(1) if m else None
    except Exception:
        return None


def transcript_of(sid):
    hits = glob.glob(os.path.join(CLAUDE, "projects", "*", sid + ".jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def list_sessions():
    out = []
    by_sid = {}
    for f in glob.glob(os.path.join(CLAUDE, "sessions", "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        pid = d.get("pid")
        if not pid or not pid_alive(pid):
            continue
        sid = d.get("sessionId")
        tr = transcript_of(sid) if sid else None
        item = {
            "pid": str(pid),
            "session": sid,
            "name": d.get("name") or "",
            "named": d.get("nameSource") == "user",
            "status": d.get("status") or "",
            "cwd": d.get("cwd") or "",
            "terminal": terminal_of(pid),
            "updated": (tr and os.path.getmtime(tr)) or (d.get("updatedAt") or 0) / 1000,
            "transcript": tr,
            "hooked": False,
        }
        out.append(item)
        if sid:
            by_sid[sid] = item

    # 훅이 기록한 세션 정보도 함께 읽는다(~/.claude/sessions 가 없는 옛 Claude Code 에서도 동작)
    now_t = time.time()
    for f in glob.glob(os.path.join(HOOK_SESSIONS, "*.json")):
        try:
            hm_ = json.load(open(f))
        except Exception:
            continue
        sid = hm_.get("session_id")
        if not sid or hm_.get("ended"):
            continue
        pid = hm_.get("pid")
        if pid and not pid_alive(pid):
            continue
        hst = read_hook_state(sid)
        if hst and hst.get("ended"):
            continue
        ev_path = os.path.join(HOOK_EVENTS, safe_sid(sid) + ".jsonl")
        ev_mtime = os.path.getmtime(ev_path) if os.path.exists(ev_path) else 0
        last_t = max(hm_.get("updated_at") or 0, ev_mtime)
        if not pid and now_t - last_t > 6 * 3600:
            continue
        tr = hm_.get("transcript_path") or (hst and hst.get("transcript")) or transcript_of(sid)
        term = hm_.get("terminal") or (hst and hst.get("terminal")) or (terminal_of(pid) if pid else None)
        hstatus = hook_session_status(hst)
        tr_mtime = os.path.getmtime(tr) if tr and os.path.exists(tr) else 0
        updated = max(tr_mtime, last_t)
        if sid in by_sid:
            cur = by_sid[sid]
            if not cur.get("terminal") and term:
                cur["terminal"] = term
            if not cur.get("transcript") and tr:
                cur["transcript"] = tr
            if hstatus and (not cur.get("status") or hstatus == "waiting"):
                cur["status"] = hstatus
            cur["updated"] = max(cur.get("updated") or 0, updated)
            cur["hooked"] = True
        else:
            item = {
                "pid": str(pid or ""),
                "session": sid,
                "name": "",
                "named": False,
                "status": hstatus,
                "cwd": hm_.get("cwd") or (hst and hst.get("cwd")) or "",
                "terminal": term,
                "updated": updated,
                "transcript": tr,
                "hooked": True,
            }
            out.append(item)
            by_sid[sid] = item

    # 시험용: 이 맥에 실제로 떠 있는 다른 에이전트(codex · agy · opencode · copilot)는 훑지 않는다
    if os.environ.get("WWI_SKIP_LIVE_AGENTS") == "1":
        out.sort(key=lambda s: -(s["updated"] or 0))
        return out
    for c in codex_procs():
        out.append({"session": "codex:" + os.path.basename(c["rollout"]), "name": "", "named": False,
                    "status": "", "cwd": c.get("cwd") or "", "terminal": c["terminal"], "agent": "codex",
                    "updated": os.path.getmtime(c["rollout"]), "transcript": c["rollout"]})
    for a in antigravity_procs():
        out.append({"session": "agy:" + a["conv_id"], "name": a.get("name") or "", "named": False,
                    "status": a.get("status") or "", "cwd": a.get("cwd") or "", "terminal": a["terminal"],
                    "agent": "antigravity", "updated": a["updated"], "transcript": a["transcript"]})
    for o in opencode_sessions():
        out.append({"session": "opencode:" + o["id"], "name": o.get("title") or "", "named": bool(o.get("title")),
                    "status": o.get("status") or "", "cwd": o.get("cwd") or "", "terminal": o.get("terminal"),
                    "agent": "opencode", "updated": o["updated"], "transcript": o["db"]})
    for cp in copilot_sessions():
        out.append({"session": "copilot:" + cp["id"], "name": cp.get("name") or "", "named": False,
                    "status": cp.get("status") or "", "cwd": cp.get("cwd") or "", "terminal": cp.get("terminal"),
                    "agent": "copilot", "updated": cp["updated"], "transcript": cp["events"],
                    "working_hint": bool(cp.get("working"))})
    out.sort(key=lambda s: -(s["updated"] or 0))
    return out


# ── 도우미 하나 읽기 ──────────────────────────────────────

def helper_kind(desc, agent_type):
    if REVIEW.search(desc):
        return "검토"
    if FIX.search(desc):
        return "고침"
    if agent_type in ("Explore", "Plan") or RESEARCH.search(desc):
        return "조사"
    if PLAN_DESC.search(desc):
        return "계획"
    return "구현"


def read_helper(jsonl):
    return scan_lines(iter_jsonl(jsonl))


def new_ev():
    return {"first": None, "last": None, "test_write": None, "fail": None, "impl": None, "pass": None,
            "commit": None, "steps": None, "now": "", "last_text": "", "tools": 0, "_pending": {}, "acts": [],
            "files": [], "commits": 0, "tests": "", "plan_path": ""}


def scan_lines(lines, ev=None):
    """기록 줄들에서 흔적을 모은다: 시험 쓰기 · 실패 · 구현 · 통과 · 커밋 · 마지막 행동. ev 를 주면 이어서 쌓는다."""
    ev = ev or new_ev()
    pending = ev.setdefault("_pending", {})
    for d in lines:
        ts = d.get("timestamp")
        if ts:
            ev["first"] = ev["first"] or ts
            ev["last"] = ts
        m = d.get("message") or {}
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            t = b.get("type")
            if t == "text" and d.get("type") == "assistant" and b.get("text", "").strip():
                ev["last_text"] = b["text"]
            elif t == "tool_use":
                ev["tools"] += 1
                before = ev["now"]
                name, inp = b.get("name"), b.get("input") or {}
                if name in ("Edit", "Write", "MultiEdit"):
                    p = inp.get("file_path", "")
                    base = os.path.basename(p)
                    if name == "Write" and PLAN_PATH.search(p):
                        ev["plan_path"] = p
                    fl = ev.setdefault("files", [])
                    if base and base not in fl and len(fl) < 40:
                        fl.append(base)
                    if TEST_FILE.search(p):
                        ev["test_write"] = ev["test_write"] or ts
                        ev["now"] = f"시험 파일 {base} 쓰는 중"
                    elif re.search(r"report|보고", base, re.I):
                        ev["now"] = "보고서 쓰는 중"
                    else:
                        if ev["fail"] or ev["test_write"]:
                            ev["impl"] = ev["impl"] or ts
                        ev["now"] = f"{base} 고치는 중"
                elif name == "Bash":
                    c = inp.get("command", "")
                    sm = re.search(r":\s*wwi-step\s+(plan|now|done)\s*(.*?)(?:&&|;|\n|$)", c)
                    if sm:
                        ev["steps"] = ev["steps"] or {"plan": [], "now": 0}
                        if sm.group(1) == "plan":
                            ev["steps"]["plan"] = re.findall(r'"([^"]+)"', sm.group(2))
                        elif sm.group(1) == "now":
                            try:
                                ev["steps"]["now"] = int(sm.group(2).split()[0])
                            except Exception:
                                pass
                        else:
                            ev["steps"]["now"] = 999
                    if "git commit" in c:
                        pending[b.get("id")] = ("commit", ts)
                        ev["now"] = "커밋하는 중"
                    elif TEST_CMD.search(c):
                        pending[b.get("id")] = ("test", ts)
                        ev["now"] = "시험 돌리는 중"
                    elif re.search(r"\b(sed -i|apply_patch|python3? .*write|cat >)", c):
                        if ev["fail"] or ev["test_write"]:
                            ev["impl"] = ev["impl"] or ts
                        ev["now"] = "파일 고치는 중"
                    else:
                        ev["now"] = "명령 돌리는 중"
                elif name in ("Read", "Grep", "Glob"):
                    ev["now"] = "코드 읽는 중"
                elif name == "SubagentHandback":
                    ev["now"] = "보고하고 끝냄"
                    ev["report"] = str(inp.get("message") or "")[:1200]
                if ev["now"] and (ev["now"] != before or not ev["acts"]):
                    acts = ev.setdefault("acts", [])
                    if not acts or acts[-1][1] != ev["now"]:
                        acts.append([ts, ev["now"]])
                        del acts[:-12]
            elif t == "tool_result":
                kind = pending.pop(b.get("tool_use_id"), None)
                if not kind:
                    continue
                out = text_of(b.get("content"))
                if kind[0] == "commit":
                    ev["commit"] = ts or kind[1]
                    ev["commits"] = ev.get("commits", 0) + 1
                elif kind[0] == "test":
                    if FAIL_OUT.search(out):
                        if not ev["pass"]:
                            ev["fail"] = ev["fail"] or ts
                        ev["tests"] = "실패"
                    elif PASS_OUT.search(out):
                        ev["pass"] = ts
                        ev["tests"] = "통과"
    return ev


def helper_cells(kind, ev, running):
    """도우미 하나의 칸 목록 [이름, 상태, 시각, 출처]."""
    if ev["steps"] and ev["steps"]["plan"]:
        plan, now = ev["steps"]["plan"], ev["steps"]["now"]
        return [[p, "done" if i + 1 < now or now == 999 else ("working" if i + 1 == now and running else ("done" if i + 1 == now else "todo")), "", "알림"]
                for i, p in enumerate(plan)]
    if kind in ("검토", "조사", "계획"):
        return [[kind, "working" if running else "done", hm(ev["last"]) if not running else "", "짐작"]]
    names = ["시험 쓰기", "실패 확인", "구현", "통과", "커밋"]
    times = [ev["test_write"], ev["fail"], ev["impl"], ev["pass"], ev["commit"]]
    # 뒤 칸이 끝났으면 앞 칸도 끝
    done = [bool(t) for t in times]
    for i in range(len(done) - 2, -1, -1):
        if done[i + 1]:
            done[i] = True
    if not any(done) and not running:
        return [["끝", "done", hm(ev["last"]), "짐작"]]
    cells = []
    cur_set = False
    for i, n in enumerate(names):
        if done[i]:
            cells.append([n, "done", hm(times[i]), "짐작"])
        elif running and not cur_set:
            cells.append([n, "working", "", "짐작"])
            cur_set = True
        else:
            cells.append([n, "todo" if running else "done", "", "짐작"])
    return cells


# ── 세션 지도 ─────────────────────────────────────────────

def task_key(desc):
    m = TNUM.search(" " + desc)
    return ("T" + m.group(1)) if m else None


def read_new_lines(path, offset):
    """offset 뒤에 붙은 온전한 줄만 읽는다(기록 파일은 뒤에 붙기만 한다)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset, False
    reset = size < offset
    if reset:
        offset = 0
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    cut = data.rfind(b"\n")
    if cut < 0:
        return [], offset, reset
    lines = []
    for raw in data[: cut + 1].splitlines():
        try:
            lines.append(json.loads(raw))
        except Exception:
            continue
    return lines, offset + cut + 1, reset


def load_state(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def save_state(path, st):
    with open(path + ".tmp", "w") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


RELEVANT = {"Skill", "Write", "AskUserQuestion", "Agent", "TaskCreate", "TaskUpdate", "TodoWrite", "ExitPlanMode", "Bash"}


def commit_message(cmd):
    """git commit 명령에서 커밋 글 첫 줄을 꺼낸다(-m \"…\" 또는 heredoc)."""
    m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?\s*\n\s*(\S[^\n]*)", cmd or "")
    if m:
        return m.group(2).strip()
    m = re.search(r"-m\s+([\"'])(.*?)\1", cmd or "", re.S)
    return m.group(2).strip().splitlines()[0] if m and m.group(2).strip() else ""


def open_turn(st, ts, said, asked, kind):
    turns = st.setdefault("turns", [])
    turns.append({"ts": ts, "said": said, "asked": asked, "kind": kind, "first": "", "last": "", "last_ts": None,
                  "helpers": [], "ev": None})
    del turns[:-300]


def turn_out(tn, live):
    ev = tn.get("ev") or {}
    last = clean_md(tn.get("last", ""), 600)
    sents = re.findall(r".+?(?:[.!?。]|니다|어요|해요)(?=\s|$)", last)
    acts = [past(a[1]) for a in (ev.get("acts") or [])][-4:]
    return {"ts": tn["ts"], "time": hm(tn["ts"]), "date": md(tn["ts"]), "said": tn["said"], "short": is_short(tn["said"]),
            "kind": tn.get("kind", ""), "asked": tn.get("asked", ""), "first": tn.get("first", ""),
            "last": short(" ".join(sents[:2]) if sents else last, 200), "last_time": hm(tn.get("last_ts")),
            "helpers": tn.get("helpers", []), "files": ev.get("files", []), "commits": ev.get("commits", 0),
            "tests": ev.get("tests", ""), "acts": acts, "state": "working" if live else "done",
            "hids": tn.get("hids", []), "work": bool(ev.get("files") or ev.get("commits") or ev.get("tests")),
            "acts_full": [[a[0], past(a[1])] for a in (ev.get("acts") or [])][-6:]}


def iter_new_lines(path, st, chunk=4 << 20):
    """offset 뒤의 온전한 줄을 4MB 씩 나눠 읽는다(큰 기록도 메모리를 적게)."""
    off = st.get("offset", 0)
    with open(path, "rb") as f:
        f.seek(off)
        buf = b""
        while True:
            part = f.read(chunk)
            if not part:
                break
            buf += part
            cut = buf.rfind(b"\n")
            if cut < 0:
                continue
            for raw in buf[: cut + 1].splitlines():
                try:
                    yield json.loads(raw)
                except Exception:
                    continue
            off += cut + 1
            st["offset"] = off
            buf = buf[cut + 1:]


def update_main(tr, st):
    """세션 기록의 새 줄만 읽어 지도에 쓸 사건만 모은다."""
    try:
        if os.path.getsize(tr) < st.get("offset", 0):
            st.clear()
    except OSError:
        return st
    lines = iter_new_lines(tr, st)
    st.setdefault("events", [])
    seen = set(st.get("seen", []))
    agent_ids = set(e[2] for e in st["events"] if e[0] == "agent")
    done_ids = []
    for d in lines:
        ts = d.get("timestamp")
        if ts:
            st["first_ts"] = st.get("first_ts") or ts
            st["last_ts"] = ts
        m = d.get("message") or {}
        content = m.get("content")
        att = d.get("attachment") if d.get("type") == "attachment" else None
        if isinstance(att, dict) and "<task-notification>" in str(att.get("prompt") or ""):
            for m3 in re.finditer(r"<tool-use-id>([^<]+)</tool-use-id>[\s\S]{0,800}?<status>(\w+)</status>", str(att["prompt"])):
                if m3.group(1) in agent_ids:
                    st.setdefault("ended", {})[m3.group(1)] = m3.group(2)
                    if m3.group(2) == "completed":
                        done_ids.append(m3.group(1))
        if d.get("type") in ("user", "attachment"):
            note = content if isinstance(content, str) else ""
            if isinstance(content, list):
                for b in content:
                    if b.get("type") == "tool_result" and b.get("tool_use_id") in agent_ids:
                        r = text_of(b.get("content"))
                        if "Async agent launched" not in r and "launched successfully" not in r:
                            done_ids.append(b.get("tool_use_id"))
                            st.setdefault("ended", {})[b.get("tool_use_id")] = "completed"
                    elif b.get("type") == "tool_result" and b.get("tool_use_id") and b.get("tool_use_id") == (st.get("plan_mode") or {}).get("id"):
                        r = text_of(b.get("content")).lstrip()
                        if r.startswith(("The user doesn't want", "User rejected", "[Request interrupted")):
                            st.pop("plan_mode", None)
                    elif b.get("type") == "tool_result" and b.get("tool_use_id") in (st.get("tasks") or {}).get("pending", {}):
                        ahead.on_task_result(st["tasks"], b.get("tool_use_id"), text_of(b.get("content")))
                    elif b.get("type") == "text":
                        note += b.get("text", "")
            for m3 in re.finditer(r"<tool-use-id>([^<]+)</tool-use-id>[\s\S]{0,800}?<status>completed</status>", note):
                if m3.group(1) in agent_ids:
                    done_ids.append(m3.group(1))
            if done_ids:
                w = st.setdefault("waiting_after", [])
                w.extend(i for i in done_ids if i not in w and i not in st.get("after", {}))
                done_ids.clear()
        if d.get("type") == "user" and isinstance(content, str) and not d.get("isMeta"):
            x = content.lstrip()
            if x and not x.startswith(("<", "[SYSTEM", "Another Claude", "Caveat:", "[Request interrupted", "This session is being continued", "The user doesn")) and not d.get("isCompactSummary"):
                st["asked"] = None
                st["prompt"], st["prompt_ts"], st["seg"] = x[:400], ts, new_ev()
                open_turn(st, ts, x[:400], ask_context(st.get("last_ai", "")), "")
            continue
        # 선택 상자 답(AskUserQuestion 결과)도 지시 하나로
        if d.get("type") == "user" and isinstance(content, list) and st.get("pending_ask"):
            for b in content:
                if b.get("type") == "tool_result" and b.get("tool_use_id") == st["pending_ask"]["id"]:
                    r = text_of(b.get("content"))
                    picks = re.findall(r'"([^"]{1,200})"="([^"]{1,300})"', r)
                    pick = " / ".join(a for _, a in picks) or short(r, 200)
                    pa = st.pop("pending_ask")
                    if picks or not r.lstrip().startswith(("The user doesn", "User rejected", "[Request interrupted")):
                        open_turn(st, ts, pick, pa["q"], "pick")
        st["seg"] = scan_lines([d], st.get("seg"))
        if st.get("turns"):
            st["turns"][-1]["ev"] = scan_lines([d], st["turns"][-1].get("ev"))
        if not isinstance(content, list):
            continue
        for b in content:
            t = b.get("type")
            if t == "text" and d.get("type") == "assistant" and b.get("text", "").strip():
                txt = b["text"]
                if hangul(txt):
                    st["words"], st["words_ts"] = txt[:1200], ts
                    st["last_ai"] = txt[-1500:]
                    if st.get("turns"):
                        tn = st["turns"][-1]
                        if not tn["first"]:
                            tn["first"] = first_sentence(clean_md(txt, 400), 160)
                        tn["last"], tn["last_ts"] = txt[:1200], ts
                    # 도우미가 끝난 뒤 세션이 처음 쓴 한국어 글 = 그 도우미 일의 쉬운 설명
                    for aid in st.get("waiting_after", []):
                        st.setdefault("after", {})[aid] = txt[:1500]
                    st["waiting_after"] = []
                elif not st.get("words"):
                    st["words"], st["words_ts"] = txt[:1200], ts
            if t != "tool_use" or b.get("name") not in RELEVANT or b.get("id") in seen:
                continue
            seen.add(b.get("id"))
            name, inp = b.get("name"), b.get("input") or {}
            if name == "Skill" and inp.get("skill") in ("superpowers:writing-plans", "superpowers:brainstorming"):
                st["events"].append(["phase", ts, "설계 · 계획" if "brainstorm" in inp["skill"] else "계획", "", ""])
            elif name == "Write" and PLAN_PATH.search(inp.get("file_path", "")):
                c = inp.get("content", "")
                m2 = re.search(r"^#\s+(.+)$", c, re.M)
                goal = re.search(r"\*\*Goal:\*\*\s*(.+)", c)
                st["events"].append(["phase", ts, short(m2.group(1) if m2 else os.path.basename(inp["file_path"]), 60),
                                     inp["file_path"], first_sentence(goal.group(1), 160) if goal else "", ahead.plan_steps(c)])
            elif name == "AskUserQuestion":
                qs = inp.get("questions") or []
                st["asked"] = short(qs[0].get("question") if qs else "질문", 120)
                q0 = qs[0] if qs else {}
                labs = [o.get("label", "") for o in (q0.get("options") or [])][:4]
                st["pending_ask"] = {"id": b.get("id"), "q": short((q0.get("question") or "질문") + (" — " + " / ".join(labs) if labs else ""), 300)}
            elif name == "Agent":
                agent_ids.add(b.get("id"))
                if st.get("turns"):
                    st["turns"][-1]["helpers"].append(inp.get("description") or "")
                    st["turns"][-1].setdefault("hids", []).append(b.get("id"))
                st["events"].append(["agent", ts, b.get("id"), inp.get("description") or "", inp.get("subagent_type") or "",
                                     first_sentence(re.sub(r"^너는[^.]*?\.\s*", "", inp.get("prompt") or ""), 150),
                                     inp.get("model") or ""])
            elif name in ("TaskCreate", "TaskUpdate", "TodoWrite"):
                ahead.on_task_tool(st.setdefault("tasks", ahead.new_tasks()), b.get("id"), name, inp, ts)
            elif name == "ExitPlanMode":
                st["plan_mode"] = {"id": b.get("id"), "ts": ts, "steps": ahead.plan_steps(inp.get("plan") or "")}
            elif name == "Bash" and "git commit" in (inp.get("command") or ""):
                msg = commit_message(inp.get("command"))
                if msg:
                    st.setdefault("commits", []).append([ts, msg])
                    del st["commits"][:-100]
    st["seen"] = list(seen)
    return st


def update_helper(path, hst):
    """도우미 기록도 새 줄만 읽어 흔적을 이어 쌓는다."""
    lines, off, reset = read_new_lines(path, hst.get("offset", 0))
    ev = new_ev() if reset or "ev" not in hst else hst["ev"]
    hst["ev"], hst["offset"] = scan_lines(lines, ev), off
    return hst


def model_name(mid):
    m = re.search(r"(opus|sonnet|haiku|fable)", str(mid or ""), re.I)
    return m.group(1).capitalize() if m else ""


def _merge_ev(base_ev, hook_ev):
    """대화 기록의 ev 와 훅의 ev 를 합친 새 dict 를 만든다(원본 캐시는 건드리지 않음)."""
    ev = dict(base_ev)
    ev["files"] = list(base_ev.get("files") or [])
    ev["acts"] = [list(a) for a in (base_ev.get("acts") or [])]
    if not hook_ev:
        return ev
    for k in ("first", "test_write", "fail", "impl"):
        if not ev.get(k) and hook_ev.get(k):
            ev[k] = hook_ev[k]
    for k in ("last", "pass", "commit"):
        if hook_ev.get(k) and (not ev.get(k) or hook_ev[k] > ev[k]):
            ev[k] = hook_ev[k]
    if hook_ev.get("steps") and not ev.get("steps"):
        ev["steps"] = hook_ev["steps"]
    if hook_ev.get("tests") and not ev.get("tests"):
        ev["tests"] = hook_ev["tests"]
    if hook_ev.get("commits", 0) > ev.get("commits", 0):
        ev["commits"] = hook_ev["commits"]
    for fn in hook_ev.get("files") or []:
        if fn and fn not in ev["files"] and len(ev["files"]) < 40:
            ev["files"].append(fn)
    if hook_ev.get("acts"):
        if not ev["acts"] or (hook_ev.get("last") or "") > (ev.get("last") or ""):
            merged = ev["acts"] + [list(a) for a in hook_ev["acts"] if not ev["acts"] or a[0] > ev["acts"][-1][0]]
            ev["acts"] = merged[-12:]
    return ev


def _agent_id_from_meta(meta, mf_path):
    if meta.get("agentId"):
        return str(meta["agentId"])
    base = os.path.basename(mf_path)
    m = re.match(r"^agent-(.+)\.meta\.json$", base)
    return m.group(1) if m else ""


def build_map(sid, tr, hook_state=None):
    os.makedirs(CACHE, exist_ok=True)
    mpath, hpath = os.path.join(CACHE, safe_sid(sid) + ".main.json"), os.path.join(CACHE, safe_sid(sid) + ".helpers.json")
    if tr and os.path.exists(tr):
        st0 = load_state(mpath, {})
        if st0.get("v") != ST_VERSION:
            st0 = {}
        st = update_main(tr, st0)
        st["v"] = ST_VERSION
        save_state(mpath, st)
    else:
        st = load_state(mpath, {})
    st.setdefault("events", [])
    hcache = load_state(hpath, {})

    sub_dir = tr[:-6] + "/subagents" if tr and tr.endswith(".jsonl") else None
    metas = {}
    if sub_dir and os.path.isdir(sub_dir):
        for mf in glob.glob(os.path.join(sub_dir, "*.meta.json")):
            try:
                meta = json.load(open(mf))
            except Exception:
                continue
            meta["_jsonl"] = mf[:-10] + ".jsonl"
            meta["_agent_id"] = _agent_id_from_meta(meta, mf)
            if meta.get("toolUseId"):
                metas[meta["toolUseId"]] = meta

    phases = [{"name": "처음", "start": None, "items": [], "goal": "", "src": "", "steps": None}]

    def new_phase(name, ts, src="", goal="", steps=None):
        cur = phases[-1]
        st_ = kst(cur["start"]) if cur["start"] else None
        if (not cur["items"] and cur["name"] == "처음") or (st_ and kst(ts) and (kst(ts) - st_) < timedelta(minutes=30) and not cur["items"]):
            cur.update(name=name, start=cur["start"] or ts, src=src or cur["src"], goal=goal or cur["goal"], steps=steps or cur.get("steps"))
            return
        phases.append({"name": name, "start": ts, "items": [], "goal": goal, "src": src, "steps": steps})

    for e in st["events"]:
        if e[0] == "phase":
            new_phase(e[2], e[1], e[3], e[4], e[5] if len(e) > 5 else None)
        elif e[0] == "agent":
            _, ts, bid, desc, atype, job, model = (e + [""])[:7]
            meta = metas.get(bid) or {}
            kind = helper_kind(desc, atype or meta.get("agentType"))
            if kind == "계획" or PLAN_DESC.search(desc):
                label = re.sub(r"\s*(구현\s*)?계획서\s*(작성|쓰기)?\s*", " ", desc).strip() or "계획"
                new_phase(short(label, 40), ts)
            phases[-1]["items"].append({"id": bid, "name": desc, "kind": kind, "key": task_key(desc), "job": job, "start": ts,
                                        "jsonl": meta.get("_jsonl"), "agent_id": meta.get("_agent_id") or "",
                                        "model": model_name(model or meta.get("pickedEffortModel"))})

    hook_subs = (hook_state or {}).get("subagents") or {}
    hook_agent_tools = (hook_state or {}).get("agent_tools") or {}
    matched_aids = set()

    def find_hook_sub(h):
        aid = h.get("agent_id") or ""
        if aid and aid in hook_subs:
            return aid, hook_subs[aid]
        jpath = h.get("jsonl") or ""
        if jpath:
            for k, sub in hook_subs.items():
                if sub.get("transcript") and sub["transcript"] == jpath:
                    return k, sub
        return "", None

    now_t = time.time()
    running_helpers = []
    for ph in phases:
        for h in ph["items"]:
            if h["jsonl"] and os.path.exists(h["jsonl"]):
                hst = hcache.get(h["jsonl"], {})
                if hst.get("mtime") != os.path.getmtime(h["jsonl"]):
                    hst = update_helper(h["jsonl"], hst)
                    hst["mtime"] = os.path.getmtime(h["jsonl"])
                    hcache[h["jsonl"]] = hst
                ev = hst["ev"]
            else:
                ev = new_ev()
                ev["first"] = ev["last"] = h["start"]
            ended = (st.get("ended") or {}).get(h["id"])
            h_aid, hsub = find_hook_sub(h)
            if hsub:
                matched_aids.add(h_aid)
                ev = _merge_ev(ev, hsub.get("ev"))
                if hsub.get("ended_at"):
                    ended = ended or "completed"
                    if not ev["last"] or hsub["ended_at"] > ev["last"]:
                        ev["last"] = hsub["ended_at"]
                    if not ev["last_text"] and hsub.get("last_msg"):
                        ev["last_text"] = hsub["last_msg"]
                elif hsub.get("running") and hsub.get("now") and (not ev["last"] or (hsub.get("now_ts") or "") >= (ev["last"] or "")):
                    ev["now"] = hsub["now"]
            hat = hook_agent_tools.get(h["id"])
            if hat and hat.get("done_ts"):
                ended = ended or "completed"
            # 끝 알림이 없고 보고도 안 했으면 일하는 중(오래 걸리는 명령으로 기록이 한동안 안 바뀌어도).
            # 훅이 도우미 시작을 잡았으면 meta/jsonl 이 덜 쌓였어도 바로 「일하는 중」으로 본다.
            # 세션이 이미 꺼졌거나 6시간 넘게 기록이 안 바뀌었으면 멈춘 것으로 본다.
            fresh = bool(h["jsonl"]) and os.path.exists(h["jsonl"]) and now_t - os.path.getmtime(h["jsonl"]) < 6 * 3600
            if hsub and hsub.get("running") and not hsub.get("ended_at"):
                hts = kst(hsub.get("now_ts") or hsub.get("started_at"))
                if hts and now_t - hts.timestamp() < 6 * 3600:
                    fresh = True
            running = (not ended) and ev["now"] != "보고하고 끝냄" and fresh and SESSION_ALIVE
            h["ended"] = ended or ""
            h["after"] = (st.get("after") or {}).get(h["id"], "")
            h["report"] = ev.get("report", "")
            h["plan_path"] = ev.get("plan_path", "")
            stopped = ended and ended != "completed"
            h.update(running=running, end=ev["last"],
                     now=(f"멈춤({ended})" if stopped else (ev["now"] or ("일하는 중" if running else "끝"))),
                     said=first_sentence(re.sub(r"(?mi)^\W*(상태|status|결과)\s*[:：].*$", "", ev.get("report") or "").strip() or ev["last_text"], 160), cells=helper_cells(h["kind"], ev, running))
            if running:
                running_helpers.append(h)

    # 대화 기록이 아직 flush 되지 않은 방금 뜬 도우미도 훅으로 바로 보여 준다
    if SESSION_ALIVE:
        for aid, sub in hook_subs.items():
            if aid in matched_aids or not sub.get("running") or sub.get("ended_at"):
                continue
            hts = kst(sub.get("now_ts") or sub.get("started_at"))
            if hts and now_t - hts.timestamp() >= 6 * 3600:
                continue
            ev = _merge_ev(new_ev(), sub.get("ev"))
            atype = sub.get("agent_type") or ""
            kind = helper_kind("", atype)
            h = {
                "id": "hook:" + aid, "agent_id": aid,
                "name": atype or "도우미", "kind": kind, "key": None,
                "job": "", "start": sub.get("started_at"), "end": ev.get("last"),
                "jsonl": sub.get("transcript") or None, "model": "",
                "ended": "", "after": "", "report": "", "running": True,
                "now": sub.get("now") or ev.get("now") or "일하는 중",
                "said": "", "cells": helper_cells(kind, ev, True),
            }
            phases[-1]["items"].append(h)
            running_helpers.append(h)

    save_state(hpath, hcache)
    first_ts = st.get("first_ts") or (hook_state and hook_state.get("first_ts"))
    last_ts = st.get("last_ts") or (hook_state and hook_state.get("last_ts"))
    if hook_state and hook_state.get("last_ts") and (not last_ts or hook_state["last_ts"] > last_ts):
        last_ts = hook_state["last_ts"]
    last_words, last_words_ts, asked = st.get("words", ""), st.get("words_ts"), st.get("asked")
    if hook_state and hook_state.get("last_stop_msg") and (not last_words_ts or (hook_state.get("last_stop_ts") or "") > last_words_ts):
        if hangul(hook_state["last_stop_msg"]) or not last_words:
            last_words, last_words_ts = hook_state["last_stop_msg"], hook_state.get("last_stop_ts")
    last_prompt, last_prompt_ts = st.get("prompt", ""), st.get("prompt_ts")
    if hook_state and hook_state.get("last_prompt") and (not last_prompt_ts or (hook_state.get("last_prompt_ts") or "") > last_prompt_ts):
        last_prompt, last_prompt_ts = hook_state["last_prompt"], hook_state.get("last_prompt_ts")
    main_ev = _merge_ev(st.get("seg") or new_ev(), hook_state.get("main_ev") if hook_state else None)

    # 할 일 카드로 묶기: 같은 덩어리 안 같은 번호
    out_phases = []
    for ph in phases:
        if not ph["items"]:
            continue
        cards, by_key = [], {}
        for h in ph["items"]:
            k = h["key"]
            if k and k in by_key:
                by_key[k]["helpers"].append(h)
                continue
            card = {"key": k, "title": re.sub(r"^\s*\d*[A-Z]?\s*(Task\s*|T)\S+\s*", "", h["name"]).strip() or h["name"],
                    "helpers": [h]}
            if k:
                by_key[k] = card
            cards.append(card)
        for c in cards:
            hs = c["helpers"]
            c["start"] = hs[0]["start"]
            c["end"] = max((x["end"] or "") for x in hs)
            c["running"] = any(x["running"] for x in hs)
            c["cells"] = []
            for x in hs:
                c["cells"] += x["cells"]
            c["state"] = "working" if c["running"] else "done"
            aft = next((x["after"] for x in reversed(hs) if x.get("after")), "")
            if aft:
                cm = clean_md(aft, 600)
                sents = re.findall(r".+?(?:[.!?。]|니다|어요|해요)(?=\s|$)", cm)
                c["done_text"] = short(" ".join(sents[:2]) if sents else cm, 150)
            else:
                c["done_text"] = hs[-1]["said"]
            c["after"] = clean_md(aft, 520)
            # 이름에서 번호 뗀 뒤 구현 도우미 이름을 제목으로
            impl = next((x for x in hs if x["kind"] not in ("검토",)), hs[0])
            c["title"] = re.sub(r"^\s*\d*[A-Z]?\s*(Task\s*|T)\S+\s*", "", impl["name"]).strip() or impl["name"]
        total = len(cards)
        done = sum(1 for c in cards if c["state"] == "done")
        out_phases.append({
            "start_ts": ph["start"] or ph["items"][0]["start"],
            "name": ph["name"], "date": md(ph["start"] or ph["items"][0]["start"]),
            "goal": ph["goal"], "src": ph["src"], "total": total, "done": done,
            "state": "working" if any(c["running"] for c in cards) else "done",
            "cards": [{
                "key": c["key"], "title": c["title"], "state": c["state"],
                "time": f'{hm(c["start"])} ~ {"" if c["running"] else hm(c["end"])}',
                "cells": c["cells"], "done_text": c["done_text"], "after": c.get("after", ""),
                "hids": [x["id"] for x in c["helpers"]], "start_ts": c["start"],
                "helpers": [{
                    "kind": x["kind"], "name": x["name"], "job": x["job"], "model": x.get("model", ""), "report": clean_md(x.get("report", ""), 320), "state": "working" if x["running"] else "done",
                    "time": f'{hm(x["start"])} ~ {"" if x["running"] else hm(x["end"])}',
                    "cells": x["cells"], "now": x["now"], "said": x["said"],
                } for x in c["helpers"]],
            } for c in cards],
        })

    ahead_out = session_ahead(st, phases)
    save_state(mpath, st)

    status = "working" if running_helpers or (last_ts and now_t - (kst(last_ts).timestamp()) < 60) else "idle"
    if asked:
        status = "asking"
    started = kst(first_ts)
    days = (datetime.now(KST).date() - started.date()).days + 1 if started else 0
    return {
        "session": sid, "transcript": tr,
        "source": "hook+log" if hook_state else "log",
        "started": md(first_ts), "days": days, "helpers": sum(len(p["items"]) for p in phases),
        "status": status, "asked": asked,
        "last_words": first_sentence(last_words, 200), "last_words_at": hm(last_words_ts),
        "now": [{"name": h["name"], "now": h["now"],
                 "mins": int((now_t - kst(h["start"]).timestamp()) // 60) if kst(h["start"]) else 0}
                for h in running_helpers],
        "phases": out_phases,
        "ahead": ahead_out,
        "built": datetime.now(KST).strftime("%H:%M:%S"),
        "turns": [turn_out(tn, False) for tn in (st.get("turns") or [])],
        "direct": {"prompt": (first_sentence(last_prompt, 90) if not is_short(last_prompt) or not (st.get("turns") or [{}])[-1].get("asked")
                              else "「" + last_prompt.strip()[:20] + "」 → " + short((st.get("turns") or [{}])[-1].get("asked", ""), 120)),
                   "since": hm(last_prompt_ts), "since_ts": last_prompt_ts,
                   "ev": main_ev},
    }


def session_ahead(st, phases):
    """가장 최근 계획 덩어리(계획서가 있는 덩어리, 없으면 마지막 덩어리)의 계획 · 할 일 목록에서 남은 일을 고른다.
    계획서는 수정 시각이 바뀔 때만 다시 읽는다. 그 뒤에 「계획서 검토」 같은 덩어리가 생겨도 계획을 잃지 않는다."""
    def plan_of(ph):
        return ph.get("src") or next((h.get("plan_path") for h in ph["items"] if h.get("plan_path")), "")
    at = next((i for i in range(len(phases) - 1, -1, -1) if plan_of(phases[i]) or phases[i].get("steps")), len(phases) - 1)
    cur = phases[at]
    since = cur.get("start")
    path = plan_of(cur)
    steps = None
    if path and os.path.exists(path):
        pc, mt = st.get("plan_cache") or {}, os.path.getmtime(path)
        if pc.get("path") != path or pc.get("mtime") != mt:
            try:
                txt = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                txt = ""
            pc = st["plan_cache"] = {"path": path, "mtime": mt, "steps": ahead.plan_steps(txt)}
        steps = pc.get("steps")
    if not steps:
        steps = cur.get("steps")
    plan = {"source": "계획서", "steps": steps} if steps else None
    pm = st.get("plan_mode") or {}
    if pm.get("steps") and (not since or (pm.get("ts") or "") >= since):
        plan = {"source": "계획 모드", "steps": pm["steps"]}
    helpers = [{"key": h.get("key"), "running": h.get("running", False), "ended": h.get("ended", "")}
               for ph in phases[at:] for h in ph["items"]]
    tasks = ahead.task_list(st.get("tasks") or ahead.new_tasks(), since)
    commits = [c[1] for c in (st.get("commits") or []) if isinstance(c, list) and (not since or (c[0] or "") >= since)]
    return ahead.build_ahead(plan, tasks, helpers, commits)


def cached_map(sid, tr, hook_state=None):
    return build_map(sid, tr, hook_state=hook_state)


def direct_card(tn, live=False):
    """세션이 도우미 없이 직접 한 지시 → 카드 하나."""
    title = tn["said"] if not tn["short"] else (tn.get("first") or tn.get("asked") or tn["said"])
    acts = tn.get("acts_full") or []
    if live:
        cells = [[a[1], "done", hm(a[0]), "기록"] for a in acts[:-1]] + ([[acts[-1][1], "working", hm(acts[-1][0]), "기록"]] if acts else [["일하는 중", "working", "", "기록"]])
    else:
        cells = [[a[1], "done", hm(a[0]), "기록"] for a in acts] or [["답", "done", tn.get("last_time", ""), "기록"]]
    return {"key": None, "title": first_sentence(title, 70), "state": "working" if live else "done",
            "time": f'{tn["time"]} ~ {"" if live else tn.get("last_time", "")}', "cells": cells,
            "done_text": tn.get("last", ""), "after": "", "hids": [], "start_ts": tn.get("ts"), "direct": True,
            "helpers": [{"kind": "세션", "name": "세션이 직접", "job": first_sentence(title, 150), "model": "", "report": "",
                         "state": "working" if live else "done", "time": tn["time"], "cells": cells,
                         "now": (cells[-1][0] if live else "끝"), "said": ""}],
            "turns": []}


def bind_turns(m, live_working):
    """대화(지시)를 그때의 작업 카드에 묶는다.
    작업을 시작시킨 지시 → 그 카드, 세션이 직접 일한 지시 → 새 「세션이 직접 한 일」 카드,
    일 없이 묻고 답한 지시 → 그때 가장 최근에 시작된 카드(없으면 다음 카드)."""
    phases, turns = m.get("phases") or [], m.get("turns") or []
    for p in phases:
        for c in p["cards"]:
            c.setdefault("turns", [])
    by_hid = {}
    for p in phases:
        for c in p["cards"]:
            for h in c.get("hids", []):
                by_hid[h] = c
    starts = [kst(p.get("start_ts")) for p in phases]

    def phase_of(ts):
        t, idx = kst(ts), 0
        for i, st_ in enumerate(starts):
            if st_ and t and st_ <= t:
                idx = i
        return idx

    timeline = sorted([(kst(c.get("start_ts")) or datetime.min.replace(tzinfo=KST), c) for p in phases for c in p["cards"]],
                      key=lambda x: x[0])
    pre = []
    for i, tn in enumerate(turns):
        last = i == len(turns) - 1
        card = next((by_hid[h] for h in tn.get("hids", []) if h in by_hid), None)
        if not card and (tn.get("work") or (last and live_working)):
            card = direct_card(tn, live=last and live_working)
            if not phases:
                phases.append({"name": "지시", "start_ts": tn.get("ts"), "date": tn["date"], "goal": "", "src": "",
                               "total": 0, "done": 0, "state": "done", "cards": []})
                starts.append(kst(tn.get("ts")))
            ph = phases[phase_of(tn.get("ts"))]
            ph["cards"].append(card)
            ph["total"] += 1
            if card["state"] == "done":
                ph["done"] += 1
            else:
                ph["state"] = "working"
            timeline.append((kst(tn.get("ts")) or datetime.min.replace(tzinfo=KST), card))
            timeline.sort(key=lambda x: x[0])
        if not card:
            t = kst(tn.get("ts"))
            before = [c for (ct, c) in timeline if t and ct <= t]
            card = before[-1] if before else None
        if card is None:
            pre.append(tn)
        else:
            card["turns"].append(tn)
    if pre:
        first = timeline[0][1] if timeline else None
        if first is None:
            first = direct_card(pre[-1])
            phases.append({"name": "지시", "start_ts": pre[0].get("ts"), "date": pre[0]["date"], "goal": "", "src": "",
                           "total": 1, "done": 1, "state": "done", "cards": [first]})
        first["turns"] = pre + first["turns"]
    for p in phases:
        p["cards"].sort(key=lambda c: c.get("start_ts") or "")
        for c in p["cards"]:
            c["turns"].sort(key=lambda t: t.get("ts") or "")


def overlay_live(m, live, hook_state=None):
    """세션 파일과 훅의 지금 상태(busy · waiting · idle)를 덧씌운다."""
    if live == "busy":
        m["status"] = "working"
    elif live == "waiting":
        m["status"] = "asking"
        m["asked"] = m.get("asked") or "확인 · 허락을 기다리는 중"
    elif live in ("idle", "shell", ""):
        m["status"] = "asking" if m.get("asked") else "idle"

    if hook_state and SESSION_ALIVE:
        if hook_state.get("permission"):
            m["status"] = "asking"
            m["asked"] = hook_state["permission"]
        elif hook_state.get("asked"):
            m["status"] = "asking"
            m["asked"] = hook_state["asked"]
        elif hook_state.get("status") == "busy":
            m["status"] = "working"
            m["asked"] = None
            if hook_state.get("now"):
                d_ = m.setdefault("direct", {})
                dev = dict(d_.get("ev") or {})
                if not dev.get("last") or (hook_state.get("now_ts") or "") >= (dev.get("last") or ""):
                    dev["now"] = hook_state["now"]
                    d_["ev"] = dev
        elif hook_state.get("status") == "idle":
            helper_running = any(h for h in (m.get("now") or []) if h.get("name") != "세션이 직접")
            if helper_running:
                m["status"] = "working"
            else:
                m["asked"] = None
                m["status"] = "idle"

    if m.get("turns") and m["status"] == "working":
        m["turns"][-1]["state"] = "working"
    d = m.get("direct") or {}
    if m["status"] == "working" and not m.get("now"):
        ev = d.get("ev") or {}
        since = kst(d.get("since_ts"))
        m["now"] = [{"name": "세션이 직접", "now": ev.get("now") or "생각하는 중",
                     "mins": int((datetime.now(KST) - since).total_seconds() // 60) if since else 0}]
    return
    if m["status"] != "working" or m.get("now"):
        return
    ev = d.get("ev") or {}
    acts = (ev.get("acts") or [])[-6:]
    if acts:
        cells = [[past(a[1]), "done", hm(a[0]), "기록"] for a in acts[:-1]] + [[acts[-1][1], "working", hm(acts[-1][0]), "기록"]]
    else:
        cells = [["생각하는 중", "working", "", "기록"]]
    since = kst(d.get("since_ts"))
    mins = int((datetime.now(KST) - since).total_seconds() // 60) if since else 0
    card = {"key": None, "title": "세션이 직접 하는 일", "state": "working", "time": f'{d.get("since", "")} ~',
            "cells": cells, "done_text": "", "direct": True,
            "helpers": [{"kind": "세션", "name": "세션이 직접", "job": d.get("prompt", ""), "state": "working",
                         "time": f'{d.get("since", "")} ~', "cells": cells, "now": ev.get("now") or "생각하는 중", "said": ""}]}
    m["now"] = [{"name": "세션이 직접", "now": ev.get("now") or "생각하는 중", "mins": mins}]
    if not m["phases"]:
        m["phases"].append({"name": "지금", "start_ts": d.get("since_ts"), "date": md(d.get("since_ts")), "goal": "", "src": "", "total": 0, "done": 0, "state": "working", "cards": []})
    ph = m["phases"][-1]
    ph["cards"].append(card)
    ph["total"] += 1
    ph["state"] = "working"


# ── Codex ─────────────────────────────────────────────────
# Codex 기록: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
# 요청 하나 = task_started … task_complete(또는 turn_aborted). 도우미는 없다.

CODEX_DIR = os.path.join(HOME, ".codex", "sessions")


def codex_procs():
    """떠 있는 codex 프로세스와 그 기록 파일(열어 둔 rollout)."""
    out = []
    try:
        ps = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return out
    for line in ps.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or os.path.basename(parts[1]) != "codex":
            continue
        pid = parts[0]
        roll = None
        try:
            lf = subprocess.run(["lsof", "-p", pid, "-Fn"], capture_output=True, text=True, timeout=3).stdout
            hits = [l[1:] for l in lf.splitlines() if l.startswith("n") and "/.codex/sessions/" in l and l.endswith(".jsonl")]
            roll = hits[-1] if hits else None
        except Exception:
            pass
        if roll:
            out.append({"pid": pid, "rollout": roll, "terminal": terminal_of(pid)})
    return out


def codex_cmd(inp):
    m = re.search(r'"cmd"\s*:\s*"((?:[^"\\]|\\.)*)"', str(inp or ""))
    if m:
        try:
            return json.loads('"' + m.group(1) + '"')
        except Exception:
            return m.group(1)
    return str(inp or "")[:200]


def codex_act(cmd):
    c = cmd.strip()
    if "apply_patch" in c or re.search(r"\b(cat\s*>|tee |sed -i|perl -pi)", c):
        return "파일 고침"
    if "git commit" in c:
        return "커밋"
    if TEST_CMD.search(c):
        return "시험 돌림"
    if re.match(r"(sed -n|cat |rg |grep |ls|find |head |tail |nl )", c):
        return "코드 읽음"
    return "명령 돌림"


def build_codex_map(path, alive):
    turns, model, sid, first_ts, last_ts = [], "", "", None, None
    cur, pending = None, {}
    words, words_ts = "", None
    for d in iter_jsonl(path):
        ts = d.get("timestamp")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        t, p = d.get("type"), d.get("payload") or {}
        pt = p.get("type")
        if t == "session_meta":
            sid = p.get("id") or sid
        elif t == "event_msg" and pt == "thread_settings_applied":
            model = ((p.get("thread_settings") or {}).get("model")) or model
        elif t == "event_msg" and pt == "task_started":
            cur = {"start": ts, "end": None, "prompt": "", "acts": [], "answer": "", "state": "working",
                   "fail": None, "pass": None, "first": "", "asked": ask_context(turns[-1]["answer"] if turns else "")}
            turns.append(cur)
        elif t == "event_msg" and pt == "item_completed" and cur is not None:
            it = p.get("item") or {}
            if it.get("type") == "UserMessage" and not cur["prompt"]:
                cur["prompt"] = text_of(it.get("content"))
        elif t == "response_item" and pt == "custom_tool_call" and cur is not None:
            cmd = codex_cmd(p.get("input"))
            act = codex_act(cmd)
            pending[p.get("call_id")] = act
            if not cur["acts"] or cur["acts"][-1][1] != act:
                cur["acts"].append([ts, act])
        elif t == "response_item" and pt == "custom_tool_call_output" and cur is not None:
            act = pending.pop(p.get("call_id"), "")
            if act == "시험 돌림":
                out = text_of(p.get("output"))
                if FAIL_OUT.search(out):
                    cur["fail"] = cur["fail"] or ts
                elif PASS_OUT.search(out):
                    cur["pass"] = ts
        elif t == "response_item" and pt == "message" and p.get("role") == "assistant":
            txt = text_of(p.get("content"))
            if txt.strip() and (hangul(txt) or not words):
                words, words_ts = txt[:1200], ts
            if cur is not None and txt.strip() and not cur["first"]:
                cur["first"] = first_sentence(clean_md(txt, 400), 160)
        elif t == "event_msg" and pt in ("task_complete", "turn_aborted") and cur is not None:
            cur["end"] = ts
            cur["state"] = "done" if pt == "task_complete" else "aborted"
            cur["answer"] = str(p.get("last_agent_message") or "")[:1500]
            cur = None
    mname = model or "Codex"
    cards = []
    for i, tn in enumerate(turns):
        running = tn["state"] == "working" and alive
        acts = tn["acts"][-6:]
        if running:
            cells = [[past(a[1]), "done", hm(a[0]), "기록"] for a in acts[:-1]] + ([[acts[-1][1].replace("고침", "고치는 중").replace("돌림", "돌리는 중").replace("읽음", "읽는 중"), "working", hm(acts[-1][0]), "기록"]] if acts else [["생각하는 중", "working", "", "기록"]])
        else:
            cells = [[a[1], "done", hm(a[0]), "기록"] for a in acts] or [["답", "done", hm(tn["end"]), "기록"]]
        ans = clean_md(tn["answer"], 600)
        sents = re.findall(r".+?(?:[.!?。]|니다|어요|해요)(?=\s|$)", ans)
        state = "working" if running else "done"
        title = first_sentence(tn["prompt"], 70) or f"요청 {i + 1}"
        now = (cells[-1][0] if running else ("멈춤(중단)" if tn["state"] == "aborted" else "끝"))
        cards.append({
            "key": None, "title": title, "state": state,
            "time": f'{hm(tn["start"])} ~ {"" if running else hm(tn["end"])}', "cells": cells,
            "done_text": short(" ".join(sents[:2]) if sents else ans, 150), "after": ans,
            "helpers": [{"kind": "Codex", "name": mname, "job": first_sentence(tn["prompt"], 150), "model": mname,
                         "report": "", "state": state, "time": f'{hm(tn["start"])} ~', "cells": cells,
                         "now": now, "said": ""}],
        })
    working = [c for c in cards if c["state"] == "working"]
    started = kst(first_ts)
    cturns = []
    for tn, cd in zip(turns, cards):
        cturns.append({"ts": tn["start"], "time": hm(tn["start"]), "date": md(tn["start"]), "said": tn["prompt"][:400], "short": is_short(tn["prompt"]),
                       "kind": "", "asked": tn.get("asked", ""), "first": tn.get("first", ""), "last": cd["done_text"],
                       "last_time": hm(tn["end"]), "helpers": [], "files": [], "commits": sum(1 for a in tn["acts"] if a[1] == "커밋"),
                       "tests": "실패" if tn["fail"] and not tn["pass"] else ("통과" if tn["pass"] else ""),
                       "acts": [a[1] for a in tn["acts"]][-4:], "state": cd["state"]})
    return {
        "turns": cturns,
        "session": "codex:" + (sid or os.path.basename(path)), "transcript": path, "agent": "codex",
        "started": md(first_ts), "days": (datetime.now(KST).date() - started.date()).days + 1 if started else 0,
        "helpers": 0, "status": "working" if working else "idle", "asked": None,
        "last_words": first_sentence(words, 200), "last_words_at": hm(words_ts),
        "now": [{"name": "Codex", "now": working[-1]["cells"][-1][0],
                 "mins": int((datetime.now(KST) - kst(turns[-1]["start"])).total_seconds() // 60) if kst(turns[-1]["start"]) else 0}] if working else [],
        "phases": [{"name": f"Codex · {mname}", "date": md(first_ts), "goal": "", "src": "", "total": len(cards),
                    "done": sum(1 for c in cards if c["state"] == "done"), "state": "working" if working else "done",
                    "cards": cards}] if cards else [],
        "built": datetime.now(KST).strftime("%H:%M:%S"),
    }


# ── 다른 에이전트 공통 유틸 · 카드 변환 ───────────────────

def _ms_iso(ms):
    if not ms:
        return None
    try:
        val = float(ms)
        if val > 1e12:
            val /= 1000.0
        return datetime.fromtimestamp(val, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except Exception:
        return None


def _lsof_info(pid):
    """lsof 한 번으로 프로세스의 cwd 와 열어 둔 파일 경로 목록을 가져온다."""
    cwd, files = "", []
    try:
        lf = subprocess.run(["lsof", "-p", str(pid), "-Fn"], capture_output=True, text=True, timeout=3).stdout
        cur_fd = ""
        for line in lf.splitlines():
            if line.startswith("f"):
                cur_fd = line[1:]
            elif line.startswith("n"):
                p = line[1:]
                if cur_fd == "cwd" and not cwd:
                    cwd = p
                files.append(p)
    except Exception:
        pass
    return cwd, files


def _unwrap_arg(v):
    """Antigravity transcript.jsonl 의 JSON 문자열 감싸기('\"...\"')를 풀어낸다."""
    if isinstance(v, str):
        s = v.strip()
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            try:
                out = json.loads(s)
                if isinstance(out, str):
                    return out
            except Exception:
                pass
        return v
    return "" if v is None else str(v)


def _format_model(mid, fallback):
    s = str(mid or "").strip()
    if not s or s.lower() == "auto":
        return fallback
    if len(s) > 36:
        s = s[:35] + "…"
    return s


def _turns_to_map(agent_key, agent_label, session_key, transcript, model_label, turns, alive,
                  first_ts, last_ts, words, words_ts, asked=None, source="log", helpers_count=0):
    """Codex · Antigravity · OpenCode · Copilot 공통 턴 목록을 지도 JSON 으로 바꾼다."""
    mname = _format_model(model_label, agent_label)
    cards = []
    for i, tn in enumerate(turns):
        running = tn.get("state") == "working" and alive
        acts = (tn.get("acts") or [])[-6:]
        if running:
            cells = [[past(a[1]), "done", hm(a[0]), "기록"] for a in acts[:-1]] + (
                [[acts[-1][1].replace("고침", "고치는 중").replace("돌림", "돌리는 중").replace("읽음", "읽는 중").replace("씀", "쓰는 중"),
                  "working", hm(acts[-1][0]), "기록"]] if acts else [["생각하는 중", "working", "", "기록"]])
        else:
            cells = [[a[1], "done", hm(a[0]), "기록"] for a in acts] or [["답", "done", hm(tn.get("end")), "기록"]]
        ans = clean_md(tn.get("answer", ""), 600)
        sents = re.findall(r".+?(?:[.!?。]|니다|어요|해요)(?=\s|$)", ans)
        state = "working" if running else "done"
        prompt = tn.get("prompt") or ""
        title = first_sentence(prompt if not is_short(prompt) else (tn.get("first") or tn.get("asked") or prompt), 70) or f"요청 {i + 1}"
        now = (cells[-1][0] if running else ("멈춤(중단)" if tn.get("state") == "aborted" else "끝"))
        cards.append({
            "key": None, "title": title, "state": state,
            "time": f'{hm(tn.get("start"))} ~ {"" if running else hm(tn.get("end"))}', "cells": cells,
            "done_text": short(" ".join(sents[:2]) if sents else ans, 150), "after": ans,
            "helpers": [{"kind": agent_label, "name": mname, "job": first_sentence(prompt, 150), "model": mname,
                         "report": "", "state": state, "time": f'{hm(tn.get("start"))} ~', "cells": cells,
                         "now": now, "said": ""}],
        })
    working = [c for c in cards if c["state"] == "working"]
    started = kst(first_ts)
    cturns = []
    for tn, cd in zip(turns, cards):
        prompt = tn.get("prompt") or ""
        acts_list = tn.get("acts") or []
        commits = tn.get("commits") if isinstance(tn.get("commits"), int) else sum(1 for a in acts_list if a[1] == "커밋")
        cturns.append({
            "ts": tn.get("start"), "time": hm(tn.get("start")), "date": md(tn.get("start")),
            "said": prompt[:400], "short": is_short(prompt),
            "kind": "", "asked": tn.get("asked", ""), "first": tn.get("first", ""), "last": cd["done_text"],
            "last_time": hm(tn.get("end")), "helpers": tn.get("helpers") or [], "files": tn.get("files") or [],
            "commits": commits,
            "tests": "실패" if tn.get("fail") and not tn.get("pass") else ("통과" if tn.get("pass") else ""),
            "acts": [a[1] for a in acts_list][-4:], "state": cd["state"],
        })
    status = "asking" if (asked and alive) else ("working" if working else "idle")
    return {
        "turns": cturns,
        "session": session_key, "transcript": transcript, "agent": agent_key, "source": source,
        "started": md(first_ts), "days": (datetime.now(KST).date() - started.date()).days + 1 if started else 0,
        "helpers": helpers_count, "status": status, "asked": asked if alive else None,
        "last_words": first_sentence(words, 200), "last_words_at": hm(words_ts),
        "now": [{"name": agent_label, "now": working[-1]["cells"][-1][0],
                 "mins": int((datetime.now(KST) - kst(turns[-1].get("start"))).total_seconds() // 60) if turns and kst(turns[-1].get("start")) else 0}] if working else [],
        "phases": [{"name": f"{agent_label} · {mname}", "date": md(first_ts), "goal": "", "src": "", "total": len(cards),
                    "done": sum(1 for c in cards if c["state"] == "done"), "state": "working" if working else "done",
                    "cards": cards}] if cards else [],
        "built": datetime.now(KST).strftime("%H:%M:%S"),
    }


def _finalize_external_map(m, s):
    for p_ in m.get("phases") or []:
        for c_, t_ in zip(p_["cards"], m.get("turns") or []):
            c_["turns"] = [t_]
    m.pop("turns", None)
    m.update(name=s.get("name") or "", named=bool(s.get("named")), terminal=s.get("terminal"), cwd=s.get("cwd"))
    return m


# ── Antigravity (agy) ─────────────────────────────────────
# 기록: ~/.gemini/antigravity-cli/brain/<conv-id>/.system_generated/logs/transcript.jsonl
#       (또는 ~/.gemini/antigravity, ~/.gemini/antigravity-ide)

AGY_ROOTS = [
    os.path.join(HOME, ".gemini", "antigravity-cli"),
    os.path.join(HOME, ".gemini", "antigravity"),
    os.path.join(HOME, ".gemini", "antigravity-ide"),
]
AGY_CONV_RE = re.compile(r"(.*/\.gemini/antigravity[^/]*)/(?:conversations/([0-9a-f-]+)\.db|brain/([0-9a-f-]+))")


def agy_transcript_of(conv_id):
    for root in AGY_ROOTS:
        p = os.path.join(root, "brain", conv_id, ".system_generated", "logs", "transcript.jsonl")
        if os.path.exists(p):
            return p
    return None


def antigravity_procs():
    """떠 있는 agy / antigravity 프로세스와 열어 둔 대화 기록(transcript.jsonl)을 찾는다."""
    out = []
    seen_conv = set()
    try:
        ps = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return out
    for line in ps.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid, comm = parts[0], os.path.basename(parts[1])
        if comm not in ("agy", "antigravity-cli", "antigravity"):
            continue
        cwd, files = _lsof_info(pid)
        conv_id, tr = None, None
        for fp in files:
            m = AGY_CONV_RE.search(fp)
            if m:
                base_dir = m.group(1)
                cid = m.group(2) or m.group(3)
                cand = os.path.join(base_dir, "brain", cid, ".system_generated", "logs", "transcript.jsonl")
                if os.path.exists(cand):
                    conv_id, tr = cid, cand
                    break
                if not conv_id:
                    conv_id, tr = cid, cand
        if not conv_id or conv_id in seen_conv:
            continue
        seen_conv.add(conv_id)
        updated = os.path.getmtime(tr) if tr and os.path.exists(tr) else time.time()
        out.append({
            "pid": pid, "conv_id": conv_id, "transcript": tr,
            "terminal": terminal_of(pid), "cwd": cwd, "updated": updated,
            "status": "busy" if tr and os.path.exists(tr) and time.time() - updated < 30 else "idle",
        })
    return out


def _agy_user_text(raw):
    s = str(raw or "")
    m = re.search(r"<USER_REQUEST>\s*([\s\S]*?)\s*</USER_REQUEST>", s)
    if m:
        return m.group(1).strip()
    s = re.sub(r"<ADDITIONAL_METADATA>[\s\S]*?</ADDITIONAL_METADATA>", "", s)
    s = re.sub(r"<USER_SETTINGS_CHANGE>[\s\S]*?</USER_SETTINGS_CHANGE>", "", s)
    return s.strip()


def _agy_model_label(mid):
    s = str(mid or "").strip()
    if not s:
        return "Antigravity"
    # gemini-4-neon-high -> Gemini 4 Neon
    parts = [p for p in re.split(r"[-_]+", s) if p and p.lower() not in ("high", "medium", "low", "default")]
    return " ".join(p.capitalize() if not p.isdigit() else p for p in parts[:4]) or s


def build_antigravity_map(path, alive, sid=""):
    turns, model, first_ts, last_ts = [], "", None, None
    cur, pending = None, []
    words, words_ts = "", None
    last_step_kind = ""
    helpers_count = 0
    for d in iter_jsonl(path):
        ts = d.get("created_at") or d.get("timestamp")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        t, src = d.get("type"), d.get("source")
        if t == "USER_INPUT" and src == "USER_EXPLICIT":
            txt = _agy_user_text(d.get("content"))
            if not txt:
                continue
            if cur is not None and cur["state"] == "working":
                cur["end"] = cur["end"] or ts
                cur["state"] = "done"
            prev_ans = turns[-1]["answer"] if turns else ""
            cur = {"start": ts, "end": None, "prompt": txt, "acts": [], "answer": "", "state": "working",
                   "fail": None, "pass": None, "first": "", "asked": ask_context(prev_ans),
                   "files": [], "helpers": [], "commits": 0, "asking": None}
            turns.append(cur)
            pending.clear()
            last_step_kind = "user"
        elif t == "PLANNER_RESPONSE":
            if d.get("model"):
                model = d["model"]
            txt = str(d.get("content") or "").strip()
            tcalls = d.get("tool_calls") or []
            if txt:
                if hangul(txt) or not words:
                    words, words_ts = txt[:1200], ts
                if cur is not None:
                    if not cur["first"]:
                        cur["first"] = first_sentence(clean_md(txt, 400), 160)
                    cur["answer"] = txt[:1500]
                    cur["end"] = ts
            if cur is not None and tcalls:
                cur["end"] = ts
                asked_here = None
                for tc in tcalls:
                    tname = str(tc.get("name") or "")
                    args = tc.get("args") or {}
                    act = ""
                    pkind = ""
                    if tname in ("edit_file", "write_file", "replace_file_content", "multi_replace_file_content"):
                        p = _unwrap_arg(args.get("Path") or args.get("TargetFile") or "")
                        base = os.path.basename(p)
                        if base and base not in cur["files"] and len(cur["files"]) < 40:
                            cur["files"].append(base)
                        act = f"시험 파일 {base} 씀" if TEST_FILE.search(p) else (f"{base} 고침" if base else "파일 고침")
                    elif tname == "run_command":
                        cmd = _unwrap_arg(args.get("CommandLine") or "")
                        if "git commit" in cmd:
                            act, pkind = "커밋", "commit"
                        elif TEST_CMD.search(cmd):
                            act, pkind = "시험 돌림", "test"
                        elif re.search(r"\b(sed -i|apply_patch|cat\s*>)", cmd):
                            act = "파일 고침"
                        else:
                            act = "명령 돌림"
                    elif tname in ("read_file", "view_file", "grep_search", "find_by_name", "list_dir", "view_file_outline", "view_code_item"):
                        act = "코드 읽음"
                    elif tname in ("search_web", "read_url_content"):
                        act = "웹 조사"
                    elif tname == "invoke_subagent":
                        role = _unwrap_arg(args.get("Role") or args.get("TypeName") or "서브에이전트")
                        helpers_count += 1
                        cur["helpers"].append(short(role, 40))
                        act = f"도우미({short(role, 20)})"
                    elif tname == "ask_question":
                        raw_q = str(args.get("questions") or "")
                        qm = re.search(r'"question"\s*:\s*"([^"]{1,200})"', raw_q)
                        asked_here = short(qm.group(1) if qm else "질문 선택 기다리는 중", 160)
                        act = "질문함"
                    elif tname:
                        act = f"{tname}"
                    pending.append(pkind)
                    if act and (not cur["acts"] or cur["acts"][-1][1] != act):
                        cur["acts"].append([ts, act])
                if asked_here:
                    cur["asking"] = asked_here
                    last_step_kind = "ask"
                else:
                    last_step_kind = "tool"
            elif txt and not tcalls:
                if cur is not None:
                    cur["state"] = "done"
                    cur["end"] = ts
                    cur["asking"] = None
                last_step_kind = "reply"
        elif t == "GENERIC" and cur is not None:
            pkind = pending.pop(0) if pending else ""
            out_txt = str(d.get("content") or "")
            if pkind == "commit" and "Command exited with code 0" in out_txt:
                cur["commits"] += 1
            elif pkind == "test":
                if FAIL_OUT.search(out_txt):
                    if not cur["pass"]:
                        cur["fail"] = cur["fail"] or ts
                elif PASS_OUT.search(out_txt):
                    cur["pass"] = ts
                elif "Command exited with code" in out_txt and "Command exited with code 0" not in out_txt:
                    if not cur["pass"]:
                        cur["fail"] = cur["fail"] or ts
                elif "Command exited with code 0" in out_txt:
                    cur["pass"] = ts
            cur["end"] = ts
            last_step_kind = "result"

    asked = None
    if turns:
        recent = os.path.exists(path) and (time.time() - os.path.getmtime(path) < 600)
        if alive and last_step_kind == "ask":
            turns[-1]["state"] = "working"
            asked = turns[-1].get("asking") or "질문 답 기다리는 중"
        elif alive and last_step_kind in ("user", "tool", "result") and recent:
            turns[-1]["state"] = "working"
        else:
            turns[-1]["state"] = "done"

    conv_id = sid or os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(path))))
    return _turns_to_map(
        "antigravity", "Antigravity", "agy:" + conv_id, path, _agy_model_label(model),
        turns, alive, first_ts, last_ts, words, words_ts, asked=asked, source="log", helpers_count=helpers_count,
    )


# ── OpenCode ──────────────────────────────────────────────
# 기록: ~/.local/share/opencode/opencode.db (SQLite, session_v2 + session_message)

OPENCODE_DB = os.path.join(HOME, ".local", "share", "opencode", "opencode.db")


def _open_ro_sqlite(db_path):
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)


def opencode_sessions(db_path=None):
    """OpenCode DB(session_v2)와 떠 있는 opencode TUI 프로세스를 연결해 세션 목록을 만든다."""
    dbp = db_path or OPENCODE_DB
    if not os.path.exists(dbp):
        return []
    # 떠 있는 대화형 opencode 프로세스(serve 데몬 제외)의 cwd ↔ herdr 칸
    term_by_cwd = {}
    try:
        ps = subprocess.run(["ps", "-axo", "pid=,comm=,args="], capture_output=True, text=True, timeout=3).stdout
        for line in ps.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 2:
                continue
            pid, comm = parts[0], os.path.basename(parts[1])
            args = parts[2] if len(parts) > 2 else ""
            if comm != "opencode" or " serve" in args:
                continue
            term = terminal_of(pid)
            cwd, _ = _lsof_info(pid)
            if cwd and term:
                term_by_cwd.setdefault(cwd, []).append((pid, term))
    except Exception:
        pass

    out = []
    now_t = time.time()
    try:
        con = _open_ro_sqlite(dbp)
        cur = con.cursor()
        rows = cur.execute(
            "SELECT id, directory, title, time_updated, time_busy, time_idle "
            "FROM session_v2 WHERE parent_id IS NULL ORDER BY time_updated DESC LIMIT 12"
        ).fetchall()
        con.close()
    except Exception:
        return out

    used_terms = set()
    for sid, directory, title, t_upd, t_busy, t_idle in rows:
        upd_sec = (t_upd or 0) / 1000.0 if (t_upd or 0) > 1e12 else float(t_upd or 0)
        busy = (t_busy or 0) > (t_idle or 0)
        term = None
        if directory and directory in term_by_cwd:
            for _pid, cand in term_by_cwd[directory]:
                if cand not in used_terms:
                    term = cand
                    used_terms.add(cand)
                    break
        # 떠 있는 터미널과 매칭되거나, 일하는 중이거나, 24시간 내 활동한 세션만 노출
        if not term and not busy and (now_t - upd_sec > 24 * 3600):
            continue
        out.append({
            "id": sid, "title": title or "", "cwd": directory or "", "terminal": term,
            "status": "busy" if busy else "idle", "updated": upd_sec, "db": dbp,
        })
    return out


def build_opencode_map(db_path, oc_sid, alive):
    turns, model, first_ts, last_ts = [], "", None, None
    cur = None
    words, words_ts = "", None
    asked = None
    helpers_count = 0
    busy = False
    try:
        con = _open_ro_sqlite(db_path)
        c = con.cursor()
        srow = c.execute(
            "SELECT title, directory, model, time_created, time_updated, time_busy, time_idle, idle_outcome "
            "FROM session_v2 WHERE id = ?", (oc_sid,)
        ).fetchone()
        if srow:
            _title, _dir, mraw, t_cr, t_up, t_busy, t_idle, _out = srow
            first_ts = _ms_iso(t_cr)
            last_ts = _ms_iso(t_up)
            busy = (t_busy or 0) > (t_idle or 0)
            if mraw:
                try:
                    mj = json.loads(mraw) if isinstance(mraw, str) else mraw
                    if isinstance(mj, dict):
                        model = mj.get("id") or ""
                except Exception:
                    model = str(mraw)
        hrow = c.execute("SELECT COUNT(*) FROM session_v2 WHERE parent_id = ?", (oc_sid,)).fetchone()
        helpers_count = int(hrow[0]) if hrow else 0
        mrows = c.execute(
            "SELECT type, time_created, data FROM session_message WHERE session_id = ? ORDER BY seq ASC, time_created ASC",
            (oc_sid,)
        ).fetchall()
        con.close()
    except Exception:
        mrows = []

    for mtype, t_cr, raw in mrows:
        ts = _ms_iso(t_cr)
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        try:
            d = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            continue
        if mtype == "user":
            txt = str(d.get("text") or "").strip()
            if not txt:
                continue
            if cur is not None and cur["state"] == "working":
                cur["end"] = cur["end"] or ts
                cur["state"] = "done"
            prev_ans = turns[-1]["answer"] if turns else ""
            cur = {"start": ts, "end": None, "prompt": txt, "acts": [], "answer": "", "state": "working",
                   "fail": None, "pass": None, "first": "", "asked": ask_context(prev_ans),
                   "files": [], "helpers": [], "commits": 0}
            turns.append(cur)
        elif mtype == "assistant":
            mj = d.get("model")
            if isinstance(mj, dict) and mj.get("id"):
                model = mj["id"]
            for blk in d.get("content") or []:
                if not isinstance(blk, dict):
                    continue
                bt = blk.get("type")
                if bt == "text":
                    txt = str(blk.get("text") or "").strip()
                    if txt:
                        if hangul(txt) or not words:
                            words, words_ts = txt[:1200], ts
                        if cur is not None:
                            if not cur["first"]:
                                cur["first"] = first_sentence(clean_md(txt, 400), 160)
                            cur["answer"] = txt[:1500]
                            cur["end"] = ts
                elif bt == "tool" and cur is not None:
                    tname = str(blk.get("name") or "").lower()
                    st = blk.get("state") or {}
                    if isinstance(st, str):
                        try:
                            st = json.loads(st)
                        except Exception:
                            st = {}
                    inp = st.get("input") or {}
                    if not isinstance(inp, dict):
                        inp = {}
                    status = str(st.get("status") or "")
                    out = text_of(st.get("content")) or str(st.get("output") or "")
                    bts = _ms_iso((blk.get("time") or {}).get("created")) or ts
                    act = ""
                    if tname in ("shell", "bash", "run_command"):
                        cmd = str(inp.get("command") or inp.get("cmd") or "")
                        if "git commit" in cmd:
                            act = "커밋"
                            if status == "completed" and not re.search(r"(nothing to commit|error:|fatal:)", out, re.I):
                                cur["commits"] += 1
                        elif TEST_CMD.search(cmd):
                            act = "시험 돌림"
                            if status == "completed":
                                if FAIL_OUT.search(out):
                                    if not cur["pass"]:
                                        cur["fail"] = cur["fail"] or bts
                                elif PASS_OUT.search(out):
                                    cur["pass"] = bts
                        elif re.search(r"\b(sed -i|apply_patch|cat\s*>)", cmd):
                            act = "파일 고침"
                        else:
                            act = "명령 돌림"
                    elif tname in ("edit", "write", "patch", "apply_patch", "multiedit"):
                        p = str(inp.get("filePath") or inp.get("file_path") or inp.get("path") or "")
                        base = os.path.basename(p)
                        if base and base not in cur["files"] and len(cur["files"]) < 40:
                            cur["files"].append(base)
                        act = f"시험 파일 {base} 씀" if TEST_FILE.search(p) else (f"{base} 고침" if base else "파일 고침")
                    elif tname in ("read", "grep", "glob", "list", "find"):
                        act = "코드 읽음"
                    elif tname in ("task", "subagent"):
                        desc = short(inp.get("description") or "도우미", 30)
                        cur["helpers"].append(desc)
                        act = f"도우미({desc})"
                    elif tname in ("question", "ask"):
                        if status in ("pending", "running"):
                            asked = short(inp.get("question") or "질문 답 기다리는 중", 160)
                        act = "질문함"
                    elif tname:
                        act = tname
                    if act and (not cur["acts"] or cur["acts"][-1][1] != act):
                        cur["acts"].append([bts, act])
                    cur["end"] = bts
        elif mtype == "idle" and cur is not None:
            cur["end"] = ts
            cur["state"] = "aborted" if d.get("outcome") == "interrupted" else "done"

    if turns:
        if busy:
            turns[-1]["state"] = "working"
        elif turns[-1]["state"] == "working":
            turns[-1]["state"] = "done"

    return _turns_to_map(
        "opencode", "OpenCode", "opencode:" + oc_sid, db_path, model or "OpenCode",
        turns, alive or busy, first_ts, last_ts, words, words_ts, asked=asked, source="db", helpers_count=helpers_count,
    )


# ── GitHub Copilot CLI ────────────────────────────────────
# 기록: ~/.copilot/session-state/<sid>/events.jsonl + workspace.yaml + ~/.copilot/open-sessions-state.json

COPILOT_DIR = os.path.join(HOME, ".copilot")
COPILOT_SESSIONS = os.path.join(COPILOT_DIR, "session-state")


def _parse_simple_yaml(path):
    out = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if ":" in line and not line.startswith(" "):
                    k, v = line.split(":", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def copilot_events_of(sid):
    p = os.path.join(COPILOT_SESSIONS, sid, "events.jsonl")
    return p if os.path.exists(p) else None


def copilot_sessions():
    """떠 있는 copilot CLI 프로세스와 ~/.copilot/open-sessions-state.json 에서 세션을 찾는다."""
    if not os.path.isdir(COPILOT_SESSIONS):
        return []
    open_state = {}
    ost_path = os.path.join(COPILOT_DIR, "open-sessions-state.json")
    if os.path.exists(ost_path):
        try:
            open_state = json.load(open(ost_path, encoding="utf-8"))
        except Exception:
            open_state = {}

    term_by_sid, term_by_cwd = {}, {}
    try:
        ps = subprocess.run(["ps", "-axo", "pid=,comm=,args="], capture_output=True, text=True, timeout=3).stdout
        for line in ps.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 2:
                continue
            pid, comm = parts[0], os.path.basename(parts[1])
            args = parts[2] if len(parts) > 2 else ""
            if comm != "copilot" and "gh-copilot" not in args and "@github/copilot" not in args:
                continue
            term = terminal_of(pid)
            cwd, files = _lsof_info(pid)
            matched = False
            for fp in files:
                m = re.search(r"/\.copilot/session-state/(?:\.session-operation-locks/)?([0-9a-f-]+)(?:\.lock|/|$)", fp)
                if m:
                    term_by_sid[m.group(1)] = (pid, term, cwd)
                    matched = True
                    break
            if not matched and cwd and term:
                term_by_cwd.setdefault(cwd, []).append((pid, term))
    except Exception:
        pass

    out = []
    now_t = time.time()
    used_terms = set(v[1] for v in term_by_sid.values() if v[1])
    for entry in os.listdir(COPILOT_SESSIONS):
        if entry.startswith("."):
            continue
        sdir = os.path.join(COPILOT_SESSIONS, entry)
        ev_path = os.path.join(sdir, "events.jsonl")
        if not os.path.isdir(sdir) or not os.path.exists(ev_path):
            continue
        ws = _parse_simple_yaml(os.path.join(sdir, "workspace.yaml"))
        cwd = ws.get("cwd") or ""
        ost = open_state.get(entry) if isinstance(open_state, dict) else None
        working = bool(isinstance(ost, dict) and ost.get("working"))
        term = None
        if entry in term_by_sid:
            term = term_by_sid[entry][1]
            cwd = cwd or term_by_sid[entry][2]
        elif cwd and cwd in term_by_cwd:
            for _pid, cand in term_by_cwd[cwd]:
                if cand not in used_terms:
                    term = cand
                    used_terms.add(cand)
                    break
        upd = os.path.getmtime(ev_path)
        if not term and not working and (now_t - upd > 24 * 3600):
            continue
        out.append({
            "id": entry, "name": ws.get("summary") or ws.get("name") or "",
            "cwd": cwd, "terminal": term, "working": working,
            "status": "busy" if working else "idle", "updated": upd, "events": ev_path,
        })
    return out


def _copilot_parse_args(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return {}


def _copilot_tool_act(tname, args, cur):
    tl = str(tname or "").lower()
    if tl in ("bash", "shell", "run_in_terminal", "powershell", "exec"):
        cmd = str(args.get("command") or args.get("cmd") or "")
        if "git commit" in cmd:
            return "커밋", "commit"
        if TEST_CMD.search(cmd):
            return "시험 돌림", "test"
        if re.search(r"\b(sed -i|apply_patch|cat\s*>)", cmd):
            return "파일 고침", ""
        return "명령 돌림", ""
    if tl in ("edit", "write", "create", "str_replace_editor", "replace_string_in_file", "create_file", "apply_patch"):
        p = str(args.get("path") or args.get("file_path") or args.get("filePath") or "")
        base = os.path.basename(p)
        if base and base not in cur["files"] and len(cur["files"]) < 40:
            cur["files"].append(base)
        return (f"시험 파일 {base} 씀" if TEST_FILE.search(p) else (f"{base} 고침" if base else "파일 고침")), ""
    if tl in ("view", "read", "read_file", "grep", "glob", "list_dir", "find"):
        return "코드 읽음", ""
    if tl in ("task", "subagent", "agent"):
        desc = short(args.get("description") or "도우미", 30)
        cur["helpers"].append(desc)
        return f"도우미({desc})", ""
    if tl in ("ask_user", "ask_questions"):
        return "질문함", "ask"
    return (tname or "도구"), ""


def build_copilot_map(path, alive, sid="", working_hint=False):
    turns, model, first_ts, last_ts = [], "", None, None
    cur, pending = None, {}
    words, words_ts = "", None
    asked = None
    helpers_count = 0
    for d in iter_jsonl(path):
        ts = d.get("timestamp") or (d.get("data") or {}).get("startTime")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        t = str(d.get("type") or "")
        data = d.get("data") or {}
        if t == "session.start":
            sid = data.get("sessionId") or sid
        elif t == "session.model_change":
            model = data.get("newModel") or model
        elif t == "user.message":
            txt = str(data.get("content") or data.get("text") or "").strip()
            if not txt:
                continue
            if cur is not None and cur["state"] == "working":
                cur["end"] = cur["end"] or ts
                cur["state"] = "done"
            prev_ans = turns[-1]["answer"] if turns else ""
            cur = {"start": ts, "end": None, "prompt": txt, "acts": [], "answer": "", "state": "working",
                   "fail": None, "pass": None, "first": "", "asked": ask_context(prev_ans),
                   "files": [], "helpers": [], "commits": 0}
            turns.append(cur)
            asked = None
        elif t == "assistant.message":
            txt = str(data.get("content") or data.get("text") or "").strip()
            if txt:
                if hangul(txt) or not words:
                    words, words_ts = txt[:1200], ts
                if cur is not None:
                    if not cur["first"]:
                        cur["first"] = first_sentence(clean_md(txt, 400), 160)
                    cur["answer"] = txt[:1500]
                    cur["end"] = ts
            for trq in data.get("toolRequests") or []:
                if cur is None or not isinstance(trq, dict):
                    continue
                tid = trq.get("toolCallId") or trq.get("id") or ""
                tname = trq.get("name") or ""
                args = _copilot_parse_args(trq.get("arguments"))
                act, pkind = _copilot_tool_act(tname, args, cur)
                if tid:
                    pending[tid] = pkind
                if pkind == "ask":
                    asked = short(args.get("question") or "질문 답 기다리는 중", 160)
                if act and (not cur["acts"] or cur["acts"][-1][1] != act):
                    cur["acts"].append([ts, act])
        elif t == "tool.execution_start" and cur is not None:
            tid = data.get("toolCallId") or ""
            tname = data.get("toolName") or data.get("name") or ""
            args = _copilot_parse_args(data.get("arguments") or data.get("input"))
            act, pkind = _copilot_tool_act(tname, args, cur)
            if tid:
                pending[tid] = pkind
            if pkind == "ask":
                asked = short(args.get("question") or "질문 답 기다리는 중", 160)
            if act and (not cur["acts"] or cur["acts"][-1][1] != act):
                cur["acts"].append([ts, act])
            cur["end"] = ts
        elif t == "tool.execution_complete" and cur is not None:
            tid = data.get("toolCallId") or ""
            pkind = pending.pop(tid, "")
            out_txt = text_of(data.get("result") or data.get("output") or "")
            if pkind == "commit" and data.get("success", True):
                cur["commits"] += 1
            elif pkind == "test":
                if FAIL_OUT.search(out_txt) or data.get("success") is False:
                    if not cur["pass"]:
                        cur["fail"] = cur["fail"] or ts
                elif PASS_OUT.search(out_txt) or data.get("success") is True:
                    cur["pass"] = ts
            elif pkind == "ask":
                asked = None
            cur["end"] = ts
        elif t == "subagent.started":
            helpers_count += 1
        elif t in ("assistant.turn_end", "session.idle", "session.shutdown", "abort") and cur is not None:
            cur["end"] = ts
            cur["state"] = "aborted" if t == "abort" else "done"
            asked = None

    if turns and working_hint:
        turns[-1]["state"] = "working"

    csid = sid or os.path.basename(os.path.dirname(path))
    return _turns_to_map(
        "copilot", "Copilot", "copilot:" + csid, path, model or "Copilot",
        turns, alive or working_hint, first_ts, last_ts, words, words_ts, asked=asked, source="log", helpers_count=helpers_count,
    )


def main(argv):
    if len(argv) < 2 or argv[1] not in ("list", "map"):
        print(__doc__)
        return 2
    # 14일 넘게 안 쓴 읽기 · 훅 기록은 지운다(쌓이지 않게)
    for pat in (os.path.join(CACHE, "*.json"), os.path.join(HOOK_SESSIONS, "*.json"), os.path.join(HOOK_EVENTS, "*.jsonl")):
        for f in glob.glob(pat):
            try:
                if time.time() - os.path.getmtime(f) > 14 * 86400:
                    os.remove(f)
            except OSError:
                pass
    sessions = list_sessions()
    if argv[1] == "list":
        print(json.dumps([{k: s.get(k) for k in ("session", "name", "named", "status", "terminal", "updated", "cwd", "agent", "hooked")}
                          for s in sessions], ensure_ascii=False))
        return 0
    sid = term = None
    if "--session" in argv:
        sid = argv[argv.index("--session") + 1]
    if "--terminal" in argv:
        term = argv[argv.index("--terminal") + 1]
    s = next((x for x in sessions if (sid and x["session"] == sid) or (term and x["terminal"] == term)), None)
    if "--codex-file" in argv and not s:
        s = {"session": "codex:file", "name": "", "terminal": None, "transcript": argv[argv.index("--codex-file") + 1], "cwd": "", "agent": "codex"}
    if "--antigravity-file" in argv and not s:
        s = {"session": "agy:file", "name": "", "terminal": None, "transcript": argv[argv.index("--antigravity-file") + 1], "cwd": "", "agent": "antigravity"}
    if "--opencode-session" in argv and not s:
        oc_id = argv[argv.index("--opencode-session") + 1]
        oc_db = argv[argv.index("--opencode-db") + 1] if "--opencode-db" in argv else OPENCODE_DB
        s = {"session": "opencode:" + oc_id, "name": "", "terminal": None, "transcript": oc_db, "cwd": "", "agent": "opencode"}
    if "--copilot-file" in argv and not s:
        s = {"session": "copilot:file", "name": "", "terminal": None, "transcript": argv[argv.index("--copilot-file") + 1], "cwd": "", "agent": "copilot"}
    if not s and sid:
        if sid.startswith("agy:"):
            tr = agy_transcript_of(sid[4:])
            s = {"session": sid, "name": "", "terminal": None, "transcript": tr, "cwd": "", "named": False, "agent": "antigravity"}
        elif sid.startswith("opencode:"):
            s = {"session": sid, "name": "", "terminal": None, "transcript": OPENCODE_DB if os.path.exists(OPENCODE_DB) else None,
                 "cwd": "", "named": False, "agent": "opencode"}
        elif sid.startswith("copilot:"):
            s = {"session": sid, "name": "", "terminal": None, "transcript": copilot_events_of(sid[8:]),
                 "cwd": "", "named": False, "agent": "copilot"}
        else:
            hst = read_hook_state(sid)
            s = {"session": sid, "name": "", "terminal": hst.get("terminal") if hst else None,
                 "transcript": (hst.get("transcript") if hst else None) or transcript_of(sid),
                 "cwd": hst.get("cwd", "") if hst else "", "named": False, "hooked": bool(hst)}
    if not s or (not s.get("transcript") and not s.get("hooked")):
        print(json.dumps({"error": "no-session", "terminal": term, "session": sid}, ensure_ascii=False))
        return 0
    global SESSION_ALIVE
    SESSION_ALIVE = "status" in s
    agent = s.get("agent") or ""
    if agent == "codex" or str(s.get("session", "")).startswith("codex:") or "--codex-file" in argv:
        path = argv[argv.index("--codex-file") + 1] if "--codex-file" in argv else s["transcript"]
        m = build_codex_map(path, SESSION_ALIVE and "--codex-file" not in argv)
        print(json.dumps(_finalize_external_map(m, s), ensure_ascii=False))
        return 0
    if agent == "antigravity" or str(s.get("session", "")).startswith("agy:") or "--antigravity-file" in argv:
        path = argv[argv.index("--antigravity-file") + 1] if "--antigravity-file" in argv else s["transcript"]
        cid = str(s.get("session", ""))[4:] if str(s.get("session", "")).startswith("agy:") else ""
        m = build_antigravity_map(path, SESSION_ALIVE and "--antigravity-file" not in argv, sid=cid)
        print(json.dumps(_finalize_external_map(m, s), ensure_ascii=False))
        return 0
    if agent == "opencode" or str(s.get("session", "")).startswith("opencode:") or "--opencode-session" in argv:
        oc_id = str(s.get("session", ""))[9:] if str(s.get("session", "")).startswith("opencode:") else argv[argv.index("--opencode-session") + 1]
        m = build_opencode_map(s["transcript"], oc_id, SESSION_ALIVE)
        print(json.dumps(_finalize_external_map(m, s), ensure_ascii=False))
        return 0
    if agent == "copilot" or str(s.get("session", "")).startswith("copilot:") or "--copilot-file" in argv:
        path = argv[argv.index("--copilot-file") + 1] if "--copilot-file" in argv else s["transcript"]
        cid = str(s.get("session", ""))[8:] if str(s.get("session", "")).startswith("copilot:") else ""
        m = build_copilot_map(path, SESSION_ALIVE and "--copilot-file" not in argv, sid=cid, working_hint=bool(s.get("working_hint")))
        print(json.dumps(_finalize_external_map(m, s), ensure_ascii=False))
        return 0
    hook_state = read_hook_state(s["session"])
    m = cached_map(s["session"], s.get("transcript"), hook_state=hook_state)
    overlay_live(m, s.get("status") or "", hook_state=hook_state)
    helper_running = any(h for h in (m.get("now") or []) if h.get("name") != "세션이 직접")
    bind_turns(m, m.get("status") == "working" and not helper_running)
    m.pop("turns", None)
    m.update(name=s.get("name") or "", named=s.get("named"), terminal=s.get("terminal"), cwd=s.get("cwd"))
    print(json.dumps(m, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
