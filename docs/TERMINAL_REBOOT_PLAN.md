# OSRS GE Quant Reboot Plan ("Bloomberg Terminal" Edition)

## Vision
Build a true **OSRS market intelligence terminal** with:
1. **Institutional-grade data model** (prices, liquidity, fills, signals, events).
2. **Professional JavaScript front-end** (React/Next.js + polished components).
3. **Quant/stat engine** in Python + optional **R** for regime and volatility research.

## Phase 1: Data + Delivery Foundation ✅

### Completed in-repo
- DB path override via `OSRS_GE_DB_PATH`.
- FastAPI service (`osrs-ge-quant api`) for live dashboard reads.
- Snapshot export (`export-terminal-snapshot`) for static/rapid frontend bootstrapping.
- Snapshot schema contract at `dashboard/contracts/terminalSnapshot.schema.json`.

### Your immediate commands
```powershell
$env:OSRS_GE_DB_PATH = "C:\Users\londo\OneDrive\Desktop\osrs-ge-quant\data\db\osrs_ge.db"
```

```bash
PYTHONPATH=src osrs-ge-quant api --host 127.0.0.1 --port 8080
osrs-ge-quant export-terminal-snapshot --output artifacts/terminal_snapshot.json --top-items 200
```

## Phase 2: JS Terminal Application

### Recommended stack
- **Next.js (App Router) + TypeScript**
- **shadcn/ui + Tailwind CSS**
- **ECharts/TradingView Lightweight Charts**
- **TanStack Query**

### First terminal pages
1. **Market Monitor**: movers, spread heatmap, liquidity ranks.
2. **Item Blotter**: chart, microstructure stats, event markers.
3. **Signals Console**: open/taken/skipped recommendations.
4. **Portfolio**: PnL, exposure, concentration.
5. **Event Intelligence**: news/patch impact timelines.

## Phase 3: Quant + R Research Layer

### Python owns
- ETL, ingestion, strategy execution, backtesting, portfolio accounting.

### R owns
- Regime segmentation (HMM/MSM).
- Volatility models (ARCH/GARCH family).
- Cross-sectional factor diagnostics + report generation.

### Shared interface
- Canonical feature store in Parquet/DuckDB.
- Nightly R artifact pipeline (see `r/market_regime_report.R`).

## Phase 4: Production
- Scheduler/orchestration (Prefect/Airflow).
- Websocket updates for near-real-time terminal feel.
- CI: lint/test/type-check + schema validation for API/snapshots.
- Deployment with automated SQLite backups.

## Success Metrics
- Price-update-to-dashboard latency < 2 minutes.
- Strategy hit-rate + Sharpe monitored weekly.
- Nightly research report fully automated.
- Dashboard becomes primary decision console.
