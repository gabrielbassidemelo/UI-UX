"use strict";
(() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let META = null, mainChart = null, sleepChart = null, chartDays = 30;

  async function api(path, opts = {}) {
    const r = await fetch(path, { credentials: "same-origin", ...opts,
      headers: { "X-HM": "1", "Content-Type": "application/json", ...(opts.headers || {}) } });
    if (r.status === 401) { location.href = "/login"; throw new Error("401"); }
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  }

  // ---------- formatação ----------
  const nf = (v, casas = 0) => v == null ? "—" : Number(v).toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
  function fmtVal(key, v) {
    if (v == null) return "—";
    const m = META.metricas[key];
    if (m.unidade === "h") { const h = Math.floor(v); let mi = Math.round((v - h) * 60); return `${h}h${String(mi).padStart(2, "0")}`; }
    return `${nf(v, m.casas)}${m.unidade ? " " + m.unidade : ""}`;
  }
  const dateBR = (iso) => iso ? new Date(iso + (iso.length === 10 ? "T12:00:00" : "")).toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" }) : "—";
  const dtBR = (iso) => iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "nunca";

  // ---------- tema ----------
  function applyTheme(t) {
    if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
    if (mainChart) loadChart();
  }
  try { applyTheme(localStorage.getItem("hm-theme")); } catch (_) {}
  $("#btn-theme").onclick = () => {
    const dark = getComputedStyle(document.documentElement).colorScheme.includes("dark");
    const next = dark ? "light" : "dark";
    try { localStorage.setItem("hm-theme", next); } catch (_) {}
    applyTheme(next);
  };
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  // ---------- navegação ----------
  $$("nav.tabs button").forEach((b) => b.onclick = () => {
    $$("nav.tabs button").forEach((x) => x.classList.toggle("on", x === b));
    $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
    window.scrollTo(0, 0);
    ({ graficos: loadCharts, comparar: loadCompare, relatorios: () => loadReports(""), sistema: loadSystem })[b.dataset.tab]?.();
  });

  // ---------- Hoje ----------
  async function loadToday() {
    const o = await api("/api/overview");
    $("#subtitle").textContent = `Dados de ${dateBR(o.dia)} · sincronizado ${dtBR(o.ultima_sincronizacao)}`;
    const r = o.recuperacao;
    $("#recovery").innerHTML = `<div class="dot ${esc(r.status)}" aria-hidden="true"></div><div>
      <h3>${esc(r.texto)}</h3>
      <p>${r.sinais.length ? "Fora do seu padrão (desfavorável): " + esc(r.sinais.join(", ")) : "Nenhum sinal desfavorável relevante."}
      ${r.persistente ? "<br><strong>Padrão persistente (3 dos últimos 4 dias).</strong>" : ""}</p></div>`;
    $("#alerts-today").innerHTML = o.alertas.map((a) => `<div class="card alert"><h3>${esc(a.title)}</h3>
      <p>${esc(a.message)}</p><button class="secondary" data-ack="${a.id}">Entendi</button></div>`).join("");
    $$("[data-ack]").forEach((b) => b.onclick = async () => { await api(`/api/alerts/${b.dataset.ack}/ack`, { method: "POST" }); loadToday(); });
    const order = ["hrv_last_night", "resting_hr", "sleep_seconds", "sleep_score", "bb_max", "stress_avg",
      "training_readiness", "steps", "deep_seconds", "rem_seconds", "sleep_resp_avg", "sleep_spo2_avg",
      "skin_temp_dev", "calories_total", "intensity_minutes", "vo2max"];
    $("#kpis").innerHTML = order.filter((k) => o.avaliacoes[k]).map((k) => {
      const a = o.avaliacoes[k];
      const cls = a.leitura === "favoravel" ? "fav" : a.leitura === "desfavoravel" ? "desf" : "";
      const cmp = a.sentido === "sem_base" ? "construindo linha de base" :
        `${a.sentido === "acima" ? "↑ acima" : a.sentido === "abaixo" ? "↓ abaixo" : "= dentro"} · mediana ${fmtVal(k, a.mediana)}`;
      const tag = a.padrao !== "estavel" ? `<span class="tag ${a.padrao}">${a.padrao}</span>` : "";
      return `<div class="kpi ${cls}"><div class="lbl">${esc(a.rotulo)}</div><div class="val">${fmtVal(k, a.valor)}</div>
        <div class="cmp">${esc(cmp)}</div>${tag}</div>`;
    }).join("") || `<p class="muted">Ainda não há dados para hoje.</p>`;
    $("#attention").innerHTML = `<strong>Merece atenção</strong><ul>${(o.atencao.length ? o.atencao.map((p) => `<li>${esc(p.texto)}</li>`)
      : ["<li>Nenhum indicador relevante fora do seu padrão.</li>"]).join("")}</ul>` +
      (o.ultimo_relatorio ? `<p class="small muted">Último relatório: ${esc(o.ultimo_relatorio.title)}</p>` : "");
  }

  // ---------- Gráficos ----------
  function fillMetricSelect() {
    const sel = $("#metric-select");
    sel.innerHTML = Object.entries(META.categorias).map(([cat, nome]) =>
      `<optgroup label="${esc(nome)}">` + Object.entries(META.metricas).filter(([, m]) => m.categoria === cat)
        .map(([k, m]) => `<option value="${k}">${esc(m.rotulo)}</option>`).join("") + "</optgroup>").join("");
    sel.value = "hrv_last_night";
    sel.onchange = loadChart;
  }
  $$("[data-days]").forEach((b) => b.onclick = () => {
    $$("[data-days]").forEach((x) => x.classList.toggle("on", x === b)); chartDays = +b.dataset.days; loadCharts();
  });

  function baseOpts() {
    const text = css("--muted"), grid = css("--line");
    return { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: text, boxWidth: 12 } } },
      scales: { x: { ticks: { color: text, maxTicksLimit: 7 }, grid: { color: grid } },
        y: { ticks: { color: text }, grid: { color: grid } } } };
  }
  async function loadChart() {
    const k = $("#metric-select").value, m = META.metricas[k];
    const d = await api(`/api/series?keys=${k}&days=${chartDays}`);
    const labels = d.datas.map((x) => x.slice(8, 10) + "/" + x.slice(5, 7));
    const ds = [{ label: m.rotulo, data: d.series[k], borderColor: css("--accent"), backgroundColor: css("--accent"),
      pointRadius: chartDays > 90 ? 0 : 2, spanGaps: false, tension: 0.2 },
      { label: "Média móvel 7 dias", data: d.series[k + "__mm7"], borderColor: css("--muted"), borderDash: [5, 4], pointRadius: 0, tension: 0.3 }];
    const b = d.base[k];
    if (b) {
      ds.push({ label: "Faixa usual (limite inferior)", data: labels.map(() => b.faixa[0]), borderColor: "transparent", pointRadius: 0, fill: false });
      ds.push({ label: "Faixa usual", data: labels.map(() => b.faixa[1]), borderColor: "transparent",
        backgroundColor: "rgba(59,130,246,0.13)", pointRadius: 0, fill: "-1" });
    }
    const cfg = { type: "line", data: { labels, datasets: ds }, options: baseOpts() };
    cfg.options.plugins.legend.labels.filter = (i) => !i.text.includes("limite inferior");
    if (mainChart) mainChart.destroy();
    mainChart = new Chart($("#main-chart"), cfg);
    const n = d.series[k].filter((v) => v != null).length;
    $("#chart-note").textContent = b ? `Faixa usual: ${fmtVal(k, b.faixa[0])} a ${fmtVal(k, b.faixa[1])} (mediana ${fmtVal(k, b.mediana)}, base de ${b.n} dias). ${n} dias com dado no período.`
      : `${n} dias com dado no período. Linha de base ainda em construção.`;
  }
  async function loadSleep() {
    const keys = "deep_seconds,light_seconds,rem_seconds,awake_seconds";
    const d = await api(`/api/series?keys=${keys}&days=${Math.min(chartDays, 90)}`);
    const labels = d.datas.map((x) => x.slice(8, 10) + "/" + x.slice(5, 7));
    const col = { deep_seconds: "#1e3a8a", light_seconds: "#60a5fa", rem_seconds: "#a78bfa", awake_seconds: "#f59e0b" };
    const ds = keys.split(",").map((k) => ({ label: META.metricas[k].rotulo, backgroundColor: col[k],
      data: d.series[k].map((v) => v == null ? null : (k === "awake_seconds" ? v / 60 : v)), stack: "s" }));
    const opts = baseOpts(); opts.scales.x.stacked = true; opts.scales.y.stacked = true;
    opts.scales.y.title = { display: true, text: "horas", color: css("--muted") };
    if (sleepChart) sleepChart.destroy();
    sleepChart = new Chart($("#sleep-chart"), { type: "bar", data: { labels, datasets: ds }, options: opts });
  }
  async function loadInsights() {
    const d = await api(`/api/insights?days=${Math.max(chartDays, 14)}`);
    const reg = d.regularidade_sono;
    $("#insights").innerHTML = `<strong>Tendências</strong><ul>${(d.frases_tendencia.length ? d.frases_tendencia : ["Nenhuma tendência estatisticamente consistente no período."]).map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
      <strong>Associações</strong><ul>${(d.frases_associacao.length ? d.frases_associacao : ["Nenhuma associação moderada/forte (ou dados insuficientes)."]).map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
      ${reg ? `<p class="small">Horário mediano de dormir: <strong>${esc(reg.horario_mediano)}</strong> (variação ±${reg.desvio_min} min).</p>` : ""}
      <p class="small muted">Associação não implica causa. Tendências usam Theil–Sen e Mann–Kendall (p &lt; 0,05).</p>`;
  }
  function loadCharts() { loadChart(); loadSleep(); loadInsights(); }

  // ---------- Comparar ----------
  let cmpDays = 7;
  $$("[data-cmp]").forEach((b) => b.onclick = () => { $$("[data-cmp]").forEach((x) => x.classList.toggle("on", x === b)); cmpDays = +b.dataset.cmp; loadCompare(); });
  async function loadCompare() {
    const d = await api(`/api/compare?days=${cmpDays}`);
    $("#cmp-period").textContent = `Período atual ${dateBR(d.atual[0])} – ${dateBR(d.atual[1])} vs. anterior ${dateBR(d.anterior[0])} – ${dateBR(d.anterior[1])} (médias).`;
    $("#compare").innerHTML = `<div class="tbl"><table><thead><tr><th>Indicador</th><th class="num">Atual</th><th class="num">Anterior</th><th class="num">Var.</th></tr></thead><tbody>` +
      d.linhas.map((l) => `<tr><td>${esc(l.rotulo)}</td><td class="num">${fmtVal(l.metrica, l.atual.media)}</td>
      <td class="num">${fmtVal(l.metrica, l.anterior.media)}</td>
      <td class="num ${l.leitura === "favoravel" ? "fav-t" : l.leitura === "desfavoravel" ? "desf-t" : ""}">${l.variacao_pct == null ? "—" : (l.variacao_pct > 0 ? "+" : "") + nf(l.variacao_pct, 0) + "%"}</td></tr>`).join("") + "</tbody></table></div>";
    const acts = await api(`/api/activities?days=${cmpDays}`);
    $("#activities").innerHTML = acts.length ? `<div class="tbl"><table><thead><tr><th>Data</th><th>Atividade</th><th class="num">Duração</th><th class="num">FC méd.</th><th class="num">Carga</th></tr></thead><tbody>` +
      acts.slice().reverse().map((a) => `<tr><td>${esc(dateBR(a.date))}</td><td>${esc(a.name || a.type)}</td><td class="num">${nf((a.duration_s || 0) / 60)} min</td>
      <td class="num">${nf(a.avg_hr)}</td><td class="num">${nf(a.training_load)}</td></tr>`).join("") + "</tbody></table></div>" : `<p class="muted">Nenhuma atividade no período.</p>`;
  }

  // ---------- Relatórios ----------
  $$("[data-kind]").forEach((b) => b.onclick = () => { $$("[data-kind]").forEach((x) => x.classList.toggle("on", x === b)); loadReports(b.dataset.kind); });
  const KIND = { diario: "Diário", noturno: "Noturno", semanal: "Semanal", mensal: "Mensal" };
  async function loadReports(kind) {
    $("#report-view").hidden = true;
    const list = await api("/api/reports" + (kind ? `?kind=${kind}` : ""));
    $("#report-list").innerHTML = list.length ? list.map((r) => `<button class="report-item" data-rid="${r.id}">
      <strong>${esc(r.title)}</strong><small>${esc(KIND[r.kind])} · ${esc(r.summary || "")}</small></button>`).join("")
      : `<p class="muted">Nenhum relatório ainda. Eles são gerados automaticamente nos horários configurados.</p>`;
    $$("[data-rid]").forEach((b) => b.onclick = async () => {
      const r = await api(`/api/reports/${b.dataset.rid}`);
      const v = $("#report-view");
      v.innerHTML = `<button class="secondary" id="rep-back">← Voltar</button>` + r.html; // HTML gerado e escapado pelo servidor
      v.hidden = false; $("#report-list").innerHTML = ""; window.scrollTo(0, 0);
      $("#rep-back").onclick = () => loadReports(kind);
    });
  }

  // ---------- Sistema ----------
  async function loadSystem() {
    const [s, al] = await Promise.all([api("/api/status"), api("/api/alerts")]);
    $("#alert-list").innerHTML = al.length ? al.slice(0, 30).map((a) => `<p><strong>${esc(a.title)}</strong><br>
      <span class="small muted">${dtBR(a.created_at)}${a.notified_at ? " · notificado" : ""}${a.acknowledged ? " · visto" : ""}</span><br>${esc(a.message)}</p>`).join("")
      : `<p class="muted">Nenhum alerta.</p>`;
    const rows = Object.values(s.disponibilidade);
    $("#availability").innerHTML = `<div class="tbl"><table><thead><tr><th>Indicador</th><th>Status</th><th class="num">Dias</th></tr></thead><tbody>` +
      rows.map((r) => `<tr><td>${esc(r.rotulo)}</td><td class="st-${esc(r.status.split(" ")[0].normalize("NFD").replace(/[̀-ͯ]/g, ""))}">${esc(r.status)}</td><td class="num">${r.dias_com_dado}/${r.dias}</td></tr>`).join("") + "</tbody></table></div>";
    const consent = s.ia_nuvem_consentimento;
    $("#system").innerHTML = `<p>Período no banco: <strong>${esc(dateBR(s.periodo[0]))}</strong> a <strong>${esc(dateBR(s.periodo[1]))}</strong></p>
      <p>IA: <strong>${esc({ regras: "motor local de regras", local: "modelo local (Ollama)", nuvem: "Claude (nuvem)" }[s.ia_modo] || s.ia_modo)}</strong>
      ${s.ia_modo === "nuvem" ? (consent ? ` — consentimento em ${esc(dtBR(consent.concedido_em))}` : " — <strong>sem consentimento: desativada</strong>") : " — nenhum dado de saúde sai do computador"}</p>
      <p>Notificações: <strong>${esc(s.notificacoes.join(", ") || "desativadas")}</strong> (conteúdo ${esc(s.nivel_conteudo)})</p>
      <strong>Últimas sincronizações</strong><ul>${s.sincronizacoes.slice(0, 8).map((h) => `<li>${esc(dtBR(h.started_at))} — ${esc(h.status)}</li>`).join("")}</ul>`;
    $("#disclaimer").textContent = META.aviso;
  }
  $("#btn-logout").onclick = async () => { await api("/api/logout", { method: "POST" }); location.href = "/login"; };
  $("#btn-sync").onclick = async () => {
    const b = $("#btn-sync"); b.classList.add("spin");
    try { const r = await api("/api/sync", { method: "POST" }); $("#subtitle").textContent = r.mensagem; setTimeout(() => { b.classList.remove("spin"); loadToday(); }, 25000); }
    catch (e) { b.classList.remove("spin"); $("#subtitle").textContent = "Falha: " + e.message; }
  };

  // ---------- início ----------
  (async () => {
    META = await api("/api/meta");
    $("#demo-banner").hidden = !META.demo;
    fillMetricSelect();
    await loadToday();
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  })().catch((e) => { $("#subtitle").textContent = "Erro ao carregar: " + e.message; });
})();
