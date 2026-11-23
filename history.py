"""세션을 넘어 지난 기록을 모은다 — 되풀이 지적 · 일지 · 지난 세션 찾기(토큰 0).

Claude Code 대화 기록(~/.claude/projects/*/<세션>.jsonl)을 새로 붙은 줄만 이어 읽어
지시마다 「내 말 · 바로 앞 AI 질문 · AI 가 알아들은 것 · 커밋」을 모으고
~/.cache/where-was-i/history/<세션>.json 에 둔다. AI 를 부르지 않는다.
"""
import glob
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
SESSIONS = os.path.join(HOME, ".claude", "sessions")
CACHE = os.path.join(HOME, ".cache", "where-was-i", "history")
VERSION = 1
KST = timezone(timedelta(hours=9))
SKIP_PREFIX = ("<", "[SYSTEM", "Another Claude", "Caveat:", "[Request interrupted", "This session is being continued", "The user doesn")
SHORT_OK = re.compile(r"^(ㅇㅇ|ㄱㄱ|ㅇㅋ|ㄱ|응|ㅇ|네|예|그래|좋아|좋다|진행|고고|ok|오케이|넵|ㄴㄴ|아니|ㅇㅇ\s*ㄱㄱ)", re.I)
QUESTION = re.compile(r"(\?|까요|여쭤|골라 주|정해 주)")
SENTENCE = re.compile(r".+?(?:[.!?。]|니다|어요|해요)(?=\s|$)")
# 바로잡는 말 · 사과(낱말로만 짐작한다)
# 「~하지 말고 ~해줘」 같은 조건 단 지시는 바로잡는 말이 아니다 — 말머리 부정 · 「왜 ~했어」 · 「말했잖아」 · 「몇 번」 등만
CORRECTION = re.compile(r"(^\s*(아니|아냐|아닌데|그게 아니)|왜 .{0,20}(했|안 ?했|그랬|안 ?해|안 ?하)(어|냐|지|니|나|음|는지)|"
                        r"말했잖|했잖아|라고 했잖|랬잖|몇 ?번(을|이나|째)|틀렸잖|잘못했|빼먹|놓쳤|확인 ?안 ?하|^\s*또 )")
LONG = 300  # 이보다 긴 말은 지시 · 붙여 넣은 글로 보고 바로잡는 말에서 뺀다
APOLOGY = re.compile(r"^\s*(죄송|사과드|제가 잘못|잘못했|실수했)")
# 묶을 때 빼는 흔한 말(앞 두 글자)
STOP = {"아니", "다시", "그냥", "지금", "이거", "그거", "저거", "하고", "하는", "해줘", "했어", "했잖", "근데", "그리고", "이렇",
        "저렇", "그렇", "했는", "하지", "안하", "말고", "제발", "진짜", "계속", "아까", "방금", "어떻", "그래", "이게", "왜이", "있는",
        "없는", "같은", "이건", "그건", "해서", "하면", "해야", "했네", "했다", "합니", "니다", "죄송"}


def commit_message(cmd):
    """git commit 명령에서 커밋 글 첫 줄을 꺼낸다(heredoc 먼저, 그다음 -m "…")."""
    m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?\s*\n\s*(\S[^\n]*)", cmd or "")
    if m:
        return m.group(2).strip()
    m = re.search(r"-m\s+([\"'])(.*?)\1", cmd or "", re.S)
    return m.group(2).strip().splitlines()[0] if m and m.group(2).strip() else ""


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _first_sentence(txt, n=160):
    t = re.sub(r"\s+", " ", re.sub(r"[*`#>]", "", txt or "")).strip()
    m = SENTENCE.search(t)
    return (m.group(0) if m else t)[:n]


def _question(txt):
    """AI 글의 마지막 질문 문장."""
    lines = [l.strip() for l in str(txt or "").splitlines() if l.strip()]
    for l in reversed(lines):
        if QUESTION.search(l):
            sents = [s.strip() for s in re.split(r"(?<=[.!?。])\s+", re.sub(r"[*`]", "", l)) if s.strip()]
            q = next((s for s in reversed(sents) if QUESTION.search(s)), l)
            return q[:200]
    return ""


def _short(said):
    t = str(said or "").strip()
    return len(t) <= 15 or (len(t) <= 30 and bool(SHORT_OK.match(t)))


def _open(st, ts, said, kind, asked):
    st.setdefault("turns", []).append({"ts": ts, "said": said[:400], "kind": kind, "asked": asked, "first": "", "last": "", "commits": []})
    del st["turns"][:-500]
    st["last_q"] = ""


def scan(path, st):
    """기록의 새 줄만 읽어 지시를 모은다. st 는 캐시(offset · turns · 짝 정보)."""
    st = dict(st or {})
    try:
        size = os.path.getsize(path)
    except OSError:
        return st
    if st.get("v") != VERSION or size < st.get("offset", 0):
        st = {"v": VERSION}
    off = st.get("offset", 0)
    with open(path, "rb") as f:
        f.seek(off)
        data = f.read()
    cut = data.rfind(b"\n")
    if cut < 0:
        return st
    st["offset"] = off + cut + 1
    for raw in data[: cut + 1].splitlines():
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        ts, typ = d.get("timestamp"), d.get("type")
        content = (d.get("message") or {}).get("content")
        if typ == "user" and isinstance(content, str):
            x = content.strip()
            if x and not d.get("isMeta") and not d.get("isCompactSummary") and not x.startswith(SKIP_PREFIX):
                q = st.get("last_q", "")
                answer = bool(q) and _short(x)
                _open(st, ts, x, "answer" if answer else "direct", q if answer else "")
            continue
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if typ == "user" and bt == "tool_result" and st.get("pending_ask") and b.get("tool_use_id") == st["pending_ask"]["id"]:
                r = _text(b.get("content")) if not isinstance(b.get("content"), str) else b.get("content")
                picks = re.findall(r'"([^"]{1,200})"="([^"]{1,300})"', r or "")
                pa = st.pop("pending_ask")
                if picks:
                    _open(st, ts, " / ".join(a for _, a in picks), "answer", pa["q"])
            elif typ == "assistant" and bt == "text" and re.search(r"[가-힣]", b.get("text", "")):
                txt = b["text"]
                if st.get("turns"):
                    tn = st["turns"][-1]
                    if not tn["first"]:
                        tn["first"] = _first_sentence(txt)
                    tn["last"] = _first_sentence(txt, 200)
                st["last_q"] = _question(txt)
            elif typ == "assistant" and bt == "tool_use":
                name, inp = b.get("name"), b.get("input") or {}
                if name == "AskUserQuestion":
                    q0 = (inp.get("questions") or [{}])[0]
                    labs = [o.get("label", "") for o in (q0.get("options") or [])][:4]
                    st["pending_ask"] = {"id": b.get("id"), "q": (q0.get("question") or "질문")[:200] + (" — " + " / ".join(labs) if labs else "")}
                elif name == "Bash" and "git commit" in (inp.get("command") or "") and st.get("turns"):
                    msg = commit_message(inp.get("command"))
                    if msg:
                        st["turns"][-1]["commits"].append(msg[:200])
    return st


def _names():
    """떠 있는 세션의 이름(세션 정보 파일)."""
    out = {}
    for f in glob.glob(os.path.join(SESSIONS, "*.json")):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        if d.get("sessionId") and d.get("name"):
            out[d["sessionId"]] = d["name"]
    return out


def collect(since_epoch):
    """since_epoch 뒤에 바뀐 세션 기록을 이어 읽어 [(sid, name, turns)] 를 낸다."""
    os.makedirs(CACHE, exist_ok=True)
    names = _names()
    out = []
    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        try:
            if os.path.getmtime(path) < since_epoch:
                continue
        except OSError:
            continue
        sid = os.path.basename(path)[:-6]
        cpath = os.path.join(CACHE, re.sub(r"[^A-Za-z0-9._-]", "_", sid)[:120] + ".json")
        try:
            old = json.load(open(cpath))
        except (OSError, ValueError):
            old = {}
        st = scan(path, old)
        if st.get("offset") != old.get("offset"):
            with open(cpath + ".tmp", "w") as f:
                json.dump(st, f, ensure_ascii=False)
            os.replace(cpath + ".tmp", cpath)
        turns = st.get("turns") or []
        if not turns:
            continue
        name = names.get(sid) or next((t["said"][:30] for t in turns if t["kind"] == "direct"), sid[:8])
        out.append((sid, name, turns))
    return out


def _kst(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(KST)
    except ValueError:
        return None


def _stems(s):
    out = set()
    for w in re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", s or ""):
        k = w[:2] if re.match(r"[가-힣]", w) else w.lower()
        if k not in STOP:
            out.add(k)
    return out


def nags(turns):
    """바로잡는 말 · 사과가 붙은 지시를 비슷한 것끼리 묶는다(낱말 앞 두 글자가 겹치면 같은 지적)."""
    picked = [t for t in turns if len(t.get("said") or "") <= LONG
              and (CORRECTION.search(t.get("said") or "") or APOLOGY.match(t.get("first") or ""))]
    picked.sort(key=lambda t: t.get("ts") or "")
    groups = []
    for t in picked:
        s = _stems(t["said"])
        best = None
        for g in groups:
            shared = s & g["stems"]
            if len(shared) >= 2 and len(shared) / max(1, len(s | g["stems"])) >= 0.34:
                best = g
                break
        if best is None:
            best = {"stems": set(s), "items": []}
            groups.append(best)
        best["items"].append(t)
        best["stems"] |= s

    def view(t):
        k = _kst(t.get("ts"))
        return {"date": k.strftime("%m-%d") if k else "", "time": k.strftime("%H:%M") if k else "", "session": t.get("name", ""),
                "sid": t.get("sid", ""), "said": t["said"], "reply": t.get("first", "")}
    out = [{"count": len(g["items"]), "latest": view(g["items"][-1]), "examples": [view(x) for x in reversed(g["items"][-5:])],
            "words": sorted(g["stems"])[:6]} for g in groups]
    out.sort(key=lambda g: (g["count"], g["latest"]["date"] + g["latest"]["time"]), reverse=True)  # 많이 · 최근 순
    return out


def _flat(rows):
    return [dict(t, sid=sid, name=name) for sid, name, turns in rows for t in turns]


# ── 명령 ────────────────────────────────────────────────────────

def cli_nags(args):
    days = int(args[args.index("--days") + 1]) if "--days" in args else 7
    since = time.time() - days * 86400
    turns = [t for t in _flat(collect(since)) if (_kst(t.get("ts")) or datetime.min.replace(tzinfo=KST)).timestamp() >= since]
    print(json.dumps({"days": days, "groups": nags(turns)[:20]}, ensure_ascii=False))
    return 0


def cli_find(args):
    days = int(args[args.index("--days") + 1]) if "--days" in args else 30
    words = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--days")]
    if not words:
        print("쓰는 법: pmap.py find <낱말 …> [--days 30] [--json]")
        return 2
    rows = []
    for t in _flat(collect(time.time() - days * 86400)):
        low = (t.get("said") or "").lower()
        if all(w.lower() in low for w in words):
            k = _kst(t.get("ts"))
            rows.append({"sid": t["sid"], "name": t["name"], "date": k.strftime("%Y-%m-%d") if k else "", "time": k.strftime("%H:%M") if k else "",
                         "said": t["said"], "first": t.get("first", ""), "kind": t["kind"], "ts": t.get("ts") or ""})
    rows.sort(key=lambda r: r["ts"], reverse=True)
    if "--json" in args:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for r in rows[:50]:
            print(f'{r["date"]} {r["time"]} · {r["name"]} ({r["sid"][:8]}) · {r["said"][:80]}' + (f' → {r["first"][:60]}' if r["first"] else ""))
        if not rows:
            print("찾은 지시가 없습니다.")
    return 0


def journal_md(date, rows):
    lines = [f"# where was i 일지 — {date}", "",
             "> 세션 기록에서 뽑음(토큰 0). 「직접 지시」는 내가 먼저 한 말, 「AI 제안에 답함」은 AI 질문 · 선택 상자에 답한 말이다.", ""]
    for sid, name, turns in rows:
        day = [t for t in turns if (_kst(t.get("ts")) or datetime.min.replace(tzinfo=KST)).strftime("%Y-%m-%d") == date]
        if not day:
            continue
        lines += [f"## {name} ({sid[:8]})", ""]
        for kind, title in (("direct", "직접 지시"), ("answer", "AI 제안에 답함")):
            part = [t for t in day if t["kind"] == kind]
            if not part:
                continue
            lines += [f"### {title}", ""]
            for t in part:
                hm = _kst(t["ts"]).strftime("%H:%M")
                said = re.sub(r"\s+", " ", t["said"])[:160]
                head = f"- {hm} 「{said}」 ← {t['asked']}" if kind == "answer" else f"- {hm} {said}"
                tail = f" → {t['first']}" if t.get("first") else ""
                com = f" · 커밋: {', '.join(t['commits'][:3])}" if t.get("commits") else ""
                lines.append(head + tail + com)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def cli_journal(args):
    date = args[args.index("--date") + 1] if "--date" in args else datetime.now(KST).strftime("%Y-%m-%d")
    start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=KST).timestamp()
    rows = sorted(collect(start), key=lambda r: r[1])
    md = journal_md(date, rows)
    if "--out" in args:
        out_dir = os.path.expanduser(args[args.index("--out") + 1])
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"where was i 일지 {date}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
        print(path)
    else:
        print(md, end="")
    return 0
