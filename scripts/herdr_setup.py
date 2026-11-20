#!/usr/bin/env python3
"""where was i — herdr 칸 설치 · 지우기.

  python3 scripts/herdr_setup.py install    칸 복사 · 레이아웃(main-progress) · 단축키
  python3 scripts/herdr_setup.py uninstall  위를 모두 걷는다(칸 · 레이아웃은 휴지통으로, 단축키는 우리 것만)

단축키
  ⌘⇧I  오른쪽 칸 숨김 · 보임 (숨기면 기록을 읽지 않는다)
  ⌘⌥I  아예 켜기 · 끄기 (기본 화면 main ↔ 진행 지도 화면 main-progress)
"""
import json, os, shutil, subprocess, sys, time

HOME = os.path.expanduser("~")
APP = os.path.join(HOME, ".config", "herdr-app")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD_SRC = os.path.join(REPO, "herdr-mod", "where-was-i")
MOD_DST = os.path.join(APP, "mods", "where-was-i")
LAYOUT = os.path.join(APP, "layouts", "main-progress.json")
KEYS = os.path.join(APP, "keys.json")
CACHE = os.path.join(HOME, ".cache", "where-was-i")
OURS = [("global", "cmd-shift-i", "layout.toggle:where-was-i"),
        ("layout:main", "cmd-alt-i", "layout.switch:main-progress"),
        ("layout:main-progress", "cmd-alt-i", "layout.switch:main")]
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


def to_trash(path):
    if os.path.exists(path):
        dst = os.path.join(HOME, ".Trash", os.path.basename(path) + f"-{int(time.time())}")
        shutil.move(path, dst)
        return dst
    return None


def install():
    os.makedirs(MOD_DST, exist_ok=True)
    for f in ("main.js", "mod.json"):
        shutil.copy(os.path.join(MOD_SRC, f), os.path.join(MOD_DST, f))
    print("칸 복사:", MOD_DST)
    if not os.path.exists(LAYOUT):
        app("do", "layout.clone", "--name", "main", "--as", "main-progress")
        for _ in range(20):
            if os.path.exists(LAYOUT):
                break
            time.sleep(0.3)
    d = json.load(open(LAYOUT))
    if not find(d, "where-was-i"):
        find(d, "work-body")["children"].append(BLOCK)
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
    print("단축키: ⌘⇧I 칸 숨김 · 보임, ⌘⌥I 아예 켜기 · 끄기")


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
    for p in (MOD_DST, LAYOUT):
        t = to_trash(p)
        if t:
            print("휴지통으로:", p)
    if os.path.isdir(CACHE):
        shutil.rmtree(CACHE)
        print("읽기 기록 지움:", CACHE)
    print("저장소(~/Documents/where-was-i)는 그대로 둠")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "install":
        install()
    elif cmd == "uninstall":
        uninstall()
    else:
        print(__doc__)
        sys.exit(2)
