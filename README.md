# where was i

Claude Code · Codex · Antigravity(`agy`) · OpenCode · Copilot CLI 세션이 **무엇을 지시받아 어떻게, 어디까지 일했는지**를 herdr 오른쪽 칸에 보여 주는 개인 도구다. 각 도구의 로컬 기록(과 Claude Code 훅)만 읽고 적으며 AI를 부르지 않는다(토큰 0).

프로토타입이다.

## 팀원이 처음 깔 때

준비물: herdr 앱(**설치할 때 창이 열려 있어야 함**, `herdr-app` 명령이 PATH 에 있음), python3 3.9 이상(맥 기본 것으로 됨), 지원하는 AI CLI 중 하나 이상(Claude Code · Codex · Antigravity · OpenCode · Copilot CLI). 설치가 이것들을 먼저 점검하고, 끝나면 칸 검사 · 훅 검사 · 세션 찾기까지 스스로 확인한다.

설치 자리는 **`~/.where-was-i`** 로 정해 둔다(어느 AI 든 이 자리만 보면 되게).

```bash
git clone https://github.com/gulzzaang/where-was-i.git ~/.where-was-i
python3 ~/.where-was-i/scripts/herdr_setup.py install
```

그다음 herdr 에서 **⌘⌥I** 를 누르면 오른쪽에 칸이 나온다. AI 에게 「버디 팀 메모리에 있는 where was i 도구를 설치해줘」라고 해도 된다.

| 할 일 | 명령 |
|---|---|
| 설치 | `git clone https://github.com/gulzzaang/where-was-i.git ~/.where-was-i && python3 ~/.where-was-i/scripts/herdr_setup.py install` |
| 업데이트 | `python3 ~/.where-was-i/scripts/herdr_setup.py update` (새로 받고 다시 설치) |
| 지우기 | `python3 ~/.where-was-i/scripts/herdr_setup.py uninstall` (저장소 폴더 `~/.where-was-i` 도 지우려면 그다음 휴지통으로) |

설치하면 생기는 것: 저장소 `~/.where-was-i`, herdr 칸 `~/.config/herdr-app/mods/where-was-i`, 레이아웃 `~/.config/herdr-app/layouts/main-progress.json`, 단축키 3개(`~/.config/herdr-app/keys.json`, 바꾸기 전 `.bak-where-was-i` 백업), Claude Code 훅(`~/.claude/settings.json`, 바꾸기 전 `.bak-where-was-i` 백업, 버디 등 기존 훅은 건드리지 않음), 읽기 · 훅 기록 `~/.cache/where-was-i`.

**알아 둘 것**
- 이 맥 안의 기록 파일만 읽고 적는다. AI 를 부르지 않고(토큰 0), 바깥으로 보내는 것도 없다.
- 훅은 세션 시작(herdr 칸 ↔ 세션 짝짓기) · 도우미 시작/끝 · 도구/질문/권한 대기 상태를 로컬 캐시에만 적고, AI 중간 글과 설치 전 세션은 기존처럼 대화 기록(JSONL)에서 채운다.
- 설치하기 전에 이미 떠 있던 세션은 기존 기록 파일 방식으로 동작하고, 새로 여는 세션부터 훅이 붙는다.
- herdr 가 없는 사람(Orca 등)용 브라우저 페이지는 아직 없다.
- 남은 일(「다음」 줄 · 「앞으로」 칩)은 세션의 할 일 목록 · 계획서 파일(경로에 `plans/` · `plan-` · `계획` 이 든 md) · Claude Code 계획 모드 계획에서 읽는다. Codex 는 `update_plan` 도구가 있는 판에서만 그 계획을 읽는다(2026-11 의 gpt-5.6 Codex 처럼 `exec` 도구만 있는 판에는 없다). 셋 다 없으면 안 보이고, where was i 가 에이전트에게 계획을 시키지는 않는다.
- Claude Code 할 일 도구를 켜면 남은 일이 더 잘 보인다(구독 사용량 약 2% 증가, 새로 여는 세션부터): `~/.claude/settings.json` 의 `"env"` 에 `"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"`. 설치가 대신 켜지는 않는다.

## 쓰는 법

- **⌘⇧I**: 오른쪽 칸 숨김 · 보임. 숨겨 두면 기록을 읽지 않는다.
- **⌘⌥I**: 아예 켜기 · 끄기. 기본 화면(`main`)과 진행 지도 화면(`main-progress`)을 오간다.
- 칸 오른쪽 위 「tNN 따라가는 중 ▾」: 다른 세션 고르기, 「지금 보는 칸 따라가기」로 돌아오기.
- 「지나온 길」 칩으로 단계를 고르면 그 단계의 작업 카드가 최근 것부터 나온다. 카드를 누르면 단계별 시각 · 도우미가 펼쳐진다.
- 카드 아래 「대화 N개」를 누르면 그 작업에 묶인 대화(내 말 · 바로 앞 AI 물음 · 알아들은 것 · 한 일 · 끝에 한 말)가 최근 것부터 펼쳐진다. 지시 카드를 누르면 전체 글.
- 머리의 「다음 · …」 줄: 세션이 세워 둔 남은 일 중 다음 차례. 「지나온 길」 끝의 「앞으로 N」 칩을 누르면(또는 `herdr-app do where-was-i.ahead`) 남은 일 목록이 나온다. 꼬리표 「할 일 목록에서」는 AI 가 적은 상태 그대로, 「계획서에서 · 끝남은 짐작」 · 「계획 모드에서 · 끝남은 짐작」은 도우미 · 커밋 · 체크 칸으로 끝났는지 짐작한 것이다.

## 세션을 넘어 지난 기록 보기 (토큰 0)

Claude Code 대화 기록에서 지시마다 「내 말 · 바로 앞 AI 질문 · AI 가 알아들은 것 · 커밋」을 모아 `~/.cache/where-was-i/history` 에 이어 쌓는다(처음 한 번 몇 초, 그다음은 새 줄만).

- **되풀이 지적**: 칸 아래 줄 「되풀이 지적」(또는 `herdr-app do where-was-i.nags`) — 최근 7일 모든 세션에서 바로잡은 말(「아니 …」 · 「왜 ~했어」 · 「몇 번을 말해」 등)과 AI 가 사과로 답한 지시를 모아 비슷한 말끼리 「N번」으로 묶는다. 낱말로만 짐작하므로 말버릇 「아니」도 섞일 수 있다. 명령: `python3 pmap.py nags --days 7`(JSON).
- **일지**: `python3 pmap.py journal [--date 2026-11-23] [--out 폴더]` — 그날 세션마다 「직접 지시」와 「AI 제안에 답함」(AI 질문 · 선택 상자에 답한 짧은 말, 무엇에 대한 답이었는지 함께)을 갈라 md 로 낸다. `--out` 이면 `where was i 일지 <날짜>.md` 파일로 쓴다.
- **지난 세션 찾기**: `python3 pmap.py find 낱말 … [--days 30] [--json]` — 지시 글에 낱말이 모두 든 지시를 최근 것부터 세션 이름 · 시각과 함께 보여 준다. 「지난주에 X 시킨 세션 찾아줘」처럼 AI 에게 시켜도 된다.

## AI 에게 시킬 때

- **설치(또는 고친 칸 다시 깔기)**: 위 표. 저장소가 이미 `~/.where-was-i` 에 있으면 `install` 만.
  - 칸을 `~/.config/herdr-app/mods/where-was-i` 로 복사하고, 없으면 레이아웃 `main-progress` 를 만들어 오른쪽에 칸을 붙이고, 위 단축키를 넣는다(`keys.json` 은 `.bak-where-was-i` 로 백업, 이미 다른 뜻으로 쓰는 키는 건너뜀).
  - 칸 코드를 고친 뒤 `herdr-app check herdr-mod/where-was-i` 를 먼저 통과시킨다.
- **지우기**: `python3 ~/.where-was-i/scripts/herdr_setup.py uninstall`
  - 진행 지도 화면이면 기본 화면으로 돌리고, 우리가 넣은 단축키(`keys.json` 백업)와 Claude Code 훅(`~/.claude/settings.json` 백업)만 걷고, 칸 폴더와 레이아웃 복사본은 휴지통으로, 읽기 · 훅 기록(`~/.cache/where-was-i`)은 지운다. 이 저장소는 그대로 둔다.
- 화면 확인: `herdr-app shot <파일.png>`(화면 기록 권한 없이 herdr 창만 찍는다).

## 파일

- `wwi_hook.py`: Claude Code 훅 기록기(토큰 0). 세션 ↔ herdr 칸 · 도우미 시작/끝 · 도구/질문/권한 대기 상태를 `~/.cache/where-was-i/hooks/` 에만 적는다.
- `pmap.py`: 훅 기록과 각 AI 도구의 로컬 기록(Claude Code · Codex · Antigravity · OpenCode · Copilot CLI)을 읽어 지도 자료(JSON)를 만든다. `list`, `map --terminal tNN`, `map --session <id>`, `map --codex-file <rollout.jsonl>`, `map --antigravity-file <transcript.jsonl>`, `map --opencode-session <id>`, `map --copilot-file <events.jsonl>`.
- `ahead.py`: 남은 일(할 일 목록 · 계획서 · 계획 모드 · Codex update_plan)을 뽑고 고르는 순수 함수.
- `history.py`: 세션을 넘은 지난 기록 — 되풀이 지적(`nags`) · 일지(`journal`) · 지난 세션 찾기(`find`).
- `herdr-mod/where-was-i/`: herdr 칸(gpui-kit JS).
- `scripts/herdr_setup.py`: 설치 · 업데이트 · 지우기.

## 자원(2026-11-20 측정)

- 토큰 0. 훅은 로컬 캐시에만 한 줄씩 적고(`async: true` 로 도구 대기 0ms, 실행 자체는 평균 약 37ms), 대화 기록은 새로 붙은 줄만 이어 읽는다(한 번 0.2초 · 21MB, 처음 한 번 1.5초 · 67MB).
- herdr 앱 CPU: 칸을 띄워도 숨긴 것과 차이가 거의 없다(움직이는 표시 없음, 자료가 바뀔 때만 다시 그림).
- 읽기 · 훅 기록은 `~/.cache/where-was-i`, 14일 지나면 지운다.
