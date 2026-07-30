/**
 * Hase Runtime-Status — eigenstaendige Seite (status.html).
 *
 * Unabhaengig von der tradingGbr-Share-App (index.html/app.js/style.css):
 * kein Token-Schema, keine GbR-Daten. Zugriff ueber Supabase-Auth (Google),
 * Daten aus der Tabelle `runtime_heartbeats` (RLS: SELECT fuer authenticated).
 *
 * Der anon/publishable Key ist oeffentlich — das Zugriffs-Gate ist die RLS-Policy.
 */

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const SUPABASE_URL = "https://iwgilugtlvpxunokqsxx.supabase.co";
const SUPABASE_ANON_KEY = "sb_publishable_Hi-ahQqFr9rs_5aSN4RPPQ_63zYRJEY";

/** Heartbeat aelter als das hier -> STALE. Ein toter Runtime schreibt gar nichts mehr. */
const STALE_AFTER_MS = 10 * 60 * 1000;
const REFRESH_MS = 60 * 1000;
const TICK_MS = 15 * 1000; // nur Neuberechnung des Alters aus dem Cache

/** Produktions-Environments zuerst, Rest alphabetisch. */
const PROD_ENVS = ["real-ibkr", "real2-ibkr"];

const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

const $ = (id) => document.getElementById(id);

const showView = (id) => {
  ["view-loading", "view-login", "view-data"].forEach((v) =>
    $(v).classList.toggle("hidden", v !== id)
  );
  $("topbar").classList.toggle("hidden", id !== "view-data");
};

// ── Formatierung ─────────────────────────────────────────────

const fmtNum = (n) =>
  Number(n || 0).toLocaleString("de-DE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

const fmtSigned = (n) => {
  const v = Number(n || 0);
  return (v > 0 ? "+" : "") + fmtNum(v);
};

const signClass = (n) => {
  const v = Number(n || 0);
  if (v > 0.005) return "positive";
  if (v < -0.005) return "negative";
  return "";
};

const fmtAge = (ms) => {
  if (ms === null || !isFinite(ms)) return "unbekannt";
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  if (h < 24) return rest ? `${h} h ${rest} min` : `${h} h`;
  const d = Math.floor(h / 24);
  return `${d} d ${h % 24} h`;
};

const fmtClock = (date) =>
  date.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** Timestamp des Heartbeats (UTC, vom Runtime selbst) — DB-updated_at nur als Fallback. */
const heartbeatTime = (row) => {
  const raw = row?.heartbeat?.timestamp || row?.updated_at;
  if (!raw) return null;
  const t = Date.parse(raw);
  return isNaN(t) ? null : t;
};

const envRank = (name) => {
  const i = PROD_ENVS.indexOf(name);
  return i >= 0 ? i : PROD_ENVS.length;
};

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
};

// ── Rendering ────────────────────────────────────────────────

let cachedRows = [];

function strategyStateClass(strat) {
  const health = (strat.health && strat.health.health) || "OK";
  if (strat.status === "error" || health === "ERROR") return "err";
  if (health === "WARN") return "warn";
  if (strat.status === "stopped") return "warn";
  return "ok";
}

function renderStrategy(strat) {
  const li = el("li", "strat");

  const top = el("div", "strat-top");
  top.appendChild(el("span", "strat-name", strat.name || "?"));

  const display = strat.display || {};
  const stateLabel = display.fsm_state || (strat.status || "?").toUpperCase();
  top.appendChild(el("span", `strat-state ${strategyStateClass(strat)}`, stateLabel));
  li.appendChild(top);

  const msg = display.status_message;
  li.appendChild(el("p", "strat-msg", msg ? msg : "keine Statusmeldung"));

  const problems = (strat.health && strat.health.problems) || [];
  if (problems.length) {
    li.appendChild(el("p", "strat-problems", problems.join(" · ")));
  }
  return li;
}

function renderEnvCard(row, now) {
  const hb = row.heartbeat || {};
  const name = row.environment || hb.environment || "?";
  const card = el("div", "env-card");

  // Kopf: Name + Alters-Badge
  const head = el("div", "env-head");
  const nameWrap = el("div", "env-name");
  nameWrap.appendChild(el("span", null, name));
  if (PROD_ENVS.includes(name)) nameWrap.appendChild(el("span", "tag-prod", "prod"));
  head.appendChild(nameWrap);

  const t = heartbeatTime(row);
  const age = t === null ? null : now - t;
  const stale = age === null || age > STALE_AFTER_MS;
  const badge = el(
    "span",
    `badge-age ${stale ? "stale" : "fresh"}`,
    stale ? `STALE · ${fmtAge(age)}` : `vor ${fmtAge(age)}`
  );
  head.appendChild(badge);
  card.appendChild(head);

  // PnL
  const pnl = hb.pnl || {};
  const currency = (hb.cash && hb.cash.currency) || "";
  const grid = el("div", "env-pnl");
  const cells = [
    ["realisiert", pnl.daily_realized],
    ["unrealisiert", pnl.daily_unrealized],
    ["gesamt", pnl.total],
  ];
  for (const [label, value] of cells) {
    const cell = el("div");
    cell.appendChild(el("p", "pnl-cell-label", label));
    cell.appendChild(el("p", `pnl-cell-value ${signClass(value)}`, fmtSigned(value)));
    grid.appendChild(cell);
  }
  card.appendChild(grid);

  // Meta-Zeile
  const th = hb.trading_hours || {};
  const trades = hb.trades || {};
  const metaParts = [];
  if (currency) metaParts.push(currency);
  metaParts.push(`${hb.positions_count ?? 0} Positionen`);
  metaParts.push(`${trades.count_today ?? 0} Trades heute`);
  metaParts.push(th.is_within_hours ? "innerhalb Handelszeit" : "ausserhalb Handelszeit");
  card.appendChild(el("p", "env-meta", metaParts.join(" · ")));

  // Strategien
  const strategies = hb.strategies || [];
  if (!strategies.length) {
    card.appendChild(el("p", "strat-empty", "keine Strategien gemeldet"));
  } else {
    const ul = el("ul", "strat-list");
    for (const s of strategies) ul.appendChild(renderStrategy(s));
    card.appendChild(ul);
  }

  return card;
}

function render() {
  const now = Date.now();
  const list = $("env-list");
  list.innerHTML = "";

  if (!cachedRows.length) {
    list.appendChild(el("p", "strat-empty", "keine Heartbeats gefunden"));
    return;
  }

  const rows = [...cachedRows].sort((a, b) => {
    const ra = envRank(a.environment);
    const rb = envRank(b.environment);
    if (ra !== rb) return ra - rb;
    return String(a.environment).localeCompare(String(b.environment));
  });

  for (const row of rows) list.appendChild(renderEnvCard(row, now));
}

// ── Daten ────────────────────────────────────────────────────

async function loadData() {
  const { data, error } = await supabase
    .from("runtime_heartbeats")
    .select("environment, heartbeat, updated_at");

  const banner = $("data-error");
  if (error) {
    banner.textContent = `Daten konnten nicht geladen werden: ${error.message}`;
    banner.classList.remove("hidden");
    render();
    return;
  }
  banner.classList.add("hidden");
  cachedRows = data || [];
  $("fetched-at").textContent = fmtClock(new Date());
  render();
}

// ── Auth ─────────────────────────────────────────────────────

async function signInWithGoogle() {
  const redirectTo = window.location.origin + window.location.pathname;
  const { error } = await supabase.auth.signInWithOAuth({
    provider: "google",
    options: { redirectTo },
  });
  if (error) throw error;
}

/** OAuth-Reste (?code=…, #access_token=…) aus der Adresszeile raeumen. */
function cleanAuthParamsFromUrl() {
  const url = new URL(window.location.href);
  let dirty = false;
  for (const key of ["code", "error", "error_description", "state"]) {
    if (url.searchParams.has(key)) {
      url.searchParams.delete(key);
      dirty = true;
    }
  }
  if (window.location.hash.includes("access_token")) {
    url.hash = "";
    dirty = true;
  }
  if (dirty) window.history.replaceState({}, document.title, url.toString());
}

let timers = [];

function stopTimers() {
  timers.forEach(clearInterval);
  timers = [];
}

async function enterSignedIn() {
  showView("view-data");
  await loadData();
  stopTimers();
  timers.push(setInterval(loadData, REFRESH_MS));
  timers.push(setInterval(render, TICK_MS)); // Alter mitlaufen lassen
}

function enterSignedOut() {
  stopTimers();
  cachedRows = [];
  showView("view-login");
}

async function main() {
  $("login-btn").addEventListener("click", async () => {
    const btn = $("login-btn");
    btn.disabled = true;
    try {
      await signInWithGoogle();
    } catch (e) {
      const err = $("login-error");
      err.textContent = e instanceof Error ? e.message : "Login fehlgeschlagen";
      err.classList.remove("hidden");
      btn.disabled = false;
    }
  });

  $("logout-btn").addEventListener("click", async () => {
    await supabase.auth.signOut();
    enterSignedOut();
  });

  supabase.auth.onAuthStateChange((event, session) => {
    if (session) {
      cleanAuthParamsFromUrl();
      if ($("view-data").classList.contains("hidden")) enterSignedIn();
    } else if (event === "SIGNED_OUT") {
      enterSignedOut();
    }
  });

  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (session) {
    cleanAuthParamsFromUrl();
    await enterSignedIn();
  } else {
    enterSignedOut();
  }

  // Beim Zurueckkehren auf den Tab sofort frische Daten holen.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !$("view-data").classList.contains("hidden")) loadData();
  });
}

main();
