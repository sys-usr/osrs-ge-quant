# JS Dashboard Starter (Professional Front-End)

This repo now exposes backend data in two ways:
1. **FastAPI service** (`osrs-ge-quant api`) for live frontend consumption.
2. **Exported snapshot JSON** (`export-terminal-snapshot`) for rapid prototyping.

## Suggested frontend bootstrap (Next.js + TS)

```bash
npx create-next-app@latest ge-terminal --typescript --tailwind --eslint --app
cd ge-terminal
npm install @tanstack/react-query echarts echarts-for-react zod
```

## Data integration options

### Option A: consume API directly (recommended)
- `GET /api/snapshot?top_items=200`
- `GET /api/items/{item_id}/history?timestep=24h&limit=500`
- `GET /api/recommendations?status=open`

### Option B: consume exported snapshot JSON
1. Run:
   ```bash
   osrs-ge-quant export-terminal-snapshot --output artifacts/terminal_snapshot.json --top-items 200
   ```
2. Load this file from a Next.js route handler.

## Contract
Validate payloads against:
- `dashboard/contracts/terminalSnapshot.schema.json`

## Initial widgets to implement
- KPI cards: active signals, est. total edge, 24h traded volume.
- Liquidity table with sorting/filtering.
- Item chart with selectable timestep.
- Recommendation feed with taken/skipped badges.
- Account PnL leaderboard.

## UI quality bar
- Dark theme by default.
- Keyboard shortcuts for navigation + quick item lookup.
- Responsive layout with dense mode.
- Strong empty/loading/error states.
