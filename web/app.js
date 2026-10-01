/* Paper Reader 화면. 서버(/api/*)에서 데이터를 받아 그리고, 버튼은 작업을 넣은 뒤 /api/jobs 로 진행 상황을 지켜봅니다. */
"use strict";

// ---------- 공용 ----------
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pref = {
  get(k, d) { try { const v = localStorage.getItem("rp." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("rp." + k, JSON.stringify(v)); } catch (e) { /* 저장 못 해도 동작에는 문제 없음 */ } },
};

// 서버가 켤 때마다 새로 만든 비밀 토큰 (index.html 에 심어져 옴). 모든 /api · /pdf · /fig 요청에 붙입니다.
const TOKEN = (document.querySelector('meta[name="rp-token"]') || {}).content || "";
const authUrl = (u) => u + (u.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN);

const api = {
  async req(method, path, body) {
    const r = await fetch(path, { method, headers: { "Content-Type": "application/json", "X-RP-Token": TOKEN }, body: body ? JSON.stringify(body) : undefined });
    if (r.status === 403) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.error || "인증 실패 — 창을 새로 고침(F5) 해 주세요.");
    }
    const d = await r.json().catch(() => ({ error: "응답을 읽지 못했습니다" }));
    if (!r.ok || d.error) throw new Error(d.error || r.statusText);
    return d;
  },
  get(p) { return this.req("GET", p); },
  post(p, b) { return this.req("POST", p, b || {}); },
};

function toast(msg, err) {
  const t = document.createElement("div");
  t.className = "toast" + (err ? " err" : "");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => { t.style.transition = "opacity .3s"; t.style.opacity = "0"; setTimeout(() => t.remove(), 300); }, err ? 6000 : 3200);
}

async function run(fn) {
  try { return await fn(); } catch (e) { toast(e.message, true); }
}

// 마크다운 + 수식: 수식($..$, $$..$$)을 먼저 떼어 두고 marked 로 바꾼 뒤 KaTeX 로 채웁니다
const MATH_RE = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|(?<![\\$\w])\$(?!\s)([^\n$]+?)(?<!\s)\$(?!\w)/g;
function tex(m, display) {
  try { return katex.renderToString(m, { displayMode: display, throwOnError: false, strict: "ignore" }); } catch (e) { return esc(m); }
}
function md(text) {
  if (!text) return "";
  const store = [];
  const src = String(text).replace(MATH_RE, (all, a, b, c) => {
    store.push(a !== undefined ? [a, true] : b !== undefined ? [b, true] : [c, false]);
    return "@@M" + (store.length - 1) + "@@";
  });
  const html = marked.parse(src, { gfm: true, breaks: false });
  return html.replace(/@@M(\d+)@@/g, (_, i) => tex(store[+i][0], store[+i][1]));
}
function inl(text) {
  // 원문·번역 문단: 마크다운 없이, 수식만 그림
  if (!text) return "";
  let out = "", last = 0;
  String(text).replace(MATH_RE, (all, a, b, c, off) => {
    out += esc(text.slice(last, off));
    out += a !== undefined ? tex(a, true) : b !== undefined ? tex(b, true) : tex(c, false);
    last = off + all.length;
    return all;
  });
  return out + esc(text.slice(last));
}

// 토큰 수 짧게: 1234 → 1.2K, 1234567 → 1.23M
function fmtTok(n) {
  n = n || 0;
  return n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 1 : 2) + "M" : n >= 1e3 ? (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "K" : String(n);
}
function usageText(u) {
  return u ? `Claude ${u.calls}회 · 토큰 입력 ${fmtTok(u.tin)} / 출력 ${fmtTok(u.tout)}` : "";
}

const CHEV = '<svg class="chev" viewBox="0 0 20 20"><path d="M7 4l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const KIND = { qa: "Q&A", explain: "깊게 해설", figure: "그림 해설", equation: "수식 풀이" };
const READING = { todo: "읽을 예정", reading: "읽는 중", done: "완료" };

// ---------- 상태 ----------
const S = {
  papers: [], key: pref.get("key", null), paper: null, units: null,
  tab: pref.get("tab", "overview"), mode: pref.get("mode", "both"), filter: "all",
  view: "paper", anchor: null, qaOrder: "section", qaTag: null,
  compareSel: new Set(), jobs: [], seenJobs: {}, pendingAsks: [], panel: "ask", usage: null, lastDone: {}, figCache: {},
};

// ---------- 라이브러리 ----------
async function loadLibrary() {
  const d = await api.get("/api/library");
  S.papers = d.papers;
  S.usage = d.usage;
  S.limits = d.limits;
  renderLimits();
  S.notionReady = d.notion;
  if (!d.claude) $("#footNote").innerHTML = "Claude 실행 파일을 찾지 못했습니다. VS Code 의 Claude Code 확장을 확인하세요.";
  renderLibrary();
}

function renderLibrary() {
  const list = $("#paperList");
  const sig = JSON.stringify([S.papers, S.filter, S.key, S.view, [...S.compareSel]]);
  if (sig === S.libSig) return;
  S.libSig = sig;
  const items = S.papers.filter((p) => S.filter === "all" || (p.reading || "todo") === S.filter);
  if (!items.length) {
    list.innerHTML = `<div class="muted small" style="padding:20px 6px;text-align:center">${S.papers.length ? "이 분류에 논문이 없습니다" : "papers 폴더에 PDF 를 넣거나<br>아래에 arXiv 번호를 입력하세요"}</div>`;
    return;
  }
  const comparing = S.view === "compare";
  list.innerHTML = items.map((p) => {
    const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
    const status = p.status === "new" ? '<span class="pill amber">새 논문</span>'
      : p.missing ? '<span class="pill red">PDF 없음</span>' : "";
    const r = p.reading || "todo";
    return `<div class="pcard r-${r} ${p.key === S.key && S.view === "paper" ? "on" : ""} ${p.missing ? "missing" : ""}" data-key="${esc(p.key)}">
      ${comparing ? `<input type="checkbox" class="cmp" ${S.compareSel.has(p.key) ? "checked" : ""}>` : ""}
      <div class="row">${p.venue ? `<span class="pill venue">${esc(p.venue)} ${esc(p.year)}</span>` : ""}${p.short ? `<b style="color:var(--ink)">${esc(p.short)}</b>` : ""}${status}</div>
      <div class="t">${esc(p.title)}</div>
      <div class="row"><span class="pill r-${r}">${READING[r]}</span>${p.total ? `번역 ${p.done}/${p.total}` : "분석 전"} · Q&A ${p.qa}</div>
      ${p.total ? `<div class="bar ${pct === 100 ? "done" : ""}"><i style="width:${pct}%"></i></div>` : ""}
    </div>`;
  }).join("");
}

$("#paperList").addEventListener("click", (e) => {
  const card = e.target.closest(".pcard");
  if (!card) return;
  const key = card.dataset.key;
  if (S.view === "compare") {
    S.compareSel.has(key) ? S.compareSel.delete(key) : S.compareSel.add(key);
    renderLibrary();
    renderCompare();
    return;
  }
  openPaper(key);
});

$("#readingFilter").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  S.filter = b.dataset.f;
  $$("#readingFilter button").forEach((x) => x.classList.toggle("on", x === b));
  renderLibrary();
});

$("#arxivBtn").addEventListener("click", addArxiv);
$("#arxivInput").addEventListener("keydown", (e) => { if (e.key === "Enter") addArxiv(); });
async function addArxiv() {
  const text = $("#arxivInput").value.trim();
  if (!text) return;
  await run(async () => {
    await api.post("/api/arxiv", { text });
    $("#arxivInput").value = "";
    toast("arXiv 에서 내려받는 중입니다. 받으면 바로 분석을 시작합니다.");
    pollJobs();
  });
}

// ---------- 논문 열기 ----------
async function openPaper(key, opts = {}) {
  S.view = "paper";
  if (S.key !== key) { S.anchor = null; S.units = null; renderAnchor(); }
  S.key = key;
  pref.set("key", key);
  renderLibrary();
  await reloadPaper();
  if (opts.tab) setTab(opts.tab);
  if (opts.block) jumpTo(opts.block, opts.instant);
  if (S.panel === "pdf") showPdf();
}

async function reloadPaper(keepScroll = true) {
  if (!S.key) return renderMain();
  const main = $("#main");
  const top = main.scrollTop;
  try {
    S.paper = await api.get("/api/paper/" + encodeURIComponent(S.key));
  } catch (e) {
    S.key = null; S.paper = null;
    return renderMain();
  }
  if (needBlocks()) S.units = (await api.get(`/api/paper/${encodeURIComponent(S.key)}/blocks`)).units;
  renderMain();
  renderRecent();
  if (keepScroll) main.scrollTop = top;
}

function needBlocks() {
  return ["reader", "memos"].includes(S.tab) || S.anchor;
}

function setTab(tab) {
  S.tab = tab;
  pref.set("tab", tab);
  if (needBlocks() && !S.units) return reloadPaper(false);
  renderMain();
  $("#main").scrollTop = 0;
}

// ---------- 본문 그리기 ----------
function renderMain() {
  const main = $("#main");
  if (S.view === "compare") return renderCompare();
  if (S.view === "lineage") return renderLineage();
  if (S.view === "global") return renderGlobal();
  if (S.view === "search") return renderSearch();
  if (!S.paper) {
    main.innerHTML = `<div class="empty"><h2>논문을 골라 주세요</h2><div>왼쪽 목록에서 논문을 고르면 해설 · 원문/번역 · Q&A 를 볼 수 있습니다.</div></div>`;
    return;
  }
  const p = S.paper, m = p.meta;
  const tabs = [["overview", "해설"], ["reader", "원문 · 번역"], ["qa", "Q&A", p.qa.length], ["glossary", "용어집", p.glossary.length],
    ["memos", "메모", Object.keys(p.notes).length], ["code", "코드"], ["slides", "발표 요약"]];
  main.innerHTML = `<div class="wrap">
    ${headerHtml(p)}
    <div class="tabs">${tabs.map(([k, n, c]) => `<button data-tab="${k}" class="${S.tab === k ? "on" : ""}">${n}${c ? `<span class="count">${c}</span>` : ""}</button>`).join("")}
      <span class="spacer"></span>
      ${S.tab === "reader" ? `<div class="seg" id="modeSeg"><button data-m="both" class="${S.mode === "both" ? "on" : ""}">원문 + 번역</button><button data-m="ko" class="${S.mode === "ko" ? "on" : ""}">번역만</button><button data-m="en" class="${S.mode === "en" ? "on" : ""}">원문만</button></div>` : ""}
    </div>
    <div id="tabBody"></div>
  </div>`;
  const body = $("#tabBody");
  ({ overview: renderOverview, reader: renderReader, qa: renderQaTab, glossary: renderGlossary, memos: renderMemos, code: renderCode, slides: renderSlides }[S.tab] || renderOverview)(body);
}

function headerHtml(p) {
  const m = p.meta;
  const [done, total] = p.progress;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const busy = p.busy || [];
  const busyKinds = new Set(busy.map((j) => j.kind));
  const authors = (m.authors || []).slice(0, 8).join(", ") + ((m.authors || []).length > 8 ? " 외" : "");
  const actions = [];
  if (m.status === "new") {
    actions.push(`<button class="btn primary" data-act="start" ${busyKinds.has("analyze") ? "disabled" : ""} title="분석 1~3분 → 해설(2~4분)과 번역이 동시에 진행됩니다">읽기 시작 — 분석 · 해설 · 번역</button>`);
  } else {
    const est = p.estimate || {};
    if (done < total && !busyKinds.has("translate")) actions.push(`<button class="btn primary" data-act="translate" title="예상 토큰: 입력 ${fmtTok(est.tin)} · 출력 ${fmtTok(est.tout)}">번역 ${done ? "계속" : "시작"} · ${est.left}단위 약 ${est.minutes}분</button>`);
    if (!p.overview && !busyKinds.has("overview")) actions.push(`<button class="btn primary" data-act="overview">해설 만들기</button>`);
  }
  actions.push(`<button class="btn pearl" data-act="pdf">PDF 열기</button>`);
  actions.push(`<button class="btn pearl" data-act="folder">문서 폴더</button>`);
  actions.push(`<button class="btn pearl" data-act="notion" ${busyKinds.has("notion") ? "disabled" : ""}>${p.notion ? "Notion 다시 동기화" : "Notion 에 올리기"}</button>`);
  if (p.notion && p.notion.url) actions.push(`<a class="btn ghost" href="${esc(p.notion.url)}" target="_blank">Notion 열기 ↗</a>`);
  return `<div class="phead">
    <div class="badges">
      ${m.venue ? `<span class="pill venue">${esc(m.venue)} ${esc(m.year || "")}</span>` : ""}
      ${m.task ? `<span class="pill">${esc(m.task)}</span>` : ""}
      ${(m.keywords || []).slice(0, 5).map((k) => `<span class="pill gray">${esc(k)}</span>`).join("")}
      <span style="flex:1"></span>
      <div class="seg" id="readingSeg">${Object.entries(READING).map(([k, v]) => `<button data-r="${k}" class="${(m.reading || "todo") === k ? "on" : ""}">${v}</button>`).join("")}</div>
    </div>
    <h1>${esc(m.title)}</h1>
    ${authors ? `<div class="authors">${esc(authors)}</div>` : ""}
    ${p.overview ? `<div class="one">${inl(p.overview.one_liner)}</div>` : m.abstract_ko ? `<div class="one">${inl(m.abstract_ko)}</div>` : ""}
    <div class="actions">${actions.join("")}</div>
    ${total ? `<div class="prog"><span>번역 ${done}/${total}</span><div class="bar ${pct === 100 ? "done" : ""}"><i style="width:${pct}%"></i></div><span>${usageText(p.usage)}</span></div>` : ""}
    ${busy.map((j) => `<div class="pending" style="margin-top:10px"><span class="spinner"></span><b>${esc(j.label)}</b><span>${esc(j.message || "")}${j.n ? ` (${j.i}/${j.n})` : ""}</span>${j.kind === "translate" ? `<button class="btn ghost sm" data-stop="${j.id}" style="margin-left:auto">멈추기</button>` : ""}</div>`).join("")}
  </div>`;
}

$("#main").addEventListener("click", async (e) => {
  const t = e.target;
  const tabBtn = t.closest(".tabs [data-tab]");
  if (tabBtn) return setTab(tabBtn.dataset.tab);
  const mode = t.closest("#modeSeg button");
  if (mode) {
    S.mode = mode.dataset.m; pref.set("mode", S.mode);
    $$("#modeSeg button").forEach((b) => b.classList.toggle("on", b === mode));
    const r = $(".reader-body"); if (r) r.className = "reader-body mode-" + S.mode;
    return;
  }
  const rd = t.closest("#readingSeg button");
  if (rd) {
    await run(() => api.post(`/api/paper/${S.key}/reading`, { state: rd.dataset.r }));
    S.paper.meta.reading = rd.dataset.r;
    $$("#readingSeg button").forEach((b) => b.classList.toggle("on", b === rd));
    loadLibrary();
    return;
  }
  const stop = t.closest("[data-stop]");
  if (stop) { await run(() => api.post(`/api/jobs/${stop.dataset.stop}/stop`)); toast("지금 번역 중인 단위까지 마치고 멈춥니다."); return; }
  const act = t.closest("[data-act]");
  if (act) return doAction(act.dataset.act, act);
});

async function doAction(act, el) {
  const k = encodeURIComponent(S.key);
  const jobActs = { start: "start", translate: "translate", overview: "overview", slides: "slides", code: "code", notion: "notion", reanalyze: "analyze" };
  if (act in jobActs) {
    if (act === "notion" && !S.notionReady) return showNotionHelp();
    if (act === "reanalyze" && !confirm("논문 구조를 다시 분석합니다. 이미 번역한 단위는 id 가 같으면 그대로 둡니다. 계속할까요?")) return;
    const body = act === "translate" && el.dataset.unit !== undefined ? { units: [+el.dataset.unit] } : {};
    await run(async () => {
      await api.post(`/api/paper/${k}/${jobActs[act]}`, body);
      toast({ start: "분석을 시작합니다. 끝나면 해설과 번역이 이어서 진행됩니다.", translate: "번역을 시작합니다. 단위마다 1분 안팎 걸립니다.",
        overview: "해설을 만드는 중입니다 (관련 논문 확인 포함, 몇 분).", slides: "발표 요약을 만드는 중입니다.", code: "코드 저장소를 찾는 중입니다.",
        notion: "Notion 에 올리는 중입니다.", reanalyze: "다시 분석합니다." }[act]);
      await pollJobs();
      reloadPaper();
    });
    return;
  }
  if (act === "pdf") return run(() => api.post(`/api/paper/${k}/open`, { what: "pdf" }));
  if (act === "folder") return run(() => api.post(`/api/paper/${k}/open`, { what: "export" }));
}

function showNotionHelp() {
  modal(`<h3>Notion 연결이 필요합니다</h3>
  <div class="md small" style="color:var(--ink2)">
  <ol>
  <li><a href="https://www.notion.so/profile/integrations" target="_blank">notion.so/profile/integrations</a> 에서 <b>새 통합(Internal)</b> 을 만들고 토큰(<code>ntn_…</code>)을 복사합니다.</li>
  <li>Notion 의 <b>Research Paper</b> 페이지 → 오른쪽 위 <b>⋯ → 연결(Connections)</b> → 방금 만든 통합을 추가합니다.</li>
  <li>프로젝트 폴더의 <code>config.local.json</code> 에 <code>{"notion_token": "ntn_…"}</code> 을 저장하고 프로그램을 다시 엽니다.</li>
  </ol>
  Notion API 는 무료이고, Claude 사용량도 들지 않습니다.</div>
  <div class="row"><button class="btn primary" data-close>확인</button></div>`);
}

// ---------- 해설 탭 ----------
function renderOverview(el) {
  const ov = S.paper.overview;
  const busy = (S.paper.busy || []).some((j) => j.kind === "overview" || j.kind === "analyze");
  if (!ov) {
    el.innerHTML = `<div class="callout"><div><b>${busy ? "해설을 만드는 중입니다" : "아직 해설이 없습니다"}</b><p>한 줄 요약 · 풀려는 문제 · 핵심 아이디어 · 방법 흐름 · 읽기 가이드 · Task 내 위치 · 관련 논문 · 결과 · 한계 · 사전 지식을 한 페이지로 정리합니다.</p></div>
      ${busy ? '<span class="spinner"></span>' : S.paper.meta.status === "new" ? "" : '<button class="btn primary" data-act="overview">해설 만들기</button>'}</div>`;
    return;
  }
  const G = { foundation: "기반 기법", predecessor: "직접 선행 연구", competitor: "경쟁 방법", successor: "후속 연구", dataset: "벤치마크 · 데이터셋" };
  const P = { must: ["꼭 읽기", ""], later: ["나중에", "gray"], skip: ["건너뛰어도 됨", "gray"] };
  const link = (t, u) => u ? `<a href="${esc(u)}" target="_blank">${esc(t)}</a>` : esc(t);
  const rel = ov.related || [];
  const regenBusy = (S.paper.busy || []).some((j) => j.kind === "overview");
  el.innerHTML = `
    <div class="ov-bar"><span class="muted small">${esc(ov.created || "")} · ${esc(ov.model || "")} 이 만든 해설</span>
      ${regenBusy ? '<span class="pending" style="padding:6px 12px"><span class="spinner"></span>다시 만드는 중…</span>' : '<button class="btn pearl sm" id="ovRegen">다시 만들기</button>'}</div>
    <div class="card hero"><h2>한 줄 요약</h2><div class="big">${inl(ov.one_liner)}</div></div>
    ${(ov.prerequisites || []).length ? `<div class="card"><h2>읽기 전에 알아야 할 배경</h2><div class="prereq">${ov.prerequisites.map((x) => `<div><b>${esc(x.concept)}</b>${md(x.explain)}</div>`).join("")}</div></div>` : ""}
    <div class="grid2">
      <div class="card"><h2>풀려는 문제</h2><div class="md">${md(ov.problem)}</div></div>
      <div class="card"><h2>핵심 아이디어</h2><div class="md">${md(ov.key_idea)}</div></div>
    </div>
    <div class="card"><h2>방법 흐름</h2><div class="flow">${(ov.method_flow || []).map((s) => `<div><b>${esc(s.step)}</b><div class="md">${md(s.detail)}</div>${s.shape ? `<span class="shape">${esc(s.shape)}</span>` : ""}</div>`).join("")}</div></div>
    <div class="card"><h2>읽기 가이드</h2><div class="guide">${(ov.reading_guide || []).map((g) => `<div><span class="pill ${P[g.priority]?.[1] || ""}">${P[g.priority]?.[0] || g.priority}</span><div><b>${esc(g.target)}</b> — <span class="muted">${inl(g.why)}</span></div></div>`).join("")}</div></div>
    <div class="card"><h2>Task 내 위치</h2><div class="md">${md(ov.task_position)}</div>
      ${(ov.timeline || []).length ? `<h3>연구 흐름</h3><div class="timeline">${ov.timeline.map((t) => `<div class="${t.role === "this" ? "this" : ""}"><span class="yr">${esc(t.year)}</span><span class="nm">${link(t.title, t.url)}</span> <span class="muted small">${esc(t.venue || "")}</span>${t.verified === false ? ' <span class="pill amber">미확인</span>' : ""}<div class="nt">${inl(t.note)}</div></div>`).join("")}</div>` : ""}
    </div>
    ${rel.length ? `<div class="card related"><h2>관련 중요 논문</h2>${Object.keys(G).map((g) => {
      const items = rel.filter((r) => r.group === g);
      if (!items.length) return "";
      return `<h3>${G[g]}</h3>` + items.map((r) => `<div class="r"><span class="nm">${link(r.title, r.url)}</span> <span class="muted small">${esc(r.venue || "")} ${esc(r.year || "")}</span>
        ${r.source === "references" && r.ref_no ? `<span class="pill gray">참고문헌 [${esc(r.ref_no)}]</span>` : r.verified ? '<span class="pill green">웹 확인</span>' : '<span class="pill amber">미확인</span>'}
        <div class="df">${inl(r.diff)}</div></div>`).join("");
    }).join("")}</div>` : ""}
    ${(ov.results || []).length ? `<div class="card"><h2>결과 요약</h2><div class="tbl-wrap"><table class="tbl"><tr><th>벤치마크</th><th>지표</th><th>이 논문</th><th>이전 최고</th><th>비고</th></tr>
      ${ov.results.map((r) => `<tr><td>${esc(r.benchmark)}</td><td>${esc(r.metric)}</td><td><b>${esc(r.value)}</b></td><td>${esc(r.prev_best)}</td><td class="muted">${inl(r.note)}</td></tr>`).join("")}</table></div>
      ${ov.results_note ? `<div class="md" style="margin-top:14px">${md(ov.results_note)}</div>` : ""}</div>` : ""}
    <div class="card"><h2>한계 & 열린 질문</h2><div class="md">${md(ov.limitations)}</div></div>`;
  const rg = $("#ovRegen");
  if (rg) rg.onclick = () => {
    if (!confirm("해설을 처음부터 새로 만들어 지금 해설을 바꿉니다.\n(Opus · 관련 논문 확인 포함 2~4분, 토큰을 씁니다. 다 만들어질 때까지 지금 해설은 그대로 보입니다.)")) return;
    doAction("overview", rg);
  };
}

// ---------- 원문 · 번역 탭 ----------
function qaByAnchor() {
  const map = {};
  for (const q of S.paper.qa) (map[q.anchor] = map[q.anchor] || []).push(q);
  return map;
}

function renderReader(el) {
  const units = S.units || [];
  if (!units.length) {
    el.innerHTML = `<div class="callout"><div><b>아직 분석 전입니다</b><p>위의 <b>읽기 시작</b> 을 누르면 논문 구조를 나누고 해설과 번역을 시작합니다.</p></div></div>`;
    return;
  }
  const notes = S.paper.notes, qmap = qaByAnchor();
  let appx = false;
  const toc = units.filter((u) => u.kind !== "references").map((u) => {
    let h = "";
    if (u.kind === "appendix" && !appx) { appx = true; h = '<div class="appx">부록</div>'; }
    return h + `<a href="#" data-unit="${u.i}"><span class="dot ${u.status || ""}"></span>${esc(u.id === "abs" ? "" : u.id)} ${esc(u.title.replace(/^[A-Z0-9.]+\s+/, ""))}</a>`;
  }).join("");
  const body = units.filter((u) => u.kind !== "references").map((u) => {
    const title = u.title.replace(/^[A-Z0-9.]+\s+/, "");
    let inner;
    if (u.blocks.length) inner = u.blocks.map((b) => blockHtml(b, notes[b.id], qmap[b.id])).join("");
    else if (u.status === "running") inner = `<div class="unit-empty"><span><span class="spinner"></span>&nbsp; 번역 중입니다…</span></div>`;
    else if (u.status === "error") inner = `<div class="unit-empty"><span style="color:var(--red)">번역 실패: ${esc(u.error || "")}</span><button class="btn pearl sm" data-act="translate" data-unit="${u.i}">다시 번역</button></div>`;
    else inner = `<div class="unit-empty"><span>아직 번역되지 않았습니다 (PDF ${u.page_start}~${u.page_end}쪽)</span><button class="btn pearl sm" data-act="translate" data-unit="${u.i}">이 단위만 번역</button></div>`;
    return `<section class="unit" id="u-${u.i}" data-i="${u.i}">
      <div class="unit-h"><span class="no">${esc(u.id === "abs" ? "" : u.id)}</span><h2>${esc(title)}</h2><span style="flex:1"></span>
      ${u.blocks.length ? `<button class="btn ghost sm" data-act="translate" data-unit="${u.i}" title="이 단위를 다시 번역합니다">다시 번역</button>` : ""}</div>
      ${inner}</section>`;
  }).join("");
  el.innerHTML = `<div class="reader"><nav class="toc">${toc}</nav><div class="reader-body mode-${S.mode}">${body}</div></div>`;
  setupTocSpy();
  setupFigures();
}

// ---------- 그림 · 표 이미지 (PDF.js 로 쪽을 그리고 캡션 위치로 그림 영역만 잘라 냄) ----------
const RECROP_BTN = '<button class="btn pearl sm recrop" data-recrop title="그림 영역을 직접 끌어서 다시 고릅니다">다시 자르기</button>';
let pdfLibReady = null;
const pdfDocs = {};
let figQueue = Promise.resolve();
let figObserver = null;

function loadPdfJs() {
  if (!pdfLibReady) pdfLibReady = new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "/vendor/pdf.min.js";
    s.onload = () => { pdfjsLib.GlobalWorkerOptions.workerSrc = "/vendor/pdf.worker.min.js"; res(); };
    s.onerror = rej;
    document.head.appendChild(s);
  });
  return pdfLibReady;
}
async function pdfDoc(key) {
  await loadPdfJs();
  if (!pdfDocs[key]) pdfDocs[key] = pdfjsLib.getDocument({ url: authUrl(`/pdf/${encodeURIComponent(key)}`) }).promise;
  return pdfDocs[key];
}

function setupFigures() {
  if (figObserver) figObserver.disconnect();
  figObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      figObserver.unobserve(e.target);
      const box = e.target, key = S.key;
      figQueue = figQueue.then(() => showFigure(box, key)).catch(() => {});
    }
  }, { root: $("#main"), rootMargin: "400px 0px" });
  $$(".figbox").forEach((b) => figObserver.observe(b));
}

async function showFigure(box, key) {
  if (!box.isConnected || key !== S.key) return;
  const id = box.dataset.fig;
  const mem = S.figCache[key + "/" + id];
  if (mem || box.dataset.cached === "1") {
    box.innerHTML = `<img src="${mem || authUrl(`/fig/${encodeURIComponent(key)}/${encodeURIComponent(id)}.png`)}" alt="${esc(box.dataset.label)}">${RECROP_BTN}`;
    return;
  }
  try {
    const doc = await pdfDoc(key);
    const page = await doc.getPage(+box.dataset.page);
    const vp = page.getViewport({ scale: 2 });
    const rect = await figureRect(page, vp, box.dataset.label, box.dataset.type);
    const canvas = document.createElement("canvas");
    canvas.width = vp.width; canvas.height = vp.height;
    await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
    if (!rect) {
      // 캡션을 못 찾으면 쪽 전체를 작게 (저장하지 않음)
      box.innerHTML = `<img src="${canvas.toDataURL("image/jpeg", 0.85)}" class="whole"><div class="fig-note">그림 위치를 찾지 못해 ${box.dataset.page}쪽 전체를 보여 줍니다 — '다시 자르기' 로 직접 고를 수 있습니다</div>${RECROP_BTN}`;
      return;
    }
    const t = trimWhite(canvas, rect);
    const out = document.createElement("canvas");
    out.width = Math.round(t.w); out.height = Math.round(t.h);
    out.getContext("2d").drawImage(canvas, t.x, t.y, t.w, t.h, 0, 0, t.w, t.h);
    const png = out.toDataURL("image/png");
    if (!box.isConnected) return;
    box.innerHTML = `<img src="${png}" alt="${esc(box.dataset.label)}">${RECROP_BTN}`;
    box.dataset.cached = "1";
    S.figCache[key + "/" + id] = png;
    api.post(`/api/paper/${encodeURIComponent(key)}/figure`, { block: id, png }).then(() => {
      const b = (S.units || []).flatMap((u) => u.blocks).find((x) => x.id === id);
      if (b) b.fig = true;
    }).catch(() => {});
  } catch (e) {
    box.innerHTML = `<div class="fig-note">그림을 그리지 못했습니다: ${esc(e.message || e)}</div>`;
  }
}

function trimWhite(canvas, r) {
  // 잘라 낸 영역 둘레의 흰 여백을 걷어 냄 (여유 14px)
  const x0 = Math.max(0, Math.floor(r.x)), y0 = Math.max(0, Math.floor(r.y));
  const w = Math.min(canvas.width - x0, Math.ceil(r.w)), h = Math.min(canvas.height - y0, Math.ceil(r.h));
  if (w < 4 || h < 4) return r;
  const d = canvas.getContext("2d").getImageData(x0, y0, w, h).data;
  let minX = w, minY = h, maxX = -1, maxY = -1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      if (d[i] < 240 || d[i + 1] < 240 || d[i + 2] < 240) {
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < 0) return r;
  const pad = 14;
  const nx = Math.max(0, x0 + minX - pad), ny = Math.max(0, y0 + minY - pad);
  return { x: nx, y: ny, w: Math.min(canvas.width, x0 + maxX + pad) - nx, h: Math.min(canvas.height, y0 + maxY + pad) - ny };
}

async function figureRect(page, vp, label, type) {
  const W = vp.width, H = vp.height;
  const num = (String(label).match(/\d+/) || [])[0];
  if (!num) return null;
  // 1) 글자 조각 → 줄 (2단 편집이면 같은 높이라도 왼쪽·오른쪽 단은 다른 줄)
  const tc = await page.getTextContent();
  const items = [];
  for (const it of tc.items) {
    if (!it.str || !it.str.trim()) continue;
    const [x, y] = vp.convertToViewportPoint(it.transform[4], it.transform[5]);
    const h = Math.hypot(it.transform[2], it.transform[3]) * vp.scale || 16;
    items.push({ x, y, w: it.width * vp.scale, h, s: it.str });
  }
  items.sort((a, b) => a.y - b.y || a.x - b.x);
  const lines = [];
  for (const it of items) {
    const l = lines.find((L) => Math.abs(L.y - it.y) < Math.max(3, it.h * 0.45) && it.x >= L.x0 - 4 && it.x - L.x1 < W * 0.03);
    if (l) { l.text += (it.x - l.x1 > it.h * 0.15 ? " " : "") + it.s; l.x1 = Math.max(l.x1, it.x + it.w); l.h = Math.max(l.h, it.h); }
    else lines.push({ x0: it.x, x1: it.x + it.w, y: it.y, h: it.h, text: it.s });
  }
  lines.sort((a, b) => a.y - b.y);
  const isBody = (l) => {
    const t = l.text;
    return t.length > 60 && (t.match(/[A-Za-z]{3,}/g) || []).length >= 8 && (t.match(/\d/g) || []).length / t.length < 0.12;
  };
  // 2) 캡션 줄 찾기
  const head = type === "table" ? "Table" : "Fig(?:ure)?\\.?";
  const strict = new RegExp(`^${head}\\s*${num}\\s*[.:|]`, "i"), loose = new RegExp(`^${head}\\s*${num}\\b`, "i");
  const cap = lines.find((l) => strict.test(l.text.trim())) || lines.find((l) => loose.test(l.text.trim()));
  if (!cap) return null;
  const capLines = [cap];
  for (const l of lines) {
    const last = capLines[capLines.length - 1];
    if (l.y <= last.y || !(l.x0 < cap.x1 + W * 0.3 && l.x1 > cap.x0)) continue;
    if (l.y - last.y < last.h * 1.9) capLines.push(l); else break;
  }
  const capTop = cap.y - cap.h, capBot = capLines[capLines.length - 1].y + cap.h * 0.35;
  const capX0 = Math.min(...capLines.map((l) => l.x0)), capX1 = Math.max(...capLines.map((l) => l.x1));
  // 3) 단 정하기: 본문 줄이 대부분 반쪽 폭이면 2단 편집
  const bodies = lines.filter(isBody);
  const twoCol = bodies.length > 4 && bodies.filter((l) => l.x1 - l.x0 > W * 0.6).length < bodies.length * 0.3;
  let cx0 = W * 0.03, cx1 = W * 0.97;
  if (twoCol && capX1 - capX0 < W * 0.55) {
    if ((capX0 + capX1) / 2 < W / 2) cx1 = W * 0.505; else cx0 = W * 0.495;
  }
  const inCol = (l) => l.x0 < cx1 && l.x1 > cx0;
  const colBodies = bodies.filter(inCol);
  // 쪽 머리말은 그림에 넣지 않음: 쪽 맨 위 글줄이 위쪽 15% 안에 있고 15자 넘으면 머리말 (그림 축 숫자 같은 짧은 글자는 제외)
  const first = lines[0];
  const isHeader = first && first.y < H * 0.15 && first.text.trim().length > 15;
  const headerBottom = isHeader ? Math.max(...lines.filter((l) => Math.abs(l.y - first.y) < 4).map((l) => l.y)) + 10 : H * 0.03;
  const above = () => {
    const prev = colBodies.filter((l) => l.y < capTop - 2);
    const top = prev.length ? Math.max(...prev.map((l) => l.y)) + prev[0].h * 0.45 : headerBottom;
    return [Math.max(top, headerBottom), capTop - 4];
  };
  const below = () => {
    const next = colBodies.filter((l) => l.y - l.h > capBot + 2);
    const bot = next.length ? Math.min(...next.map((l) => l.y - l.h)) - 4 : H * 0.95;
    return [capBot + 4, bot];
  };
  // 그림은 보통 캡션 위, 표는 캡션 아래. 영역이 너무 작으면 반대쪽을 봄
  let [y0, y1] = type === "table" ? below() : above();
  if (y1 - y0 < H * 0.06) [y0, y1] = type === "table" ? above() : below();
  if (y1 - y0 < H * 0.04) return null;
  return { x: cx0, y: Math.max(0, y0), w: cx1 - cx0, h: Math.min(H, y1) - Math.max(0, y0) };
}

// 드래그로 고른 하이라이트를 그린 글에 <mark> 로 표시 (태그 밖 글자에서만 찾음, 처음 나오는 곳 한 번)
function withMarks(html, note, field) {
  const hls = ((note || {}).hls || []).filter((h) => h.f === field && h.s);
  for (const h of hls) {
    const needle = esc(h.s);
    let done = false;
    html = html.split(/(<[^>]+>)/).map((part) => {
      if (done || part.startsWith("<")) return part;
      const i = part.indexOf(needle);
      if (i < 0) return part;
      done = true;
      return part.slice(0, i) + `<mark class="hlm" data-f="${field}" data-s="${esc(h.s)}" title="누르면 하이라이트 지우기">${needle}</mark>` + part.slice(i + needle.length);
    }).join("");
  }
  return html;
}

function blockHtml(b, note, qas) {
  const t = b.type;
  const hl = "";
  const sel = S.anchor === b.id ? " sel" : "";
  const tools = [`<button data-b="ask" title="이 블록에 대해 질문">질문</button>`];
  if (t === "equation") tools.push(`<button data-b="equation">∑ 수식 풀이</button>`);
  else if (t === "figure" || t === "table") tools.push(`<button data-b="figure">${t === "table" ? "표" : "그림"} 해설</button>`);
  else tools.push(`<button data-b="explain">깊게 해설</button>`);
  tools.push(`<button data-b="memo" title="메모">메모</button>`);
  if (b.page) tools.push(`<button data-b="page" title="PDF 에서 보기">p.${b.page}</button>`);
  let inner;
  if (t === "equation") {
    inner = `<div class="eq">${tex(b.latex || "", true)}<span class="lbl">${esc(b.label || "")}</span></div>${b.ko ? `<div class="ko">→ ${inl(b.ko)}</div>` : ""}`;
  } else if (t === "figure" || t === "table") {
    const fig = b.page ? `<div class="figbox" data-fig="${esc(b.id)}" data-page="${b.page}" data-label="${esc(b.label || "")}" data-type="${t}" data-cached="${b.fig ? 1 : 0}" title="누르면 오른쪽에 PDF ${b.page}쪽이 열립니다"><div class="fig-ph">그림 불러오는 중…</div></div>` : "";
    inner = `<div class="cap-h"><span class="pill">${esc(b.label || (t === "table" ? "Table" : "Figure"))}</span><button class="btn ghost sm" data-b="page">PDF ${b.page}쪽에서 보기</button></div>
      ${fig}<div class="orig">${withMarks(inl(b.orig), note, "orig")}</div><div class="ko">${withMarks(inl(b.ko), note, "ko")}</div>`;
  } else if (t === "heading" && (b.ko || "").trim() === (b.orig || "").trim()) {
    inner = `<div class="orig">${inl(b.orig)}</div>`;   // 제목이 번역해도 같으면 한 번만
  } else {
    inner = `<div class="orig">${withMarks(inl(b.orig), note, "orig")}</div><div class="ko">${withMarks(inl(b.ko), note, "ko")}</div>`;
  }
  const noteHtml = b.note ? `<div class="note"><b>해설</b> ${inl(b.note)}</div>` : "";
  const memo = note && note.memo ? `<div class="memo">${esc(note.memo)}</div>` : "";
  // 깊게 해설 · 그림 해설 · 수식 풀이는 문단 바로 아래 카드로, 일반 Q&A 는 💬 안에
  const exps = (qas || []).filter((q) => q.kind !== "qa");
  const qs = (qas || []).filter((q) => q.kind === "qa");
  const expHtml = exps.length ? `<div class="explains">${exps.map((q) => qaHtml(q, S.justAnswered === q.id, "explain")).join("")}</div>` : "";
  const qa = qs.length ? `<details class="qa-inline" ${qs.some((q) => q.id === S.justAnswered) ? "open" : ""}><summary>Q&A ${qs.length}</summary>${qs.map((q) => qaHtml(q, S.justAnswered === q.id)).join("")}</details>` : "";
  return `<div class="blk ${t}${hl}${sel}" id="b-${esc(b.id)}" data-id="${esc(b.id)}" data-type="${t}" data-page="${b.page || ""}">
    <span class="bid">${esc(b.id)}</span><div class="tools">${tools.join("")}</div>${inner}${noteHtml}${memo}${expHtml}${qa}</div>`;
}

$("#main").addEventListener("click", async (e) => {
  const tocA = e.target.closest(".toc a");
  if (tocA) { e.preventDefault(); $("#u-" + tocA.dataset.unit)?.scrollIntoView({ behavior: "smooth" }); return; }
  const mk = e.target.closest("mark.hlm");
  if (mk) {
    e.stopPropagation();
    const id = mk.closest(".blk").dataset.id;
    if (!confirm("이 하이라이트를 지울까요?")) return;
    await run(async () => {
      S.paper.notes = (await api.post(`/api/paper/${S.key}/note`, { block: id, remove_hl: { f: mk.dataset.f, s: mk.dataset.s } })).notes;
      rerenderKeepScroll();
    });
    return;
  }
  const rc = e.target.closest("[data-recrop]");
  if (rc) { e.stopPropagation(); openRecrop(rc.closest(".figbox")); return; }
  const fb = e.target.closest(".figbox");
  if (fb) { e.stopPropagation(); showPdf(+fb.dataset.page); return; }
  const tb = e.target.closest("[data-b]");
  const blk = e.target.closest(".blk");
  if (tb && blk) {
    e.stopPropagation();
    const id = blk.dataset.id, kind = tb.dataset.b;
    if (kind === "ask") { setAnchor(id); $("#askInput").focus(); }
    else if (kind === "memo") editMemo(id);
    else if (kind === "page") showPdf(+blk.dataset.page);
    else ask(kind, "", id);
    return;
  }
  if (blk && !e.target.closest("details, a, button, mark") && !String(window.getSelection())) setAnchor(S.anchor === blk.dataset.id ? null : blk.dataset.id);
});

function setupTocSpy() {
  const main = $("#main");
  const units = $$(".unit");
  if (!units.length) return;
  const spy = () => {
    let cur = units[0];
    for (const u of units) if (u.getBoundingClientRect().top < 140) cur = u;
    $$(".toc a").forEach((a) => a.classList.toggle("on", a.dataset.unit === cur.dataset.i));
  };
  main.onscroll = spy;
  spy();
}

function jumpTo(blockId, instant) {
  const go = () => {
    const el = document.getElementById("b-" + blockId);
    if (!el) return;
    el.scrollIntoView({ behavior: instant ? "instant" : "smooth", block: "center" });
    el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
  };
  if (S.tab !== "reader") { setTab("reader"); setTimeout(go, 250); } else go();
}

function editMemo(id) {
  const b = (S.units || []).flatMap((u) => u.blocks).find((x) => x.id === id);
  const cur = (S.paper.notes[id] || {}).memo || "";
  modal(`<h3>메모 · §${esc(id)}</h3><div class="src">${inl((b && (b.ko || b.orig || b.latex)) || "").slice(0, 400)}</div>
    <textarea id="memoText" rows="6" placeholder="이 문단에 대한 내 생각, 연구 아이디어, 확인할 것…">${esc(cur)}</textarea>
    <div class="row">${cur ? '<button class="btn danger" data-memo="del">지우기</button>' : ""}<span style="flex:1"></span><button class="btn pearl" data-close>취소</button><button class="btn primary" data-memo="save">저장</button></div>`);
  $("#memoText").focus();
  $("#modalBody").onclick = async (e) => {
    const a = e.target.closest("[data-memo]");
    if (!a) return;
    const memo = a.dataset.memo === "del" ? "" : $("#memoText").value;
    await run(async () => {
      S.paper.notes = (await api.post(`/api/paper/${S.key}/note`, { block: id, memo })).notes;
      closeModal();
      renderMain();
    });
  };
}

// ---------- Q&A ----------
// "2026-10-01 16:33:04" → "10-01 오후 4:33"
function fmtTs(ts) {
  const m = String(ts || "").match(/^\d{4}-(\d{2})-(\d{2}) (\d{2}):(\d{2})/);
  if (!m) return esc(ts || "");
  const h = +m[3];
  return `${m[1]}-${m[2]} ${h < 12 ? "오전" : "오후"} ${h % 12 || 12}:${m[4]}`;
}

function qaHtml(q, open, cls) {
  const anchorBtn = q.anchor && q.anchor !== "all" ? `<button class="btn ghost sm" data-jump="${esc(q.anchor)}">본문에서 보기</button>` : "";
  return `<details class="qa ${cls || ""}" ${open ? "open" : ""} data-qid="${esc(q.id)}">
    <summary>${CHEV}<span class="pill ${q.anchor === "all" ? "gray" : ""}">${esc(q.tag || "전체")}</span><span class="ttl">${esc(q.title || q.question)}</span>
      <span class="meta">${q.kind !== "qa" ? `<span class="pill amber">${KIND[q.kind]}</span>` : ""}${(q.tags || []).map((t) => `<span>#${esc(t)}</span>`).join("")}<span>${fmtTs(q.ts)}</span><button class="sum-del" data-delqa="${esc(q.id)}" title="삭제">삭제</button></span></summary>
    <div class="body">
      ${q.question ? `<div class="q"><b>질문</b> ${esc(q.question)}</div>` : ""}
      <div class="md">${md(q.answer)}</div>
      ${q.key_point ? `<div class="kp"><b>핵심</b> ${inl(q.key_point)}</div>` : ""}
      <div class="foot"><span class="muted small" style="margin-right:auto">${q.model ? esc(q.model) : ""}${q.length && q.length !== "normal" ? " · " + ({ short: "짧게", long: "자세히" }[q.length] || "") : ""}</span>${cls === "explain" ? "" : anchorBtn}<button class="btn danger" data-delqa="${esc(q.id)}">삭제</button></div>
    </div></details>`;
}

function renderQaTab(el) {
  const items = S.paper.qa;
  const tags = [...new Set(items.flatMap((q) => q.tags || []))];
  const shown = items.filter((q) => !S.qaTag || (S.qaTag.startsWith("k:") ? q.kind === S.qaTag.slice(2) : (q.tags || []).includes(S.qaTag)));
  el.innerHTML = `<div class="qa-tools">
      <div class="seg" id="qaOrder"><button data-o="section" class="${S.qaOrder === "section" ? "on" : ""}">논문 순서</button><button data-o="time" class="${S.qaOrder === "time" ? "on" : ""}">시간순</button></div>
      <div class="chips"><button class="chip ${!S.qaTag ? "on" : ""}" data-tag="">전체 ${items.length}</button>
      ${Object.keys(KIND).filter((k) => k !== "qa" && items.some((q) => q.kind === k)).map((k) => `<button class="chip ${S.qaTag === "k:" + k ? "on" : ""}" data-tag="k:${k}">${KIND[k]}</button>`).join("")}
      ${tags.map((t) => `<button class="chip ${S.qaTag === t ? "on" : ""}" data-tag="${esc(t)}">#${esc(t)}</button>`).join("")}</div>
    </div>
    ${shown.length ? shown.map((q) => qaHtml(q)).join("") : `<div class="callout"><div><b>아직 Q&A 가 없습니다</b><p>오른쪽 패널에서 질문하거나, 원문·번역 탭에서 문단에 마우스를 올려 '질문' / '깊게 해설' 을 누르거나, 글자를 드래그해 보세요.</p></div></div>`}`;
}

document.addEventListener("click", async (e) => {
  const o = e.target.closest("#qaOrder button");
  if (o) {
    S.qaOrder = o.dataset.o;
    S.paper.qa = (await api.get(`/api/paper/${S.key}?order=${S.qaOrder}`)).qa;
    return renderMain();
  }
  const chip = e.target.closest("[data-tag]");
  if (chip) { S.qaTag = chip.dataset.tag || null; return renderMain(); }
  const j = e.target.closest("[data-jump]");
  if (j) { e.preventDefault(); return jumpTo(j.dataset.jump); }
  const d = e.target.closest("[data-delqa]");
  if (d) {
    e.preventDefault();   // 제목 줄의 삭제 버튼을 눌러도 토글이 열리지 않게
    if (!confirm("이 Q&A / 해설을 지울까요?")) return;
    await run(async () => { await api.post(`/api/paper/${S.key}/qa/delete`, { id: d.dataset.delqa }); S.recentSig = null; await reloadPaper(); });
  }
  if (e.target.closest("[data-close]") || e.target.id === "modal") closeModal();
});

// 오른쪽 패널: 질문
function setAnchor(id, selText) {
  S.anchor = id;
  S.anchorSel = id ? selText || null : null;
  $$(".blk.sel").forEach((x) => x.classList.remove("sel"));
  if (id) document.getElementById("b-" + id)?.classList.add("sel");
  if (S.panel !== "ask") setPanel("ask");
  renderAnchor();
}

function renderAnchor() {
  const chip = $("#anchorChip");
  if (!S.anchor) { chip.hidden = true; return; }
  const b = (S.units || []).flatMap((u) => u.blocks).find((x) => x.id === S.anchor);
  const label = b && b.label ? b.label : "§" + S.anchor;
  const snip = b ? (b.type === "equation" ? b.ko || b.latex : b.ko || b.orig) : "";
  chip.hidden = false;
  chip.innerHTML = `<button class="x" title="선택 해제 (Esc)">×</button><div><b>${esc(label)}</b> 에 대해 질문합니다</div>` +
    (S.anchorSel ? `<div class="selq">“${esc(S.anchorSel)}”</div>` : `<div class="snip">${inl(snip)}</div>`);
  chip.querySelector(".x").onclick = () => setAnchor(null);
}

async function ask(kind, question, anchor) {
  if (!S.key) return toast("먼저 논문을 골라 주세요.", true);
  if (S.paper && S.paper.meta.status === "new") return toast("먼저 '읽기 시작' 으로 논문을 분석해 주세요.", true);
  await run(async () => {
    const d = await api.post(`/api/paper/${S.key}/ask`, { kind, question, anchor: anchor || "all", length: S.len, model: S.model || null });
    S.pendingAsks.unshift({ job: d.job.id, key: S.key, kind, question, anchor: anchor || "all" });
    setPanel("ask");
    renderRecent();
    pollJobs();
  });
}

$("#askBtn").addEventListener("click", submitAsk);
$("#askInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submitAsk(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { if (!$("#modal").hidden) closeModal(); else setAnchor(null); } });
async function submitAsk() {
  let q = $("#askInput").value.trim();
  if (!q) return $("#askInput").focus();
  if (S.anchorSel) q = `선택한 부분: "${S.anchorSel}"\n${q}`;
  await ask("qa", q, S.anchor);
  $("#askInput").value = "";
}

// 답변 길이 · 모델 (기억해 둠)
S.len = pref.get("len", "normal");
S.model = pref.get("model", "");
function renderAskOpts() {
  $$("#lenSeg button").forEach((b) => b.classList.toggle("on", b.dataset.len === S.len));
  $$("#modelSeg button").forEach((b) => b.classList.toggle("on", b.dataset.model === S.model));
}
$("#lenSeg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { S.len = b.dataset.len; pref.set("len", S.len); renderAskOpts(); } });
$("#modelSeg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { S.model = b.dataset.model; pref.set("model", S.model); renderAskOpts(); } });
renderAskOpts();

// ---------- 드래그로 고른 글: 하이라이트 · 이 부분 질문 · 이 부분 해설 ----------
function rerenderKeepScroll() {
  const m = $("#main"), top = m.scrollTop;
  renderMain();
  m.scrollTop = top;
}
document.addEventListener("mouseup", (e) => {
  if (e.target.closest("#selPop")) return;
  setTimeout(() => {
    const sel = window.getSelection();
    const text = String(sel || "").replace(/\s+/g, " ").trim();
    const pop = $("#selPop");
    if (!text || text.length < 2 || !sel.rangeCount) { pop.hidden = true; return; }
    const el = (n) => (n && n.nodeType === 3 ? n.parentElement : n);
    const a = el(sel.anchorNode) && el(sel.anchorNode).closest(".blk .orig, .blk .ko");
    const f = el(sel.focusNode) && el(sel.focusNode).closest(".blk .orig, .blk .ko");
    if (!a || a !== f) { pop.hidden = true; return; }
    S.selInfo = { block: a.closest(".blk").dataset.id, f: a.classList.contains("ko") ? "ko" : "orig", s: text };
    const r = sel.getRangeAt(0).getBoundingClientRect();
    pop.hidden = false;
    pop.style.left = Math.max(8, Math.min(window.innerWidth - pop.offsetWidth - 8, r.left + r.width / 2 - pop.offsetWidth / 2)) + "px";
    pop.style.top = Math.max(66, r.top - pop.offsetHeight - 8) + "px";
  }, 0);
});
document.addEventListener("mousedown", (e) => { if (!e.target.closest("#selPop")) $("#selPop").hidden = true; });
$("#main").addEventListener("scroll", () => { $("#selPop").hidden = true; });
$("#selPop").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-sel]");
  const info = S.selInfo;
  $("#selPop").hidden = true;
  if (!b || !info) return;
  window.getSelection().removeAllRanges();
  if (b.dataset.sel === "hl") {
    await run(async () => {
      S.paper.notes = (await api.post(`/api/paper/${S.key}/note`, { block: info.block, add_hl: { f: info.f, s: info.s } })).notes;
      rerenderKeepScroll();
    });
  } else if (b.dataset.sel === "ask") {
    setAnchor(info.block, info.s);
    $("#askInput").focus();
  } else {
    ask("explain", `선택한 부분: "${info.s}" — 이 부분을 중심으로 해설해 주세요.`, info.block);
  }
});
$("#newSessionBtn").addEventListener("click", async () => {
  if (!S.key) return;
  await run(async () => { await api.post(`/api/paper/${S.key}/reset-session`); toast("다음 질문부터 새 대화로 시작합니다. 지난 Q&A 기록은 그대로 남습니다."); });
});

function renderPending() {
  // 답을 기다리는 질문: Claude 가 쓰는 중인 답을 실시간으로 보여 줌
  const box = $("#pendingAsk");
  const pend = S.pendingAsks.filter((p) => p.key === S.key);
  const html = pend.map((p) => {
    const j = S.jobs.find((x) => x.id === p.job) || {};
    const head = `<div class="pend-h"><span class="spinner"></span><b>${KIND[p.kind]}</b><span class="pill ${p.anchor === "all" ? "gray" : ""}">${esc(p.anchor === "all" ? "전체" : "§" + p.anchor)}</span><span class="muted small">${esc(j.message || "대기 중")}</span></div>`;
    const q = p.question ? `<div class="q small">${esc(p.question)}</div>` : "";
    const body = j.partial ? `<div class="md stream">${md(j.partial)}</div>` : "";
    return `<div class="pending live">${head}${q}${body}</div>`;
  }).join("");
  if (html !== S.pendingHtml) { box.innerHTML = html; S.pendingHtml = html; }
}

function renderRecent() {
  renderPending();
  const box = $("#recentQa");
  const pend = S.pendingAsks.filter((p) => p.key === S.key);
  const recent = S.paper ? [...S.paper.qa].sort((a, b) => (b.ts || "").localeCompare(a.ts || "")).slice(0, 8) : [];
  // 바뀐 게 없으면 다시 그리지 않음 (펼쳐 둔 토글이 닫히지 않게)
  const sig = JSON.stringify([S.key, recent.map((q) => q.id), S.justAnswered]);
  if (sig === S.recentSig) return;
  S.recentSig = sig;
  box.innerHTML = recent.length ? recent.map((q, i) => qaHtml(q, i === 0 && S.justAnswered === q.id)).join("") : pend.length ? "" : '<div class="muted small">아직 질문이 없습니다.</div>';
}

// 오른쪽 패널: PDF
$("#panelSeg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setPanel(b.dataset.p); });
function setPanel(p) {
  S.panel = p;
  $$("#panelSeg button").forEach((b) => b.classList.toggle("on", b.dataset.p === p));
  $("#panelAsk").hidden = p !== "ask";
  $("#panelPdf").hidden = p !== "pdf";
  if (p === "pdf") showPdf();
}
let pdfShown = null;
function showPdf(page) {
  if (!S.key) return;
  if (S.panel !== "pdf") { S.panel = "pdf"; setPanel("pdf"); }
  const base = authUrl(`/pdf/${encodeURIComponent(S.key)}`);
  const want = `${base}#page=${page || 1}&view=FitH`;
  if (pdfShown === want && !page) return;
  if (!page && pdfShown && pdfShown.startsWith(base + "#")) return;
  const f = $("#pdfFrame");
  f.src = "about:blank";
  setTimeout(() => { f.src = want; }, 30);
  pdfShown = want;
}

// ---------- 용어집 ----------
function renderGlossary(el) {
  const terms = S.paper.glossary;
  el.innerHTML = `<div class="callout"><div><b>용어집 · ${terms.length}개</b><p>번역할 때 이 표기를 지킵니다. 고친 뒤 저장하면 다음 번역부터 적용됩니다. ★ 은 모든 논문에 쓰는 공용 용어집에도 넣습니다.</p></div>
    <div style="display:flex;gap:8px"><button class="btn pearl" id="glAdd">＋ 용어</button><button class="btn primary" id="glSave">저장</button></div></div>
    <div class="card" style="padding:10px 14px"><table class="tbl gl-table"><tr><th style="width:36px"></th><th style="width:30%">원어</th><th style="width:26%">번역 표기</th><th>뜻</th></tr>
    ${terms.map((t) => glRow(t)).join("")}</table></div>`;
  $("#glAdd").onclick = () => { $(".gl-table tbody").insertAdjacentHTML("beforeend", glRow({ en: "", ko: "", note: "" })); $(".gl-table tr:last-child input").focus(); };
  $("#glSave").onclick = async () => {
    const rows = $$(".gl-table tr").slice(1).map((tr) => { const [en, ko, note] = $$("input", tr).map((i) => i.value); return { en, ko, note, star: $(".star", tr).classList.contains("on") }; });
    await run(async () => {
      const d = await api.post(`/api/paper/${S.key}/glossary`, { terms: rows, to_global: rows.filter((r) => r.star && r.en) });
      S.paper.glossary = d.terms;
      toast("용어집을 저장했습니다.");
      renderMain();
    });
  };
  el.querySelector(".gl-table").addEventListener("click", (e) => { const s = e.target.closest(".star"); if (s) s.classList.toggle("on"); });
}
function glRow(t) {
  return `<tr><td><button class="star" title="공용 용어집에도 넣기">★</button></td><td><input value="${esc(t.en)}" placeholder="영어"></td><td><input value="${esc(t.ko)}" placeholder="번역 표기"></td><td><input value="${esc(t.note)}" placeholder="한 줄 뜻"></td></tr>`;
}

// ---------- 메모 ----------
function renderMemos(el) {
  const notes = S.paper.notes;
  const blocks = Object.fromEntries((S.units || []).flatMap((u) => u.blocks).map((b) => [b.id, b]));
  const ids = Object.keys(notes);
  if (!ids.length) {
    el.innerHTML = `<div class="callout"><div><b>아직 메모가 없습니다</b><p>원문·번역 탭에서 글자를 드래그해 하이라이트하거나 문단의 '메모' 를 남기면 여기에 모입니다. Notion 에도 함께 올라갑니다.</p></div></div>`;
    return;
  }
  el.innerHTML = ids.map((id) => {
    const b = blocks[id] || {}, n = notes[id];
    return `<div class="memo-item" data-jump="${esc(id)}"><div class="h small"><span class="pill">§${esc(id)}</span> ${(n.hls || []).length ? `<span class="pill amber">하이라이트 ${n.hls.length}</span>` : ""}</div>
      <div class="src">${inl(b.ko || b.orig || b.latex || "")}</div>${(n.hls || []).map((h) => `<div class="small"><mark class="hlm">${esc(h.s)}</mark></div>`).join("")}${n.memo ? `<div style="white-space:pre-wrap;margin-top:6px"><b>메모</b> ${esc(n.memo)}</div>` : ""}</div>`;
  }).join("");
}

// ---------- 코드 · 발표 요약 ----------
function renderCode(el) {
  const c = S.paper.code;
  const busy = (S.paper.busy || []).some((j) => j.kind === "code");
  if (!c) {
    el.innerHTML = `<div class="callout"><div><b>코드 저장소 연결</b><p>공식 GitHub 저장소를 찾아 논문의 모듈(예: §3.2 의 attention, Eq. 5 의 손실)이 어느 파일에 있는지 연결합니다.</p></div>
      ${busy ? '<span class="spinner"></span>' : '<button class="btn primary" data-act="code">저장소 찾기</button>'}</div>`;
    return;
  }
  el.innerHTML = `<div class="card"><h2>${c.repo_url ? `<a href="${esc(c.repo_url)}" target="_blank">${esc(c.repo_url.replace(/^https?:\/\/(www\.)?github\.com\//, ""))}</a>` : "저장소를 찾지 못했습니다"}</h2>
      <div style="display:flex;gap:6px;margin-bottom:10px">${c.repo_url ? `<span class="pill ${c.official ? "green" : "amber"}">${c.official ? "공식" : "비공식"}</span><span class="pill ${c.verified ? "gray" : "amber"}">${c.verified ? "확인됨" : "미확인"}</span>` : ""}${c.framework ? `<span class="pill gray">${esc(c.framework)}</span>` : ""}</div>
      <div class="md">${md(c.summary)}</div></div>
    ${(c.mapping || []).length ? `<div class="card"><h2>논문 ↔ 코드</h2><div class="tbl-wrap"><table class="tbl"><tr><th>논문</th><th>코드</th><th>비고</th></tr>
      ${c.mapping.map((r) => `<tr><td>${inl(r.paper)}</td><td>${r.url ? `<a href="${esc(r.url)}" target="_blank"><code>${esc(r.path)}</code></a>` : `<code>${esc(r.path)}</code>`}</td><td class="muted">${inl(r.note)}</td></tr>`).join("")}</table></div></div>` : ""}
    ${c.how_to_read ? `<div class="card"><h2>읽는 순서</h2><div class="md">${md(c.how_to_read)}</div></div>` : ""}
    <div class="muted small" style="text-align:right">${esc(c.created || "")} · ${busy ? "다시 찾는 중…" : '<a href="#" data-act="code">다시 찾기</a>'}</div>`;
}

function renderSlides(el) {
  const s = S.paper.slides;
  const busy = (S.paper.busy || []).some((j) => j.kind === "slides");
  if (!s) {
    el.innerHTML = `<div class="callout"><div><b>발표 요약</b><p>랩미팅용으로 슬라이드 5~7장 분량의 개조식 요약을 만듭니다 (문제 → 아이디어 → 방법 → 결과 → 토론 질문).</p></div>
      ${busy ? '<span class="spinner"></span>' : '<button class="btn primary" data-act="slides">발표 요약 만들기</button>'}</div>`;
    return;
  }
  const parts = s.split(/^(?=## )/m).filter((x) => x.trim());
  el.innerHTML = parts.map((p) => {
    const [h, ...rest] = p.split("\n");
    return `<div class="slide"><h2>${inl(h.replace(/^##\s*/, ""))}</h2><div class="md">${md(rest.join("\n"))}</div></div>`;
  }).join("") + `<div class="muted small" style="text-align:right">${busy ? "다시 만드는 중…" : '<a href="#" data-act="slides">다시 만들기</a>'}</div>`;
}

// ---------- 라이브러리 화면들 ----------
$$(".top-actions [data-view]").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
function setView(v) {
  S.view = v;
  $$(".top-actions [data-view]").forEach((b) => b.classList.toggle("primary", b.dataset.view === v));
  $$(".top-actions [data-view]").forEach((b) => b.classList.toggle("pearl", b.dataset.view !== v));
  renderLibrary();
  renderMain();
  $("#main").scrollTop = 0;
}
function backBtn() {
  return S.key ? `<button class="btn ghost sm" id="backBtn">← 논문으로</button>` : "";
}
document.addEventListener("click", (e) => { if (e.target.closest("#backBtn")) { setView("paper"); openPaper(S.key); } });

async function renderCompare() {
  const main = $("#main");
  const sel = [...S.compareSel];
  const names = sel.map((k) => (S.papers.find((p) => p.key === k) || {}).short || k);
  let items = [];
  try { items = (await api.get("/api/comparisons")).items; } catch (e) { /* 비교표가 아직 없으면 빈 목록 */ }
  if (S.view !== "compare") return;
  main.innerHTML = `<div class="wrap">${backBtn()}
    <div class="callout"><div><b>논문 비교표</b><p>왼쪽 목록에서 비교할 논문을 두 편 이상 고르세요 (해설이 있는 논문일수록 정확합니다).${sel.length ? "<br>선택: " + names.map(esc).join(", ") : ""}</p></div>
    <button class="btn primary" id="cmpBtn" ${sel.length < 2 ? "disabled" : ""}>비교표 만들기 (${sel.length}편)</button></div>
    ${items.map((c) => `<div class="card"><h2>${esc(c.title)}<span style="flex:1"></span><button class="btn danger sm" data-delcmp="${esc(c.id)}">삭제</button></h2><div class="muted small" style="margin:-6px 0 12px">${esc(c.created)}</div>
      <div class="tbl-wrap"><table class="tbl"><tr><th></th>${c.columns.map((x) => `<th>${esc(x)}</th>`).join("")}</tr>
      ${c.rows.map((r) => `<tr><td><b>${esc(r.aspect)}</b></td>${r.values.map((v) => `<td>${inl(v)}</td>`).join("")}</tr>`).join("")}</table></div>
      <div class="md" style="margin-top:14px">${md(c.summary)}</div></div>`).join("")}</div>`;
  const b = $("#cmpBtn");
  if (b) b.onclick = () => run(async () => { await api.post("/api/compare", { keys: sel }); toast("비교표를 만드는 중입니다 (몇 분)."); pollJobs(); });
}

let mermaidReady = null;
function loadMermaid() {
  if (!mermaidReady) mermaidReady = new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "/vendor/mermaid.min.js";
    s.onload = () => { mermaid.initialize({ startOnLoad: false, securityLevel: "loose", theme: "base", fontFamily: "Pretendard, sans-serif",
      flowchart: { useMaxWidth: false, nodeSpacing: 40, rankSpacing: 70, padding: 14 },
      themeVariables: { primaryColor: "#ffffff", primaryBorderColor: "#d2d2d7", lineColor: "#9a9aa0", fontSize: "17px" } }); res(); };
    s.onerror = rej;
    document.head.appendChild(s);
  });
  return mermaidReady;
}
window.openPaperFromGraph = (key) => { setView("paper"); openPaper(key); };

async function renderLineage() {
  const main = $("#main");
  const d = (await api.get("/api/lineage")).lineage;
  if (S.view !== "lineage") return;
  const busy = S.jobs.some((j) => j.kind === "lineage" && ["queued", "running"].includes(j.status));
  main.innerHTML = `<div class="wrap">${backBtn()}
    <div class="callout"><div><b>Task 계보도</b><p>라이브러리 논문들의 해설(연구 흐름 · 관련 논문)을 모아 Task 별 계보를 그리고, 다음에 읽을 논문을 추천합니다. 파란 칸 = 내 라이브러리 (누르면 열림).</p></div>
    <div style="display:flex;gap:8px">${d && !busy ? '<button class="btn danger" id="linDel">삭제</button>' : ""}${busy ? '<span class="spinner"></span>' : `<button class="btn primary" id="linBtn">${d ? "다시 만들기" : "계보도 만들기"}</button>`}</div></div>
    <div id="linBody">${d ? "" : '<div class="muted">아직 계보도가 없습니다.</div>'}</div></div>`;
  const ld = $("#linDel");
  if (ld) ld.onclick = () => { if (confirm("계보도를 지울까요?")) run(async () => { await api.post("/api/lineage/delete"); renderLineage(); }); };
  const b = $("#linBtn");
  if (b) b.onclick = () => run(async () => { await api.post("/api/lineage"); toast("계보도를 만드는 중입니다 (몇 분)."); pollJobs(); renderLineage(); });
  if (!d) return;
  const body = $("#linBody");
  body.innerHTML = d.tasks.map((t, i) => `<div class="card"><h2>${esc(t.name)}</h2><div class="md muted" style="margin-bottom:12px">${md(t.summary)}</div><div class="graph-tools" data-g="${i}"><button class="btn pearl sm" data-z="-">－</button><span class="zoom" id="lz-${i}">100%</span><button class="btn pearl sm" data-z="+">＋</button><button class="btn pearl sm" data-z="fit">맞춤</button><button class="btn pearl sm" data-z="big">크게 보기</button></div><div class="lineage-graph" id="lg-${i}"></div></div>`).join("")
    + (d.recommendations.length ? `<div class="card"><h2>다음에 읽을 논문</h2>${d.recommendations.map((r) => `<div class="related"><div class="r"><span class="nm">${r.url ? `<a href="${esc(r.url)}" target="_blank">${esc(r.title)}</a>` : esc(r.title)}</span> <span class="muted small">${esc(r.venue || "")} ${esc(r.year || "")}</span> ${r.verified ? '<span class="pill green">확인</span>' : '<span class="pill amber">미확인</span>'}<div class="df">${inl(r.why)}</div></div></div>`).join("")}</div>` : "")
    + `<div class="muted small" style="text-align:right">${esc(d.created)} · 논문 ${d.keys.length}편 기준</div>`;
  await loadMermaid();
  for (let i = 0; i < d.tasks.length; i++) {
    const t = d.tasks[i];
    const safe = (s) => String(s || "").replace(/["\[\]{}()<>|#;]/g, " ").trim();
    const ids = new Set(t.nodes.map((n) => n.id));
    const lines = ["flowchart LR"];
    for (const n of t.nodes) lines.push(`  ${n.id.replace(/\W/g, "_")}["<b>${safe(n.title)}</b><br/><small>${safe(n.venue)} ${safe(n.year)}</small>"]`);
    for (const e of t.edges) if (ids.has(e.from) && ids.has(e.to)) lines.push(`  ${e.from.replace(/\W/g, "_")} -->${e.label ? `|${safe(e.label)}|` : ""} ${e.to.replace(/\W/g, "_")}`);
    const mine = t.nodes.filter((n) => n.key);
    if (mine.length) {
      lines.push("  classDef mine fill:#e8f1fc,stroke:#0071e3,stroke-width:2px,color:#0062c4");
      lines.push(`  class ${mine.map((n) => n.id.replace(/\W/g, "_")).join(",")} mine`);
      for (const n of mine) lines.push(`  click ${n.id.replace(/\W/g, "_")} call openPaperFromGraph("${n.key}")`);
    }
    try {
      const { svg, bindFunctions } = await mermaid.render("lgsvg" + i + Date.now(), lines.join("\n"));
      const box = $("#lg-" + i);
      box.innerHTML = `<div class="gwrap">${svg}</div>`;
      setGraphZoom(i, 1);
      if (bindFunctions) bindFunctions(box);
    } catch (e) { $("#lg-" + i).innerHTML = `<div class="muted small">그래프를 그리지 못했습니다: ${esc(e.message)}</div>`; }
  }
}

async function renderGlobal() {
  const main = $("#main");
  const items = (await api.get("/api/global-qa")).items;
  if (S.view !== "global") return;
  const pend = S.jobs.filter((j) => j.kind === "global" && ["queued", "running"].includes(j.status));
  main.innerHTML = `<div class="wrap">${backBtn()}
    <div class="card"><h2>라이브러리 전체에 질문</h2><p class="muted small" style="margin-top:-6px">읽은 논문들의 해설 · 원문 · Q&A · 메모를 함께 찾아 답합니다. 예: "temporal fusion 을 다룬 논문들은 각각 어떻게 하지?"</p>
      <textarea id="gq" rows="3" placeholder="질문 (Ctrl+Enter)"></textarea><div style="display:flex;justify-content:flex-end;margin-top:8px"><button class="btn primary" id="gqBtn">질문하기</button></div></div>
    <div id="gPending">${pend.length ? "" : ""}</div>
    ${items.map((q) => `<details class="qa"><summary>${CHEV}<span class="ttl">${esc(q.title || q.question)}</span><span class="meta">${q.papers.map((k) => `<span class="pill gray">${esc((S.papers.find((p) => p.key === k) || {}).short || k)}</span>`).join("")}<span>${fmtTs(q.ts)}</span></span></summary>
      <div class="body"><div class="q"><b>질문</b> ${esc(q.question)}</div><div class="md">${md(q.answer)}</div><div class="foot"><button class="btn danger" data-delgqa="${esc(q.id)}">삭제</button></div></div></details>`).join("")}</div>`;
  const go = () => {
    const q = $("#gq").value.trim();
    if (!q) return;
    run(async () => { await api.post("/api/global-ask", { question: q }); toast("라이브러리에서 찾는 중입니다."); await pollJobs(); renderGlobal(); });
  };
  $("#gqBtn").onclick = go;
  $("#gq").onkeydown = (e) => { if (e.key === "Enter" && e.ctrlKey) go(); };
  S.gPendingHtml = null;
  renderGlobalPending();
}

function renderGlobalPending() {
  const box = $("#gPending");
  if (!box) return;
  const pend = S.jobs.filter((j) => j.kind === "global" && ["queued", "running"].includes(j.status));
  const html = pend.map((j) => `<div class="pending live" style="margin-bottom:8px"><div class="pend-h"><span class="spinner"></span><b>라이브러리 질문</b><span class="muted small">${esc(j.message || "대기 중")}</span></div>${j.partial ? `<div class="md stream">${md(j.partial)}</div>` : ""}</div>`).join("");
  if (html !== S.gPendingHtml) { box.innerHTML = html; S.gPendingHtml = html; }
}

// ---------- 검색 ----------
let searchTimer = null;
$("#searchInput").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { if ($("#searchInput").value.trim()) setView("search"); else if (S.view === "search") setView("paper"); }, 250);
});
async function renderSearch() {
  const q = $("#searchInput").value.trim();
  const res = q ? (await api.get("/api/search?q=" + encodeURIComponent(q))).results : [];
  if (S.view !== "search") return;
  const words = q.split(/\s+/).filter(Boolean).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const mark = (s) => words.length ? esc(s).replace(new RegExp("(" + words.join("|") + ")", "gi"), "<mark>$1</mark>") : esc(s);
  $("#main").innerHTML = `<div class="wrap">${backBtn()}<h2 style="margin:4px 0 14px">"${esc(q)}" 검색 결과 ${res.length}개</h2>
    ${res.map((r, i) => `<div class="sr" data-i="${i}"><div class="h"><span class="pill venue">${esc(r.paper)}</span><span class="pill gray">${esc(r.where)}</span><span>${esc(r.tag)}</span>${r.title ? `<b style="color:var(--ink)">${esc(r.title)}</b>` : ""}</div>${mark(r.snippet)}</div>`).join("")}</div>`;
  $$(".sr").forEach((el) => el.onclick = () => {
    const r = res[+el.dataset.i];
    setView("paper");
    if (r.where === "본문" || r.where === "메모") openPaper(r.key, { tab: "reader", block: r.id });
    else if (r.where === "Q&A") openPaper(r.key, { tab: "qa" });
    else openPaper(r.key, { tab: "overview" });
  });
}

// ---------- 작업 ----------
let pollTimer = null;
async function pollJobs() {
  clearTimeout(pollTimer);
  let d;
  try { d = await api.get("/api/jobs"); } catch (e) { pollTimer = setTimeout(pollJobs, 4000); return; }
  S.jobs = d.jobs;
  if (d.usage) S.usage = d.usage;
  if (d.limits) { S.limits = d.limits; renderLimits(); }
  const active = S.jobs.filter((j) => ["queued", "running"].includes(j.status));
  $("#jobsBtn .spinner").hidden = !active.length;
  $("#jobsLabel").textContent = active.length ? `작업 ${active.length}개 진행 중` : "작업 없음";
  let refreshPaper = false, refreshLib = false;
  for (const j of S.jobs) {
    const prev = S.seenJobs[j.id];
    if (prev && prev !== j.status && ["done", "error"].includes(j.status)) {
      if (j.status === "error") toast(`${j.label} 실패: ${j.error}`, true);
      else if (j.kind === "ask") {
        toast("답변이 도착했습니다.");
        S.justAnswered = j.result && j.result.id;
      } else toast(`${j.label} 완료`);
      if (j.key === S.key) refreshPaper = true;
      refreshLib = true;
      S.pendingAsks = S.pendingAsks.filter((p) => p.job !== j.id);
      if (j.kind === "lineage" && S.view === "lineage") renderLineage();
      if (j.kind === "compare" && S.view === "compare") renderCompare();
      if (j.kind === "global" && S.view === "global") renderGlobal();
    }
    if (j.status === "running" && ["translate", "retranslate"].includes(j.kind) && j.key === S.key && S.lastDone[j.id] !== undefined && S.lastDone[j.id] !== j.i) refreshPaper = true;
    if (["translate", "retranslate"].includes(j.kind)) S.lastDone[j.id] = j.i;
    S.seenJobs[j.id] = j.status;
  }
  if (!$("#jobsPop").hidden) renderJobsPop();
  if (refreshLib) loadLibrary();
  // 용어집을 고치는 중에는 화면을 다시 그리지 않음 (입력이 날아가지 않게)
  const editing = S.tab === "glossary" || !$("#modal").hidden;
  if (refreshPaper && S.view === "paper" && !editing) await reloadPaper();
  else if (S.paper && S.view === "paper") {
    // 진행 중 작업의 메시지만 머리글에 반영
    const busy = active.filter((j) => j.key === S.key);
    if (JSON.stringify(busy.map((j) => [j.id, j.message, j.i])) !== JSON.stringify((S.paper.busy || []).map((j) => [j.id, j.message, j.i]))) {
      S.paper.busy = busy;
      const ph = $(".phead");
      if (ph) ph.outerHTML = headerHtml(S.paper);
    }
  }
  renderRecent();
  if (S.view === "global") renderGlobalPending();
  // 질문 답을 기다릴 때는 더 자주 물어봐서 답이 써지는 모습을 부드럽게 보여 줌
  const asking = active.some((j) => j.kind === "ask" || j.kind === "global");
  pollTimer = setTimeout(pollJobs, asking ? 600 : active.length ? 1500 : 5000);
}

$("#jobsBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const p = $("#jobsPop");
  p.hidden = !p.hidden;
  if (!p.hidden) renderJobsPop();
});
document.addEventListener("click", (e) => { if (!e.target.closest("#jobsPop, #jobsBtn")) $("#jobsPop").hidden = true; });
function renderJobsPop() {
  const p = $("#jobsPop");
  const L = S.limits || {};
  const win = (w, name) => w ? `<div>${name} 한도 <b>${w.pct}%</b> 사용${w.resets ? ` · ${esc(w.resets)} 초기화` : ""}</div>` : "";
  const usage = `<div class="muted small" style="padding:8px 12px;line-height:1.7">${S.usage ? esc(usageText(S.usage)) : ""}
    ${win(L.five_hour, "5시간")}${win(L.seven_day, "주간")}${L.seen ? `<div style="color:var(--faint)">한도 정보: ${esc(L.seen)} 기준</div>` : ""}</div>`;
  p.innerHTML = (S.jobs.length ? S.jobs.map((j) => {
    const st = { queued: "대기", running: "진행 중", done: "완료", error: "실패", cancelled: "취소" }[j.status];
    const pct = j.n ? Math.round((j.i / j.n) * 100) : j.status === "done" ? 100 : 0;
    return `<div class="job ${j.status}"><div class="h"><span>${esc(j.label)}</span><span class="pill ${j.status === "error" ? "red" : j.status === "done" ? "green" : "gray"}">${st}</span></div>
      <div class="m">${esc(j.status === "error" ? j.error : j.message || "")}</div>${["running", "queued"].includes(j.status) ? `<div class="bar"><i style="width:${pct}%"></i></div>` : ""}</div>`;
  }).join("") : '<div class="muted small" style="padding:12px">최근 작업이 없습니다.</div>') + usage;
}

// 계보도 확대: svg 의 본래 크기(viewBox)에 배율을 곱해 폭을 정함 → 스크롤도 자연스럽게
const graphZoom = {};
function setGraphZoom(i, z, box) {
  box = box || $("#lg-" + i);
  const svg = box && box.querySelector("svg");
  if (!svg) return;
  const vb = (svg.getAttribute("viewBox") || "").split(/[\s,]+/).map(Number);
  const w = vb[2] || svg.getBBox().width;
  if (z === "fit") z = Math.max(0.3, Math.min(2, (box.clientWidth - 24) / w));
  z = Math.max(0.3, Math.min(3, z));
  graphZoom[i] = z;
  svg.style.width = Math.round(w * z) + "px";
  svg.style.height = "auto";
  const lab = $("#lz-" + i);
  if (lab) lab.textContent = Math.round(z * 100) + "%";
}
document.addEventListener("click", async (e) => {
  const zb = e.target.closest(".graph-tools [data-z]");
  if (zb) {
    const i = zb.closest(".graph-tools").dataset.g, z = graphZoom[i] || 1, a = zb.dataset.z;
    if (a === "big") {
      modal(`<div style="display:flex;align-items:center;gap:8px"><h3 style="margin:0">계보도</h3><span style="flex:1"></span><button class="btn pearl sm" data-mz="-">－</button><button class="btn pearl sm" data-mz="+">＋</button><button class="btn primary sm" data-close>닫기</button></div><div class="lineage-graph" id="lgBig" style="margin-top:10px">${$("#lg-" + i).innerHTML}</div>`);
      $("#modalBody").className = "modal graph";
      let mzv = 1.3;
      setGraphZoom("big", mzv, $("#lgBig"));
      $("#modalBody").onclick = (ev) => { const m = ev.target.closest("[data-mz]"); if (m) { mzv = m.dataset.mz === "+" ? mzv * 1.25 : mzv / 1.25; setGraphZoom("big", mzv, $("#lgBig")); } };
      return;
    }
    setGraphZoom(i, a === "fit" ? "fit" : a === "+" ? z * 1.25 : z / 1.25);
    return;
  }
  const dc = e.target.closest("[data-delcmp]");
  if (dc) {
    if (!confirm("이 비교표를 지울까요?")) return;
    await run(async () => { await api.post("/api/comparisons/delete", { id: dc.dataset.delcmp }); renderCompare(); });
    return;
  }
  const dg = e.target.closest("[data-delgqa]");
  if (dg) {
    if (!confirm("이 질문을 지울까요?")) return;
    await run(async () => { await api.post("/api/global-qa/delete", { id: dg.dataset.delgqa }); renderGlobal(); });
  }
});

// ---------- 그림 다시 자르기: 쪽 위에서 끌어서 영역 고르기 ----------
async function openRecrop(box) {
  const key = S.key, id = box.dataset.fig, pageNo = +box.dataset.page;
  modal(`<div style="display:flex;align-items:center;gap:10px"><h3 style="margin:0">${esc(box.dataset.label)} 다시 자르기</h3><span class="muted small">PDF ${pageNo}쪽 위에서 그림 영역을 끌어서 고르세요</span><span style="flex:1"></span>
    <button class="btn pearl" data-close>취소</button><button class="btn primary" id="cropSave" disabled>저장</button></div>
    <div class="crop-wrap" id="cropWrap"><div class="fig-ph">쪽을 그리는 중…</div></div>`);
  $("#modalBody").className = "modal wide";
  const doc = await pdfDoc(key);
  const page = await doc.getPage(pageNo);
  const base = page.getViewport({ scale: 1 });
  const wrap = $("#cropWrap");
  const scale = Math.min(2, (wrap.clientWidth - 4) / base.width);
  const vp = page.getViewport({ scale });
  const cv = document.createElement("canvas");
  cv.width = vp.width; cv.height = vp.height;
  await page.render({ canvasContext: cv.getContext("2d"), viewport: vp }).promise;
  wrap.innerHTML = "";
  wrap.appendChild(cv);
  const rectEl = document.createElement("div");
  rectEl.className = "crop-rect";
  rectEl.hidden = true;
  wrap.appendChild(rectEl);
  let start = null, rect = null;
  const pos = (ev) => { const r = cv.getBoundingClientRect(); return { x: Math.max(0, Math.min(cv.width, ev.clientX - r.left)), y: Math.max(0, Math.min(cv.height, ev.clientY - r.top)) }; };
  const draw = () => { rectEl.hidden = false; Object.assign(rectEl.style, { left: cv.offsetLeft + rect.x + "px", top: cv.offsetTop + rect.y + "px", width: rect.w + "px", height: rect.h + "px" }); };
  cv.addEventListener("mousedown", (ev) => { start = pos(ev); rect = { x: start.x, y: start.y, w: 0, h: 0 }; draw(); ev.preventDefault(); });
  window.addEventListener("mousemove", function mv(ev) {
    if (!start) return;
    if ($("#modal").hidden) { window.removeEventListener("mousemove", mv); return; }
    const p = pos(ev);
    rect = { x: Math.min(start.x, p.x), y: Math.min(start.y, p.y), w: Math.abs(p.x - start.x), h: Math.abs(p.y - start.y) };
    draw();
  });
  window.addEventListener("mouseup", () => { if (start) { start = null; $("#cropSave").disabled = !(rect && rect.w > 10 && rect.h > 10); } }, { once: false });
  $("#cropSave").onclick = () => run(async () => {
    const hi = 2, f = hi / scale;   // 저장은 고해상도(배율 2)로 다시 그려서 자름
    const vp2 = page.getViewport({ scale: hi });
    const big = document.createElement("canvas");
    big.width = vp2.width; big.height = vp2.height;
    await page.render({ canvasContext: big.getContext("2d"), viewport: vp2 }).promise;
    const out = document.createElement("canvas");
    out.width = Math.round(rect.w * f); out.height = Math.round(rect.h * f);
    out.getContext("2d").drawImage(big, rect.x * f, rect.y * f, rect.w * f, rect.h * f, 0, 0, out.width, out.height);
    const png = out.toDataURL("image/png");
    await api.post(`/api/paper/${encodeURIComponent(key)}/figure`, { block: id, png });
    S.figCache[key + "/" + id] = png;
    box.dataset.cached = "1";
    box.innerHTML = `<img src="${png}" alt="${esc(box.dataset.label)}">${RECROP_BTN}`;
    closeModal();
    toast("그림을 다시 잘라 저장했습니다.");
  });
}

// ---------- 화면 테마: 자동(윈도우 설정) → 밝게 → 어둡게 ----------
const THEME_ICON = {   // 선 아이콘 (자동 = 반쪽 원, 밝게 = 해, 어둡게 = 달)
  auto: '<svg viewBox="0 0 20 20" width="16" height="16"><circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10 3a7 7 0 0 1 0 14z" fill="currentColor"/></svg>',
  light: '<svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="10" cy="10" r="3.6"/><path d="M10 1.8v2M10 16.2v2M1.8 10h2M16.2 10h2M4.2 4.2l1.4 1.4M14.4 14.4l1.4 1.4M4.2 15.8l1.4-1.4M14.4 5.6l1.4-1.4"/></svg>',
  dark: '<svg viewBox="0 0 20 20" width="16" height="16"><path d="M15.5 12.6A6.6 6.6 0 0 1 7.4 4.5a6.6 6.6 0 1 0 8.1 8.1z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
};
function applyTheme(t) {
  if (t === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  $("#themeBtn").innerHTML = THEME_ICON[t];
  $("#themeBtn").title = "화면 테마: " + { auto: "자동 (윈도우 설정)", light: "밝게", dark: "어둡게" }[t] + " — 누르면 바뀜";
}
applyTheme(pref.get("theme", "auto"));
$("#themeBtn").addEventListener("click", () => {
  const order = ["auto", "light", "dark"];
  const t = order[(order.indexOf(pref.get("theme", "auto")) + 1) % 3];
  pref.set("theme", t);
  applyTheme(t);
});

// ---------- 사용 한도 (Claude 구독 5시간 창) ----------
function renderLimits() {
  const pill = $("#limitPill");
  const w = S.limits && S.limits.five_hour;
  if (!w) { pill.hidden = true; return; }
  pill.hidden = false;
  pill.className = "limit-pill" + (w.pct >= 80 ? " high" : w.pct >= 50 ? " mid" : "");
  pill.textContent = `사용량 ${w.pct}%`;
  pill.title = `Claude 구독 5시간 한도 ${w.pct}% 사용${w.resets ? " · " + w.resets + " 초기화" : ""}` +
    (S.limits.seven_day ? ` / 주간 ${S.limits.seven_day.pct}%` : "") + " — 누르면 자세히";
}
$("#limitPill").addEventListener("click", (e) => { e.stopPropagation(); $("#jobsPop").hidden = false; renderJobsPop(); });

// ---------- 오른쪽 패널 폭 (끌어서 조절, 두 번 누르면 넓게 ↔ 기본) ----------
const PANEL_DEFAULT = 520;
function setPanelWidth(w, save) {
  const max = Math.max(380, window.innerWidth - 300 - 520);   // 가운데 본문이 최소 520px 은 남게
  w = Math.round(Math.min(Math.max(w, 360), max));
  document.documentElement.style.setProperty("--panel-w", w + "px");
  if (save) pref.set("panelW", w);
  return w;
}
setPanelWidth(pref.get("panelW", PANEL_DEFAULT));
(function () {
  const sp = $("#splitter");
  let dragging = false;
  sp.addEventListener("mousedown", (e) => { dragging = true; document.body.classList.add("dragging"); e.preventDefault(); });
  window.addEventListener("mousemove", (e) => { if (dragging) setPanelWidth(window.innerWidth - e.clientX, false); });
  window.addEventListener("mouseup", (e) => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove("dragging");
    setPanelWidth(window.innerWidth - e.clientX, true);
  });
  sp.addEventListener("dblclick", () => {
    const cur = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--panel-w")) || PANEL_DEFAULT;
    setPanelWidth(cur > PANEL_DEFAULT + 40 ? PANEL_DEFAULT : window.innerWidth * 0.45, true);
  });
  window.addEventListener("resize", () => setPanelWidth(pref.get("panelW", PANEL_DEFAULT), false));
})();

// ---------- 모달 ----------
function modal(html) { $("#modalBody").innerHTML = html; $("#modal").hidden = false; }
function closeModal() { $("#modal").hidden = true; $("#modalBody").onclick = null; $("#modalBody").className = "modal"; }

// ---------- 시작 ----------
async function boot() {
  const qs = new URLSearchParams(location.search);   // 예: /?tab=reader&key=...
  if (qs.get("tab")) S.tab = qs.get("tab");
  if (qs.get("key")) S.key = qs.get("key");
  if (qs.get("theme")) applyTheme(qs.get("theme"));
  await run(loadLibrary);
  if (S.key && !S.papers.some((p) => p.key === S.key)) S.key = null;
  if (!S.key && S.papers.length) S.key = S.papers[0].key;
  if (S.key) await openPaper(S.key, { block: qs.get("block") || undefined, instant: true }); else renderMain();
  if (["compare", "lineage", "global"].includes(qs.get("view"))) setView(qs.get("view"));
  pollJobs();
  setInterval(() => api.get("/api/ping").catch(() => {}), 5000);
  $("#reloadBtn").onclick = () => run(async () => { S.libSig = null; await loadLibrary(); toast("목록을 새로 고쳤습니다."); });
}
boot();
