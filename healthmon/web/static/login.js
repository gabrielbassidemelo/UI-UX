"use strict";
try { const t = localStorage.getItem("hm-theme"); if (t) document.documentElement.dataset.theme = t; } catch (_) {}
document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("err");
  err.textContent = "";
  const r = await fetch("/api/login", { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-HM": "1" },
    body: JSON.stringify({ senha: document.getElementById("senha").value }) });
  if (r.ok) { location.href = "/"; return; }
  const d = await r.json().catch(() => ({}));
  err.textContent = d.detail || "Não foi possível entrar.";
});
