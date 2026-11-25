#!/usr/bin/env python3
"""where was i — Claude Code 훅 기록기(토큰 0).

표준 입력(stdin)으로 Claude Code 훅 JSON 을 받아 로컬 캐시(~/.cache/where-was-i/hooks/)에만 적는다.
표준 출력(stdout)에는 아무것도 쓰지 않고 항상 0 으로 끝나므로 대화에 토큰을 넣지 않으며
Claude Code 동작을 막지 않는다.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
HOOK_DIR = os.path.join(HOME, ".cache", "where-was-i", "hooks")
SESSIONS_DIR = os.path.join(HOOK_DIR, "sessions")
EVENTS_DIR = os.path.join(HOOK_DIR, "events")

TEST_FILE = re.compile(r"(_test\.|\.test\.|\.spec\.|/tests?/|test_[^/]*\.py$)")
TEST_CMD = re.compile(r"(go test|pytest|unittest|vitest|jest|pnpm (run )?test|npm (run )?test|gradlew? .*test|make test)")
FAIL_OUT = re.compile(r"(\bFAIL\b|\bfailed\b|FAILED|Error Trace|panic:|AssertionError|✗)")
PASS_OUT = re.compile(r"((^|\n)ok\s|\bPASS\b|\bpassed\b|BUILD SUCCESSFUL|Ran \d+ tests?[\s\S]*\nOK\b)")
PLAN_FILE = re.compile(r"(plans?/|plan-|계획)[^/]*\.md$")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def safe_id(sid):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(sid or ""))[:120]


def short(s, n=160):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def find_claude_pid():
    """훅을 띄운 조상 프로세스 중 claude PID 를 찾는다(세션당 한 번만 호출)."""
    env_pid = os.environ.get("WWI_SESSION_PID")
    if env_pid and env_pid.isdigit():
        return int(env_pid)
    pid = os.getppid()
    for _ in range(6):
        if pid <= 1:
            break
        try:
            out = subprocess.run(
                ["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                capture_output=True, text=True, timeout=1,
            ).stdout.strip()
        except Exception:
            break
        if not out:
            break
        parts = out.split(None, 1)
        if len(parts) < 2:
            break
        ppid_s, comm = parts[0], parts[1].strip()
        base = os.path.basename(comm).lower()
        if base == "claude" or base.startswith("claude-"):
            return pid
        try:
            pid = int(ppid_s)
        except ValueError:
            break
    return None


def write_session_meta(sid, data, ended=False, force_pid=False):
    """세션 기본 정보(herdr 칸 · PID · 기록 파일 경로)를 원자적으로 저장한다."""
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    path = os.path.join(SESSIONS_DIR, safe_id(sid) + ".json")
    cur = {}
    try:
        with open(path, encoding="utf-8") as f:
            cur = json.load(f)
    except Exception:
        cur = {}

    term = os.environ.get("HC_TERMINAL_ID") or cur.get("terminal") or None
    tr = data.get("transcript_path") or cur.get("transcript_path") or None
    cwd = data.get("cwd") or cur.get("cwd") or ""
    pid = cur.get("pid")
    if (not pid and not ended) or force_pid:
        found = find_claude_pid()
        if found:
            pid = found

    now_t = time.time()
    meta = {
        "session_id": sid,
        "transcript_path": tr,
        "cwd": cwd,
        "terminal": term,
        "pid": pid,
        "started_at": cur.get("started_at") or now_iso(),
        "updated_at": now_t,
        "ended": bool(ended),
    }
    if ended:
        meta["ended_at"] = now_iso()

    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass


def ensure_session_meta(sid, data):
    path = os.path.join(SESSIONS_DIR, safe_id(sid) + ".json")
    if not os.path.exists(path):
        write_session_meta(sid, data, ended=False, force_pid=True)


def append_event(sid, ev):
    """사건 한 줄을 events/<sid>.jsonl 뒤에 붙인다(O_APPEND, 잠금 불필요)."""
    os.makedirs(EVENTS_DIR, exist_ok=True)
    path = os.path.join(EVENTS_DIR, safe_id(sid) + ".jsonl")
    line = json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def summarize_tool_input(tool, inp):
    """도구 입력에서 지도에 필요한 최소 정보만 뽑는다(파일 내용 · 비밀 값은 적지 않는다)."""
    if not isinstance(inp, dict):
        return {}
    if tool in ("Edit", "Write", "MultiEdit"):
        p = str(inp.get("file_path") or "")
        base = os.path.basename(p)
        info = {"file": base}
        if TEST_FILE.search(p):
            info["test"] = True
            info["act"] = f"시험 파일 {base} 쓰는 중"
        elif re.search(r"report|보고", base, re.I):
            info["act"] = "보고서 쓰는 중"
        else:
            info["act"] = f"{base} 고치는 중" if base else "파일 고치는 중"
        if tool == "Write" and PLAN_FILE.search(p):
            info["plan"] = True
            info["path"] = p
        return info
    if tool == "Bash":
        c = str(inp.get("command") or "")
        info = {}
        sm = re.search(r":\s*wwi-step\s+(plan|now|done)\s*(.*?)(?:&&|;|\n|$)", c)
        if sm:
            kind = sm.group(1)
            if kind == "plan":
                info["step"] = ["plan", re.findall(r'"([^"]+)"', sm.group(2))]
            elif kind == "now":
                try:
                    info["step"] = ["now", int(sm.group(2).split()[0])]
                except Exception:
                    pass
            else:
                info["step"] = ["done", 999]
        if "git commit" in c:
            info["commit"] = True
            info["act"] = "커밋하는 중"
        elif TEST_CMD.search(c):
            info["test"] = True
            info["act"] = "시험 돌리는 중"
        elif re.search(r"\b(sed -i|apply_patch|python3? .*write|cat >)", c):
            info["edit"] = True
            info["act"] = "파일 고치는 중"
        else:
            info["act"] = "명령 돌리는 중"
        # 권한 확인 안내에 쓸 짧은 명령 앞부분(환경변수/토큰 대입은 제외)
        first_cmd = re.sub(r"\s+", " ", c.strip().splitlines()[0] if c.strip() else "")
        tokens = [tok for tok in first_cmd.split("&&")[0].split("|")[0].split() if "=" not in tok]
        info["brief"] = short(" ".join(tokens[:4]), 60)
        return info
    if tool in ("Read", "Grep", "Glob"):
        return {"act": "코드 읽는 중"}
    if tool == "AskUserQuestion":
        qs = inp.get("questions") or []
        q0 = qs[0] if qs and isinstance(qs[0], dict) else {}
        labs = [o.get("label", "") for o in (q0.get("options") or []) if isinstance(o, dict)][:4]
        qtxt = short((q0.get("question") or "질문") + (" — " + " / ".join(l for l in labs if l) if labs else ""), 260)
        return {"q": qtxt, "act": "질문하는 중"}
    if tool == "Agent":
        return {
            "desc": short(inp.get("description") or "", 120),
            "atype": str(inp.get("subagent_type") or "")[:60],
            "model": str(inp.get("model") or "")[:60],
            "act": "서브에이전트 부르는 중",
        }
    if tool == "Skill":
        return {"skill": str(inp.get("skill") or "")[:80], "act": "스킬 실행 중"}
    if tool == "SubagentHandback":
        return {"act": "보고하고 끝냄"}
    return {"act": f"{tool} 쓰는 중"}


def text_from_response(resp):
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        parts = []
        for k in ("stdout", "stderr", "output", "text", "content", "message"):
            v = resp.get(k)
            if isinstance(v, str):
                parts.append(v)
        if parts:
            return "\n".join(parts)
        return json.dumps(resp, ensure_ascii=False)[:2000]
    if isinstance(resp, list):
        return "\n".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in resp)[:2000]
    return str(resp or "")[:2000]


def summarize_tool_result(tool, inp, resp):
    if tool == "Bash":
        c = str((inp or {}).get("command") or "") if isinstance(inp, dict) else ""
        if "git commit" in c:
            out = text_from_response(resp)
            if not re.search(r"(nothing to commit|error:|fatal:|hook failed)", out, re.I):
                return "commit"
            return ""
        if TEST_CMD.search(c):
            out = text_from_response(resp)
            if FAIL_OUT.search(out):
                return "fail"
            if PASS_OUT.search(out):
                return "pass"
        return ""
    if tool == "Agent":
        out = text_from_response(resp)
        if "Async agent launched" in out or "launched successfully" in out:
            return "async"
        return "done"
    if tool == "AskUserQuestion":
        out = text_from_response(resp)
        picks = re.findall(r'"([^"]{1,200})"="([^"]{1,300})"', out)
        return short(" / ".join(a for _, a in picks), 160) if picks else "answered"
    return ""


def handle(data):
    sid = data.get("session_id")
    if not sid:
        return
    ev_name = data.get("hook_event_name") or ""
    ts = now_iso()
    aid = data.get("agent_id") or ""

    if ev_name == "SessionStart":
        write_session_meta(sid, data, ended=False, force_pid=True)
        append_event(sid, {
            "ts": ts, "ev": "session_start",
            "term": os.environ.get("HC_TERMINAL_ID") or "",
            "cwd": data.get("cwd") or "",
            "tr": data.get("transcript_path") or "",
            "src": data.get("source") or "",
        })
        return

    if ev_name == "SessionEnd":
        write_session_meta(sid, data, ended=True)
        append_event(sid, {"ts": ts, "ev": "session_end", "reason": str(data.get("reason") or "")[:80]})
        return

    # 다른 훅이 먼저 들어와도 세션 ↔ herdr 칸 연결을 놓치지 않는다
    ensure_session_meta(sid, data)

    if ev_name == "UserPromptSubmit":
        append_event(sid, {"ts": ts, "ev": "prompt", "said": short(data.get("prompt") or "", 400)})
    elif ev_name == "PreToolUse":
        tool = str(data.get("tool_name") or "")
        info = summarize_tool_input(tool, data.get("tool_input"))
        rec = {"ts": ts, "ev": "pre_tool", "tool": tool, "id": data.get("tool_use_id") or "", "info": info}
        if aid:
            rec["aid"] = aid
        append_event(sid, rec)
    elif ev_name == "PostToolUse":
        tool = str(data.get("tool_name") or "")
        res = summarize_tool_result(tool, data.get("tool_input"), data.get("tool_response"))
        rec = {"ts": ts, "ev": "post_tool", "tool": tool, "id": data.get("tool_use_id") or ""}
        if res:
            rec["res"] = res
        if aid:
            rec["aid"] = aid
        append_event(sid, rec)
    elif ev_name == "PostToolUseFailure":
        rec = {
            "ts": ts, "ev": "tool_fail",
            "tool": str(data.get("tool_name") or ""),
            "id": data.get("tool_use_id") or "",
            "interrupt": bool(data.get("is_interrupt")),
        }
        if aid:
            rec["aid"] = aid
        append_event(sid, rec)
    elif ev_name == "SubagentStart":
        append_event(sid, {
            "ts": ts, "ev": "sub_start",
            "aid": aid,
            "atype": str(data.get("agent_type") or "")[:60],
        })
    elif ev_name == "SubagentStop":
        append_event(sid, {
            "ts": ts, "ev": "sub_stop",
            "aid": aid,
            "atype": str(data.get("agent_type") or "")[:60],
            "tr": data.get("agent_transcript_path") or "",
            "last": short(data.get("last_assistant_message") or "", 400),
        })
    elif ev_name == "PermissionRequest":
        tool = str(data.get("tool_name") or "")
        info = summarize_tool_input(tool, data.get("tool_input"))
        detail = info.get("brief") or info.get("file") or tool
        rec = {"ts": ts, "ev": "permission", "tool": tool, "msg": short(f"권한 확인 기다리는 중 · {tool}({detail})" if detail and detail != tool else f"권한 확인 기다리는 중 · {tool}", 160)}
        if aid:
            rec["aid"] = aid
        append_event(sid, rec)
    elif ev_name == "Notification":
        kind = str(data.get("notification_type") or "")
        if kind in ("permission_prompt", "elicitation_dialog", "idle_prompt"):
            append_event(sid, {
                "ts": ts, "ev": "notify",
                "kind": kind,
                "msg": short(data.get("message") or data.get("title") or "", 180),
            })
    elif ev_name == "Stop":
        if data.get("stop_hook_active"):
            return
        append_event(sid, {
            "ts": ts, "ev": "stop",
            "last": short(data.get("last_assistant_message") or "", 400),
        })


def main(argv):
    if len(argv) > 1 and argv[1] == "--self-check":
        # 설치 뒤 스스로 확인용(stdout 은 이 모드에서만 씀)
        print("ok")
        return 0
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        data = json.loads(raw)
        if isinstance(data, dict):
            handle(data)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
