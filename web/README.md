# tradingGbr Share-App

Statische Web-App fuer Vercel. Zeigt jedem Holder seinen aktuellen Saldo unter einer eigenen Token-URL.

## Aufbau

- `index.html` / `style.css` / `app.js` — Single-Page-App, vanilla, kein Build
- `balances.json` — wird von `eule accounting refresh` ueberschrieben (Pfad in `tradingGbr/config.yaml`)
- `vercel.json` — verhindert Caching der balances.json

## Deployment

Vercel-Project auf das Repo zeigen und das Root-Verzeichnis auf `web/` setzen (Project Settings → Root Directory). Framework Preset: "Other". Kein Build Command, kein Output Directory.

Workflow:
```
eule accounting refresh    # schreibt web/balances.json
git add web/balances.json
git commit -m "snapshot"
git push                    # Vercel deployed automatisch
```

## URL-Format

`https://<projekt>.vercel.app/?t=<token>` — Token aus `tradingGbr/tokens.yaml`.
Ohne Token oder mit unbekanntem Token wird eine leere Seite angezeigt.

## Runtime-Status (`status.html`)

Zweite, komplett unabhaengige Seite im selben Vercel-Projekt: zeigt den Zustand
der Hase-Trading-Runtimes (PnL, Strategie-Status, Heartbeat-Alter). Kein Bezug
zum Token-Schema der Share-App, keine GbR-Daten, keine Querlinks.

- `status.html` / `status.js` — vanilla ESM, kein Build; supabase-js v2 per CDN
- Datenquelle: Supabase-Tabelle `runtime_heartbeats` (der Hase-Runtime schreibt
  dort per `SupabaseHeartbeatPolicy` seinen Heartbeat-Blob als Upsert)
- Zugriff: Supabase-Auth via Google. Der anon/publishable Key steht im Klartext
  in `status.js` — das Gate ist die RLS-Policy (`SELECT` nur `authenticated`).
- Aufruf: `https://<projekt>.vercel.app/status.html`

Voraussetzung in Supabase (Auth → URL Configuration): die Vercel-Domain muss als
Redirect-URL erlaubt sein, sonst schlaegt der Google-Login-Rueckweg fehl.
