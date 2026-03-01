# osrs-ge-quant

Data analytics + quant research framework for the Old School RuneScape Grand Exchange.

## Point this project at your existing DB

You can override the DB path with an environment variable.

### Windows PowerShell
```powershell
$env:OSRS_GE_DB_PATH = "C:\Users\londo\OneDrive\Desktop\osrs-ge-quant\data\db\osrs_ge.db"
```

### macOS/Linux
```bash
export OSRS_GE_DB_PATH="/absolute/path/to/osrs_ge.db"
```

## Export data for a professional JS dashboard

```bash
osrs-ge-quant export-terminal-snapshot --output artifacts/terminal_snapshot.json --top-items 200
```

The exported JSON is designed for React/Next.js dashboards.

## Reboot plan

See `docs/TERMINAL_REBOOT_PLAN.md` for a staged path to a Bloomberg-style terminal, including JS UI architecture and where to leverage R for advanced stats.
