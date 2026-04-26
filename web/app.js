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

  const setSigned = (id, value) => {
    const el = $(id);
    el.textContent = fmt(value);
    el.classList.remove("positive", "negative");
    if (value > 0.005) el.classList.add("positive");
    if (value < -0.005) el.classList.add("negative");
  };

  $("holder-name").textContent = entry.name;
  $("balance-amount").textContent = fmt(entry.balance_broker);
  $("currency").textContent = entry.currency || "EUR";

  // Giro-Reserve nur zeigen wenn != 0
  if (Math.abs(entry.balance_giro) >= 0.01) {
    $("giro-amount").textContent = fmt(entry.balance_giro) + " " + (entry.currency || "EUR");
    $("giro-line").classList.remove("hidden");
  }

  $("capital").textContent = fmt(entry.capital);
  setSigned("pnl", entry.allocated_pnl);
  $("expenses").textContent = fmt(entry.allocated_expenses);
  $("as-of").textContent = entry.as_of;
  $("generated-at").textContent = (payload.generated_at || "").replace("T", " ").slice(0, 16);

  // Recent trades
  const list = $("recent-trades");
  list.innerHTML = "";
  const trades = entry.recent_trades || [];
  if (trades.length === 0) {
    const li = document.createElement("li");
    li.className = "trade-empty";
    li.textContent = "keine Trades";
    list.appendChild(li);
  } else {
    for (const t of trades) {
      const li = document.createElement("li");
      const sign = t.pnl_eur > 0 ? "positive" : t.pnl_eur < 0 ? "negative" : "";
      li.innerHTML =
        `<span class="trade-date">${t.date}</span>` +
        `<span class="trade-symbol">${t.symbol}</span>` +
        `<span class="trade-pnl ${sign}">${fmt(t.pnl_eur)}</span>`;
      list.appendChild(li);
    }
  }

  show("balance");
})();
