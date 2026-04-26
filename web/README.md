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
