# OSRS GE Quant Reboot Plan ("Bloomberg Terminal" Edition)

## Vision
Build a true **OSRS market intelligence terminal** with:
1. **Institutional-grade data model** (prices, liquidity, fills, signals, events).
2. **Professional JavaScript front-end** (React/Next.js + polished components).
3. **Quant/stat engine** in Python + optional **R** for deeper time-series and regime analysis.

## Phase 1: Stabilize Data Foundation (now)

### 1) Standardize DB access
- Your database can now be injected with `OSRS_GE_DB_PATH`.
- Example (PowerShell):

```powershell
$env:OSRS_GE_DB_PATH = "C:\Users\londo\OneDrive\Desktop\osrs-ge-quant\data\db\osrs_ge.db"
```

### 2) Produce dashboard-friendly JSON snapshot
Use:

```bash
osrs-ge-quant export-terminal-snapshot --output artifacts/terminal_snapshot.json --top-items 200
```

This gives your JS dashboards an easy source while you build APIs.

## Phase 2: JS Terminal App

### Recommended stack
- **Next.js (App Router) + TypeScript**
- **shadcn/ui + Tailwind CSS** for pro UI
- **ECharts or TradingView Lightweight Charts** for market charts
- **TanStack Query** for fast data fetching + caching

### Core pages
1. **Market Monitor**: movers, spread heatmap, liquidity ranks, watchlist.
2. **Item Blotter**: candles, volume profile, z-score bands, event markers.
3. **Signals**: live strategy outputs with confidence and expected edge.
4. **Portfolio**: PnL, exposure, concentration, realized vs unrealized.
5. **Event Intel**: patch/news timeline and item-basket impacts.

## Phase 3: Quant & Research Layer

### Python responsibilities
- ETL + collection jobs
- signal generation
- backtests
- portfolio accounting

### R responsibilities
- regime detection (MSM/HMM)
- volatility clustering (ARCH/GARCH)
- cointegration/pairs research
- Quarto statistical reporting

### Interop pattern
- Store canonical features in DuckDB/Parquet
- Use Python + R over shared tables
- Publish nightly `research_report.html`

## Phase 4: Productionization
- Airflow/Prefect orchestration for refresh/analyze/export cycles.
- Websocket push for near-real-time updates.
- CI: lint, tests, type-check, snapshot schema validation.
- Deploy API + dashboard with daily DB backups.

## Success Metrics
- Time from price update -> dashboard visibility < 2 minutes.
- Signal hit-rate + Sharpe tracked weekly.
- Zero manual steps for nightly reports.
- Dashboard becomes primary decision console.
