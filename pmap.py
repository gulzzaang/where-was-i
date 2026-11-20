#!/usr/bin/env python3
"""where was i 프로토타입 — Claude Code 대화 기록 파일을 읽어 세션 하나의 지도 자료(JSON)를 만든다.

프로토타입이다. 설계서의 「지난 기록 채우기」 부분만 먼저 만든 것이고, 훅 기록은 다음 단계다.
쓰는 법:
  python3 pmap.py list                     떠 있는 Claude 세션 목록(JSON)
  python3 pmap.py map --terminal t29       herdr 칸 번호로 세션 지도
  python3 pmap.py map --session <id>       세션 id 로 지도
"""

import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
KST = timezone(timedelta(hours=9))
CACHE = os.path.join(HOME, ".cache", "where-was-i")
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
        out.append({
            "session": sid,
            "name": d.get("name") or "",
            "named": d.get("nameSource") == "user",
            "status": d.get("status") or "",
            "cwd": d.get("cwd") or "",
            "terminal": terminal_of(pid),
            "updated": (tr and os.path.getmtime(tr)) or (d.get("updatedAt") or 0) / 1000,
            "transcript": tr,
        })
    for c in codex_procs():
        out.append({"session": "codex:" + os.path.basename(c["rollout"]), "name": "", "named": False,
                    "status": "", "cwd": "", "terminal": c["terminal"], "agent": "codex",
                    "updated": os.path.getmtime(c["rollout"]), "transcript": c["rollout"]})
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
            "files": [], "commits": 0, "tests": ""}


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


RELEVANT = {"Skill", "Write", "AskUserQuestion", "Agent"}


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
            if x and not x.startswith(("<", "[SYSTEM", "Another Claude", "Caveat:", "[Request interrupted", "This session is being continued")) and not d.get("isCompactSummary"):
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
            elif name == "Write" and re.search(r"(plans?/|plan-|계획)[^/]*\.md$", inp.get("file_path", "")):
                c = inp.get("content", "")
                m2 = re.search(r"^#\s+(.+)$", c, re.M)
                goal = re.search(r"\*\*Goal:\*\*\s*(.+)", c)
                st["events"].append(["phase", ts, short(m2.group(1) if m2 else os.path.basename(inp["file_path"]), 60),
                                     inp["file_path"], first_sentence(goal.group(1), 160) if goal else ""])
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


def build_map(sid, tr):
    os.makedirs(CACHE, exist_ok=True)
    mpath, hpath = os.path.join(CACHE, sid + ".main.json"), os.path.join(CACHE, sid + ".helpers.json")
    st = update_main(tr, load_state(mpath, {}))
    save_state(mpath, st)
    hcache = load_state(hpath, {})

    sub_dir = tr[:-6] + "/subagents" if tr else None
    metas = {}
    if sub_dir and os.path.isdir(sub_dir):
        for mf in glob.glob(os.path.join(sub_dir, "*.meta.json")):
            try:
                meta = json.load(open(mf))
            except Exception:
                continue
            meta["_jsonl"] = mf[:-10] + ".jsonl"
            if meta.get("toolUseId"):
                metas[meta["toolUseId"]] = meta

    phases = [{"name": "처음", "start": None, "items": [], "goal": "", "src": ""}]

    def new_phase(name, ts, src="", goal=""):
        cur = phases[-1]
        st_ = kst(cur["start"]) if cur["start"] else None
        if (not cur["items"] and cur["name"] == "처음") or (st_ and kst(ts) and (kst(ts) - st_) < timedelta(minutes=30) and not cur["items"]):
            cur.update(name=name, start=cur["start"] or ts, src=src or cur["src"], goal=goal or cur["goal"])
            return
        phases.append({"name": name, "start": ts, "items": [], "goal": goal, "src": src})

    for e in st["events"]:
        if e[0] == "phase":
            new_phase(e[2], e[1], e[3], e[4])
        elif e[0] == "agent":
            _, ts, bid, desc, atype, job, model = (e + [""])[:7]
            meta = metas.get(bid) or {}
            kind = helper_kind(desc, atype or meta.get("agentType"))
            if kind == "계획" or PLAN_DESC.search(desc):
                label = re.sub(r"\s*(구현\s*)?계획서\s*(작성|쓰기)?\s*", " ", desc).strip() or "계획"
                new_phase(short(label, 40), ts)
            phases[-1]["items"].append({"id": bid, "name": desc, "kind": kind, "key": task_key(desc), "job": job, "start": ts,
                                        "jsonl": meta.get("_jsonl"), "model": model_name(model or meta.get("pickedEffortModel"))})

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
            # 끝 알림이 없고 보고도 안 했으면 일하는 중(오래 걸리는 명령으로 기록이 한동안 안 바뀌어도).
            # 세션이 이미 꺼졌거나 6시간 넘게 기록이 안 바뀌었으면 멈춘 것으로 본다.
            fresh = bool(h["jsonl"]) and os.path.exists(h["jsonl"]) and now_t - os.path.getmtime(h["jsonl"]) < 6 * 3600
            running = (not ended) and ev["now"] != "보고하고 끝냄" and fresh and SESSION_ALIVE
            h["ended"] = ended or ""
            h["after"] = (st.get("after") or {}).get(h["id"], "")
            h["report"] = ev.get("report", "")
            stopped = ended and ended != "completed"
            h.update(running=running, end=ev["last"],
                     now=(f"멈춤({ended})" if stopped else (ev["now"] or ("일하는 중" if running else "끝"))),
                     said=first_sentence(re.sub(r"(?mi)^\W*(상태|status|결과)\s*[:：].*$", "", ev.get("report") or "").strip() or ev["last_text"], 160), cells=helper_cells(h["kind"], ev, running))
            if running:
                running_helpers.append(h)
    save_state(hpath, hcache)
    first_ts, last_ts = st.get("first_ts"), st.get("last_ts")
    last_words, last_words_ts, asked = st.get("words", ""), st.get("words_ts"), st.get("asked")
    last_prompt, last_prompt_ts = st.get("prompt", ""), st.get("prompt_ts")
    main_ev = st.get("seg") or new_ev()

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

    status = "working" if running_helpers or (last_ts and now_t - (kst(last_ts).timestamp()) < 60) else "idle"
    if asked:
        status = "asking"
    started = kst(first_ts)
    days = (datetime.now(KST).date() - started.date()).days + 1 if started else 0
    return {
        "session": sid, "transcript": tr,
        "started": md(first_ts), "days": days, "helpers": sum(len(p["items"]) for p in phases),
        "status": status, "asked": asked,
        "last_words": first_sentence(last_words, 200), "last_words_at": hm(last_words_ts),
        "now": [{"name": h["name"], "now": h["now"],
                 "mins": int((now_t - kst(h["start"]).timestamp()) // 60) if kst(h["start"]) else 0}
                for h in running_helpers],
        "phases": out_phases,
        "built": datetime.now(KST).strftime("%H:%M:%S"),
        "turns": [turn_out(tn, False) for tn in (st.get("turns") or [])],
        "direct": {"prompt": (first_sentence(last_prompt, 90) if not is_short(last_prompt) or not (st.get("turns") or [{}])[-1].get("asked")
                              else "「" + last_prompt.strip()[:20] + "」 → " + short((st.get("turns") or [{}])[-1].get("asked", ""), 120)),
                   "since": hm(last_prompt_ts), "since_ts": last_prompt_ts,
                   "ev": main_ev},
    }


def cached_map(sid, tr):
    return build_map(sid, tr)


def direct_card(tn, live=False):
    """세션이 도우미 없이 직접 한 지시 → 카드 하나."""
    title = tn["said"] if not tn["short"] else (tn.get("asked") or tn.get("first") or tn["said"])
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


def overlay_live(m, live):
    """세션 파일의 지금 상태(busy · waiting · idle)를 덧씌우고, 세션이 직접 일하는 중이면 카드 하나를 더한다."""
    if live == "busy":
        m["status"] = "working"
    elif live == "waiting":
        m["status"] = "asking"
        m["asked"] = m.get("asked") or "확인 · 허락을 기다리는 중"
    elif live in ("idle", "shell", ""):
        m["status"] = "asking" if m.get("asked") else "idle"
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


def main(argv):
    if len(argv) < 2 or argv[1] not in ("list", "map"):
        print(__doc__)
        return 2
    # 14일 넘게 안 쓴 읽기 기록은 지운다(쌓이지 않게)
    for f in glob.glob(os.path.join(CACHE, "*.json")):
        try:
            if time.time() - os.path.getmtime(f) > 14 * 86400:
                os.remove(f)
        except OSError:
            pass
    sessions = list_sessions()
    if argv[1] == "list":
        print(json.dumps([{k: s.get(k) for k in ("session", "name", "named", "status", "terminal", "updated", "cwd", "agent")}
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
    if not s and sid:
        s = {"session": sid, "name": "", "terminal": None, "transcript": transcript_of(sid), "cwd": "", "named": False}
    if not s or not s.get("transcript"):
        print(json.dumps({"error": "no-session", "terminal": term, "session": sid}, ensure_ascii=False))
        return 0
    global SESSION_ALIVE
    SESSION_ALIVE = "status" in s
    if s.get("agent") == "codex" or str(s.get("session", "")).startswith("codex:") or "--codex-file" in argv:
        path = argv[argv.index("--codex-file") + 1] if "--codex-file" in argv else s["transcript"]
        m = build_codex_map(path, SESSION_ALIVE and "--codex-file" not in argv)
        for p_ in m.get("phases") or []:
            for c_, t_ in zip(p_["cards"], m.get("turns") or []):
                c_["turns"] = [t_]
        m.pop("turns", None)
        m.update(name=s.get("name") or "", named=False, terminal=s.get("terminal"), cwd=s.get("cwd"))
        print(json.dumps(m, ensure_ascii=False))
        return 0
    m = cached_map(s["session"], s["transcript"])
    overlay_live(m, s.get("status") or "")
    helper_running = any(h for h in (m.get("now") or []) if h.get("name") != "세션이 직접")
    bind_turns(m, m.get("status") == "working" and not helper_running)
    m.pop("turns", None)
    m.update(name=s.get("name") or "", named=s.get("named"), terminal=s.get("terminal"), cwd=s.get("cwd"))
    print(json.dumps(m, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
