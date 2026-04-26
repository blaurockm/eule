"""GbR-Buchhaltung fuer Joint-Account real2-ibkr.

Verteilungsregeln:
- Trading-Gewinne: 60:40 (= 50:50 Kapitaleinkunft + 10% Taetigkeitsverguetung an Operator)
- Trading-Verluste: 50:50
- Externe Kosten: 50:50
- Zinsen/Dividenden: nicht erfasst (User-Entscheidung)

Datenquellen: Hase-DB + manuell gepflegtes Cash-File in ~/Dokumente/obsidian/tradingGbr/.
"""
