"use strict";

(async function main() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("t");

  const $ = (id) => document.getElementById(id);
  const show = (id) => {
    document.querySelectorAll(".state").forEach((el) => el.classList.add("hidden"));
    $(id).classList.remove("hidden");
  };

  const showError = (msg) => {
    $("error-message").textContent = msg;
    show("error");
  };

  if (!token) {
    show("empty");
    return;
  }

  let payload;
  try {
    const res = await fetch("balances.json", { cache: "no-cache" });
    if (!res.ok) {
      showError(`Daten konnten nicht geladen werden (HTTP ${res.status}).`);
      return;
    }
    payload = await res.json();
  } catch (err) {
    showError(`Netzwerk-Fehler: ${err.message}`);
    return;
  }

  const entry = payload.tokens && payload.tokens[token];
  if (!entry) {
    show("empty");
    return;
  }

  const fmt = (n) =>
    n.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const fmtPct = (frac, digits = 1) =>
    (frac * 100).toLocaleString("de-DE", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }) + " %";

  const setSigned = (id, value) => {
    const el = $(id);
    el.textContent = fmt(value);
    el.classList.remove("positive", "negative");
    if (value > 0.005) el.classList.add("positive");
    if (value < -0.005) el.classList.add("negative");
  };

  const g = payload.global || {};
  const currency = g.currency || "EUR";

  $("holder-name").textContent = entry.name;
  $("broker-total").textContent = fmt(g.broker_total ?? 0);
  $("currency").textContent = currency;

  setSigned("total-pnl", g.total_pnl ?? 0);
  $("cagr-meta").textContent =
    g.cagr === null || g.cagr === undefined ? "" : `(CAGR ${fmtPct(g.cagr)})`;

  setSigned("pnl-share", entry.pnl_share ?? 0);
  $("pnl-share-pct").textContent = `(${fmtPct(entry.pnl_share_pct ?? 0, 0)})`;

  $("total-expenses").textContent = fmt(g.total_expenses ?? 0);
  $("expenses-share").textContent = fmt(entry.expenses_share ?? 0);
  $("expenses-share-pct").textContent = `(${fmtPct(entry.expenses_share_pct ?? 0, 0)})`;

  $("as-of").textContent = entry.as_of;
  $("generated-at").textContent = (payload.generated_at || "").replace("T", " ").slice(0, 16);

  // Recent trades (global — gleich fuer alle Holder)
  const list = $("recent-trades");
  list.innerHTML = "";
  const trades = g.recent_trades || [];
  if (trades.length === 0) {
    const li = document.createElement("li");
    li.className = "trade-empty";
    li.textContent = "keine Trades";
    list.appendChild(li);
  } else {
    for (const t of trades) {
      const li = document.createElement("li");
      const sign = t.pnl_eur > 0 ? "positive" : t.pnl_eur < 0 ? "negative" : "";
      const label = t.count === 1 ? "Roundtrip" : "Roundtrips";
      li.innerHTML =
        `<span class="trade-date">${t.date}</span>` +
        `<span class="trade-symbol">${t.count} ${label}</span>` +
        `<span class="trade-pnl ${sign}">${fmt(t.pnl_eur)}</span>`;
      list.appendChild(li);
    }
  }

  show("balance");
})();
