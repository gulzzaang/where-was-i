// where was i 프로토타입 — herdr 오른쪽 칸.
// 지금 보는 터미널(또는 고른 세션)의 Claude 세션 지도를 그린다. 자료는 pmap.py 가 만든다.
// 생김새는 Hide 의 미션 지도를 참고했다: 위 막대 · 요약 · 이어진 카드(끝 초록 체크, 일하는 중 파란 테두리와 도는 표시).
import { View, div, svg, image } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { exec, focused, theme, home, checking, agents, nextCommand, layout } from "herdr";

// 설치(scripts/herdr_setup.py install)가 아래 자리에 이 저장소의 pmap.py 경로를 적어 넣는다
const SCRIPT_PATH = "__WWI_PMAP__";
const SCRIPT = () => (SCRIPT_PATH.startsWith("__") ? `${home()}/Documents/where-was-i/pmap.py` : SCRIPT_PATH);
const ICON = (n) => `mods/workspace-chrome/${n}.svg`;
// 레이아웃에서 이 칸이 숨겨져 있으면 기록을 읽지 않는다(숨겨도 칸 코드는 살아 있어서)
const panelHidden = () => {
  const find = (n) => {
    if (!n || typeof n !== "object") return null;
    if (n.id === "where-was-i") return n;
    for (const c of n.children || []) { const r = find(c); if (r) return r; }
    return null;
  };
  try { const node = find(layout()); return !!(node && node.hidden); } catch (e) { return false; }
};
const AGENT_META = {
  claude: { icon: "icons/claude.svg", label: "Claude" },
  codex: { icon: "icons/codex.svg", label: "Codex" },
  antigravity: { icon: "icons/antigravity.svg", label: "Antigravity" },
  opencode: { icon: "icons/opencode.svg", label: "OpenCode" },
  copilot: { icon: "icons/copilot.svg", label: "Copilot" },
};
const agentKey = (kindOrAgent) => {
  const k = String(kindOrAgent || "").toLowerCase();
  if (k.includes("codex")) return "codex";
  if (k.includes("antigravity") || k === "agy") return "antigravity";
  if (k.includes("opencode")) return "opencode";
  if (k.includes("copilot")) return "copilot";
  return "claude";
};
const agentIcon = (kindOrAgent) => AGENT_META[agentKey(kindOrAgent)].icon;
const isExternalKind = (kind) => ["Codex", "Antigravity", "OpenCode", "Copilot"].includes(kind);
// 세션 이름은 herdr 탭 이름(직접 지은 것) → 탭에 보이는 제목 차례
const tabName = (term) => {
  const a = term && agents().find((x) => x.address === term);
  if (!a) return "";
  const title = String(a.title || "").replace(/^[^\p{L}\p{N}]+/u, "").trim();
  if (a.tab_name && !["Claude Code", "agy", "opencode", "copilot", "codex"].includes(a.tab_name)) return a.tab_name;
  return title || a.tab_name || "";
};

// 상태 색: 끝 초록 · 일하는 중 파랑 · 나를 기다림 주황 · 남은 칸 회색 (Hide 와 같은 뜻)
const palette = (t) => ({
  done: t.ansi2 || t.done,
  work: t.accent || t.working,
  ask: t.ansi3 || t.blocked,
  todo: t.raised,
  line: t.border,
});

export default class WhereWasI extends View {
  init(_props, cx) {
    this.data = null;
    this.err = "";
    this.sessions = [];
    this.pick = null;
    this.picking = false;
    this.lastTerm = null;
    this.phase = null;
    this.open = {};
    this.showAhead = false;  // 「앞으로 N」 칩을 눌러 남은 일 목록을 보는 중
    this.busy = false;
    this.convOpen = {};    // 카드마다 「대화」 펼침
    this.turnOpen = {};    // 지시 카드 하나 전체 보기
    if (checking()) return;
    // herdr 명령: where-was-i.toggle — 펼친 대화를 모두 접기, where-was-i.ahead — 「앞으로」 목록 열고 닫기
    cx.spawn(async (cx) => {
      for (;;) {
        const c = await nextCommand();
        if (c === "toggle") this.convOpen = {};
        if (c === "ahead") { this.showAhead = !this.showAhead; this.open = {}; }
        cx.notify();
      }
    });
    this.refresh(cx);
    cx.timer.every(1500, (cx) => {
      const t = focused()?.terminal ?? null;
      if (!this.pick && t && t !== this.lastTerm) this.refresh(cx);
    });
    this.tick = 0;
    cx.timer.every(5000, (cx) => {
      this.tick += 1;
      const idle = this.data && this.data.status !== "working";
      if (!idle || this.tick % 4 === 0) this.refresh(cx);
    });
  }

  refresh(cx) {
    if (this.busy || panelHidden()) return;
    const t = focused()?.terminal ?? null;
    const args = this.pick ? ["map", "--session", this.pick] : (t ? ["map", "--terminal", t] : null);
    if (!args) return;
    this.busy = true;
    cx.spawn(async (cx) => {
      try {
        const r = await exec("python3", [SCRIPT(), ...args], { timeout: 60000 });
        const raw = (r.stdout || "{}").replace(/"built": "[^"]*"/, "");
        if (raw === this.lastRaw && t === this.lastTerm) { this.busy = false; return; }
        this.lastRaw = raw;
        const d = JSON.parse(r.stdout || "{}");
        if (!this.pick && t !== this.lastTerm) { this.phase = null; this.open = {}; this.showAhead = false; }
        if (!this.pick) this.lastTerm = t;
        this.data = d; this.err = d.error ? d.error : "";
      } catch (e) {
        this.err = String(e);
      }
      this.busy = false;
      cx.notify();
    });
  }

  loadSessions(cx) {
    cx.spawn(async (cx) => {
      try {
        const r = await exec("python3", [SCRIPT(), "list"], { timeout: 20000 });
        this.sessions = JSON.parse(r.stdout || "[]");
      } catch (e) { this.sessions = []; }
      cx.notify();
    });
  }

  // ── 작은 조각 ──
  mark(state, t, size = 13) {
    const c = palette(t);
    if (state === "working") return svg(ICON("spinner")).size(size).flex_none().text_color(c.work);
    if (state === "asking") return div().size(size).flex_none().rounded_full().bg(c.ask).text_color(t.background).text_size(size - 4).font_semibold().flex().items_center().justify_center().child("!");
    if (state === "done") return svg(ICON("circle-check")).size(size).flex_none().text_color(c.done);
    return div().size(size - 2).flex_none().rounded_full().border(1).border_dashed().border_color(t.dim);
  }

  segs(list, t, h = 4) {
    const c = palette(t);
    if (!list || !list.length) return div();
    return h_flex().w_full().gap(3).children(list.map((s, i) => {
      const st = Array.isArray(s) ? s[1] : s;
      let el = div().id(`sg${i}`).flex_1().h(h).rounded(h).bg(st === "done" ? c.done : st === "working" ? c.work : c.todo);
      return el;
    }));
  }

  // 칸마다 이름이 붙은 막대
  stepBar(cells, t, h = 4) {
    const c = palette(t);
    if (!cells || !cells.length) return div();
    return h_flex().w_full().gap(3).children(cells.map((s, i) => {
      const st = s[1];
      let bar = div().h(h).rounded(h).bg(st === "done" ? c.done : st === "working" ? c.work : c.todo);
      return v_flex().id(`sb${i}`).flex_1().min_w(0).gap(2).children([
        bar,
        div().truncate().text_size(9).text_color(st === "working" ? c.work : st === "done" ? t.muted : t.dim)
          .when(st === "working", (x) => x.font_semibold()).child(s[0]),
      ]);
    }));
  }

  // 일하는 카드: 단계를 세로로, 지금 단계를 분명하게
  stepList(cells, nowText, t) {
    const c = palette(t);
    return v_flex().gap(3).children(cells.map((s, i) => {
      const st = s[1];
      return h_flex().id(`sl${i}`).items_center().gap(7).children([
        this.mark(st === "done" ? "done" : st === "working" ? "working" : "todo", t, 11),
        div().flex_1().min_w(0).truncate().text_size(12)
          .text_color(st === "working" ? c.work : st === "done" ? t.foreground : t.dim)
          .when(st === "working", (x) => x.font_semibold()).child(s[0]),
        div().text_size(10).text_color(st === "working" ? c.work : t.dim)
          .child(st === "working" ? (nowText ? `지금 · ${nowText}` : "지금") : (s[2] || "")),
      ]);
    }));
  }

  segCaption(list) {
    if (!list || !list.length) return "";
    const done = list.filter((c) => c[1] === "done").length;
    const cur = list.find((c) => c[1] === "working");
    const guess = list.some((c) => c[3] === "짐작") ? " · 짐작" : "";
    if (cur) return `${cur[0]}${cur[0].endsWith("중") ? "" : " 중"} · ${done}/${list.length}${guess}`;
    return `${list.length}단계 끝${guess}`;
  }

  label(text, t) { return div().text_size(11).text_color(t.dim).child(text); }

  // ── 머리: 상태 · 이름 · 덩어리 막대 ──
  header(d, p, t, cx) {
    const c = palette(t);
    const st = d.status;
    const pillColor = st === "working" ? c.work : st === "asking" ? c.ask : t.dim;
    const pillText = st === "working" ? "일하는 중" : st === "asking" ? "나를 기다림" : "쉬는 중";
    const pill = h_flex().items_center().gap(5).px(7).py(1).rounded(6).bg(t.raised)
      .child(st === "working" ? svg(ICON("spinner")).size(11).text_color(pillColor) : div().size(7).rounded_full().bg(pillColor))
      .child(div().text_size(11).font_semibold().text_color(pillColor).child(pillText));
    const segs = p ? p.cards.map((k) => k.state) : [];
    const w = p ? p.cards.find((k) => k.state === "working") : null;
    const rows = [
      h_flex().items_center().gap(8).children([
        pill,
        div().text_size(11).text_color(t.dim).child(`${d.days}일째 · 도우미 ${d.helpers}번`),
        div().flex_1(),
        h_flex().id("pick").items_center().gap(3).px(6).py(1).rounded(6).hover((s) => s.bg(t.raised))
          .on_click((_e, cx) => { this.picking = !this.picking; if (this.picking) this.loadSessions(cx); cx.notify(); })
          .child(div().text_size(11).text_color(t.muted).child(this.pick ? "고른 세션" : `${d.terminal || ""} 따라가는 중`))
          .child(svg(ICON("chevron-down")).size(12).text_color(t.muted)),
      ]),
      div().text_size(15).font_semibold().text_color(t.foreground).truncate().child(tabName(d.terminal) || d.name || "(이름 없는 세션)"),
    ];
    if (p) rows.push(h_flex().items_center().gap(10).children([
      div().flex_1().child(this.segs(segs, t, 5)),
      div().text_size(11).text_color(t.muted).child(`${p.done} / ${p.total} 끝`),
    ]));
    if (w) {
      const nowH = w.helpers.find((x) => x.state === "working") || w.helpers[0] || {};
      const cur = (w.cells || []).find((x) => x[1] === "working");
      rows.push(h_flex().items_center().gap(6).children([
        svg(ICON("spinner")).size(11).text_color(c.work),
        div().flex_1().min_w(0).truncate().text_size(12).text_color(c.work)
          .child(`지금 · ${w.key ? w.key + " " : ""}${w.title}${cur ? " · " + cur[0] : ""}${nowH.now && (!cur || cur[0] !== nowH.now) ? " · " + nowH.now : ""}`),
      ]));
    }
    const a = d.ahead;
    if (a && a.left > 0) rows.push(h_flex().items_center().gap(6).children([
      div().size(9).rounded_full().border(1).border_color(t.muted),
      div().flex_1().min_w(0).truncate().text_size(12).text_color(t.muted).child(`다음 · ${a.next}${a.guess && !a.next_exact ? " · 짐작" : ""}`),
    ]));
    if (d.asked) rows.push(h_flex().items_start().gap(6).px(8).py(5).rounded(7).bg(t.raised).border(1).border_color(c.ask).children([
      this.mark("asking", t, 12),
      div().flex_1().min_w(0).text_size(12).text_color(c.ask).child(d.asked.length > 160 ? d.asked.slice(0, 159) + "…" : d.asked),
    ]));
    if (d.last_words) rows.push(h_flex().gap(6).children([
      div().text_size(11).text_color(t.dim).child(`마지막 말 ${d.last_words_at || ""}`),
      div().flex_1().min_w(0).truncate().text_size(11).text_color(t.muted).child(d.last_words),
    ]));
    return v_flex().gap(6).px(14).pt(10).pb(10).border_b(1).border_color(t.border).children(rows);
  }

  picker(t, cx) {
    const rows = [
      h_flex().id("pick-follow").px(10).py(6).rounded(6).hover((s) => s.bg(t.raised))
        .on_click((_e, cx) => { this.pick = null; this.picking = false; this.phase = null; this.open = {}; this.showAhead = false; this.lastTerm = null; this.refresh(cx); cx.notify(); })
        .child(div().text_size(12).text_color(palette(t).work).child("지금 보는 칸 따라가기")),
      ...this.sessions.map((s) => h_flex().id(`pick-${s.session}`).items_center().gap(8).px(10).py(6).rounded(6).hover((x) => x.bg(t.raised))
        .on_click((_e, cx) => { this.pick = s.session; this.picking = false; this.phase = null; this.open = {}; this.showAhead = false; this.refresh(cx); cx.notify(); })
        .children([
          this.mark(s.status === "busy" ? "working" : s.status === "waiting" ? "asking" : "idle", t, 11),
          image(agentIcon(s.agent || s.session)).size(11),
          div().w(30).text_size(11).text_color(t.dim).child(s.terminal || ""),
          div().flex_1().min_w(0).truncate().text_size(12).text_color(t.foreground).child(tabName(s.terminal) || s.name || s.session.slice(0, 12)),
        ])),
    ];
    return v_flex().mx(14).my(8).p(4).rounded(8).bg(t.surface).border(1).border_color(t.border).children(rows);
  }

  road(d, t, idx) {
    const c = palette(t);
    const a = d.ahead && d.ahead.left > 0 ? d.ahead : null;
    const chips = d.phases.map((p, i) => {
        const on = i === idx && !this.showAhead;
        return h_flex().id(`ph-${i}`).items_center().gap(5).px(8).py(3).rounded(12).border(1)
          .border_color(on ? c.work : t.border).when(on, (s) => s.bg(t.raised)).hover((s) => s.bg(t.raised))
          .on_click((_e, cx) => { this.phase = i; this.open = {}; this.showAhead = false; cx.notify(); })
          .children([
            this.mark(p.state === "working" ? "working" : "done", t, 10),
            div().text_size(11).text_color(on ? t.foreground : t.muted).child(`${p.date} ${p.name.length > 12 ? p.name.slice(0, 11) + "…" : p.name}`),
          ]);
      });
    if (a) chips.push(h_flex().id("ph-ahead").items_center().gap(5).px(8).py(3).rounded(12).border(1).border_color(c.work)
      .when(this.showAhead, (s) => s.bg(t.raised)).hover((s) => s.bg(t.raised))
      .on_click((_e, cx) => { this.showAhead = !this.showAhead; this.open = {}; cx.notify(); })
      .child(div().text_size(11).font_semibold().text_color(c.work).child(`앞으로 ${a.left}`)));
    return v_flex().gap(6).children([
      d.phases.length ? this.label("지나온 길", t) : div(),
      h_flex().flex_wrap().gap(4).children(chips),
    ]);
  }

  aheadList(a, t) {
    const c = palette(t);
    const tag = a.source === "할 일 목록" ? "할 일 목록에서" : `${a.source}에서 · 끝남은 짐작`;
    const left = a.items.filter((x) => x.state !== "done");
    const done = a.items.filter((x) => x.state === "done");
    // 움직이지 않는 기호만: 대기 빈 원 · 하는 중 채운 원 · 멈춤 노란 원 · 끝 흐린 원
    const dot = (st) => st === "working" ? div().size(9).rounded_full().bg(c.work)
      : st === "stopped" ? div().size(9).rounded_full().bg(c.ask)
      : st === "done" ? div().size(9).rounded_full().bg(t.dim)
      : div().size(9).rounded_full().border(1).border_color(t.muted);
    const row = (x) => h_flex().items_center().gap(7).children([
      dot(x.state),
      x.key ? div().w(26).text_size(11).text_color(t.dim).child(x.key) : div(),
      div().flex_1().min_w(0).truncate().text_size(12).text_color(x.state === "done" ? t.dim : t.foreground).child(x.title),
      x.state === "working" ? div().text_size(11).text_color(c.work).child("하는 중")
        : x.state === "stopped" ? div().text_size(11).text_color(c.ask).child("멈춤") : div(),
    ]);
    const rows = [
      h_flex().items_center().gap(8).children([
        div().text_size(16).font_semibold().child("앞으로"),
        div().text_size(11).text_color(t.dim).child(tag),
      ]),
      ...left.slice(0, 8).map(row),
    ];
    if (left.length > 8) rows.push(div().text_size(11).text_color(t.dim).child(`나머지 ${left.length - 8}개`));
    if (done.length) {
      const open = !!this.open.aheadDone;
      rows.push(h_flex().id("ahead-done").items_center().on_click((_e, cx) => { this.open.aheadDone = !open; cx.notify(); })
        .child(div().text_size(11).text_color(t.muted).child(`끝난 것 ${done.length}개 ${open ? "▾" : "▸"}`)));
      if (open) done.forEach((x) => rows.push(row(x)));
    }
    return v_flex().gap(7).p(10).rounded(8).bg(t.background).border(1).border_color(t.border).children(rows);
  }

  helperRow(h, t) {
    const c = palette(t);
    const working = h.state === "working";
    return v_flex().gap(4).p(8).rounded(7).bg(t.background).border(1).border_color(working ? c.work : t.border).children([
      h_flex().items_center().gap(6).children([
        this.mark(working ? "working" : "done", t, 11),
        div().flex_1().min_w(0).truncate().text_size(12).font_semibold().text_color(t.foreground).child(h.name),
        div().text_size(10).text_color(t.dim).child(h.time),
      ]),
      h_flex().items_center().gap(5).children([
        image(agentIcon(h.kind)).size(11),
        div().text_size(11).text_color(t.muted).child(isExternalKind(h.kind) ? `${h.kind} · ${h.model}` : `${h.model || "Claude"} · ${h.kind}`),
      ]),
      h.job ? div().text_size(11).text_color(t.muted).child(`맡은 일 · ${h.job}`) : div(),
      h.report ? div().text_size(11).text_color(t.muted).child(`보고 · ${h.report}`) : div(),
      this.stepBar(h.cells, t, 3),
      div().text_size(11).text_color(working ? c.work : t.dim)
        .child(`${working ? "지금" : "마지막"} · ${working ? h.now : (h.said || h.now)}`),
    ]);
  }

  // Hide 의 노드처럼: 끝 = 초록 체크 + 한 줄, 일하는 중 = 파란 테두리 + 모델 줄 + 지금 + 막대
  card(k, i, t) {
    const c = palette(t);
    const id = `${this.phase}-${i}`;
    const open = !!this.open[id];
    const working = k.state === "working";
    const h0 = k.helpers[0] || {};
    const top = h_flex().items_center().gap(7).children([
      this.mark(working ? "working" : "done", t, 13),
      k.key ? div().text_size(11).font_semibold().text_color(c.work).child(k.key) : div(),
      div().flex_1().min_w(0).truncate().text_size(13).font_semibold().text_color(t.foreground).child(k.title),
      div().text_size(10).text_color(t.dim).child(k.time),
    ]);
    const parts = [top];
    if (working) {
      parts.push(h_flex().items_center().gap(5).pl(20).children([
        h0.kind === "세션" ? svg(ICON("terminal")).size(11).text_color(t.muted) : image(agentIcon(h0.kind)).size(11),
        div().text_size(11).text_color(t.muted).child(h0.kind === "세션" ? "세션이 직접" : isExternalKind(h0.kind) ? `${h0.kind} · ${h0.model}` : `${h0.model || "Claude"} · ${h0.kind || ""}${k.helpers.length > 1 ? ` 외 ${k.helpers.length - 1}` : ""}`),
      ]));
      parts.push(div().h(1).bg(t.border).my(2));
      const nowH = k.helpers.find((x) => x.state === "working") || h0;
      parts.push(this.stepList(k.cells, nowH.now && !k.cells.some((x) => x[0] === nowH.now) ? nowH.now : "", t));
      if (h0.job) parts.push(div().pt(2).text_size(11).text_color(t.muted).child(`맡은 일 · ${h0.job}`));
    } else {
      if (k.done_text) parts.push(div().pl(20).text_size(12).text_color(t.muted).child(k.done_text));
      parts.push(div().pl(20).pt(2).child(this.stepBar(k.cells, t, 3)));
    }
    const box = v_flex().id(`card-${id}`).gap(4).px(10).py(8).rounded(9)
      .bg(working ? t.background : t.surface)
      .border(working ? 2 : 1).border_color(working ? c.work : t.border)
      .hover((s) => s.bg(t.raised))
      .on_click((_e, cx) => { this.open[id] = !open; cx.notify(); })
      .children(parts);
    const kids = [box];
    const turns = k.turns || [];
    if (turns.length) {
      const co = !!this.convOpen[id];
      const lastT = turns[turns.length - 1];
      kids.push(h_flex().id(`conv-${id}`).items_center().gap(6).mx(10).px(8).py(4)
        .rounded_b(8).border(1).border_t(0).border_color(t.border).bg(t.chrome).hover((x) => x.bg(t.raised))
        .on_click((_e, cx) => { this.convOpen[id] = !co; cx.notify(); })
        .children([
          svg(ICON(co ? "chevron-down" : "chevron-right")).size(11).text_color(t.muted),
          div().text_size(11).font_semibold().text_color(t.muted).child(`대화 ${turns.length}개`),
          div().flex_1().min_w(0).truncate().text_size(11).text_color(t.dim)
            .child(co ? "" : `마지막 「${lastT.said.replace(/\s+/g, " ").slice(0, 24)}${lastT.said.length > 24 ? "…" : ""}」`),
        ]));
      if (co) kids.push(v_flex().gap(5).pl(14).pt(5).children(
        turns.map((tn, j) => [tn, j]).reverse().map(([tn, j]) => this.turnCard(tn, `${id}-${j}`, t))));
    }
    if (open) {
      kids.push(v_flex().gap(5).pl(14).pt(6).children([
        ...(k.after ? [v_flex().gap(2).p(8).rounded(7).bg(t.raised).children([this.label("세션이 나에게 한 말", t), div().text_size(12).text_color(t.foreground).child(k.after)])] : []),
        ...(k.cells.some((x) => x[2]) ? [div().text_size(11).text_color(t.muted).child(k.cells.map((x) => `${x[0]}${x[2] ? " " + x[2] : ""}`).join(" → "))] : []),
        ...k.helpers.map((h) => this.helperRow(h, t)),
      ]));
    }
    return v_flex().children(kids);
  }

  chip(text, t, color) {
    return div().px(6).py(1).rounded(5).bg(t.raised).text_size(11).text_color(color || t.muted).child(text);
  }

  turnCard(tn, key, t) {
    const c = palette(t);
    const working = tn.state === "working";
    const full = !!this.turnOpen[key];
    const cut = (x, n) => (!x ? "" : full || x.length <= n ? x : x.slice(0, n - 1) + "…");
    const firstSent = (x) => { const m = String(x || "").match(/^.+?(?:[.!?。]|니다|어요|해요)(?=\s|$)/); return m ? m[0] : String(x || ""); };
    const rows = [];
    rows.push(h_flex().items_center().gap(7).children([
      this.mark(working ? "working" : "done", t, 11),
      div().text_size(11).text_color(t.dim).child(tn.time),
      div().flex_1().min_w(0).truncate().text_size(12).text_color(t.foreground).when(tn.short, (x) => x.font_semibold())
        .child(`「${cut(tn.said, full ? 999 : 60)}」`),
      tn.kind === "pick" ? this.chip("선택", t, c.work) : div(),
    ]));
    if (full && tn.said.length > 60) rows.push(div().text_size(12).text_color(t.foreground).child(tn.said));
    if (tn.asked && (tn.short || tn.kind === "pick")) {
      rows.push(v_flex().gap(1).p(6).rounded(6).bg(t.raised).children([
        this.label("이 답이 가리킨 것", t),
        div().text_size(11).text_color(t.foreground).child(cut(tn.asked, 120)),
      ]));
    }
    if (tn.first) rows.push(div().text_size(11).text_color(t.muted).child(`알아들음 · ${cut(tn.first, 90)}`));
    const chips = [];
    if (tn.helpers && tn.helpers.length) chips.push(this.chip(`도우미 ${tn.helpers.length}`, t));
    if (tn.files && tn.files.length) chips.push(this.chip(`파일 ${tn.files.length}`, t));
    if (tn.commits) chips.push(this.chip(`커밋 ${tn.commits}`, t, c.done));
    if (tn.tests) chips.push(this.chip(`시험 ${tn.tests}`, t, tn.tests === "통과" ? c.done : c.ask));
    if (chips.length) rows.push(h_flex().flex_wrap().gap(4).children(chips));
    if (full && tn.helpers && tn.helpers.length) rows.push(div().text_size(11).text_color(t.muted).child(`도우미 · ${tn.helpers.join(" · ")}`));
    if (full && tn.files && tn.files.length) rows.push(div().text_size(11).text_color(t.muted).child(`고친 파일 · ${tn.files.join(", ")}`));
    if (working) rows.push(div().text_size(11).text_color(c.work).child("지금 이 지시를 하는 중"));
    else if (tn.last) rows.push(div().text_size(11).text_color(t.foreground).child(`끝에 한 말 · ${full ? tn.last : firstSent(tn.last)}`));
    return v_flex().id(`turn-${key}`).gap(4).px(9).py(7).rounded(8)
      .bg(working ? t.background : t.surface).border(1).border_color(working ? c.work : t.border)
      .hover((x) => x.bg(t.raised))
      .on_click((_e, cx) => { this.turnOpen[key] = !full; cx.notify(); })
      .children(rows);
  }

  connector(active, t) {
    const c = palette(t);
    return h_flex().justify_center().child(div().w(active ? 2 : 1).h(12).bg(active ? c.work : t.border));
  }

  render(cx) {
    const t = theme();
    const d = this.data;
    const root = v_flex().size_full().bg(t.chrome).text_color(t.foreground);
    if (!d) return root.child(div().p(16).text_size(12).text_color(t.dim).child(this.err || "지도를 읽는 중…"));
    if (d.error) {
      return root.children([
        div().p(16).text_size(12).text_color(t.dim).child("이 칸에는 AI 세션이 없어요. 다른 칸을 보거나 세션을 골라 주세요."),
        div().id("pick2").mx(16).text_size(12).text_color(palette(t).work).on_click((_e, cx) => { this.picking = !this.picking; if (this.picking) this.loadSessions(cx); cx.notify(); }).child("세션 고르기"),
        this.picking ? this.picker(t, cx) : div(),
      ]);
    }
    const idx = this.phase ?? Math.max(0, d.phases.length - 1);
    const p = d.phases[idx];
    const parts = [];
    const aheadOn = d.ahead && d.ahead.left > 0;
    if (d.phases.length || aheadOn) parts.push(this.road(d, t, idx));
    if (this.showAhead && aheadOn) {
      parts.push(this.aheadList(d.ahead, t));
    } else if (p) {
      parts.push(v_flex().gap(3).children([
        h_flex().items_center().gap(8).children([
          div().text_size(16).font_semibold().child(p.name),
          div().text_size(11).text_color(t.dim).child(p.date),
        ]),
        p.goal ? div().text_size(12).text_color(t.muted).child(p.goal) : div(),
      ]));
      const nodes = [];
      // 최근 할 일이 위로(내림차순). 펼침 상태는 원래 번호로 기억한다.
      p.cards.map((k, i) => [k, i]).reverse().forEach(([k, i], n) => {
        if (n > 0) nodes.push(this.connector(k.state === "working", t));
        nodes.push(this.card(k, i, t));
      });
      parts.push(v_flex().children(nodes));
    }
    return root.children([
      this.header(d, p, t, cx),
      this.picking ? this.picker(t, cx) : div(),
      v_flex().id("pm-body").flex_1().min_h(0).overflow_y_scrollbar().children([
        v_flex().px(14).pt(10).pb(12).gap(10).children(parts),
      ]),
      div().px(14).py(6).border_t(1).border_color(t.border).text_size(10).text_color(t.dim)
        .child(`${d.source === "hook+log" ? "훅 + 대화 기록" : d.source === "db" ? "OpenCode 기록에서 읽음" : "대화 기록에서 읽음"} · ${d.built}${this.busy ? " · 다시 읽는 중" : ""}`),
    ]);
  }
}
