# where was i

Claude Code(와 Codex) 세션이 **무엇을 지시받아 어떻게, 어디까지 일했는지**를 herdr 오른쪽 칸에 보여 주는 개인 도구다. 세션의 대화 기록 파일을 읽기만 하고 AI를 부르지 않는다(토큰 0).

프로토타입이다. 설계서는 개인 옵시디언 볼트 `20_Projects/where was i/설계/설계서.md` 에 있다.

## 쓰는 법

- **⌘⇧I**: 오른쪽 칸 숨김 · 보임. 숨겨 두면 기록을 읽지 않는다.
- **⌘⌥I**: 아예 켜기 · 끄기. 기본 화면(`main`)과 진행 지도 화면(`main-progress`)을 오간다.
- 칸 오른쪽 위 「tNN 따라가는 중 ▾」: 다른 세션 고르기, 「지금 보는 칸 따라가기」로 돌아오기.
- 지도에서 단계 덩어리를 고르고 「이 단계의 지시 · 한 일」을 누르면 그 단계의 지시가 펼쳐진다(최근 것이 위). 카드를 누르면 전체 글.

## AI 에게 시킬 때

- **설치(또는 고친 칸 다시 깔기)**: `python3 scripts/herdr_setup.py install`
  - 칸을 `~/.config/herdr-app/mods/where-was-i` 로 복사하고, 없으면 레이아웃 `main-progress` 를 만들어 오른쪽에 칸을 붙이고, 위 단축키를 넣는다(`keys.json` 은 `.bak-where-was-i` 로 백업, 이미 다른 뜻으로 쓰는 키는 건너뜀).
  - 칸 코드를 고친 뒤 `herdr-app check herdr-mod/where-was-i` 를 먼저 통과시킨다.
- **지우기**: `python3 scripts/herdr_setup.py uninstall`
  - 진행 지도 화면이면 기본 화면으로 돌리고, 우리가 넣은 단축키만 걷고(`keys.json` 백업), 칸 폴더와 레이아웃 복사본은 휴지통으로, 읽기 기록(`~/.cache/where-was-i`)은 지운다. 이 저장소는 그대로 둔다.
- 화면 확인: `herdr-app shot <파일.png>`(화면 기록 권한 없이 herdr 창만 찍는다).

## 파일

- `pmap.py`: 기록을 읽어 지도 자료(JSON)를 만든다. `list`, `map --terminal tNN`, `map --session <id>`, `map --codex-file <rollout.jsonl>`.
- `herdr-mod/where-was-i/`: herdr 칸(gpui-kit JS).
- `scripts/herdr_setup.py`: 설치 · 지우기.

## 자원(2026-11-20 측정)

- 토큰 0. 기록을 새로 붙은 줄만 이어 읽는다(한 번 0.2초 · 21MB, 처음 한 번 1.5초 · 67MB).
- herdr 앱 CPU: 칸을 띄워도 숨긴 것과 차이가 거의 없다(움직이는 표시 없음, 자료가 바뀔 때만 다시 그림).
- 읽기 기록은 `~/.cache/where-was-i`, 14일 지나면 지운다.
