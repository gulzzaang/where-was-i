#!/usr/bin/env python3
"""where was i — herdr 패널 · Claude Code 훅 설치 · 지우기.

  python3 scripts/herdr_setup.py install    패널 복사 · 레이아웃(main-progress) · 단축키 · Claude 훅(토큰 0)
  python3 scripts/herdr_setup.py update     저장소를 새로 받고(git pull) 다시 설치
  python3 scripts/herdr_setup.py uninstall  위를 모두 걷는다(패널 · 레이아웃은 휴지통으로, 단축키 · 훅은 우리 것만)

정해 둔 설치 자리: ~/.where-was-i (git clone https://github.com/gulzzaang/where-was-i.git ~/.where-was-i)

단축키
  ⌘⇧I  오른쪽 패널 숨김 · 보임 (숨기면 기록을 읽지 않는다)
  ⌘⌥I  아예 켜기 · 끄기 (기본 화면 main ↔ 진행 지도 화면 main-progress)
"""
import json, os, shutil, subprocess, sys, time

HOME = os.path.expanduser("~")
APP = os.path.join(HOME, ".config", "herdr-app")
CLAUDE_DIR = os.path.join(HOME, ".claude")
CLAUDE_SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_SCRIPT = os.path.join(REPO, "wwi_hook.py")
HOOK_MARKER = "wwi_hook.py"
MOD_SRC = os.path.join(REPO, "herdr-mod", "where-was-i")
MOD_DST = os.path.join(APP, "mods", "where-was-i")
LAYOUT = os.path.join(APP, "layouts", "main-progress.json")
KEYS = os.path.join(APP, "keys.json")
CACHE = os.path.join(HOME, ".cache", "where-was-i")
OURS = [("global", "cmd-shift-i", "layout.toggle:where-was-i"),
        ("layout:main", "cmd-alt-i", "layout.switch:main-progress"),
        ("layout:main-progress", "cmd-alt-i", "layout.switch:main")]
# (이벤트 이름, matcher 또는 None, async 여부, timeout 초)
HOOK_SPECS = [
    ("SessionStart", None, False, 3),
    ("SessionEnd", None, False, 2),
    ("UserPromptSubmit", None, True, 3),
    ("PreToolUse", "*", True, 3),
    ("PostToolUse", "*", True, 3),
    ("SubagentStart", None, True, 3),
    ("SubagentStop", None, True, 3),
    ("Notification", None, True, 3),
    ("Stop", None, True, 3),
]
BLOCK = {"id": "where-was-i", "type": "block", "size": {"px": 400}, "min": 300, "max": 800, "resizable": True,
         "background": "chrome", "radius": 10, "border": {"width": 1, "color": "border"}, "content": {"mod": "where-was-i"}}


def app(*args):
    return subprocess.run(["herdr-app", *args], capture_output=True, text=True, timeout=30)


def find(n, i):
    if isinstance(n, dict):
        if n.get("id") == i:
            return n
        for c in n.get("children", []):
            r = find(c, i)
            if r:
                return r
    return None


def parent_of(n, i):
    if isinstance(n, dict):
        for c in n.get("children", []):
            if isinstance(c, dict) and c.get("id") == i:
                return n
            r = parent_of(c, i)
            if r:
                return r
    return None


def to_trash(path):
    if os.path.exists(path):
        dst = os.path.join(HOME, ".Trash", os.path.basename(path) + f"-{int(time.time())}")
        shutil.move(path, dst)
        return dst
    return None


def atomic_json_dump(path, data, indent=2):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def _is_our_hook_group(group):
    if not isinstance(group, dict):
        return False
    hooks = group.get("hooks")
    if not isinstance(hooks, list):
        return False
    return any(isinstance(h, dict) and HOOK_MARKER in str(h.get("command") or "") for h in hooks)


def _strip_our_hooks(settings):
    """settings['hooks'] 에서 where-was-i 훅만 걷어내고 나머지(버디 등)는 그대로 둔다."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for ev_name in list(hooks.keys()):
        groups = hooks.get(ev_name)
        if not isinstance(groups, list):
            continue
        new_groups = []
        ev_removed = 0
        for g in groups:
            if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
                new_groups.append(g)
                continue
            kept = [h for h in g["hooks"] if not (isinstance(h, dict) and HOOK_MARKER in str(h.get("command") or ""))]
            diff = len(g["hooks"]) - len(kept)
            removed += diff
            ev_removed += diff
            if kept:
                if diff:
                    g = dict(g)
                    g["hooks"] = kept
                new_groups.append(g)
        if new_groups:
            hooks[ev_name] = new_groups
        elif ev_name in hooks and ev_removed:
            del hooks[ev_name]
    return removed


def install_hooks(settings_path=CLAUDE_SETTINGS, hook_script=HOOK_SCRIPT):
    """~/.claude/settings.json 에 토큰 0 기록 훅을 넣는다(기존 훅은 그대로 유지, 중복 방지)."""
    if not os.path.exists(hook_script):
        print("✗ 훅 스크립트를 못 찾음:", hook_script)
        return False
    if os.path.exists(settings_path):
        shutil.copy(settings_path, settings_path + ".bak-where-was-i")
        with open(settings_path, encoding="utf-8") as f:
            settings = json.load(f)
    else:
        settings = {}
    _strip_our_hooks(settings)
    hooks = settings.setdefault("hooks", {})
    cmd = f'python3 "{hook_script}"'
    for ev_name, matcher, is_async, timeout in HOOK_SPECS:
        entry = {"type": "command", "command": cmd, "timeout": timeout}
        if is_async:
            entry["async"] = True
        group = {"hooks": [entry]}
        if matcher is not None:
            group["matcher"] = matcher
        hooks.setdefault(ev_name, []).append(group)
    atomic_json_dump(settings_path, settings, indent=2)
    return True


def uninstall_hooks(settings_path=CLAUDE_SETTINGS):
    """~/.claude/settings.json 에서 where-was-i 훅만 걷는다."""
    if not os.path.exists(settings_path):
        return 0
    shutil.copy(settings_path, settings_path + ".bak-where-was-i-uninstall")
    with open(settings_path, encoding="utf-8") as f:
        settings = json.load(f)
    removed = _strip_our_hooks(settings)
    if removed:
        atomic_json_dump(settings_path, settings, indent=2)
    return removed


def check():
    """설치 전 점검: herdr 앱 · 파이썬 · Claude Code."""
    ok = True
    if not os.path.isdir(APP) or shutil.which("herdr-app") is None:
        print("✗ herdr 앱이 없거나 herdr-app 명령이 PATH 에 없음 — herdr 앱을 먼저 깔고 한 번 열어 주세요")
        ok = False
    if sys.version_info < (3, 9):
        print("✗ python3 3.9 이상이 필요함")
        ok = False
    agents_found = []
    for label, cmd in (("Claude Code", ["claude", "--version"]), ("Codex", ["codex", "--version"]),
                       ("Antigravity", ["agy", "--version"]), ("OpenCode", ["opencode", "--version"]),
                       ("Copilot", ["copilot", "--version"])):
        if shutil.which(cmd[0]):
            ver = (subprocess.run(cmd, capture_output=True, text=True).stdout or "").strip().splitlines()
            agents_found.append(f"{label}({ver[0][:40]})" if ver else label)
    print("· AI 도구:", ", ".join(agents_found) if agents_found else "못 찾음")
    sdir = os.path.join(HOME, ".claude", "sessions")
    if shutil.which("claude") and not (os.path.isdir(sdir) and any(f.endswith(".json") for f in os.listdir(sdir))):
        print("  · ~/.claude/sessions 가 비어 있음 — 새로 여는 Claude Code 세션은 이번에 넣는 훅이 바로 잡는다")
    if ok:
        st = app("status")
        if st.returncode != 0 or '"layout"' not in st.stdout:
            print("✗ herdr 앱 창이 열려 있지 않음 — herdr 앱을 열어 둔 뒤 다시 설치해 주세요")
            ok = False
        else:
            print("· herdr 앱:", (subprocess.run(["herdr-app", "--version"], capture_output=True, text=True).stdout or "").strip())
    if not ok:
        sys.exit(1)


def install():
    check()
    os.makedirs(MOD_DST, exist_ok=True)
    shutil.copy(os.path.join(MOD_SRC, "mod.json"), os.path.join(MOD_DST, "mod.json"))
    js = open(os.path.join(MOD_SRC, "main.js")).read().replace("__WWI_PMAP__", os.path.join(REPO, "pmap.py"))
    open(os.path.join(MOD_DST, "main.js"), "w").write(js)
    print("패널 복사:", MOD_DST, "(기록 읽기:", os.path.join(REPO, "pmap.py") + ")")
    if not os.path.exists(LAYOUT):
        app("do", "layout.clone", "--name", "main", "--as", "main-progress")
        for _ in range(30):
            if os.path.exists(LAYOUT):
                break
            time.sleep(0.3)
    if not os.path.exists(LAYOUT):
        print("✗ 레이아웃 main-progress 를 만들지 못함 — herdr 앱에서 레이아웃 main 이 있는지 보고 다시 설치해 주세요")
        sys.exit(1)
    d = json.load(open(LAYOUT))
    if not find(d, "where-was-i"):
        # 보통은 가운데 줄(work-body) 오른쪽. herdr 판이 달라 그 이름이 없으면 터미널 블록(main)이 든 줄 오른쪽.
        host = find(d, "work-body") or parent_of(d, "main")
        if not host:
            print("✗ 이 herdr 버전의 화면 배치에서 패널을 붙일 자리를 못 찾음 — 레이아웃 파일을 사람이 봐야 함:", LAYOUT)
            sys.exit(1)
        host.setdefault("children", []).append(BLOCK)
        json.dump(d, open(LAYOUT, "w"), ensure_ascii=False, indent=1)
    print("레이아웃:", LAYOUT)
    k = json.load(open(KEYS)) if os.path.exists(KEYS) else {}
    shutil.copy(KEYS, KEYS + ".bak-where-was-i") if os.path.exists(KEYS) else None
    for sec, key, act in OURS:
        cur = k.setdefault(sec, {}).get(key)
        if cur and cur != act:
            print(f"단축키 {key}({sec}) 는 이미 「{cur}」라 건너뜀")
            continue
        k[sec][key] = act
    json.dump(k, open(KEYS, "w"), ensure_ascii=False, indent=2)
    print("단축키: ⌘⇧I 패널 숨김 · 보임, ⌘⌥I 아예 켜기 · 끄기")
    if os.path.isdir(CLAUDE_DIR) or shutil.which("claude"):
        if install_hooks():
            print("Claude 훅(토큰 0):", CLAUDE_SETTINGS, "(백업: .bak-where-was-i, 새로 여는 세션부터 적용)")
    # 설치 뒤 스스로 확인
    chk = app("check", MOD_DST)
    print("· 패널 검사:", "통과" if chk.returncode == 0 else "실패 — " + (chk.stdout + chk.stderr).strip()[-300:])
    hchk = subprocess.run([sys.executable, HOOK_SCRIPT, "--self-check"], capture_output=True, text=True, timeout=10)
    print("· 훅 검사:", "통과" if hchk.returncode == 0 and hchk.stdout.strip() == "ok" else "실패 — " + (hchk.stdout + hchk.stderr).strip()[-200:])
    try:
        n = len(json.loads(subprocess.run([sys.executable, os.path.join(REPO, "pmap.py"), "list"], capture_output=True, text=True, timeout=60).stdout or "[]"))
        print(f"· 지금 떠 있는 세션 {n}개 찾음" + ("" if n else " — Claude Code 를 herdr 안에서 하나 열면 패널에 나온다"))
    except Exception as e:
        print("· 세션 찾기 실패:", e)
    print("끝. herdr 에서 ⌘⌥I 를 누르면 패널이 나온다.")


def update():
    r = subprocess.run(["git", "-C", REPO, "pull", "--ff-only"], capture_output=True, text=True)
    print((r.stdout or r.stderr).strip() or "git pull 끝")
    if r.returncode != 0:
        print("✗ 새로 받기 실패 — 저장소에 고친 파일이 있는지 보세요")
        sys.exit(1)
    install()


def uninstall():
    st = app("status").stdout
    if '"layout": "main-progress"' in st:
        app("do", "layout.switch", "--name", "main")
        print("기본 화면(main)으로 돌아감")
    if os.path.exists(KEYS):
        shutil.copy(KEYS, KEYS + ".bak-where-was-i-uninstall")
        k = json.load(open(KEYS))
        for sec, key, act in OURS:
            if k.get(sec, {}).get(key) == act:
                del k[sec][key]
                if not k[sec] and sec != "global":
                    del k[sec]
        json.dump(k, open(KEYS, "w"), ensure_ascii=False, indent=2)
        print("단축키 걷음(우리가 넣은 것만)")
    removed = uninstall_hooks()
    if removed:
        print(f"Claude 훅 걷음({removed}개, 우리 것만, 백업: .bak-where-was-i-uninstall)")
    for p in (MOD_DST, LAYOUT):
        t = to_trash(p)
        if t:
            print("휴지통으로:", p)
    if os.path.isdir(CACHE):
        shutil.rmtree(CACHE)
        print("읽기 · 훅 기록 지움:", CACHE)
    print(f"저장소({REPO})는 그대로 둠 — 이것까지 지우려면 그 폴더를 휴지통으로")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "install":
        install()
    elif cmd == "update":
        update()
    elif cmd == "uninstall":
        uninstall()
    else:
        print(__doc__)
        sys.exit(2)
