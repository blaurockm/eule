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
    if (value > 0) el.classList.add("positive");
    if (value < 0) el.classList.add("negative");
  };

  $("holder-name").textContent = entry.name;
  $("balance-amount").textContent = fmt(entry.balance);
  $("currency").textContent = entry.currency || "EUR";
  $("capital").textContent = fmt(entry.capital);
  setSigned("pnl", entry.allocated_pnl);
  $("expenses").textContent = fmt(entry.allocated_expenses);
  $("as-of").textContent = entry.as_of;
  $("generated-at").textContent = (payload.generated_at || "").replace("T", " ").slice(0, 16);

  show("balance");
})();
