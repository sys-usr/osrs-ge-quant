# osrs-ge-quant

Data analytics + quant research framework for the Old School RuneScape Grand Exchange.

## 1) Point this project at your existing DB

Use an environment variable so the app can use your existing SQLite database directly.

### Windows PowerShell
```powershell
$env:OSRS_GE_DB_PATH = "C:\Users\londo\OneDrive\Desktop\osrs-ge-quant\data\db\osrs_ge.db"
```

### macOS/Linux
```bash
export OSRS_GE_DB_PATH="/absolute/path/to/osrs_ge.db"
```

## 2) Start the API for a professional JS frontend

```bash
PYTHONPATH=src osrs-ge-quant api --host 127.0.0.1 --port 8080
```

Key endpoints:
- `GET /health`
- `GET /api/meta`
- `GET /api/snapshot?top_items=200`
- `GET /api/items/{item_id}/history?timestep=24h&limit=500`
- `GET /api/recommendations?status=open`

## 3) Export snapshot JSON (no API needed)

```bash
osrs-ge-quant export-terminal-snapshot --output artifacts/terminal_snapshot.json --top-items 200
```

The exported JSON matches `dashboard/contracts/terminalSnapshot.schema.json`.

## 4) Frontend + R tracks

- Frontend starter guidance: `dashboard/README.md`
- R stats workflow starter: `r/README.md` and `r/market_regime_report.R`
- Full reboot strategy: `docs/TERMINAL_REBOOT_PLAN.md`
