// where was i 프로토타입 — herdr 오른쪽 칸.
// 지금 보는 터미널(또는 고른 세션)의 Claude 세션 지도를 그린다. 자료는 pmap.py 가 만든다.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { exec, focused, theme, home, checking, agents } from "herdr";

const SCRIPT = () => `${home()}/Documents/where-was-i/pmap.py`;
// 세션 이름은 herdr 탭 이름을 먼저 쓴다
const tabName = (term) => {
  const a = term && agents().find((x) => x.address === term);
  if (!a) return "";
  const title = String(a.title || "").replace(/^[^\p{L}\p{N}]+/u, "").trim();
  if (a.tab_name && a.tab_name !== "Claude Code") return a.tab_name;
  return title || a.tab_name || "";
};
const KIND = { 구현: "구현", 검토: "검토", 고침: "고침", 조사: "조사", 계획: "계획", 세션: "세션" };

export default class ProgressMap extends View {
  init(_props, cx) {
    this.data = null;          // 지도 자료
    this.err = "";
    this.sessions = [];        // 세션 목록(고르기)
    this.pick = null;          // 고른 세션 id, null 이면 지금 칸 따라가기
    this.picking = false;
    this.lastTerm = null;
    this.phase = null;         // 고른 덩어리 번호
    this.open = {};            // 펼친 카드
    this.busy = false;
    if (checking()) return;
    this.refresh(cx);
    cx.timer.every(1500, (cx) => {
      const t = focused()?.terminal ?? null;
      if (!this.pick && t && t !== this.lastTerm) this.refresh(cx);
    });
    cx.timer.every(5000, (cx) => this.refresh(cx));
  }

  refresh(cx) {
    if (this.busy) return;
    const t = focused()?.terminal ?? null;
    const args = this.pick ? ["map", "--session", this.pick] : (t ? ["map", "--terminal", t] : null);
    if (!args) return;
    this.busy = true;
    cx.spawn(async (cx) => {
      try {
        const r = await exec("python3", [SCRIPT(), ...args], { timeout: 60000 });
        const d = JSON.parse(r.stdout || "{}");
        if (!this.pick && t !== this.lastTerm) { this.phase = null; this.open = {}; }
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
  cells(list, t) {
    if (!list || !list.length) return div();
    const color = (s) => (s === "done" ? t.done : s === "working" ? t.working : t.raised);
    return h_flex().w_full().gap(2).mt(4).children(list.map((c, i) => {
      let el = div().id(`c${i}-${c[0]}`).flex_1().h(4).rounded(2).bg(color(c[1]));
      if (c[1] === "working") el = el.loop("opacity", { from: 0.35, to: 1, period: 1400 });
      return el;
    }));
  }

  cellCap(list, t) {
    if (!list || !list.length) return div();
    const done = list.filter((c) => c[1] === "done").length;
    const cur = list.find((c) => c[1] === "working");
    const guess = list.some((c) => c[3] === "짐작");
    const txt = cur ? `${cur[0]}${cur[0].endsWith("중") ? "" : " 중"} · ${done}/${list.length}` : `${list.length}칸 끝`;
    return div().text_size(11).text_color(t.dim).child(txt + (guess ? " · 짐작" : ""));
  }

  pill(status, t) {
    const map = { working: ["일하는 중", t.working], asking: ["나를 기다림", t.blocked], idle: ["쉬는 중", t.idle] };
    const [label, c] = map[status] || ["?", t.dim];
    let dot = div().w(7).h(7).rounded_full().bg(c);
    if (status === "working") dot = dot.loop("opacity", { from: 0.3, to: 1, period: 1600 });
    return h_flex().items_center().gap(6).px(8).py(2).rounded(10).border(1).border_color(c)
      .child(dot).child(div().text_size(12).text_color(c).child(label));
  }

  header(d, t, cx) {
    const name = tabName(d.terminal) || d.name || "(이름 없는 세션)";
    const nowLine = d.now && d.now.length
      ? `${d.now.length}명 일하는 중: ${d.now[0].name} · ${d.now[0].now} · ${d.now[0].mins}분째`
      : "일하는 도우미 없음";
    return v_flex().gap(6).px(14).pt(12).pb(10).border_b(1).border_color(t.border).children([
      h_flex().items_center().justify_between().children([
        this.pill(d.status, t),
        div().id("pick").text_size(11).text_color(t.accent).px(6).py(2).rounded(6).hover((s) => s.bg(t.raised))
          .on_click((_e, cx) => { this.picking = !this.picking; if (this.picking) this.loadSessions(cx); cx.notify(); })
          .child(this.pick ? "고른 세션 ▾" : `${d.terminal || ""} 따라가는 중 ▾`),
      ]),
      div().text_size(16).font_semibold().text_color(t.foreground).child(name),
      div().text_size(12).text_color(t.muted).child(`${d.started} 시작 · ${d.days}일째 · 도우미 ${d.helpers}번`),
      v_flex().gap(3).p(8).rounded(8).bg(t.surface).border(1).border_color(t.border).children([
        h_flex().gap(8).children([div().w(48).text_size(11).text_color(t.dim).child("지금"),
          div().flex_1().min_w(0).text_size(12).text_color(d.now && d.now.length ? t.working : t.muted).child(nowLine)]),
        h_flex().gap(8).children([div().w(48).text_size(11).text_color(t.dim).child("마지막 말"),
          div().flex_1().min_w(0).text_size(12).text_color(t.foreground).child(`${d.last_words || "-"} · ${d.last_words_at || ""}`)]),
        h_flex().gap(8).children([div().w(48).text_size(11).text_color(t.dim).child("나에게"),
          div().flex_1().min_w(0).text_size(12).text_color(d.asked ? t.blocked : t.muted).child(d.asked || "물어본 것 없음")]),
      ]),
    ]);
  }

  picker(t, cx) {
    const rows = [
      h_flex().id("pick-follow").px(10).py(6).rounded(6).hover((s) => s.bg(t.raised))
        .on_click((_e, cx) => { this.pick = null; this.picking = false; this.phase = null; this.open = {}; this.lastTerm = null; this.refresh(cx); cx.notify(); })
        .child(div().text_size(12).text_color(t.accent).child("지금 보는 칸 따라가기")),
      ...this.sessions.map((s) => h_flex().id(`pick-${s.session}`).gap(8).px(10).py(6).rounded(6).hover((x) => x.bg(t.raised))
        .on_click((_e, cx) => { this.pick = s.session; this.picking = false; this.phase = null; this.open = {}; this.refresh(cx); cx.notify(); })
        .children([
          div().w(36).text_size(11).text_color(t.dim).child(s.terminal || ""),
          div().flex_1().min_w(0).truncate().text_size(12).text_color(t.foreground).child(tabName(s.terminal) || s.name || s.session.slice(0, 8)),
          div().text_size(11).text_color(s.status === "busy" ? t.working : t.dim).child(s.status === "busy" ? "일하는 중" : s.status === "waiting" ? "기다림" : ""),
        ])),
    ];
    return v_flex().mx(14).my(8).p(4).rounded(8).bg(t.surface).border(1).border_color(t.border).children(rows);
  }

  road(d, t, cx, idx) {
    return v_flex().gap(6).children([
      div().text_size(11).text_color(t.dim).child("지나온 길"),
      h_flex().flex_wrap().gap(4).children(d.phases.map((p, i) => {
        const on = i === idx;
        return v_flex().id(`ph-${i}`).px(7).py(5).rounded(7).border(1).border_color(on ? t.accent : t.border)
          .when(on, (s) => s.bg(t.raised)).hover((s) => s.bg(t.raised))
          .on_click((_e, cx) => { this.phase = i; this.open = {}; cx.notify(); })
          .children([
            div().text_size(10).text_color(t.dim).child(p.date),
            h_flex().items_center().gap(5).children([
              div().w(6).h(6).rounded_full().bg(p.state === "working" ? t.working : t.done),
              div().text_size(11).text_color(on ? t.foreground : t.muted).child(p.name.length > 14 ? p.name.slice(0, 13) + "…" : p.name),
            ]),
          ]);
      })),
    ]);
  }

  helperRow(h, t, key) {
    return v_flex().gap(2).p(7).rounded(7).bg(t.chrome).border(1).border_color(h.state === "working" ? t.working : t.border).children([
      h_flex().items_center().gap(6).children([
        div().px(5).rounded(4).border(1).border_color(t.border).text_size(10).text_color(h.state === "working" ? t.working : t.accent).child(KIND[h.kind] || h.kind),
        div().flex_1().min_w(0).truncate().text_size(12).text_color(t.foreground).child(h.name),
        div().text_size(10).text_color(t.dim).child(h.time),
      ]),
      h.job ? div().text_size(11).text_color(t.muted).child(`맡은 일 · ${h.job}`) : div(),
      this.cells(h.cells, t),
      this.cellCap(h.cells, t),
      div().text_size(11).text_color(h.state === "working" ? t.working : t.dim).child((h.state === "working" ? "지금 · " : "마지막 · ") + (h.state === "working" ? h.now : (h.said || h.now))),
    ]);
  }

  card(c, i, t, cx) {
    const id = `${this.phase}-${i}`;
    const open = !!this.open[id];
    const tag = c.key ? div().text_size(11).text_color(t.accent).child(c.key) : div();
    let body = v_flex().id(`card-${id}`).p(8).rounded(8).bg(t.surface).border(1)
      .border_color(c.state === "working" ? t.working : t.border).hover((s) => s.bg(t.raised))
      .on_click((_e, cx) => { this.open[id] = !open; cx.notify(); })
      .children([
        h_flex().items_center().gap(6).children([
          tag,
          div().flex_1().min_w(0).text_size(13).text_color(t.foreground).child(c.title),
          div().text_size(10).text_color(t.dim).child(c.time),
        ]),
        this.cells(c.cells, t),
        this.cellCap(c.cells, t),
      ]);
    const kids = [body];
    if (open) {
      kids.push(v_flex().gap(5).pl(10).pt(6).children([
        c.done_text ? div().text_size(11).text_color(t.muted).child(`된 것 · ${c.done_text}`) : div(),
        ...c.helpers.map((h, j) => this.helperRow(h, t, `${id}-${j}`)),
      ]));
    }
    return v_flex().children(kids);
  }

  render(cx) {
    const t = theme();
    const d = this.data;
    const root = v_flex().size_full().bg(t.chrome).text_color(t.foreground);
    if (!d) return root.child(div().p(16).text_size(12).text_color(t.dim).child(this.err || "지도를 읽는 중…"));
    if (d.error) {
      return root.children([
        div().p(16).text_size(12).text_color(t.dim).child("이 칸에는 Claude 세션이 없어요. 다른 칸을 보거나 세션을 골라 주세요."),
        div().id("pick2").mx(16).text_size(12).text_color(t.accent).on_click((_e, cx) => { this.picking = !this.picking; if (this.picking) this.loadSessions(cx); cx.notify(); }).child("세션 고르기 ▾"),
        this.picking ? this.picker(t, cx) : div(),
      ]);
    }
    const idx = this.phase ?? Math.max(0, d.phases.length - 1);
    const p = d.phases[idx];
    const body = v_flex().id("pm-body").flex_1().min_h(0).overflow_y_scrollbar().px(14).py(12).gap(12);
    const parts = [];
    if (d.phases.length) parts.push(this.road(d, t, cx, idx));
    if (p) {
      parts.push(v_flex().gap(6).children([
        h_flex().items_center().gap(8).children([
          div().text_size(14).font_semibold().child(p.name),
          div().text_size(11).text_color(t.dim).child(p.date),
        ]),
        p.goal ? div().text_size(12).text_color(t.muted).child(p.goal) : div(),
        this.cells(Array.from({ length: p.total }, (_, k) => ["", k < p.done ? "done" : (p.state === "working" && k === p.done ? "working" : "todo")]), t),
        div().text_size(11).text_color(t.muted).child(`${p.total}개 중 ${p.done}개 끝${p.state === "working" ? " · 일하는 중" : ""}`),
      ]));
      parts.push(v_flex().gap(6).children(p.cards.map((c, i) => this.card(c, i, t, cx))));
    }
    return root.children([
      this.header(d, t, cx),
      this.picking ? this.picker(t, cx) : div(),
      body.children(parts),
      div().px(14).py(6).border_t(1).border_color(t.border).text_size(10).text_color(t.dim)
        .child(`대화 기록에서 읽음 · ${d.built}${this.busy ? " · 다시 읽는 중" : ""}`),
    ]);
  }
}
