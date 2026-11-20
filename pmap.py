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
            "commit": None, "steps": None, "now": "", "last_text": "", "tools": 0, "_pending": {}, "acts": []}


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
                elif kind[0] == "test":
                    if FAIL_OUT.search(out):
                        if not ev["pass"]:
                            ev["fail"] = ev["fail"] or ts
                    elif PASS_OUT.search(out):
                        ev["pass"] = ts
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


def update_main(tr, st):
    """세션 기록의 새 줄만 읽어 지도에 쓸 사건만 모은다."""
    lines, st["offset"], reset = read_new_lines(tr, st.get("offset", 0))
    if reset:
        st.clear()
        st["offset"] = 0
        lines, st["offset"], _ = read_new_lines(tr, 0)
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
            if x and not x.startswith(("<", "[SYSTEM", "Another Claude", "Caveat:", "[Request interrupted")):
                st["asked"] = None
                st["prompt"], st["prompt_ts"], st["seg"] = x[:400], ts, new_ev()
            continue
        st["seg"] = scan_lines([d], st.get("seg"))
        if not isinstance(content, list):
            continue
        for b in content:
            t = b.get("type")
            if t == "text" and d.get("type") == "assistant" and b.get("text", "").strip():
                txt = b["text"]
                if hangul(txt):
                    st["words"], st["words_ts"] = txt[:1200], ts
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
            elif name == "Agent":
                agent_ids.add(b.get("id"))
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
            "name": ph["name"], "date": md(ph["start"] or ph["items"][0]["start"]),
            "goal": ph["goal"], "src": ph["src"], "total": total, "done": done,
            "state": "working" if any(c["running"] for c in cards) else "done",
            "cards": [{
                "key": c["key"], "title": c["title"], "state": c["state"],
                "time": f'{hm(c["start"])} ~ {"" if c["running"] else hm(c["end"])}',
                "cells": c["cells"], "done_text": c["done_text"], "after": c.get("after", ""),
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
        "direct": {"prompt": first_sentence(last_prompt, 90), "since": hm(last_prompt_ts), "since_ts": last_prompt_ts,
                   "ev": main_ev},
    }


def cached_map(sid, tr):
    return build_map(sid, tr)


def overlay_live(m, live):
    """세션 파일의 지금 상태(busy · waiting · idle)를 덧씌우고, 세션이 직접 일하는 중이면 카드 하나를 더한다."""
    if live == "busy":
        m["status"] = "working"
    elif live == "waiting":
        m["status"] = "asking"
        m["asked"] = m.get("asked") or "확인 · 허락을 기다리는 중"
    elif live in ("idle", "shell", ""):
        m["status"] = "asking" if m.get("asked") else "idle"
    d = m.get("direct") or {}
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
        m["phases"].append({"name": "지금", "date": md(d.get("since_ts")), "goal": "", "src": "", "total": 0, "done": 0, "state": "working", "cards": []})
    ph = m["phases"][-1]
    ph["cards"].append(card)
    ph["total"] += 1
    ph["state"] = "working"


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
        print(json.dumps([{k: s[k] for k in ("session", "name", "named", "status", "terminal", "updated", "cwd")}
                          for s in sessions], ensure_ascii=False))
        return 0
    sid = term = None
    if "--session" in argv:
        sid = argv[argv.index("--session") + 1]
    if "--terminal" in argv:
        term = argv[argv.index("--terminal") + 1]
    s = next((x for x in sessions if (sid and x["session"] == sid) or (term and x["terminal"] == term)), None)
    if not s and sid:
        s = {"session": sid, "name": "", "terminal": None, "transcript": transcript_of(sid), "cwd": "", "named": False}
    if not s or not s.get("transcript"):
        print(json.dumps({"error": "no-session", "terminal": term, "session": sid}, ensure_ascii=False))
        return 0
    global SESSION_ALIVE
    SESSION_ALIVE = "status" in s
    m = cached_map(s["session"], s["transcript"])
    overlay_live(m, s.get("status") or "")
    m.update(name=s.get("name") or "", named=s.get("named"), terminal=s.get("terminal"), cwd=s.get("cwd"))
    print(json.dumps(m, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
