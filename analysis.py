# analysis.py
#
# Extra tools on top of osrs_ge_quant:
#   - param-sweep: grid of backtests -> CSV
#   - screener: z-score value screen -> CSV
#   - auto-plan: pick "best" config from sweep CSV
#   - sector-screener: basic sector buckets for undervalued items
#
# Examples (from repo root, inside osrs_ge_quant env):
#
# Param sweep (what you already ran):
#   python analysis.py param-sweep ^
#     --years 3 ^
#     --timestep 1d_weirdgloop ^
#     --initial-capital 100000000 ^
#     --k-std-grid 0.8,1.0,1.2 ^
#     --position-grid 0.03,0.05,0.08 ^
#     --fee-rate 0.01 ^
#     --top-n 300 ^
#     --out sweeps_3y_weirdgloop.csv
#
# Autoplanning from sweep CSV:
#   python analysis.py auto-plan ^
#     --csv sweeps_3y_weirdgloop.csv ^
#     --min-sharpe 1.5 ^
#     --max-dd -0.15 ^
#     --min-return 1.5
#
# Screener -> CSV:
#   python analysis.py screener ^
#     --years 1 ^
#     --timestep 1d_weirdgloop ^
#     --z-cutoff -1.0 ^
#     --limit 200 ^
#     --out screener_1y_z-1.0.csv
#
# Sector screener:
#   python analysis.py sector-screener ^
#     --years 1 ^
#     --timestep 1d_weirdgloop ^
#     --z-cutoff -1.0 ^
#     --limit-per-sector 50 ^
#     --out-dir sector_screens

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import List, Dict, Any, Callable

import pandas as pd

from osrs_ge_quant.backtest.engine import backtest_flip_strategy
from osrs_ge_quant.screeners import run_zscore_screener


# ------------------------------
# Helpers
# ------------------------------
def _parse_float_list(s: str) -> List[float]:
    return [float(x.strip()) for x in s.split(",") if x.strip()]


def _ensure_parent(path: Path) -> None:
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)


# ------------------------------
# 1) Param sweep
# ------------------------------
def cmd_param_sweep(args: argparse.Namespace) -> None:
    k_grid = _parse_float_list(args.k_std_grid)
    pos_grid = _parse_float_list(args.position_grid)

    out_path = Path(args.out)
    _ensure_parent(out_path)

    rows: List[Dict[str, Any]] = []

    print(
        f"[SWEEP] years={args.years} timestep={args.timestep} "
        f"initial_capital={args.initial_capital} fee={args.fee_rate} top_n={args.top_n}"
    )
    print(f"[SWEEP] k_std grid: {k_grid}")
    print(f"[SWEEP] position_fraction grid: {pos_grid}")
    print("-" * 72)

    for k in k_grid:
        for pos in pos_grid:
            print(
                f"[SWEEP] running backtest: k_std={k:.3f} "
                f"position_fraction={pos:.3f} ..."
            )
            res = backtest_flip_strategy(
                years=args.years,
                timestep=args.timestep,
                initial_capital=args.initial_capital,
                k_std=k,
                position_fraction=pos,
                fee_rate=args.fee_rate,
                top_n=args.top_n,
            )

            metrics = res.get("metrics", {})
            total_ret = float(metrics.get("total_return", 0.0))
            sharpe = float(metrics.get("sharpe", 0.0))
            max_dd = float(metrics.get("max_drawdown", 0.0))
            final_eq = float(metrics.get("final_equity", 0.0))

            print(
                f"  -> ret={total_ret*100:7.2f}% "
                f"sharpe={sharpe:5.2f} "
                f"maxDD={max_dd*100:7.2f}% "
                f"final_eq={final_eq:,.0f}"
            )

            rows.append(
                {
                    "years": args.years,
                    "timestep": args.timestep,
                    "initial_capital": args.initial_capital,
                    "k_std": k,
                    "position_fraction": pos,
                    "fee_rate": args.fee_rate,
                    "top_n": args.top_n,
                    "total_return": total_ret,
                    "sharpe": sharpe,
                    "max_drawdown": max_dd,
                    "final_equity": final_eq,
                }
            )

    fieldnames = [
        "years",
        "timestep",
        "initial_capital",
        "k_std",
        "position_fraction",
        "fee_rate",
        "top_n",
        "total_return",
        "sharpe",
        "max_drawdown",
        "final_equity",
    ]

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print("-" * 72)
    print(f"[SWEEP] wrote {len(rows)} rows to {out_path}")


# ------------------------------
# 2) Screener export
# ------------------------------
def cmd_screener(args: argparse.Namespace) -> None:
    out_path = Path(args.out)
    _ensure_parent(out_path)

    print(
        f"[SCREEN] years={args.years} timestep={args.timestep} "
        f"z_cutoff={args.z_cutoff} limit={args.limit}"
    )

    df = run_zscore_screener(
        years=args.years,
        timestep=args.timestep,
        z_cutoff=args.z_cutoff,
        limit=args.limit,
    )

    if df is None or df.empty:
        print("[SCREEN] screener returned no rows.")
        return

    cols = list(df.columns)
    ordered: List[str] = []
    for c in ["item_id", "item_name", "price", "z_score"]:
        if c in cols:
            ordered.append(c)
    for c in cols:
        if c not in ordered:
            ordered.append(c)

    df = df[ordered]

    df.to_csv(out_path, index=False)
    print(f"[SCREEN] wrote {len(df)} rows to {out_path}")


# ------------------------------
# 3) Autoplanning from sweep CSV
# ------------------------------
def cmd_auto_plan(args: argparse.Namespace) -> None:
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[AUTO] CSV not found: {csv_path}")
        return

    df = pd.read_csv(csv_path)

    required_cols = {"k_std", "position_fraction", "total_return", "sharpe", "max_drawdown", "final_equity"}
    if not required_cols.issubset(set(df.columns)):
        print(f"[AUTO] CSV missing required columns: {required_cols - set(df.columns)}")
        return

    print(f"[AUTO] Loaded {len(df)} rows from {csv_path}")

    # Filter by constraints
    sel = df.copy()

    # total_return, sharpe, max_drawdown all in decimal space
    if args.min_return is not None:
        sel = sel[sel["total_return"] >= args.min_return]
    if args.min_sharpe is not None:
        sel = sel[sel["sharpe"] >= args.min_sharpe]
    if args.max_dd is not None:
        # max_drawdown is negative; "less negative" is better, so we want >= threshold
        sel = sel[sel["max_drawdown"] >= args.max_dd]

    if sel.empty:
        print("[AUTO] No configs met constraints. Falling back to best by Sharpe only.")
        sel = df.copy()

    # Rank: best Sharpe, then higher total_return, then lower position_fraction (tie-break)
    sel = sel.sort_values(
        by=["sharpe", "total_return", "position_fraction"],
        ascending=[False, False, True],
    )

    best = sel.iloc[0]

    print("\n[AUTO] Recommended configuration:")
    print("----------------------------------------")
    print(f"k_std:              {best['k_std']}")
    print(f"position_fraction:  {best['position_fraction']}")
    print(f"total_return:       {best['total_return']*100:.2f}%")
    print(f"sharpe:             {best['sharpe']:.2f}")
    print(f"max_drawdown:       {best['max_drawdown']*100:.2f}%")
    print(f"final_equity:       {best['final_equity']:,.0f} gp")

    # Infer fixed fields if present, otherwise just show placeholders
    years = int(best["years"]) if "years" in best else args.years
    timestep = str(best["timestep"]) if "timestep" in best else args.timestep
    init_cap = int(best["initial_capital"]) if "initial_capital" in best else args.initial_capital
    fee_rate = float(best["fee_rate"]) if "fee_rate" in best else args.fee_rate
    top_n = int(best["top_n"]) if "top_n" in best else args.top_n

    print("\n[AUTO] Equivalent CLI command:")
    print("----------------------------------------")
    print(
        "osrs-ge-quant backtest "
        f"--years {years} "
        f"--timestep {timestep} "
        f"--initial-capital {init_cap} "
        f"--k-std {best['k_std']} "
        f"--position-fraction {best['position_fraction']} "
        f"--fee-rate {fee_rate} "
        f"--top-n {top_n}"
    )

    print("\n[AUTO] Top 5 candidates under current constraints:")
    print("----------------------------------------")
    preview = sel.head(5)[
        ["k_std", "position_fraction", "total_return", "sharpe", "max_drawdown", "final_equity"]
    ]
    for _, r in preview.iterrows():
        print(
            f"k={r['k_std']:.2f} pos={r['position_fraction']:.3f}  "
            f"ret={r['total_return']*100:7.2f}%  "
            f"sharpe={r['sharpe']:5.2f}  "
            f"dd={r['max_drawdown']*100:7.2f}%  "
            f"eq={r['final_equity']:,.0f}"
        )


# ------------------------------
# 4) Sector screener (simple name-based buckets)
# ------------------------------

# crude text-based sectors; easy to tweak later
def _sector_herbs(name: str) -> bool:
    n = name.lower()
    return ("grimy " in n) or ("leaf" in n) or ("weed" in n) or ("tar" in n)


def _sector_runes(name: str) -> bool:
    n = name.lower()
    return n.endswith(" rune") or " rune (" in n


def _sector_fletching(name: str) -> bool:
    n = name.lower()
    return any(word in n for word in ["arrow", "dart", "bolt", "javelin", "longbow", "shortbow"])


def _sector_potions(name: str) -> bool:
    n = name.lower()
    return any(word in n for word in ["potion", "brew", "serum", "overload"])


SECTORS: Dict[str, Callable[[str], bool]] = {
    "herbs": _sector_herbs,
    "runes": _sector_runes,
    "fletching": _sector_fletching,
    "potions": _sector_potions,
}


def cmd_sector_screener(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"[SECTORS] years={args.years} timestep={args.timestep} "
        f"z_cutoff={args.z_cutoff} limit_per_sector={args.limit_per_sector}"
    )

    # Pull a decently wide universe once
    base_limit = max(args.limit_per_sector * 5, args.limit_per_sector + 100)
    df = run_zscore_screener(
        years=args.years,
        timestep=args.timestep,
        z_cutoff=args.z_cutoff,
        limit=base_limit,
    )

    if df is None or df.empty:
        print("[SECTORS] screener returned no rows.")
        return

    if "item_name" not in df.columns:
        print("[SECTORS] screener DataFrame missing 'item_name' column.")
        return

    total_rows = len(df)
    print(f"[SECTORS] got {total_rows} rows from base screener")

    # For each sector, filter & save
    for sector_name, fn in SECTORS.items():
        mask = df["item_name"].astype(str).map(fn)
        sub = df[mask].copy()

        if sub.empty:
            print(f"[SECTORS] sector={sector_name}: 0 rows (skipped)")
            continue

        # Sort by z_score ascending (most negative = most beat up)
        if "z_score" in sub.columns:
            sub = sub.sort_values("z_score", ascending=True)

        sub = sub.head(args.limit_per_sector)

        out_path = out_dir / f"screener_{sector_name}.csv"
        sub.to_csv(out_path, index=False)
        print(f"[SECTORS] sector={sector_name}: wrote {len(sub)} rows -> {out_path}")

    print("[SECTORS] done.")


# ------------------------------
# CLI glue
# ------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("osrs-ge-quant-analysis")

    sub = p.add_subparsers(dest="cmd", required=True)

    # param-sweep
    sweep = sub.add_parser("param-sweep", help="Run a grid of backtests and write metrics to CSV")
    sweep.add_argument("--years", type=int, default=3)
    sweep.add_argument("--timestep", default="1d_weirdgloop")
    sweep.add_argument("--initial-capital", type=int, default=100_000_000)
    sweep.add_argument("--k-std-grid", required=True,
                       help="Comma-separated list, e.g. '0.8,1.0,1.2'")
    sweep.add_argument("--position-grid", required=True,
                       help="Comma-separated list, e.g. '0.03,0.05,0.08'")
    sweep.add_argument("--fee-rate", type=float, default=0.01)
    sweep.add_argument("--top-n", type=int, default=300)
    sweep.add_argument("--out", required=True)

    # screener
    scr = sub.add_parser("screener", help="Run z-score screener and save CSV")
    scr.add_argument("--years", type=int, default=1)
    scr.add_argument("--timestep", default="1d_weirdgloop")
    scr.add_argument("--z-cutoff", type=float, default=-1.0)
    scr.add_argument("--limit", type=int, default=100)
    scr.add_argument("--out", required=True)

    # auto-plan (pick best config from sweep CSV)
    ap = sub.add_parser("auto-plan", help="Pick a best backtest config from sweep CSV")
    ap.add_argument("--csv", required=True, help="Sweep CSV (from param-sweep)")
    ap.add_argument("--min-sharpe", type=float, default=1.0,
                    help="Minimum Sharpe (decimal, e.g. 1.5)")
    ap.add_argument("--max-dd", type=float, default=-0.20,
                    help="Maximum drawdown allowed (negative decimal, e.g. -0.15)")
    ap.add_argument("--min-return", type=float, default=0.0,
                    help="Minimum total_return (e.g. 1.0 = 100%)")

    # These only used as fallbacks if sweep CSV is missing them:
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--timestep", default="1d_weirdgloop")
    ap.add_argument("--initial-capital", type=int, default=100_000_000)
    ap.add_argument("--fee-rate", type=float, default=0.01)
    ap.add_argument("--top-n", type=int, default=300)

    # sector screener
    ss = sub.add_parser("sector-screener", help="Run name-based sector screens and write CSVs per sector")
    ss.add_argument("--years", type=int, default=1)
    ss.add_argument("--timestep", default="1d_weirdgloop")
    ss.add_argument("--z-cutoff", type=float, default=-1.0)
    ss.add_argument("--limit-per-sector", type=int, default=50)
    ss.add_argument("--out-dir", required=True)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.cmd == "param-sweep":
        cmd_param_sweep(args)
    elif args.cmd == "screener":
        cmd_screener(args)
    elif args.cmd == "auto-plan":
        cmd_auto_plan(args)
    elif args.cmd == "sector-screener":
        cmd_sector_screener(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
