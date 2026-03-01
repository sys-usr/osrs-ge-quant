#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
db_path <- if (length(args) >= 1) args[[1]] else Sys.getenv("OSRS_GE_DB_PATH")
if (db_path == "") stop("Pass DB path as arg or set OSRS_GE_DB_PATH")

suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(dplyr)
  library(ggplot2)
  library(arrow)
})

dir.create("artifacts/r", recursive = TRUE, showWarnings = FALSE)

con <- dbConnect(SQLite(), db_path)
on.exit(dbDisconnect(con), add = TRUE)

query <- "
WITH ranked AS (
    SELECT
        p.item_id,
        i.name AS item_name,
        DATE(p.ts) AS d,
        AVG((COALESCE(p.avg_high, 0) + COALESCE(p.avg_low, 0))/2.0) AS mid_price,
        AVG((COALESCE(p.high_vol, 0) + COALESCE(p.low_vol, 0))) AS volume,
        ROW_NUMBER() OVER (PARTITION BY p.item_id, DATE(p.ts) ORDER BY p.ts DESC) AS rn
    FROM prices p
    JOIN items i ON i.id = p.item_id
    WHERE p.timestep = '24h'
    GROUP BY p.item_id, i.name, DATE(p.ts), p.ts
)
SELECT item_id, item_name, d, mid_price, volume
FROM ranked
WHERE rn = 1
"

df <- dbGetQuery(con, query) %>%
  arrange(item_id, d) %>%
  group_by(item_id, item_name) %>%
  mutate(ret = (mid_price / lag(mid_price)) - 1,
         vol_20 = stats::sd(ret, na.rm = TRUE)) %>%
  ungroup()

write_parquet(df, "artifacts/r/liquid_item_daily_features.parquet")

plot_df <- df %>%
  filter(!is.na(ret)) %>%
  group_by(d) %>%
  summarize(cross_sectional_vol = sd(ret, na.rm = TRUE), .groups = "drop")

p <- ggplot(plot_df, aes(x = as.Date(d), y = cross_sectional_vol)) +
  geom_line(color = "#00B3FF") +
  labs(
    title = "OSRS GE Cross-Sectional Volatility",
    x = "Date",
    y = "Std Dev of Daily Returns"
  ) +
  theme_minimal(base_size = 12)

ggsave("artifacts/r/cross_sectional_volatility.png", p, width = 10, height = 5, dpi = 120)
cat("Wrote artifacts/r/liquid_item_daily_features.parquet and artifacts/r/cross_sectional_volatility.png\n")
