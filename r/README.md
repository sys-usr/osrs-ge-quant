# R Research Track

Use R for the heavy statistical workflows while Python handles ingestion/execution.

## Setup

```r
install.packages(c("DBI", "RSQLite", "dplyr", "ggplot2", "arrow", "duckdb", "quarto"))
```

## First script

Run:

```bash
Rscript r/market_regime_report.R "C:/Users/londo/OneDrive/Desktop/osrs-ge-quant/data/db/osrs_ge.db"
```

This script exports a feature table (`artifacts/r/liquid_item_daily_features.parquet`) and a plot for quick regime sanity checks.
