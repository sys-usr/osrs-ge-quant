# JS Dashboard Starter (Professional Front-End)

This project should move to a dedicated JS front-end app.

## Suggested bootstrap

```bash
npx create-next-app@latest ge-terminal --typescript --tailwind --eslint --app
cd ge-terminal
npm install @tanstack/react-query echarts echarts-for-react zod
```

## Data source options

### Option A (fastest): use exported snapshot JSON
1. Run:
   ```bash
   osrs-ge-quant export-terminal-snapshot --output artifacts/terminal_snapshot.json --top-items 200
   ```
2. Have Next.js API route read that file and return JSON.

### Option B (recommended next): Python API service
Expose endpoints from this repo (FastAPI) and let Next.js consume them.

## Initial widgets to implement
- KPI cards: active signals, est. total edge, 24h traded volume.
- Liquidity table with sorting/filtering.
- Item chart with selectable timestep.
- Recommendation feed with taken/skipped status badges.
- Account PnL leaderboard.

## UI quality bar
- Dark theme by default.
- Keyboard shortcuts (search item, switch page).
- Responsive layout with dense data mode.
- Meaningful empty/loading/error states.
