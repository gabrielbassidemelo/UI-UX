"use strict";
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const view = $("#view");
  let META = null, charts = [], state = { tab: "hoje", sub: null, area: "recuperacao", days: 30, metric: null, cmp: 7, kind: "" };

  // ---------------------------------------------------------------- infra
  async function api(path, opts = {}) {
    const headers = { "X-HM": "1", ...(opts.raw ? {} : { "Content-Type": "application/json" }), ...(opts.headers || {}) };
    const r = await fetch(path, { credentials: "same-origin", ...opts, headers });
    if (r.status === 401) { location.href = "/login"; throw new Error("sessão expirada"); }
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || d.erro || r.statusText); }
    return r.json();
  }
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
  let toastT;
  function toast(msg) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toastT); toastT = setTimeout(() => (t.hidden = true), 3500); }
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const nf = (v, c = 0) => v == null ? "—" : Number(v).toLocaleString("pt-BR", { minimumFractionDigits: c, maximumFractionDigits: c });
  function fmtVal(key, v) {
    if (v == null) return "—";
    const m = META.metricas[key];
    if (m.unidade === "h") { const h = Math.floor(v), mi = Math.round((v - h) * 60); return mi === 60 ? `${h + 1}h00` : `${h}h${String(mi).padStart(2, "0")}`; }
    return `${nf(v, m.casas)}${m.unidade ? " " + m.unidade : ""}`;
  }
  const dateBR = (iso) => iso ? new Date(iso + (iso.length === 10 ? "T12:00:00" : "")).toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" }) : "—";
  const dtBR = (iso) => iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "nunca";
  const todayISO = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const CONF = { alta: "Confiança alta", "média": "Confiança média", media: "Confiança média", baixa: "Confiança baixa" };
  const chip = (cls, txt) => `<span class="chip ${esc(String(cls).replace(/\s/g, "_"))}">${esc(txt)}</span>`;
  const CLASSE = { verificada: ["ok", "Verificada"], parcialmente_verificada: ["media", "Parcialmente verificada"], nao_verificada: ["baixa", "Não verificada"], inadequada: ["erro", "Inadequada para a regra"] };
  const VERIF_AVISO = "Verificação feita por modelos de IA em bases públicas; não é auditoria humana independente";
  const seal = (r) => { const c = CLASSE[r.classe] || CLASSE.nao_verificada; return chip(c[0], c[1]); };
  const refs = (list) => (list || []).map((r) => `<p class="ref">${esc(r.referencia)} ${r.url ? `<a class="tap" href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">DOI</a>` : ""}
    <br>${chip("", "Evidência " + r.grau)} ${seal(r)}<br>${esc(r.resumo)} <em>Limitações: ${esc(r.limitacoes)}</em>${r.observacao_verificacao ? `<br><span class="small muted">Verificação: ${esc(r.observacao_verificacao)}</span>` : ""}</p>`).join("")
    + ((list || []).length ? `<p class="small muted">${VERIF_AVISO}.</p>` : "");
  const evid = (list, label = "Evidências científicas") => list && list.length ? `<details><summary>${label} (${list.length})</summary><div class="inner">${refs(list)}</div></details>` : "";
  const URGENT_TXT = "Sintoma de alerta: procure atendimento de urgência agora (SAMU 192). Não treine e não use este painel para decidir o que fazer.";
  const SUSP = { alertas_de_tendencia: "Alertas de tendência", reduzir_treino_por_recuperacao: "Redução de treino por recuperação", progressao_de_carga: "Progressão de carga",
    calibracao_metabolica: "Calibração metabólica", analise_de_tendencias: "Análise de tendências", comparacoes_longitudinais: "Comparações de longo prazo",
    avaliacao_das_recomendacoes: "Avaliação das recomendações" };
  const calibCard = (c) => { const susp = (c.suspenso || []).map((k) => SUSP[k] || k);
    return `<div class="card calib"><h3 class="sec">Calibração: ${esc(c.rotulo)}</h3>
      <p class="small">${c.dias_validos} dia(s) com dados válidos${c.proximo ? ` · faltam ${c.proximo.faltam_dias} para o próximo estágio` : ""}.</p>
      ${c.observacao ? `<p class="notice">${esc(c.observacao)}</p>` : ""}
      ${susp.length ? `<p class="small"><strong>Ainda suspenso:</strong> ${esc(susp.join("; "))}.</p>` : `<p class="small">Nada suspenso por falta de dados.</p>`}
      <p class="small muted">${esc(c.nota)}</p></div>`; };
  const blockCard = (title, reason, faltam) => `<div class="card alert"><h3>${esc(title)}</h3><p>${esc(reason || "Indisponível no momento.")}</p>
    ${faltam && faltam.length ? `<p class="small"><strong>O que falta:</strong> ${esc(faltam.join(", "))}.</p>` : ""}<button class="link tap" data-go="config">Abrir perfil e triagem</button></div>`;
  function recCard(i) {
    return `<div class="card rec"><div class="q">${esc(i.pergunta)}</div><div class="what">${esc(i.o_que_fazer)}</div>
      ${chip(i.confianca, CONF[i.confianca] || i.confianca)}
      <details><summary>Por quê, dados usados e evidências</summary><div class="inner">
      <p><strong>Por quê:</strong> ${esc(i.por_que)}</p>
      ${i.dados.length ? `<p><strong>Dados usados:</strong></p><ul>${i.dados.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>` : ""}
      ${i.limitacoes ? `<p><strong>Limitações:</strong> ${esc(i.limitacoes)}</p>` : ""}${refs(i.evidencias)}</div></details></div>`;
  }
  function formData(form) {
    const o = {};
    $$("input,select,textarea", form).forEach((el) => { if (!el.name) return;
      if (el.type === "radio") { if (el.checked) o[el.name] = el.value; } else o[el.name] = el.type === "checkbox" ? el.checked : el.value; });
    return o;
  }
  function onSubmit(sel, fn) {
    $$(sel).forEach((f) => f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const b = $("button[type=submit]", f); if (b) b.disabled = true;
      try { await fn(formData(f), f); } catch (err) { toast("Não foi possível salvar: " + err.message); } finally { if (b) b.disabled = false; }
    }));
  }
  function killCharts() { charts.forEach((c) => c.destroy()); charts = []; }
  function baseOpts() {
    const text = css("--muted"), grid = css("--line");
    return { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: text, boxWidth: 12 } } },
      scales: { x: { ticks: { color: text, maxTicksLimit: 6 }, grid: { color: grid } }, y: { ticks: { color: text }, grid: { color: grid } } } };
  }
  const mkChart = (id, cfg) => { const c = new Chart($(id), cfg); charts.push(c); return c; };
  const shortDates = (ds) => ds.map((x) => x.slice(8, 10) + "/" + x.slice(5, 7));
  const periodBar = (cur) => `<div class="filters" role="group" aria-label="Período">${[7, 30, 90, 180, 365].map((d) => `<button data-days="${d}" class="${d === cur ? "on" : ""}">${d} dias</button>`).join("")}</div>`;

  // ---------------------------------------------------------------- tema
  function applyTheme(t) { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }
  try { applyTheme(localStorage.getItem("hm-theme")); } catch (_) {}
  $("#btn-theme").onclick = () => {
    const next = getComputedStyle(document.documentElement).colorScheme.includes("dark") ? "light" : "dark";
    try { localStorage.setItem("hm-theme", next); } catch (_) {}
    applyTheme(next); render();
  };

  // ---------------------------------------------------------------- navegação
  const TITLES = { hoje: "Hoje", saude: "Saúde", treino: "Treino", nutricao: "Nutrição", mais: "Mais", metabolismo: "Metabolismo",
    composicao: "Composição corporal", suplementos: "Suplementação", exames: "Exames laboratoriais", evolucao: "Evolução",
    recomendacoes: "Recomendações", relatorios: "Relatórios", config: "Configurações", garmin: "Conferência Garmin" };
  function go(tab, sub = null) { state.tab = tab; state.sub = sub; render(); window.scrollTo(0, 0); }
  $$("nav.tabs button").forEach((b) => (b.onclick = () => go(b.dataset.tab)));
  $("#btn-back").onclick = () => go("mais");
  async function render() {
    killCharts();
    const key = state.sub || state.tab;
    $$("nav.tabs button").forEach((x) => x.classList.toggle("on", x.dataset.tab === state.tab));
    $("#title").textContent = TITLES[key];
    $("#btn-back").hidden = !state.sub;
    view.innerHTML = `<p class="empty">Carregando…</p>`;
    try { await VIEWS[key](); } catch (e) { view.innerHTML = `<div class="card"><p>Não foi possível carregar esta área: ${esc(e.message)}</p><button class="secondary" id="retry">Tentar de novo</button></div>`; $("#retry").onclick = render; }
  }

  // ---------------------------------------------------------------- HOJE
  let SYMS = [];
  async function vHoje() {
    if (!SYMS.length) SYMS = (await api("/api/seguranca/triagem")).sintomas;
    const [h, cal] = await Promise.all([api("/api/home"), api("/api/calibracao")]);
    $("#demo-banner").hidden = !h.demo;
    $("#subtitle").textContent = `${dateBR(h.dia)} · sincronizado ${dtBR(h.ultima_sincronizacao)}`;
    const rec = h.itens[0], p = h.prontidao, q = h.qualidade_dados, s = h.sessao, n = h.nutricao;
    const lvl = p.recuperacao_objetiva, sg = h.seguranca || { faltam: [], bloqueios: {} };
    const trBloq = sg.bloqueios.treino_plano, nuBloq = sg.bloqueios.metas_nutricionais || sg.bloqueios.metas_peso;
    const sessHtml = s.tipo === "forca" ? `<ul>${s.sessao.exercicios.map((e) => `<li>${esc(e.exercicio)} — ${e.series}×${e.reps[0]}–${e.reps[1]}, RIR ${e.rir}</li>`).join("")}</ul>
        <p class="small muted">~${s.sessao.duracao_min} min · ${esc(s.semana || "")}</p>${(s.ajustes || []).map((a) => `<p class="notice">${esc(a)}</p>`).join("")}`
      : s.tipo === "cardio" ? `<p>${esc(s.cardio.duracao_min)} min · ${esc(s.cardio.intensidade)}</p>${s.cardio.ajuste ? `<p class="notice">${esc(s.cardio.ajuste)}</p>` : ""}`
      : `<p>${esc(s.texto || "")}</p>`;
    const macro = (lbl, cur, tgt, unit) => { const pct = tgt ? Math.min(130, 100 * cur / tgt) : 0;
      return `<div class="row" style="border:0;padding:2px 0"><span>${lbl}</span><span class="r">${nf(cur)} / ${nf(tgt)} ${unit}</span></div><div class="bar ${pct > 108 ? "over" : ""}"><i style="width:${Math.min(100, pct)}%"></i></div>`; };
    view.innerHTML = `
      ${p.urgente ? `<div class="card alert urgent" role="alert"><h3>Procure atendimento</h3><p>${esc(URGENT_TXT)}</p></div>` : ""}
      ${calibCard(cal)}
      ${sg.faltam && sg.faltam.length ? blockCard("Triagem de saúde pendente", "Sem a triagem, treino, metas e suplementos personalizados ficam suspensos.", sg.faltam) : ""}
      ${h.alertas.map((a) => `<div class="card alert"><h3>${esc(a.title)}</h3><p>${esc(a.message)}</p><button class="secondary" data-ack="${a.id}">Entendi</button></div>`).join("")}
      <div class="card"><div class="recovery" style="display:flex"><div class="dot ${lvl}" aria-hidden="true"></div><div>
        <h3>${esc(rec.o_que_fazer)}</h3><p>${esc(p.sinais[0] || "Nenhum sinal desfavorável.")}</p></div></div>
        <div class="dots" aria-label="Recuperação nos últimos 7 dias">${h.tendencia_recuperacao.map((d) => `<i class="${d.status}" title="${dateBR(d.dia)}: ${d.status}"></i>`).join("")}</div>
        <p class="small muted">Últimos 7 dias · ${chip(p.confianca, CONF[p.confianca])} ${chip(q.nivel === "boa" ? "ok" : "media", "Dados: " + q.pontuacao + "/100")}</p>
        <button class="link" data-go="recomendacoes">Ver justificativas e evidências</button></div>
      ${h.experimento ? `<div class="card"><h3 class="sec">Experimento em andamento: ${esc(h.experimento.nome)}</h3><p>Hoje: <strong>${esc(h.experimento.condicao)}</strong></p>
        ${h.experimento.registrado ? `<p class="small muted">Dia já registrado.</p>` : `<div class="hstack"><button class="secondary" data-exp="${h.experimento.id}" data-ok="1">Segui</button><button class="secondary" data-exp="${h.experimento.id}" data-ok="0">Não segui</button></div>`}</div>` : ""}
      <h2>Treino recomendado</h2>
      ${trBloq && s.tipo !== "descanso" ? blockCard("Treino personalizado suspenso", trBloq, sg.faltam)
        : `<div class="card"><h3 class="sec">${esc(s.titulo)}</h3>${sessHtml}<button class="link tap" data-tab="treino">Abrir treino</button></div>`}
      <h2>Nutrição de hoje</h2>
      <div class="card">${n.meta_bloqueada ? `<p class="notice">Meta indisponível: ${esc(nuBloq || "complete o perfil e a triagem de saúde")}. ${sg.faltam && sg.faltam.length ? "Falta: " + esc(sg.faltam.join(", ")) + "." : ""}</p>` : n.meta ? macro("Calorias", n.total.kcal, n.meta.kcal, "kcal") + macro("Proteína", n.total.protein_g, n.meta.proteina_g, "g") + macro("Carboidrato", n.total.carb_g, n.meta.carboidrato_g, "g") + macro("Gordura", n.total.fat_g, n.meta.gordura_g, "g")
        : `<p class="muted">Sem meta confirmada. Abra Nutrição para revisar a proposta.</p>`}
        <form class="f" id="quick-meal"><label>Registrar refeição (ex.: 150 g frango grelhado, 200 g arroz cozido)
          <input name="texto" autocomplete="off" required></label><button class="primary" type="submit">Registrar</button></form></div>
      <h2>Como você está?</h2>
      <div class="card"><form class="f two" id="checkin">
        <label>Recuperação percebida (1–10)<input name="perceived_recovery" type="number" min="1" max="10" step="1" inputmode="numeric"></label>
        <label>Dor muscular (1–5)<input name="soreness" type="number" min="1" max="5" step="1" inputmode="numeric"></label>
        <label>Fadiga (1–5)<input name="fatigue" type="number" min="1" max="5" step="1" inputmode="numeric"></label>
        <label>Estresse percebido (1–5)<input name="perceived_stress" type="number" min="1" max="5" step="1" inputmode="numeric"></label>
        <label>Qualidade do sono (1–5)<input name="sleep_quality" type="number" min="1" max="5" step="1" inputmode="numeric"></label>
        <label>Seguiu a recomendação de ontem?<select name="adherence"><option value="">não informar</option><option value="sim">Sim</option><option value="parcial">Em parte</option><option value="nao">Não</option></select></label>
        <label>Cafeína hoje (mg)<input name="caffeine_mg" type="number" min="0" max="1500" inputmode="numeric"></label>
        <label>Último café (hora)<input name="caffeine_last_time" type="time"></label>
        <label>Álcool (doses)<input name="alcohol_units" type="number" min="0" max="30" inputmode="numeric"></label>
        <label>Água (ml)<input name="water_ml" type="number" min="0" max="10000" step="50" inputmode="numeric"></label>
        <label class="full">Sintomas ou observações<input name="symptoms" maxlength="300" placeholder="opcional"></label>
        <fieldset class="full tri"><legend>Sintomas de hoje</legend>
          ${SYMS.map((x) => `<label class="opt"><input type="checkbox" data-sym="${esc(x.chave)}" data-urg="${x.urgente ? 1 : 0}"><span>${esc(x.texto)}${x.urgente ? " (alerta)" : ""}</span></label>`).join("")}
          <label class="opt"><input type="checkbox" id="sym-none"><span>Não tenho nenhum destes sintomas hoje</span></label>
          <p class="small muted">Sem marcar nada, a triagem do dia fica como não respondida (e o sistema não sugere aumento de carga nem HIIT).</p>
          <div id="urgent-warn" class="card alert urgent" role="alert" hidden><h3>Procure atendimento</h3><p>${esc(URGENT_TXT)}</p></div></fieldset>
        <button class="primary full" type="submit">Salvar check-in</button></form>
        <p class="small muted">Sua percepção entra na decisão do treino junto com os indicadores do relógio.</p></div>
      ${h.proximas_acoes.length ? `<h2>Próximas ações</h2><div class="card"><ul>${h.proximas_acoes.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></div>` : ""}
      ${h.objetivos.length ? `<h2>Objetivos</h2><div class="card">${h.objetivos.map(goalRow).join("")}</div>` : ""}`;
    const ck = await api("/api/checkin");
    Object.entries(ck).forEach(([k, v]) => { const el = $(`#checkin [name=${k}]`); if (el && v != null && k !== "adherence") el.value = v; });
    if (ck.adherence != null) $("#checkin [name=adherence]").value = ck.adherence >= 1 ? "sim" : ck.adherence > 0 ? "parcial" : "nao";
    const syms = $$("#checkin [data-sym]"), none = $("#sym-none"), warn = $("#urgent-warn");
    const updWarn = () => { warn.hidden = !syms.some((c) => c.checked && c.dataset.urg === "1"); };   // local: não depende do servidor
    try { const tri = ck.triagem ? JSON.parse(ck.triagem) : null;
      if (tri) { syms.forEach((c) => { c.checked = tri[c.dataset.sym] === true; }); none.checked = !syms.some((c) => c.checked); } } catch (_) {}
    syms.forEach((c) => c.addEventListener("change", () => { if (c.checked) none.checked = false; updWarn(); }));
    none.addEventListener("change", () => { if (none.checked) syms.forEach((c) => { c.checked = false; }); updWarn(); });
    updWarn();
    onSubmit("#checkin", async (d) => {
      const marked = syms.some((c) => c.checked);
      if (marked || none.checked) d.triagem = Object.fromEntries(syms.map((c) => [c.dataset.sym, c.checked]));
      const r = await post("/api/checkin", d);
      if (r.urgente) { warn.hidden = false; toast("Sintoma de alerta: procure atendimento (SAMU 192)"); }
      else { toast("Check-in salvo"); render(); } });
    onSubmit("#quick-meal", async (d) => { const r = await post("/api/refeicoes", d); toast(`Registrado: ${nf(r.total.kcal)} kcal` + (r.nao_encontrados.length ? ` (não reconhecido: ${r.nao_encontrados.join(", ")})` : "")); render(); });
    $$("[data-ack]").forEach((b) => (b.onclick = async () => { await post(`/api/alerts/${b.dataset.ack}/ack`); render(); }));
    $$("[data-exp]").forEach((b) => (b.onclick = async () => { await post(`/api/experimentos/${b.dataset.exp}/dia`, { seguiu: b.dataset.ok === "1" }); render(); }));
    wireNav();
  }
  function wireNav() {
    $$("[data-go]", view).forEach((b) => (b.onclick = () => go("mais", b.dataset.go)));
    $$("[data-tab]", view).forEach((b) => (b.onclick = () => go(b.dataset.tab)));
  }
  const goalRow = (g) => `<div class="row"><span>${esc(g.rotulo)}<small>${g.atual ?? "—"} → ${nf(g.alvo, 1)} ${esc(g.unidade)} até ${dateBR(g.prazo)}</small>
    <div class="bar" style="min-width:160px"><i style="width:${Math.max(0, Math.min(100, g.progresso_pct || 0))}%"></i></div></span>
    <span class="r">${g.progresso_pct != null ? g.progresso_pct + "%" : ""}<small>${esc(g.situacao)}</small></span></div>`;

  // ---------------------------------------------------------------- SAÚDE
  const AREAS = {
    recuperacao: { nome: "Recuperação", keys: ["hrv_last_night", "bb_max", "training_readiness", "stress_avg", "bb_min", "sleep_stress_avg", "recovery_time_h"] },
    sono: { nome: "Sono", keys: ["sleep_seconds", "sleep_score", "deep_seconds", "rem_seconds", "light_seconds", "awake_seconds", "awake_count", "sleep_resp_avg", "sleep_spo2_avg", "skin_temp_dev"] },
    coracao: { nome: "Coração", keys: ["resting_hr", "hrv_last_night", "hrv_weekly_avg", "vo2max", "min_hr", "max_hr", "spo2_avg"] },
  };
  async function vSaude() {
    const A = AREAS[state.area];
    if (!A.keys.includes(state.metric)) state.metric = A.keys[0];
    $("#subtitle").textContent = "Comparado ao seu próprio histórico";
    const [o, ins] = await Promise.all([api("/api/overview"), api(`/api/insights?days=${Math.max(state.days, 14)}`)]);
    const kpis = A.keys.filter((k) => o.avaliacoes[k]).map((k) => { const a = o.avaliacoes[k];
      const cls = a.leitura === "favoravel" ? "fav" : a.leitura === "desfavoravel" ? "desf" : "";
      const cmp = a.sentido === "sem_base" ? "construindo linha de base" : `${a.sentido === "acima" ? "↑ acima" : a.sentido === "abaixo" ? "↓ abaixo" : "= dentro"} · mediana ${fmtVal(k, a.mediana)}`;
      return `<div class="kpi ${cls}"><div class="lbl">${esc(a.rotulo)}</div><div class="val">${fmtVal(k, a.valor)}</div><div class="cmp">${esc(cmp)}</div>${a.padrao !== "estavel" ? `<span class="tag ${a.padrao}">${a.padrao}</span>` : ""}</div>`; }).join("");
    const tr = ins.frases_tendencia.filter((s) => A.keys.some((k) => s.startsWith(META.metricas[k].rotulo)));
    view.innerHTML = `<div class="seg" role="tablist">${Object.entries(AREAS).map(([k, v]) => `<button data-area="${k}" class="${k === state.area ? "on" : ""}">${v.nome}</button>`).join("")}</div>
      ${periodBar(state.days)}
      <div class="chips">${A.keys.map((k) => `<button data-metric="${k}" class="${k === state.metric ? "on" : ""}">${esc(META.metricas[k].rotulo)}</button>`).join("")}</div>
      <div class="card chart-card"><div class="chart-wrap"><canvas id="c-main"></canvas></div><p id="chart-note" class="muted small"></p></div>
      ${state.area === "sono" ? `<h2>Fases do sono</h2><div class="card chart-card"><div class="chart-wrap short"><canvas id="c-sleep"></canvas></div></div>` : ""}
      <h2>Hoje (${dateBR(o.dia)})</h2><div class="grid">${kpis || `<p class="muted">Sem dados para hoje.</p>`}</div>
      <h2>Tendências</h2><div class="card"><ul>${(tr.length ? tr : ["Nenhuma tendência estatisticamente consistente no período."]).map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
        ${ins.regularidade_sono && state.area === "sono" ? `<p class="small">Horário mediano de dormir: <strong>${esc(ins.regularidade_sono.horario_mediano)}</strong> (±${ins.regularidade_sono.desvio_min} min).</p>` : ""}</div>
      <h2>Análise estatística do indicador</h2><div class="card" id="stat"><p class="muted">Calculando…</p></div>
      ${state.area === "recuperacao" ? `<h2>Associações observadas</h2><div class="card"><ul>${(ins.frases_associacao.length ? ins.frases_associacao : ["Nenhuma associação moderada ou forte (ou dados insuficientes)."]).map((s) => `<li>${esc(s)}</li>`).join("")}</ul><p class="small muted">Associação não implica causa.</p></div>` : ""}`;
    $$("[data-area]").forEach((b) => (b.onclick = () => { state.area = b.dataset.area; render(); }));
    $$("[data-days]").forEach((b) => (b.onclick = () => { state.days = +b.dataset.days; render(); }));
    $$("[data-metric]").forEach((b) => (b.onclick = () => { state.metric = b.dataset.metric; render(); }));
    const k = state.metric, m = META.metricas[k];
    const d = await api(`/api/series?keys=${k}&days=${state.days}`);
    const labels = shortDates(d.datas), b = d.base[k];
    const ds = [{ label: m.rotulo, data: d.series[k], borderColor: css("--accent"), backgroundColor: css("--accent"), pointRadius: state.days > 90 ? 0 : 2, tension: 0.2 },
      { label: "Média móvel 7 dias", data: d.series[k + "__mm7"], borderColor: css("--muted"), borderDash: [5, 4], pointRadius: 0, tension: 0.3 }];
    if (b) { ds.push({ label: "_inf", data: labels.map(() => b.faixa[0]), borderColor: "transparent", pointRadius: 0 });
      ds.push({ label: "Faixa usual", data: labels.map(() => b.faixa[1]), borderColor: "transparent", backgroundColor: "rgba(59,130,246,0.13)", pointRadius: 0, fill: "-1" }); }
    const cfg = { type: "line", data: { labels, datasets: ds }, options: baseOpts() };
    cfg.options.plugins.legend.labels.filter = (i) => !i.text.startsWith("_");
    mkChart("#c-main", cfg);
    const n = d.series[k].filter((v) => v != null).length;
    $("#chart-note").textContent = b ? `Faixa usual: ${fmtVal(k, b.faixa[0])} a ${fmtVal(k, b.faixa[1])} (mediana ${fmtVal(k, b.mediana)}, ${b.n} dias). ${n} dias com dado.` : `${n} dias com dado. Linha de base em construção.`;
    if (state.area === "sono") {
      const keys = "deep_seconds,light_seconds,rem_seconds,awake_seconds";
      const sd = await api(`/api/series?keys=${keys}&days=${Math.min(state.days, 90)}`);
      const col = { deep_seconds: "#1e3a8a", light_seconds: "#60a5fa", rem_seconds: "#a78bfa", awake_seconds: "#f59e0b" };
      const opts = baseOpts(); opts.scales.x.stacked = true; opts.scales.y.stacked = true;
      mkChart("#c-sleep", { type: "bar", options: opts, data: { labels: shortDates(sd.datas), datasets: keys.split(",").map((kk) => ({ label: META.metricas[kk].rotulo, backgroundColor: col[kk], stack: "s",
        data: sd.series[kk].map((v) => v == null ? null : (kk === "awake_seconds" ? v / 60 : v)) })) } });
    }
    const st = await api(`/api/estatistica?key=${k}`);
    const base = Object.entries(st.bases).map(([w, x]) => `<div class="row"><span>Base de ${w} dias<small>${x.n} dias com dado${x.confiavel ? "" : " · ainda insuficiente"}</small></span><span class="r">${x.mediana != null ? fmtVal(k, x.mediana) : "—"}</span></div>`).join("");
    const wd = st.dia_da_semana, f = st.previsao;
    $("#stat").innerHTML = base +
      `<p class="small">${wd ? (wd.relevante ? `Há efeito de dia da semana (amplitude ${nf(wd.amplitude_z, 1)} desvios): ${Object.entries(wd.desvios).map(([d2, v]) => `${d2} ${v > 0 ? "+" : ""}${nf(v, 1)}`).join(", ")}.` : "Sem efeito relevante de dia da semana.") : "Dados insuficientes para avaliar dia da semana (mínimo 35 dias)."}</p>
       <p class="small">${st.mudanca ? `Mudança de nível detectada (${st.mudanca.direcao}) com início estimado em ${dateBR(st.mudanca.inicio)}.` : "Nenhuma mudança de nível detectada nos últimos 60 dias."}</p>
       <p class="small">${f ? `Previsão de 1 dia (teste em ${f.n_teste} dias não usados na escolha): modelo escolhido “${esc(f.escolhido)}”, erro ${nf(f.ganho_pct_teste, 1)}% menor que repetir o último valor.` : "Dados insuficientes para avaliar modelos de previsão (mínimo 42 dias)."}</p>
       <p class="small muted">${esc(st.nota)}</p>`;
  }

  // ---------------------------------------------------------------- TREINO
  async function vTreino() {
    const [t, cal] = await Promise.all([api("/api/treino"), api("/api/calibracao")]);
    $("#subtitle").textContent = t.ativo ? `Plano ativo · ${t.ativo.dias_forca}×/semana` : "Sem plano ativo";
    const h = t.hoje, p = h.prontidao;
    const exHtml = (e, logging) => `<div class="ex"><h4>${esc(e.exercicio)}</h4>
      <div class="meta">${e.series} ${e.series === 1 ? "série" : "séries"} × ${e.reps[0]}–${e.reps[1]} reps · RIR ${e.rir} · descanso ${e.descanso_s}s</div>
      ${e.progressao ? `<div class="small"><strong>Carga:</strong> ${esc(e.progressao.texto)}</div>` : ""}
      <details><summary>Técnica e alternativas</summary><div class="inner"><p>${esc(e.tecnica)}</p>${e.alternativas.length ? `<p>Alternativas: ${esc(e.alternativas.join("; "))}</p>` : ""}</div></details>
      ${logging ? `<form class="f two logset" data-ex="${esc(e.chave)}"><label>Carga (kg)<input name="carga_kg" type="number" step="0.5" min="0" inputmode="decimal" required value="${e.progressao && e.progressao.carga_kg != null ? e.progressao.carga_kg : ""}"></label>
        <label>Repetições<input name="reps" type="number" min="1" max="100" inputmode="numeric" required></label>
        <label>RIR (opcional)<input name="rir" type="number" min="0" max="10" inputmode="numeric"></label>
        <button class="secondary" type="submit" style="align-self:end">Registrar série</button></form>` : ""}</div>`;
    let today = "";
    if (h.tipo === "forca") today = `<p class="small muted">${esc(h.semana || "")} · ~${h.sessao.duracao_min} min · ${esc(h.sessao.objetivo)}</p><p class="small">Aquecimento: ${esc(h.sessao.aquecimento)}</p>
      ${(h.ajustes || []).map((a) => `<p class="notice">${esc(a)}</p>`).join("")}${h.sessao.exercicios.map((e) => exHtml(e, true)).join("")}`;
    else if (h.tipo === "cardio") today = `<p>${h.cardio.duracao_min} min · ${esc(h.cardio.intensidade)}</p><p class="small muted">${esc(h.cardio.objetivo)}</p>${h.cardio.ajuste ? `<p class="notice">${esc(h.cardio.ajuste)}</p>` : ""}`;
    else today = `<p>${esc(h.texto || "")}</p>`;
    const planHtml = (pl) => pl.sessoes.map((s) => `<details><summary>${esc(s.nome)} · ~${s.duracao_min} min</summary><div class="inner">${s.exercicios.map((e) => exHtml(e, false)).join("")}</div></details>`).join("") +
      `<p class="small"><strong>Cardio:</strong> ${pl.cardio.map((c) => `${esc(c.tipo)} ${c.duracao_min} min`).join(" · ") || "—"}</p>
       <p class="small"><strong>Periodização:</strong> ${pl.mesociclo.map((m) => esc(m.nome)).join(" → ")}</p>
       <p class="small"><strong>Progressão:</strong> ${esc(pl.progressao)}</p><p class="small">${esc(pl.mobilidade)}</p>
       <p class="small"><strong>Séries semanais por grupo:</strong> ${Object.entries(pl.series_semanais_por_grupo).map(([g, n]) => `${esc(g)} ${n}`).join(", ")}</p>
       ${pl.observacoes.map((o) => `<p class="notice">${esc(o)}</p>`).join("")}`;
    view.innerHTML = `
      ${p.urgente ? `<div class="card alert urgent" role="alert"><h3>Procure atendimento</h3><p>${esc(URGENT_TXT)}</p></div>` : ""}
      ${calibCard(cal)}
      ${t.bloqueio ? blockCard("Plano de treino suspenso", t.bloqueio, t.faltam) : ""}
      ${t.faltam.length && !t.bloqueio ? `<div class="notice">Para gerar um plano, complete o perfil: ${esc(t.faltam.join(", "))}. <button class="link" data-go="config">Abrir perfil</button></div>` : ""}
      ${t.proposto ? `<div class="card alert"><h3>Plano proposto aguardando sua confirmação</h3><p class="small muted">Motivo: ${esc(t.proposto.motivo || "")}</p>${planHtml(t.proposto)}
        <button class="primary" id="confirm-plan" data-id="${t.proposto.id}">Confirmar e ativar este plano</button></div>` : ""}
      <h2>Hoje</h2><div class="card"><h3 class="sec">${esc(h.titulo)}</h3>
        <p>${chip(p.confianca, CONF[p.confianca])} ${chip(p.nivel === "normal" ? "ok" : "media", "Prontidão: " + p.nivel)}</p>
        <details><summary>Como a decisão foi tomada</summary><div class="inner"><ul>${(p.sinais.length ? p.sinais : ["Nenhum sinal desfavorável."]).map((s) => `<li>${esc(s)}</li>`).join("")}</ul><p>${esc(p.regra)}</p></div></details>${today}
        <p class="notice">${esc(h.seguranca)}</p></div>
      <h2>Registrar sessão</h2><div class="card"><form class="f two" id="log-session">
        <label>Tipo<select name="tipo"><option value="forca">Força</option><option value="cardio">Cardio</option><option value="mobilidade">Mobilidade</option></select></label>
        <label>Duração (min)<input name="duracao_min" type="number" min="1" max="400" inputmode="numeric"></label>
        <label>Esforço percebido (RPE 1–10)<input name="rpe" type="number" min="1" max="10" inputmode="numeric"></label>
        <label>Período<select name="periodo"><option value="manhã">Manhã</option><option value="tarde">Tarde</option><option value="noite">Noite</option></select></label>
        <label class="full">Segui a recomendação de hoje? <select name="seguiu"><option value="">não informar</option><option value="1">Sim</option><option value="0">Não</option></select></label>
        <button class="primary full" type="submit">Salvar sessão</button></form></div>
      ${t.ativo ? `<h2>Plano ativo</h2><div class="card">${planHtml(t.ativo)}</div>` : ""}
      <div class="card"><button class="secondary" id="propose-plan" ${t.faltam.length || t.bloqueio ? "disabled" : ""}>${t.ativo ? "Gerar nova proposta de plano" : "Gerar proposta de plano"}</button>
        <p class="small muted">Qualquer mudança de plano só vale depois que você confirmar.</p></div>
      <h2>Zonas de frequência cardíaca</h2><div class="card">${t.zonas.disponivel ? `<div class="tbl"><table><thead><tr><th>Zona</th><th>Faixa</th><th>Uso</th></tr></thead><tbody>${t.zonas.zonas.map((z) => `<tr><td>${z.zona}</td><td>${z.de}–${z.ate} bpm</td><td>${esc(z.uso)}</td></tr>`).join("")}</tbody></table></div>
        ${t.zonas.aviso ? `<p class="notice">${esc(t.zonas.aviso)}</p>` : ""}<p class="small muted">FC máx. ${t.zonas.fc_max} bpm (${esc(t.zonas.origem)}); repouso ${t.zonas.fc_repouso} bpm; ${esc(t.zonas.metodo)}.</p>` : `<p class="muted">Informe a data de nascimento no perfil.</p>`}</div>
      <h2>Últimos 7 dias</h2><div class="card"><p>${t.semana.sessoes.length} sessão(ões) · volume ${nf(t.semana.volume_total_kg)} kg</p>
        <p class="small">${Object.entries(t.semana.series_por_grupo).map(([g, n]) => `${esc(g)}: ${n} séries`).join(" · ") || "Nenhuma série registrada."}</p>
        <form class="f" id="hist"><label>Evolução de um exercício<select name="exercicio">${(t.ativo ? [...new Map(t.ativo.sessoes.flatMap((s) => s.exercicios).map((e) => [e.chave, e.exercicio])).entries()] : []).map(([k, n]) => `<option value="${esc(k)}">${esc(n)}</option>`).join("")}</select></label><button class="secondary" type="submit">Ver evolução</button></form>
        <div id="hist-out"></div></div>
      <div class="card"><h3 class="sec">Limites de segurança do sistema</h3><p class="small">Séries semanais por grupo: até ${Object.values(t.limites.max_series_grupo_semana).join("/")} (iniciante/intermediário/avançado) · HIIT: até ${t.limites.max_hiit_semana}×/semana · aumento de carga por progressão: até ${t.limites.max_aumento_carga_pct}% · no máximo ${t.limites.max_sessoes_semana} sessões por semana (força + cardio). Esses limites não mudam sozinhos.</p>${evid(t.evidencias)}</div>`;
    wireNav();
    const conf = $("#confirm-plan"); if (conf) conf.onclick = async () => { await post(`/api/treino/confirmar/${conf.dataset.id}`); toast("Plano ativado"); render(); };
    $("#propose-plan").onclick = async () => { try { await post("/api/treino/propor", {}); toast("Proposta criada: revise e confirme"); render(); } catch (e) { toast(e.message); } };
    $$(".logset").forEach((f) => f.addEventListener("submit", async (e) => { e.preventDefault(); const d = formData(f);
      try { await post("/api/treino/serie", { ...d, exercicio: f.dataset.ex }); toast("Série registrada"); f.querySelector("[name=reps]").value = ""; } catch (err) { toast(err.message); } }));
    onSubmit("#log-session", async (d) => { await post("/api/treino/sessao", { ...d, sessao: h.titulo, seguiu_recomendacao: d.seguiu === "" ? null : d.seguiu === "1" }); toast("Sessão salva"); });
    onSubmit("#hist", async (d) => { const r = await api(`/api/treino/historico?exercicio=${encodeURIComponent(d.exercicio)}`);
      $("#hist-out").innerHTML = r.historico.length ? `<div class="chart-wrap short"><canvas id="c-hist"></canvas></div><p class="small muted">${esc(r.nota)}</p>` : `<p class="muted">Sem registros deste exercício.</p>`;
      if (r.historico.length) mkChart("#c-hist", { type: "line", options: baseOpts(), data: { labels: shortDates(r.historico.map((x) => x.date)), datasets: [
        { label: "1RM estimado (kg)", data: r.historico.map((x) => Math.round(x.e1rm * 10) / 10), borderColor: css("--accent"), tension: 0.2 },
        { label: "Carga máxima (kg)", data: r.historico.map((x) => x.carga), borderColor: css("--muted"), borderDash: [5, 4], tension: 0.2 }] } }); });
  }

  // ---------------------------------------------------------------- NUTRIÇÃO
  async function vNutricao() {
    const n = await api("/api/nutricao");
    const s = n.resumo, rev = n.revisao, c = rev.calculada || {};
    $("#subtitle").textContent = s.meta ? `Meta: ${nf(s.meta.kcal)} kcal · ${s.meta.proteina_g} g proteína` : "Sem meta confirmada";
    const tgt = (t) => `<div class="row"><span>Calorias</span><span class="r">${nf(t.kcal)} kcal</span></div>
      <div class="row"><span>Proteína<small>${t.proteina_faixa_g_kg[0]}–${t.proteina_faixa_g_kg[1]} g/kg na literatura</small></span><span class="r">${t.proteina_g} g</span></div>
      <div class="row"><span>Carboidrato</span><span class="r">${t.carboidrato_g} g</span></div><div class="row"><span>Gordura</span><span class="r">${t.gordura_g} g</span></div>
      <div class="row"><span>Fibra</span><span class="r">${t.fibra_g == null ? "sem meta (evidência não verificada)" : t.fibra_g + " g"}</span></div><div class="row"><span>Água (referência geral)</span><span class="r">${t.agua_ml == null ? "sem meta (evidência não verificada)" : nf(t.agua_ml) + " ml"}</span></div>
      <p class="small">${esc(t.por_refeicao.texto)}. Balanço: ${t.balanco_kcal > 0 ? "+" : ""}${nf(t.balanco_kcal)} kcal sobre um gasto estimado de ${nf(t.tdee.kcal)} kcal (${nf(t.tdee.intervalo[0])}–${nf(t.tdee.intervalo[1])}).</p>
      ${t.observacoes.map((o) => `<p class="notice">${esc(o)}</p>`).join("")}<p class="small muted">${esc(t.limitacoes)}</p>`;
    view.innerHTML = `
      ${rev.estado === "indisponivel" ? blockCard("Meta nutricional indisponível", c.bloqueio || (c.faltam && c.faltam.length ? "Faltam informações para calcular a meta." : "Metas não calculadas no momento."), [...(c.faltam || []), ...(c.evidencia_pendente || [])]) : ""}
      ${rev.estado === "proposta" ? `<div class="card alert"><h3>Proposta de meta aguardando confirmação</h3><p class="small">${esc(rev.motivo)}</p>${tgt(c)}
        <button class="primary" id="confirm-meta">Confirmar esta meta</button></div>` : ""}
      ${s.meta && rev.estado !== "proposta" ? `<h2>Meta em vigor</h2><div class="card">${tgt(s.meta)}${chip(s.meta.confianca, CONF[s.meta.confianca])}</div>` : ""}
      <h2>Refeições de ${dateBR(s.dia)}</h2><div class="card">
        ${s.refeicoes.length ? s.refeicoes.map((m) => `<div class="row"><span>${esc(m.description)}<small>${esc(m.time || "")} P ${nf(m.protein_g)} · C ${nf(m.carb_g)} · G ${nf(m.fat_g)}${m.uncertainty_pct ? ` · incerteza ±${nf(m.uncertainty_pct)}%` : ""}</small></span>
          <span class="r">${nf(m.kcal)} kcal<br><button class="danger" data-del="${m.id}">remover</button></span></div>`).join("") : `<p class="empty">Nenhuma refeição registrada hoje.</p>`}
        <div class="row"><strong>Total</strong><span class="r"><strong>${nf(s.total.kcal)} kcal</strong><small>P ${nf(s.total.protein_g)} · C ${nf(s.total.carb_g)} · G ${nf(s.total.fat_g)} · fibra ${nf(s.total.fiber_g)}</small></span></div></div>
      <h2>Registrar por texto</h2><div class="card">
        ${n.alimentos_na_base ? "" : `<p class="notice">A base de alimentos está vazia. No servidor, rode <code>healthmon alimentos baixar-taco</code> para importar a tabela TACO, ou use o registro manual abaixo.</p>`}
        <form class="f" id="meal-text"><label>O que você comeu, com quantidades<textarea name="texto" rows="2" placeholder="150 g frango grelhado, 200 g arroz cozido, 1 banana" required></textarea></label>
        <div class="hstack"><button class="secondary" type="button" id="preview">Pré-visualizar</button><button class="primary" type="submit">Registrar</button></div></form><div id="preview-out"></div></div>
      <h2>Registro manual</h2><div class="card"><form class="f two" id="meal-manual">
        <label class="full">Descrição<input name="descricao" required maxlength="200"></label>
        <label>Calorias<input name="kcal" type="number" min="0" max="6000" inputmode="numeric" required></label><label>Proteína (g)<input name="proteina_g" type="number" min="0" step="0.1" inputmode="decimal"></label>
        <label>Carboidrato (g)<input name="carboidrato_g" type="number" min="0" step="0.1" inputmode="decimal"></label><label>Gordura (g)<input name="gordura_g" type="number" min="0" step="0.1" inputmode="decimal"></label>
        <button class="primary full" type="submit">Salvar</button></form></div>
      <h2>Qualidade do registro</h2><div class="card"><ul>${(n.consistencia.length ? n.consistencia : ["Sem inconsistências detectadas."]).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        <p class="small muted">Registro por foto: ${esc(n.foto.motivo)}</p></div>
      <div class="card"><h3 class="sec">Limites de segurança</h3><p class="small">Déficit máximo de ${n.limites.deficit_max_pct}% do gasto estimado · perda máxima de ${n.limites.perda_max_pct_semana}% do peso por semana · piso de ${n.limites.piso_kcal.masculino}/${n.limites.piso_kcal.feminino} kcal (homens/mulheres) · ajuste máximo de ${n.limites.ajuste_max_kcal} kcal por revisão. Finalidade informativa; não substitui nutricionista.</p>${evid(n.evidencias)}</div>`;
    wireNav();
    const cm = $("#confirm-meta"); if (cm) cm.onclick = async () => { await post("/api/nutricao/confirmar"); toast("Meta confirmada"); render(); };
    $$("[data-del]").forEach((b) => (b.onclick = async () => { await api(`/api/refeicoes/${b.dataset.del}`, { method: "DELETE" }); render(); }));
    $("#preview").onclick = async () => { const r = await post("/api/refeicoes/previa", { texto: $("#meal-text [name=texto]").value });
      $("#preview-out").innerHTML = (r.itens.length ? `<div class="tbl"><table><thead><tr><th>Alimento reconhecido</th><th class="num">g</th><th class="num">kcal</th></tr></thead><tbody>${r.itens.map((i) => `<tr><td>${esc(i.alimento)}${i.porcao_assumida ? " <em>(porção assumida)</em>" : ""}</td><td class="num">${i.gramas}</td><td class="num">${i.kcal}</td></tr>`).join("")}</tbody></table></div>
        <p class="small">Total ${nf(r.total.kcal)} kcal · P ${nf(r.total.protein_g)} g · incerteza estimada ±${nf(r.incerteza_pct)}%</p>` : `<p class="muted">Nenhum alimento reconhecido.</p>`) +
        (r.nao_encontrados.length ? `<p class="notice">Não reconhecido: ${esc(r.nao_encontrados.join(", "))}</p>` : ""); };
    onSubmit("#meal-text", async (d) => { await post("/api/refeicoes", d); toast("Refeição registrada"); render(); });
    onSubmit("#meal-manual", async (d) => { await post("/api/refeicoes", d); toast("Refeição registrada"); render(); });
  }

  // ---------------------------------------------------------------- MAIS
  async function vMais() {
    $("#subtitle").textContent = "Todas as áreas";
    const items = [["metabolismo", "Metabolismo", "TMB, gasto diário e incerteza"], ["composicao", "Composição corporal", "Peso, gordura e tendência"],
      ["suplementos", "Suplementação", "Avaliação por evidência"], ["exames", "Exames laboratoriais", "Registro e tendências"],
      ["evolucao", "Evolução", "Objetivos, períodos e experimentos"], ["recomendacoes", "Recomendações", "Plano do dia e histórico"],
      ["relatorios", "Relatórios", "Diário, noturno, semanal, mensal"], ["garmin", "Conferência Garmin", "Compare com o app Garmin Connect"],
      ["config", "Configurações", "Perfil, triagem, backup, sistema"]];
    view.innerHTML = `<div class="menu">${items.map(([k, t, d]) => `<button data-go="${k}"><b>${t}</b><small>${d}</small></button>`).join("")}</div>
      <p class="small muted" style="margin-top:16px">${esc(META.aviso)}</p>`;
    wireNav();
  }
  const rangeBar = (lo, hi, v, min, max) => { const p = (x) => Math.max(0, Math.min(100, 100 * (x - min) / (max - min || 1)));
    return `<div class="range" aria-hidden="true"><i style="left:${p(lo)}%;width:${p(hi) - p(lo)}%"></i><b style="left:${p(v)}%"></b></div>`; };

  async function vMetabolismo() {
    const m = await api("/api/metabolismo");
    $("#subtitle").textContent = "Estimativas, não medições clínicas";
    const e = m.estimativa, b = m.tmb, vias = Object.entries(m.vias);
    const all = vias.flatMap(([, v]) => v.intervalo), mn = Math.min(...all, 1500) - 100, mx = Math.max(...all, 3000) + 100;
    const NOME = { formula: "Fórmula × atividade", garmin: "Calorias do Garmin", adaptativa: "Recalibrada pelos seus dados" };
    view.innerHTML = `<div class="notice">${esc(m.aviso)}</div>
      <div class="card"><h3 class="sec">Gasto diário total estimado</h3>${e ? `<div class="big">${nf(e.kcal)} kcal</div><p class="small">Intervalo plausível: ${nf(e.intervalo[0])}–${nf(e.intervalo[1])} kcal · origem: ${esc(e.origem)}</p>${chip(e.confianca, CONF[e.confianca])}` : `<p class="muted">Complete o perfil (sexo, nascimento, altura) para estimar.</p>`}
        ${m.adaptativa_indisponivel ? `<p class="notice">Recalibração ainda indisponível: ${esc(m.adaptativa_indisponivel)}</p>` : ""}
        ${m.adaptacao ? `<p class="notice"><strong>Hipótese:</strong> ${esc(m.adaptacao.texto)}</p>` : ""}</div>
      <h2>Três estimativas independentes</h2><div class="card">${vias.map(([k, v]) => `<div class="row" style="border:0"><span>${NOME[k]}<small>${esc(v.detalhe)}</small></span><span class="r">${nf(v.kcal)} kcal</span></div>${rangeBar(v.intervalo[0], v.intervalo[1], v.kcal, mn, mx)}${v.aviso ? `<p class="notice">${esc(v.aviso)}</p>` : ""}`).join("") || `<p class="muted">Sem dados suficientes.</p>`}</div>
      <h2>Taxa metabólica basal</h2><div class="card">${b.disponivel ? `<div class="row"><span>Mifflin-St Jeor<small>${b.peso_kg} kg (${esc(b.peso_origem)}), ${b.idade} anos</small></span><span class="r">${nf(b.mifflin)} kcal<small>${nf(b.intervalo[0])}–${nf(b.intervalo[1])}</small></span></div>
        ${b.cunningham ? `<div class="row"><span>Cunningham<small>massa magra ${b.massa_livre_gordura_kg} kg (${esc(b.gordura_origem)})</small></span><span class="r">${nf(b.cunningham)} kcal</span></div><p class="small muted">${esc(b.nota_cunningham)}</p>` : `<p class="small muted">Cunningham exige percentual de gordura registrado em Composição.</p>`}
        <p class="small muted">${esc(b.nota)}</p>` : `<p class="muted">Faltam: ${esc(b.faltam.join(", "))}.</p>`}</div>
      <h2>Balanço e peso</h2><div class="card"><div class="row"><span>Tendência de peso</span><span class="r">${m.peso.suficiente ? `${m.peso.kg_semana > 0 ? "+" : ""}${nf(m.peso.kg_semana, 2)} kg/sem<small>IC95% ${nf(m.peso.kg_semana_ic[0], 2)} a ${nf(m.peso.kg_semana_ic[1], 2)}</small>` : "insuficiente"}</span></div>
        <div class="row"><span>Registro alimentar completo</span><span class="r">${m.registro_alimentar.dias_completos} dias (${nf(m.registro_alimentar.cobertura * 100)}%)</span></div>
        ${m.vias.adaptativa ? `<div class="row"><span>Ingestão média registrada</span><span class="r">${nf(m.vias.adaptativa.ingestao_media)} kcal</span></div>
        <p class="small muted">Energia por kg de variação de peso usada: ${nf(m.vias.adaptativa.densidade.lo)}–${nf(m.vias.adaptativa.densidade.hi)} kcal/kg (${esc(m.vias.adaptativa.densidade.base)}).</p>` : ""}</div>
      <h2>Estratégias com respaldo científico</h2>${m.estrategias.map((s) => `<div class="card"><h3 class="sec">${esc(s.titulo)}</h3><p>${esc(s.texto)}</p>${evid(s.evidencias)}</div>`).join("")}
      <div class="card">${evid(m.evidencias, "Referências dos cálculos")}</div>`;
  }

  async function vComposicao() {
    const c = await api(`/api/composicao?days=${state.days < 30 ? 30 : state.days}`);
    const t = c.tendencia, last = c.medidas[c.medidas.length - 1];
    $("#subtitle").textContent = last ? `Última pesagem: ${nf(last.weight_kg, 1)} kg em ${dateBR(last.date)}` : "Sem pesagens";
    view.innerHTML = `${periodBar(state.days < 30 ? 30 : state.days).replace('data-days="7"', 'data-days="7" hidden')}
      <div class="card chart-card"><div class="chart-wrap"><canvas id="c-w"></canvas></div><p class="small muted">${esc(c.nota)}</p></div>
      <div class="card"><div class="row"><span>Peso suavizado</span><span class="r">${t.suavizado_kg != null ? nf(t.suavizado_kg, 1) + " kg" : "—"}</span></div>
        <div class="row"><span>Tendência (até 6 semanas)</span><span class="r">${t.suficiente ? `${t.kg_semana > 0 ? "+" : ""}${nf(t.kg_semana, 2)} kg/sem (${nf(t.pct_semana, 2)}%)<small>IC95% ${nf(t.kg_semana_ic[0], 2)} a ${nf(t.kg_semana_ic[1], 2)}</small>` : "insuficiente"}</span></div>
        ${t.suficiente ? `<div class="row"><span>Oscilação diária típica</span><span class="r">±${nf(t.ruido_diario_kg, 1)} kg</span></div>` : `<p class="small muted">${esc(t.motivo || "")}</p>`}</div>
      <h2>Registrar medida</h2><div class="card"><form class="f two" id="weight"><label>Peso (kg)<input name="peso_kg" type="number" step="0.1" min="30" max="300" inputmode="decimal" required></label>
        <label>Data<input name="dia" type="date" value="${todayISO()}"></label><label>Gordura (%) opcional<input name="gordura_pct" type="number" step="0.1" min="3" max="60" inputmode="decimal"></label>
        <label>Cintura (cm) opcional<input name="cintura_cm" type="number" step="0.5" min="40" max="200" inputmode="decimal"></label><button class="primary full" type="submit">Salvar</button></form>
        <p class="small muted">Pese-se de manhã, em jejum, nas mesmas condições, 3 ou mais vezes por semana.</p></div>
      <h2>Medidas recentes</h2><div class="card">${c.medidas.slice(-10).reverse().map((m) => `<div class="row"><span>${dateBR(m.date)}<small>${esc(m.source)}</small></span><span class="r">${nf(m.weight_kg, 1)} kg${m.body_fat_pct ? ` · ${nf(m.body_fat_pct, 1)}%` : ""}</span></div>`).join("") || `<p class="empty">Nenhuma medida.</p>`}</div>`;
    $$("[data-days]").forEach((b) => (b.onclick = () => { state.days = +b.dataset.days; render(); }));
    mkChart("#c-w", { type: "line", options: baseOpts(), data: { labels: shortDates(c.datas), datasets: [
      { label: "Pesagens (kg)", data: c.peso, borderColor: "transparent", backgroundColor: css("--muted"), pointRadius: 3, showLine: false },
      { label: "Tendência suavizada", data: c.peso_suavizado, borderColor: css("--accent"), pointRadius: 0, tension: 0.3, spanGaps: true }] } });
    onSubmit("#weight", async (d) => { await post("/api/peso", d); toast("Medida salva"); render(); });
  }

  const SUP = { potencialmente_relevante: "Potencialmente relevante", desnecessario: "Desnecessário", inadequado: "Inadequado para você", requer_avaliacao: "Requer avaliação profissional", dados_insuficientes: "Dados insuficientes", evidencia_nao_verificada: "Evidência não verificada" };
  async function vSuplementos() {
    const s = await api("/api/suplementos");
    $("#subtitle").textContent = "Avaliação informativa por evidência";
    if (!s.disponivel) { view.innerHTML = blockCard("Avaliação de suplementos indisponível", s.texto, s.faltam); wireNav(); return; }
    view.innerHTML = `<div class="notice">${esc(s.aviso)}</div>` + s.itens.map((i) => `<div class="card"><h3 class="sec">${esc(i.nome)}</h3>
      <p>${chip(i.status, SUP[i.status] || i.status)} ${chip("", "Evidência " + i.evidencia)}</p><p>${esc(i.justificativa)}</p>
      ${i.contraindicacoes_aplicaveis.map((c) => `<p class="notice"><strong>Contraindicação:</strong> ${esc(c)}</p>`).join("")}${i.interacoes_aplicaveis.map((c) => `<p class="notice"><strong>Interação:</strong> ${esc(c)}</p>`).join("")}
      <details><summary>Ficha completa</summary><div class="inner"><p><strong>Finalidade:</strong> ${esc(i.finalidade)}</p><p><strong>População estudada:</strong> ${esc(i.populacao)}</p>
        <p><strong>Benefícios esperados:</strong> ${esc(i.beneficios)}</p><p><strong>Doses estudadas na literatura:</strong> ${esc(i.dose)}</p><p><strong>Horário:</strong> ${esc(i.horario)}</p>
        <p><strong>Efeitos adversos:</strong> ${esc(i.adversos)}</p><p><strong>Contraindicações:</strong> ${esc(i.contraindicacoes_gerais.join(" ") || "Nenhuma específica conhecida em adultos saudáveis.")}</p>
        <p><strong>Interações com medicamentos:</strong> ${esc(i.interacoes_gerais.join(" ") || "Nenhuma relevante conhecida.")}</p><p><strong>Sono e recuperação:</strong> ${esc(i.sono)}</p>
        <p><strong>Custo-benefício:</strong> ${esc(i.custo)}</p><p><strong>Avaliação profissional:</strong> ${esc(i.profissional)}</p>${refs(i.evidencias)}</div></details></div>`).join("") +
      `<div class="card"><form class="f" id="sub-q"><label>Consultar outra substância<input name="nome" required maxlength="60"></label><button class="secondary" type="submit">Consultar</button></form><div id="sub-out"></div>${evid(s.evidencias_gerais, "Referências gerais")}</div>`;
    onSubmit("#sub-q", async (d) => { const r = await api(`/api/suplementos/consulta?nome=${encodeURIComponent(d.nome)}`);
      $("#sub-out").innerHTML = r.recusado ? `<p class="notice">${esc(r.motivo)}</p>` : (r.resultados.length ? refs(r.resultados) : `<p class="muted">Nada na base de evidências sobre isso. Ausência de evidência cadastrada não é recomendação.</p>`); });
  }

  const FLAG = { abaixo: "abaixo da referência", acima: "acima da referência", dentro: "dentro da referência", sem_referencia: "sem referência informada" };
  async function vExames() {
    const x = await api("/api/exames");
    $("#subtitle").textContent = "Registro pessoal; não é diagnóstico";
    view.innerHTML = `<div class="notice">${esc(x.aviso)}</div>
      ${x.atencao.length ? `<div class="card alert"><h3>Para levar ao médico</h3><ul>${x.atencao.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></div>` : ""}
      <div class="card">${x.itens.map((i) => `<div class="row"><span>${esc(i.analito)}<small>${dateBR(i.data)}${i.laboratorio ? " · " + esc(i.laboratorio) : ""} · ref. ${i.ref_min ?? "—"}–${i.ref_max ?? "—"} ${esc(i.unidade)}${i.tendencia ? ` · ${i.tendencia.variacao_pct > 0 ? "+" : ""}${nf(i.tendencia.variacao_pct, 1)}% desde ${dateBR(i.tendencia.desde)}` : ""}</small>${i.contexto ? `<small>${esc(i.contexto)}</small>` : ""}</span>
        <span class="r">${i.valor != null ? nf(i.valor, 1) : esc(i.valor_texto)} ${esc(i.unidade)}<br>${chip(i.situacao, FLAG[i.situacao])}</span></div>`).join("") || `<p class="empty">Nenhum exame registrado.</p>`}</div>
      <h2>Adicionar resultado</h2><div class="card"><form class="f two" id="lab"><label class="full">Exame<input name="analito" required maxlength="80" placeholder="ex.: Vitamina D (25-OH)"></label>
        <label>Data da coleta<input name="data" type="date" required value="${todayISO()}"></label><label>Valor<input name="valor" required inputmode="decimal"></label>
        <label>Unidade<input name="unidade" maxlength="20" placeholder="ng/mL"></label><label>Laboratório<input name="laboratorio" maxlength="60"></label>
        <label>Referência mínima<input name="ref_min" inputmode="decimal"></label><label>Referência máxima<input name="ref_max" inputmode="decimal"></label>
        <button class="primary full" type="submit">Salvar</button></form><p class="small muted">Copie valor, unidade e faixa de referência exatamente como aparecem no laudo.</p></div>
      <h2>Guardar laudo original</h2><div class="card"><form class="f" id="lab-file"><label>PDF ou imagem (fica só no seu servidor)<input type="file" name="arquivo" accept=".pdf,.png,.jpg,.jpeg,.heic" required></label><button class="secondary" type="submit">Guardar arquivo</button></form>
        <p class="small muted">A leitura automática de laudos não está implementada; os valores entram pelo formulário.</p></div>`;
    onSubmit("#lab", async (d) => { await post("/api/exames", d); toast("Resultado salvo"); render(); });
    $("#lab-file").addEventListener("submit", async (e) => { e.preventDefault(); const f = $("#lab-file [type=file]").files[0]; if (!f) return;
      try { const r = await api("/api/exames/arquivo", { method: "POST", raw: true, body: f, headers: { "X-Arquivo": encodeURIComponent(f.name), "Content-Type": "application/octet-stream" } }); toast("Arquivo guardado: " + r.arquivo); } catch (err) { toast(err.message); } });
  }

  async function vEvolucao() {
    const [g, cmp, ap] = await Promise.all([api("/api/objetivos"), api(`/api/compare?days=${state.cmp}`), api("/api/aprendizado")]);
    $("#subtitle").textContent = "Objetivos, períodos e aprendizado";
    const expHtml = (e) => `<div class="row"><span>${esc(e.name)}<small>${esc(e.condition_a)} × ${esc(e.condition_b)} · desfecho: ${esc(ap.desfechos[e.outcome])} · ${e.dias_registrados}/${e.dias} dias registrados · ${e.status}</small>
      ${e.analise ? `<small><strong>${esc(e.analise.texto)}</strong> ${esc(e.analise.limitacoes)}</small>` : ""}</span>${e.status === "ativo" ? `<button class="danger" data-end="${e.id}">encerrar</button>` : ""}</div>`;
    view.innerHTML = `<h2>Objetivos</h2><div class="card">${g.objetivos.map((o) => goalRow(o) + `<button class="danger" data-delgoal="${o.id}">remover</button>`).join("") || `<p class="empty">Nenhum objetivo cadastrado.</p>`}
        <details><summary>Cadastrar objetivo</summary><div class="inner"><form class="f two" id="goal"><label class="full">Indicador<select name="metrica">${Object.entries(g.metricas).map(([k, v]) => `<option value="${k}">${esc(v[0])}</option>`).join("")}</select></label>
          <label>Valor-alvo<input name="alvo" type="number" step="0.1" required inputmode="decimal"></label><label>Prazo<input name="prazo" type="date" required></label><button class="primary full" type="submit">Salvar objetivo</button></form></div></details></div>
      <h2>Comparação de períodos</h2><div class="filters">${[[7, "Semana"], [30, "30 dias"], [90, "90 dias"], [180, "180 dias"], [365, "Ano"]].map(([d, l]) => `<button data-cmp="${d}" class="${d === state.cmp ? "on" : ""}">${l}</button>`).join("")}</div>
      <p class="small muted">${dateBR(cmp.atual[0])}–${dateBR(cmp.atual[1])} vs. ${dateBR(cmp.anterior[0])}–${dateBR(cmp.anterior[1])} (médias)</p>
      <div class="card"><div class="tbl"><table><thead><tr><th>Indicador</th><th class="num">Atual</th><th class="num">Anterior</th><th class="num">Var.</th></tr></thead><tbody>${cmp.linhas.map((l) => `<tr><td>${esc(l.rotulo)}</td><td class="num">${fmtVal(l.metrica, l.atual.media)}</td><td class="num">${fmtVal(l.metrica, l.anterior.media)}</td>
        <td class="num ${l.leitura === "favoravel" ? "fav-t" : l.leitura === "desfavoravel" ? "desf-t" : ""}">${l.variacao_pct == null ? "—" : (l.variacao_pct > 0 ? "+" : "") + nf(l.variacao_pct) + "%"}</td></tr>`).join("")}</tbody></table></div></div>
      <h2>Padrões observados em você</h2><div class="card">${ap.padroes.map((p) => `<div class="row"><span>${esc(p.titulo)}<small>${esc(p.texto)}</small></span></div>`).join("")}
        <p class="small muted">Comparações observacionais dos últimos 90 dias; exigem ao menos 6 dias em cada grupo, usam limiar corrigido para 4 comparações e não provam causa.</p></div>
      <h2>Experimentos individuais</h2><div class="card">${ap.experimentos.map(expHtml).join("") || `<p class="empty">Nenhum experimento ainda.</p>`}
        <details><summary>Iniciar experimento</summary><div class="inner"><form class="f" id="exp"><label>O que testar<select name="fator">${Object.entries(ap.fatores).map(([k, v]) => `<option value="${k}">${esc(v[0])}: ${esc(v[1])} × ${esc(v[2])}</option>`).join("")}</select></label>
          <label>O que observar<select name="desfecho">${Object.entries(ap.desfechos).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></label>
          <label>Duração (dias)<input name="dias" type="number" min="20" max="56" value="28"></label><button class="primary" type="submit">Começar amanhã</button></form>
          <p class="small muted">Só mudanças de rotina de baixo risco. O sistema sorteia a ordem das condições em blocos de 2 dias; você marca diariamente se seguiu. Um experimento por vez.</p></div></details></div>`;
    $$("[data-cmp]").forEach((b) => (b.onclick = () => { state.cmp = +b.dataset.cmp; render(); }));
    $$("[data-delgoal]").forEach((b) => (b.onclick = async () => { await api(`/api/objetivos/${b.dataset.delgoal}`, { method: "DELETE" }); render(); }));
    $$("[data-end]").forEach((b) => (b.onclick = async () => { await post(`/api/experimentos/${b.dataset.end}/encerrar`); render(); }));
    onSubmit("#goal", async (d) => { await post("/api/objetivos", d); toast("Objetivo salvo"); render(); });
    onSubmit("#exp", async (d) => { await post("/api/experimentos", d); toast("Experimento criado"); render(); });
  }

  async function vRecomendacoes() {
    const [h, ap] = await Promise.all([api("/api/home"), api("/api/aprendizado")]);
    $("#subtitle").textContent = `Plano de ${dateBR(h.dia)}`;
    const ro = ap.resultado_recomendacoes;
    view.innerHTML = h.itens.map(recCard).join("") + `<h2>Histórico de decisões e resultados</h2>
      <div class="card"><p class="small">${esc(ro.texto)}</p>${ap.decisoes.slice(0, 25).map((d) => `<div class="row"><span>${esc(d.title)}<small>${dateBR(d.date)} · ${esc(d.area)}${d.confidence ? " · confiança " + esc(d.confidence) : ""}</small></span>
        <span class="r">${d.area === "treino" ? (d.followed == null ? `<button class="link" data-dec="${d.id}" data-ok="1">segui</button> <button class="link" data-dec="${d.id}" data-ok="0">não segui</button>` : (d.followed ? "seguida" : "não seguida")) : esc(d.status)}</span></div>`).join("") || `<p class="empty">Sem decisões registradas.</p>`}
        <p class="small muted">Marcar se você seguiu cada recomendação permite ao sistema avaliar se as próprias sugestões estão ajudando.</p></div>`;
    $$("[data-dec]").forEach((b) => (b.onclick = async () => { await post(`/api/decisoes/${b.dataset.dec}`, { seguiu: b.dataset.ok === "1" }); render(); }));
  }

  const KIND = { diario: "Diário", noturno: "Noturno", semanal: "Semanal", mensal: "Mensal" };
  async function vRelatorios() {
    $("#subtitle").textContent = "Gerados automaticamente";
    const list = await api("/api/reports" + (state.kind ? `?kind=${state.kind}` : ""));
    view.innerHTML = `<div class="filters">${[["", "Todos"], ...Object.entries(KIND)].map(([k, l]) => `<button data-kind="${k}" class="${k === state.kind ? "on" : ""}">${l}</button>`).join("")}</div>
      <div id="rl">${list.map((r) => `<button class="report-item" data-rid="${r.id}"><strong>${esc(r.title)}</strong><small>${esc(KIND[r.kind])} · ${esc(r.summary || "")}</small></button>`).join("") || `<p class="empty">Nenhum relatório ainda.</p>`}</div>
      <article id="rv" class="card report" hidden></article>`;
    $$("[data-kind]").forEach((b) => (b.onclick = () => { state.kind = b.dataset.kind; render(); }));
    $$("[data-rid]").forEach((b) => (b.onclick = async () => { const r = await api(`/api/reports/${b.dataset.rid}`);
      $("#rv").innerHTML = `<button class="secondary" id="rb">← Voltar à lista</button>` + r.html; /* HTML gerado e escapado pelo servidor */
      $("#rv").hidden = false; $("#rl").hidden = true; window.scrollTo(0, 0); $("#rb").onclick = render; }));
  }

  async function vConfig() {
    const [p, s, st, kbi, tri, bk, evs] = await Promise.all([api("/api/perfil"), api("/api/sistema"), api("/api/status"), api("/api/kb"),
      api("/api/seguranca/triagem"), api("/api/backup/status"), api("/api/evidencias/resumo")]);
    $("#subtitle").textContent = "Perfil, notificações e sistema";
    const v = p.perfil, opt = (o, cur) => Object.entries(o).map(([k, l]) => `<option value="${k}" ${cur === k ? "selected" : ""}>${esc(l)}</option>`).join("");
    const lst = (k) => v[k] == null ? "" : v[k].length ? esc(v[k].join(", ")) : "nenhuma";
    const LIMLBL = { ombro: "Ombro", joelho: "Joelho", lombar: "Lombar", punho: "Punho ou mão", quadril: "Quadril ou virilha" };
    const lim = Array.isArray(v.limitacoes) ? v.limitacoes : null;
    const triRows = tri.perguntas.map((q) => `<div class="yn" role="radiogroup" aria-label="${esc(q.texto)}"><p>${esc(q.texto)}</p>
      <label class="opt"><input type="radio" name="${esc(q.chave)}" value="sim" required ${q.resposta === true ? "checked" : ""}><span>Sim</span></label>
      <label class="opt"><input type="radio" name="${esc(q.chave)}" value="nao" required ${q.resposta === false ? "checked" : ""}><span>Não</span></label></div>`).join("");
    const fmtH = (h) => h == null ? "—" : h < 48 ? `${nf(h, 1)} h` : `${nf(h / 24, 1)} dias`;
    const ext = bk.externo, loc = bk.local;
    const key = s.chave, missing = [...new Set(Object.values(p.faltam).flat())];
    view.innerHTML = `${missing.length ? `<div class="notice">Faltam para liberar todos os módulos: ${esc(missing.join(", "))}.</div>` : ""}
      <h2>Perfil</h2><div class="card"><form class="f two" id="profile">
        <label>Sexo biológico<select name="sexo"><option value=""></option>${opt({ masculino: "Masculino", feminino: "Feminino" }, v.sexo)}</select></label>
        <label>Nascimento<input name="nascimento" type="date" value="${esc(v.nascimento || "")}"></label>
        <label>Altura (cm)<input name="altura_cm" type="number" min="120" max="230" value="${v.altura_cm ?? ""}" inputmode="numeric"></label>
        <label>FC máxima medida (opcional)<input name="fc_max_medida" type="number" min="120" max="230" value="${v.fc_max_medida ?? ""}" inputmode="numeric"></label>
        <label class="full">Objetivo principal<select name="objetivo"><option value=""></option>${opt(p.objetivos, v.objetivo)}</select></label>
        <label class="full">Experiência de treino<select name="experiencia"><option value=""></option>${opt(p.experiencias, v.experiencia)}</select></label>
        <label>Dias de treino/semana<input name="dias_treino" type="number" min="1" max="7" value="${v.dias_treino ?? ""}" inputmode="numeric"></label>
        <label>Minutos por sessão<input name="minutos_sessao" type="number" min="20" max="150" value="${v.minutos_sessao ?? ""}" inputmode="numeric"></label>
        <label class="full">Equipamento<select name="equipamento"><option value=""></option>${opt(p.equipamentos, v.equipamento)}</select></label>
        <label>Refeições por dia<input name="refeicoes_dia" type="number" min="2" max="8" value="${v.refeicoes_dia ?? ""}" inputmode="numeric"></label>
        <label>Cafeína habitual (mg/dia)<input name="cafeina_habitual_mg" type="number" min="0" max="1500" value="${v.cafeina_habitual_mg ?? ""}" inputmode="numeric"></label>
        <fieldset class="full tri"><legend>Lesões ou limitações físicas</legend>
          ${tri.limitacoes_opcoes.map((k) => `<label class="opt"><input type="checkbox" data-lim="${esc(k)}" ${lim && lim.includes(k) ? "checked" : ""}><span>${esc(LIMLBL[k] || k)}</span></label>`).join("")}
          <label class="opt"><input type="checkbox" id="lim-none" ${lim && lim.length === 0 ? "checked" : ""}><span>Nenhuma limitação</span></label>
          <p class="small muted">Sem marcar nada, a informação fica como não respondida. Outras situações (ex.: pós-operatório) entram na triagem abaixo.</p></fieldset>
        <label class="full">Condições de saúde diagnosticadas (ou "nenhuma")<input name="condicoes_saude" value="${lst("condicoes_saude")}"></label>
        <label class="full">Medicamentos em uso (ou "nenhum")<input name="medicamentos" value="${lst("medicamentos")}"></label>
        <label class="full">Restrições alimentares (ou "nenhuma")<input name="restricoes_alimentares" value="${lst("restricoes_alimentares")}"></label>
        <label class="full">Liberação médica para exercício intenso<select name="liberacao_medica">${opt({ nao: "Não / não sei", sim: "Sim" }, v.liberacao_medica ? "sim" : "nao")}</select></label>
        <fieldset class="full tri"><legend>Triagem de saúde (obrigatória)</legend>
          ${tri.faltam.some((k) => tri.perguntas.some((q) => q.chave === k)) ? `<p class="notice">Responda Sim ou Não a todas as perguntas. Pergunta sem resposta não conta como "Não": o treino, as metas e os suplementos personalizados continuam suspensos.</p>` : ""}
          ${triRows}</fieldset>
        <button class="primary full" type="submit">Salvar perfil</button></form>
        <p class="small muted">Esses dados ficam só no seu servidor e alimentam as estimativas e os limites de segurança.</p></div>
      <h2>Notificações no iPhone</h2><div class="card"><p class="small">Requer o app adicionado à Tela de Início (iOS 16.4 ou mais recente) e acesso por HTTPS. As mensagens são genéricas, sem dados de saúde.</p>
        <div class="hstack"><button class="primary" id="push-on">Ativar neste aparelho</button><button class="secondary" id="push-test">Enviar teste</button></div>
        <p class="small muted">Aparelhos inscritos: ${s.push_inscritos}. Canais ativos no servidor: ${esc(st.notificacoes.join(", ") || "nenhum")}.</p></div>
      <h2>Backup externo</h2><div class="card">
        <div class="row"><span>Envio externo</span><span class="r">${ext.configurado ? (ext.alerta ? chip("erro", "atenção") : chip("ok", "ativo")) : chip("media", "não configurado")}</span></div>
        <div class="row"><span>Último envio bem-sucedido</span><span class="r">${ext.ultimo_envio_ok ? dtBR(ext.ultimo_envio_ok) : "nunca"}</span></div>
        <div class="row"><span>Tempo desde o último envio</span><span class="r">${fmtH(ext.idade_horas)}</span></div>
        <div class="row"><span>Falhas seguidas</span><span class="r">${ext.falhas_consecutivas}</span></div>
        ${ext.atrasado ? `<p class="notice">Envio atrasado: o último backup externo tem mais de 36 horas ou nunca foi enviado.</p>` : ""}
        ${ext.pendente ? `<p class="notice">O backup local mais recente ainda não foi enviado.</p>` : ""}
        ${ext.ultimo_erro ? `<p class="notice">Último erro${ext.ultimo_erro.quando ? " (" + dtBR(ext.ultimo_erro.quando) + ")" : ""}: ${esc(ext.ultimo_erro.mensagem)}</p>` : ""}
        <div class="row"><span>Último backup local</span><span class="r">${loc.existe ? esc(loc.criado_utc ? dtBR(loc.criado_utc) : loc.arquivo) : "nenhum"}</span></div>
        ${loc.existe ? `<p class="small muted">Formato ${esc(loc.formato)} · ${nf(loc.tamanho_bytes / 1024)} KB${loc.linhas_daily_metrics != null ? ` · ${loc.linhas_daily_metrics} dias de métricas` : ""}.</p>` : ""}
        ${loc.erro ? `<p class="notice">${esc(loc.erro)}</p>` : ""}
        ${loc.existe ? `<a class="secondary tap" id="bk-dl" href="/api/backup/baixar" download>Baixar backup cifrado</a>` : ""}
        <p class="small muted">Guarde uma cópia no app Arquivos/iCloud uma vez por semana. Ela só abre com a sua chave de recuperação.</p></div>
      <h2>Fonte de dados e sistema</h2><div class="card">
        <div class="row"><span>Integração Garmin</span><span class="r">${s.fonte === "oficial" ? "API oficial (Personal API)" : "não oficial"}</span></div>
        <div class="row"><span>Validação com dados reais</span><span class="r">${s.dados_reais_validados ? "validada em " + dtBR(s.dados_reais_validados) : chip("media", "ainda não validada")}</span></div>
        <div class="row"><span>Chave pessoal</span><span class="r">${key.configurada ? `expira em ${key.dias} dia(s)` : "data de expiração não informada"}</span></div>
        <div class="row"><span>Qualidade dos dados (14 dias)</span><span class="r">${s.qualidade.pontuacao}/100 · ${esc(s.qualidade.nivel)}</span></div>
        <div class="row"><span>Tarefas com falha (7 dias)</span><span class="r">${s.falhas_7d}</span></div>
        <div class="row"><span>IA generativa</span><span class="r">${esc({ regras: "desligada (regras e estatística)", local: "modelo local", nuvem: "nuvem" }[s.ia_modo] || s.ia_modo)}</span></div>
        <div class="row"><span>Base científica</span><span class="r">${evs.total} referências</span></div>
        <div class="row wrap"><span>Verificação das referências<small>${esc(evs.aviso)}</small></span><span class="r">${evs.por_classe.verificada} verificadas · ${evs.por_classe.parcialmente_verificada} parciais · ${evs.por_classe.nao_verificada} não verificadas · ${evs.por_classe.inadequada} inadequadas</span></div>
        <p class="small muted">Fora da API oficial: ${esc(s.nao_disponiveis_na_api_oficial.join("; "))}.${s.qualidade.nao_fornecidos.length ? " Não fornecidos nos últimos 90 dias: " + esc(s.qualidade.nao_fornecidos.join(", ")) + "." : ""}</p>
        <details><summary>Histórico de execuções</summary><div class="inner">${s.execucoes.map((e) => `<div class="row"><span>${esc(e.job)}<small>${dtBR(e.started_at)}${e.status !== "ok" ? " · " + esc(e.detail || "") : ""}</small></span><span class="r">${chip(e.status === "ok" ? "ok" : "erro", e.status)}</span></div>`).join("") || "Sem execuções registradas."}</div></details>
        <details><summary>Indicadores fornecidos pelo dispositivo (30 dias)</summary><div class="inner">${Object.values(st.disponibilidade).map((r) => `<div class="row"><span>${esc(r.rotulo)}</span><span class="r">${esc(r.status)} · ${r.dias_com_dado}/${r.dias}</span></div>`).join("")}</div></details></div>
      <button id="btn-logout" class="secondary">Sair</button><p class="small muted">${esc(META.aviso)}</p>`;
    const limBoxes = $$("#profile [data-lim]"), limNone = $("#lim-none");
    limBoxes.forEach((c) => c.addEventListener("change", () => { if (c.checked) limNone.checked = false; }));
    limNone.addEventListener("change", () => { if (limNone.checked) limBoxes.forEach((c) => { c.checked = false; }); });
    onSubmit("#profile", async (d) => { d.liberacao_medica = d.liberacao_medica === "sim";
      tri.perguntas.forEach((q) => { d[q.chave] = d[q.chave] === "sim" ? true : d[q.chave] === "nao" ? false : null; });
      const chosen = limBoxes.filter((c) => c.checked).map((c) => c.dataset.lim);
      d.limitacoes = chosen.length ? chosen : limNone.checked ? [] : null;   // vazio sem "nenhuma" = não informado
      // em branco = não informado (o módulo continua pedindo); "nenhum(a)" = lista vazia declarada
      ["condicoes_saude", "medicamentos", "restricoes_alimentares"].forEach((k) => { const t = d[k].trim();
        d[k] = t === "" ? null : /^nenhum/i.test(t) ? [] : t.split(",").map((x) => x.trim()).filter(Boolean); });
      Object.keys(d).forEach((k) => { if (d[k] === "") d[k] = null; });
      await post("/api/perfil", d); toast("Perfil salvo"); render(); });
    $("#btn-logout").onclick = async () => { await post("/api/logout"); location.href = "/login"; };
    $("#push-test").onclick = async () => { try { await post("/api/push/teste"); toast("Teste enviado"); } catch (e) { toast(e.message); } };
    $("#push-on").onclick = async () => {
      try {
        if (!("serviceWorker" in navigator) || !("PushManager" in window)) throw new Error("Este navegador não oferece push. No iPhone, adicione o app à Tela de Início e abra por lá.");
        if ((await Notification.requestPermission()) !== "granted") throw new Error("Permissão negada nas configurações do aparelho.");
        const reg = await navigator.serviceWorker.ready, k = (await api("/api/push/chave")).publica;
        const raw = atob(k.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - k.length % 4) % 4));
        const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: Uint8Array.from(raw, (c) => c.charCodeAt(0)) });
        await post("/api/push/inscrever", sub.toJSON()); toast("Notificações ativadas"); render();
      } catch (e) { toast(e.message); }
    };
  }

  async function vGarmin() {
    $("#subtitle").textContent = "Compare com o app Garmin Connect";
    const g = await api(`/api/garmin/conferencia${state.gday ? "?dia=" + encodeURIComponent(state.gday) : ""}`);
    state.gday = g.dia;
    const SIT = { confere: ["ok", "Igual"], diverge: ["erro", "Diferente"] };
    view.innerHTML = `<div class="notice">Abra o app Garmin Connect, na data abaixo, e compare cada valor. ${esc(g.onde_ver_no_app)}</div>
      <div class="card"><label class="select">Dia<input id="g-day" type="date" value="${esc(g.dia)}" max="${todayISO()}"></label>
      ${g.itens.map((i) => `<div class="row conf"><span>${esc(i.rotulo)}<small>${i.valor == null ? "sem valor no sistema" : "no sistema: " + esc(i.valor)}</small>
        ${SIT[i.situacao] ? chip(...SIT[i.situacao]) : ""}</span>
        <span class="r hstack"><button class="secondary tap" data-k="${esc(i.chave)}" data-ok="1" aria-label="${esc(i.rotulo)}: igual ao app">Igual</button>
        <button class="secondary tap" data-k="${esc(i.chave)}" data-ok="0" aria-label="${esc(i.rotulo)}: diferente do app">Diferente</button></span></div>`).join("")}
      <p class="small muted">${esc(g.nota)}</p></div>`;
    $("#g-day").onchange = (e) => { if (e.target.value) { state.gday = e.target.value; render(); } };
    $$("[data-k]", view).forEach((b) => (b.onclick = async () => { b.disabled = true;
      try { await post("/api/garmin/conferencia", { chave: b.dataset.k, ok: b.dataset.ok === "1", dia: g.dia }); toast("Conferência registrada"); render(); }
      catch (e) { toast("Não foi possível registrar: " + e.message); b.disabled = false; } }));
  }

  const VIEWS = { hoje: vHoje, saude: vSaude, treino: vTreino, nutricao: vNutricao, mais: vMais, metabolismo: vMetabolismo, composicao: vComposicao,
    suplementos: vSuplementos, garmin: vGarmin, exames: vExames, evolucao: vEvolucao, recomendacoes: vRecomendacoes, relatorios: vRelatorios, config: vConfig };

  $("#btn-sync").onclick = async () => {
    const b = $("#btn-sync"); b.classList.add("spin");
    try { const r = await post("/api/sync"); toast(r.mensagem); setTimeout(() => { b.classList.remove("spin"); render(); }, 25000); }
    catch (e) { b.classList.remove("spin"); toast("Falha: " + e.message); }
  };
  (async () => {
    META = await api("/api/meta");
    $("#demo-banner").hidden = !META.demo;
    await render();
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  })().catch((e) => { view.innerHTML = `<div class="card">Erro ao carregar: ${esc(e.message)}</div>`; });
})();
