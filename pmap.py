#!/usr/bin/env python3
"""진행 지도 프로토타입 — Claude Code 대화 기록 파일을 읽어 세션 하나의 지도 자료(JSON)를 만든다.

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
CACHE = os.path.join(HOME, ".cache", "progress-map")
RUNNING_SEC = 120  # 도우미 기록이 이 안에 바뀌었으면 「일하는 중」

TEST_FILE = re.compile(r"(_test\.|\.test\.|\.spec\.|/tests?/|test_[^/]*\.py$)")
TEST_CMD = re.compile(r"(go test|pytest|unittest|vitest|jest|pnpm (run )?test|npm (run )?test|gradlew? .*test|make test)")
FAIL_OUT = re.compile(r"(\bFAIL\b|\bfailed\b|FAILED|Error Trace|panic:|AssertionError|✗)")
PASS_OUT = re.compile(r"((^|\n)ok\s|\bPASS\b|\bpassed\b|BUILD SUCCESSFUL|Ran \d+ tests?[\s\S]*\nOK\b)")
TNUM = re.compile(r"(?:^|[\s(])(?:\d+[A-Z]?\s+)?(?:Task\s*|T)(\d+(?:[-~][^\s]*)?[a-d가-힣]?)(?=[\s:·,)]|$)")
REVIEW = re.compile(r"(검토|review|리뷰)", re.I)
FIX = re.compile(r"(고침|고치기|고쳐|반영|fix)", re.I)
RESEARCH = re.compile(r"(조사|파악|설계|대조|Explore|찾기)", re.I)
PLAN_DESC = re.compile(r"계획서")


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
    """도우미 기록에서 흔적을 모은다: 시험 쓰기 · 실패 · 구현 · 통과 · 커밋 · 마지막 행동."""
    ev = {"first": None, "last": None, "test_write": None, "fail": None, "impl": None, "pass": None,
          "commit": None, "steps": None, "now": "", "last_text": "", "tools": 0}
    pending = {}
    for d in iter_jsonl(jsonl):
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
                    sm = re.search(r":\s*progress-step\s+(plan|now|done)\s*(.*?)(?:&&|;|\n|$)", c)
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


def build_map(sid, tr):
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
    helpers_by_id = {}
    first_ts = last_ts = None
    last_words, last_words_ts = "", None
    asked = None
    seen_ids = set()

    def new_phase(name, ts, src=""):
        cur = phases[-1]
        if not cur["items"] and cur["name"] == "처음":
            cur.update(name=name, start=ts, src=src)
            return
        # 30분 안에 이어 나온 경계는 하나로
        st = kst(cur["start"]) if cur["start"] else None
        if st and kst(ts) and (kst(ts) - st) < timedelta(minutes=30) and not cur["items"]:
            cur.update(name=name, src=src or cur["src"])
            return
        phases.append({"name": name, "start": ts, "items": [], "goal": "", "src": src})

    for d in iter_jsonl(tr):
        ts = d.get("timestamp")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        m = d.get("message") or {}
        content = m.get("content")
        if d.get("type") == "user" and isinstance(content, str) and not d.get("isMeta"):
            s = content.lstrip()
            if s and not s.startswith(("<", "[SYSTEM", "Another Claude", "Caveat:", "[Request interrupted")):
                asked = None
            continue
        if not isinstance(content, list):
            continue
        for b in content:
            t = b.get("type")
            if t == "text" and d.get("type") == "assistant" and b.get("text", "").strip():
                last_words, last_words_ts = b["text"], ts
            if t != "tool_use":
                continue
            bid = b.get("id")
            if bid in seen_ids:
                continue
            seen_ids.add(bid)
            name, inp = b.get("name"), b.get("input") or {}
            if name == "Skill" and inp.get("skill") in ("superpowers:writing-plans", "superpowers:brainstorming"):
                new_phase("설계 · 계획" if "brainstorm" in inp["skill"] else "계획", ts)
            elif name == "Write" and re.search(r"(plans?/|plan-|계획)[^/]*\.md$", inp.get("file_path", "")):
                title = ""
                m2 = re.search(r"^#\s+(.+)$", inp.get("content", ""), re.M)
                if m2:
                    title = m2.group(1)
                goal = re.search(r"\*\*Goal:\*\*\s*(.+)", inp.get("content", ""))
                new_phase(short(title or os.path.basename(inp["file_path"]), 60), ts, inp["file_path"])
                if goal:
                    phases[-1]["goal"] = first_sentence(goal.group(1), 160)
            elif name == "AskUserQuestion":
                qs = inp.get("questions") or []
                asked = short(qs[0].get("question") if qs else "질문", 120)
            elif name == "Agent":
                desc = inp.get("description") or ""
                meta = metas.get(bid) or {}
                kind = helper_kind(desc, inp.get("subagent_type") or meta.get("agentType"))
                if kind == "계획" or PLAN_DESC.search(desc):
                    label = re.sub(r"\s*(구현\s*)?계획서\s*(작성|쓰기)?\s*", " ", desc).strip() or "계획"
                    new_phase(short(label, 40), ts)
                job = first_sentence(re.sub(r"^너는[^.]*?\.\s*", "", inp.get("prompt") or ""), 150)
                h = {"id": bid, "name": desc, "kind": kind, "key": task_key(desc), "job": job, "start": ts,
                     "jsonl": meta.get("_jsonl"), "agentType": inp.get("subagent_type") or meta.get("agentType")}
                helpers_by_id[bid] = h
                phases[-1]["items"].append(h)

    now_t = time.time()
    running_helpers = []
    for ph in phases:
        for h in ph["items"]:
            ev = read_helper(h["jsonl"]) if h["jsonl"] else {"first": h["start"], "last": h["start"], "test_write": None,
                                                              "fail": None, "impl": None, "pass": None, "commit": None,
                                                              "steps": None, "now": "", "last_text": "", "tools": 0}
            running = (bool(h["jsonl"]) and os.path.exists(h["jsonl"]) and now_t - os.path.getmtime(h["jsonl"]) < RUNNING_SEC
                       and ev["now"] != "보고하고 끝냄")
            h.update(running=running, end=ev["last"], now=ev["now"] or ("일하는 중" if running else "끝"),
                     said=first_sentence(ev["last_text"], 160), cells=helper_cells(h["kind"], ev, running))
            if running:
                running_helpers.append(h)

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
            c["done_text"] = hs[-1]["said"]
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
                "cells": c["cells"], "done_text": c["done_text"],
                "helpers": [{
                    "kind": x["kind"], "name": x["name"], "job": x["job"], "state": "working" if x["running"] else "done",
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
    }


def cached_map(sid, tr):
    """기록이 안 바뀌었으면 지난 결과를 쓴다(큰 파일을 매번 훑지 않으려고)."""
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, sid + ".json")
    sub = tr[:-6] + "/subagents"
    newest = max([os.path.getmtime(tr)] + [os.path.getmtime(p) for p in glob.glob(sub + "/*.jsonl")]) if tr else 0
    try:
        c = json.load(open(cp))
        if c.get("_src_mtime") == newest and time.time() - c.get("_built_at", 0) < 300:
            return c
    except Exception:
        pass
    m = build_map(sid, tr)
    m["_src_mtime"], m["_built_at"] = newest, time.time()
    with open(cp + ".tmp", "w") as f:
        json.dump(m, f, ensure_ascii=False)
    os.replace(cp + ".tmp", cp)
    return m


def main(argv):
    if len(argv) < 2 or argv[1] not in ("list", "map"):
        print(__doc__)
        return 2
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
    m = cached_map(s["session"], s["transcript"])
    m.update(name=s.get("name") or "", named=s.get("named"), terminal=s.get("terminal"), cwd=s.get("cwd"))
    print(json.dumps(m, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
