// Teste de fumaça em iPhone emulado (Playwright). Use SOMENTE contra um banco de demonstração:
//   HEALTHMON_HOME=/tmp/hm-demo healthmon init && healthmon demo && healthmon senha-dashboard && healthmon dashboard
//   node tests/e2e/smoke_iphone.js
// Falha (código 1) se houver erro de console, resposta HTTP >= 400 ou rolagem horizontal.
const { chromium, devices } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ ...devices['iPhone 14'], colorScheme: 'dark' });
  const p = await ctx.newPage();
  const errs = [];
  p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  p.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  p.on('response', r => { if (r.status() >= 400 && !r.url().includes('push')) errs.push(r.status() + ' ' + r.url()); });
  await p.goto(process.env.HM_URL || 'http://127.0.0.1:8765/');
  await p.fill('#senha', process.env.HM_SENHA || 'senha-demo-123'); await p.click('button[type=submit]');
  await p.waitForSelector('#checkin');
  const check = async (name) => {
    await p.waitForTimeout(1200);
    const bad = await p.evaluate(() => ({ h: document.documentElement.scrollWidth > window.innerWidth, t: document.querySelector('#view').innerText.slice(0, 200), err: /Não foi possível carregar|undefined|NaN|\[object/.test(document.querySelector('#view').innerText) }));
    if (bad.h || bad.err) errs.push('layout/conteúdo suspeito em ' + name);
    console.log(name, 'hscroll:', bad.h, 'suspeito:', bad.err, '|', bad.t.replace(/\n/g, ' ').slice(0, 90));
    if (process.env.SHOTS) await p.screenshot({ path: `${process.env.SHOTS}/v-${name}.png` });
  };
  await check('hoje');
  for (const t of ['saude', 'treino', 'nutricao']) { await p.click(`nav button[data-tab=${t}]`); await check(t); }
  await p.click('nav button[data-tab=saude]'); await p.waitForSelector('[data-area=sono]'); await p.click('[data-area=sono]'); await check('sono');
  for (const s of ['metabolismo', 'composicao', 'suplementos', 'exames', 'evolucao', 'recomendacoes', 'relatorios', 'config']) {
    await p.click('nav button[data-tab=mais]'); await p.waitForSelector(`[data-go=${s}]`); await p.click(`[data-go=${s}]`); await check(s);
  }
  console.log('ERRORS', JSON.stringify(errs, null, 1));
  await b.close();
  process.exit(errs.length ? 1 : 0);
})();
