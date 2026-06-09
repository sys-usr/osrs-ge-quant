# src/osrs_ge_quant/ge_api.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Dict, Any, Iterable

import time

import pandas as pd
import requests

from .config import load_settings
from .db import get_session
from .models import Item, PricePoint


class OSRSWikiClient:
    """
    Client for the official OSRS Wiki real-time prices API:
      https://prices.runescape.wiki/api/v1/osrs

    Endpoints used:
      - /mapping
      - /24h
      - /5m
      - /1h
      - /6h
      - /timeseries?id=...&timestep=...
    """

    def __init__(self, user_agent: Optional[str] = None):
        settings = load_settings()
        default_ua = settings["ge"].get(
            "user_agent",
            "osrs-ge-quant/0.1 (contact: you@example.com)",
        )
        ua = user_agent or default_ua

        self.base = "https://prices.runescape.wiki/api/v1/osrs"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": ua,
                "Accept": "application/json",
            }
        )

    def _get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base}/{endpoint.lstrip('/')}"
        r = self.session.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    # ---------- Core endpoints ----------

    def fetch_mapping(self) -> pd.DataFrame:
        """
        /mapping -> list of all tradeable items and their metadata.
        """
        data = self._get("mapping")
        df = pd.DataFrame(data)
        if not df.empty:
            df["id"] = df["id"].astype(int)
        return df

    def _snapshot_to_df(self, endpoint: str) -> pd.DataFrame:
        """
        For endpoints like /24h, /5m, /1h, /6h:
        they return { "data": { "<id>": { avgHighPrice, ... , timestamp }, ... } }
        """
        data = self._get(endpoint)
        df = (
            pd.DataFrame.from_dict(data["data"], orient="index")
            .reset_index()
            .rename(columns={"index": "id"})
        )
        if df.empty:
            return df

        df["id"] = df["id"].astype(int)
        if "timestamp" in df.columns:
            df["ts"] = pd.to_datetime(df["timestamp"], unit="s")
        elif "timestamp" in data:
            df["ts"] = pd.to_datetime(data["timestamp"], unit="s")
        else:
            # Fallback: use current hour
            df["ts"] = datetime.utcnow().replace(
                minute=0, second=0, microsecond=0
            )
        return df

    def fetch_24h(self) -> pd.DataFrame:
        return self._snapshot_to_df("24h")

    def fetch_5m(self) -> pd.DataFrame:
        return self._snapshot_to_df("5m")

    def fetch_1h(self) -> pd.DataFrame:
        return self._snapshot_to_df("1h")

    def fetch_6h(self) -> pd.DataFrame:
        return self._snapshot_to_df("6h")

    def fetch_timeseries_for_item(
        self,
        item_id: int,
        timestep: str = "1h",
        timestamp: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        /timeseries?id=<id>&timestep=<5m|1h|6h>[&timestamp=unix]

        Returns up to 365 points for this item at the given timestep.
        Useful for per-item historical analysis.
        """
        params: Dict[str, Any] = {"id": item_id, "timestep": timestep}
        if timestamp is not None:
            params["timestamp"] = timestamp
        data = self._get("timeseries", params=params)
        raw = data.get("data", [])
        df = pd.DataFrame(raw)
        if df.empty:
            return df
        df["item_id"] = item_id
        if "timestamp" in df.columns:
            df["ts"] = pd.to_datetime(df["timestamp"], unit="s")
        else:
            df["ts"] = datetime.utcnow()
        return df


# ---------- WeirdGloop (deep historical, daily) ----------


class WeirdGloopClient:
    """
    Client for WeirdGloop historical GE API, which underpins the wiki and
    gives very long daily histories:

      https://api.weirdgloop.org/exchange/history/osrs/all?id=<id>

    This is ideal for "data hoarder" long history, complementing the
    high-resolution real-time OSRS Wiki snapshots.
    """

    def __init__(self, user_agent: Optional[str] = None):
        settings = load_settings()
        default_ua = settings["ge"].get(
            "user_agent",
            "osrs-ge-quant/0.1 (contact: you@example.com)",
        )
        ua = user_agent or default_ua

        self.base = "https://api.weirdgloop.org/exchange/history/osrs"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": ua,
                "Accept": "application/json",
            }
        )

    def _get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base}/{endpoint.lstrip('/')}"
        r = self.session.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def fetch_all_history_for_item(self, item_id: int) -> pd.DataFrame:
        """
        /all?id=<id> -> full daily history for this item:
          - timestamp (ISO)
          - price
          - volume

        This is lower frequency but *long* history.
        """
        data = self._get("all", params={"id": item_id})
        records = data.get(str(item_id), [])
        df = pd.DataFrame(records)
        if df.empty:
            return df
        # timestamps are ISO strings
        df["ts"] = pd.to_datetime(df["timestamp"])
        df["item_id"] = item_id
        return df


# ---------- DB integration helpers ----------


def _upsert_items_from_mapping(mapping_df: pd.DataFrame) -> None:
    """
    Upsert Item rows from the mapping DF.
    """
    if mapping_df.empty:
        return

    session = get_session()

    for row in mapping_df.itertuples():
        item = session.query(Item).filter_by(id=row.id).one_or_none()
        if item is None:
            item = Item(id=row.id)

        # Mapping fields from OSRS Wiki /mapping
        item.name = getattr(row, "name", None)
        item.examine = getattr(row, "examine", None)
        item.members = bool(getattr(row, "members", False))
        item.value = getattr(row, "value", None)
        item.highalch = getattr(row, "highalch", None)
        item.limit = getattr(row, "limit", None)
        item.icon = getattr(row, "icon", None)
        item.icon_large = getattr(row, "icon_large", None)
        item.wiki_url = getattr(row, "wiki_url", None)
        item.wiki_name = getattr(row, "wiki_name", None)

        session.add(item)

    session.commit()


def _upsert_price_snapshot(
    df: pd.DataFrame,
    timestep: str,
) -> None:
    """
    Upsert a snapshot DF into PricePoint with the given timestep.
    Respects the UNIQUE(item_id, ts, timestep) constraint.
    """
    if df.empty:
        return

    session = get_session()
    rows = 0

    for row in df.itertuples():
        item_id = int(row.id)
        if hasattr(row, "ts"):
            ts = row.ts.to_pydatetime()
        else:
            ts = datetime.utcnow().replace(minute=0, second=0, microsecond=0)

        existing = (
            session.query(PricePoint)
            .filter_by(item_id=item_id, ts=ts, timestep=timestep)
            .one_or_none()
        )

        avg_high = getattr(row, "avgHighPrice", None)
        avg_low = getattr(row, "avgLowPrice", None)
        high_vol = getattr(row, "highPriceVolume", None)
        low_vol = getattr(row, "lowPriceVolume", None)

        if existing is None:
            pp = PricePoint(
                item_id=item_id,
                ts=ts,
                avg_high=avg_high,
                avg_low=avg_low,
                high_vol=high_vol,
                low_vol=low_vol,
                timestep=timestep,
            )
            session.add(pp)
        else:
            # Upsert/refresh
            if avg_high is not None:
                existing.avg_high = avg_high
            if avg_low is not None:
                existing.avg_low = avg_low
            if high_vol is not None:
                existing.high_vol = high_vol
            if low_vol is not None:
                existing.low_vol = low_vol

        rows += 1

    session.commit()
    print(f"[GE] Snapshot upserted: timestep={timestep}, rows={rows}")


# ---------- Public API used by engine/CLI ----------


def refresh_universe(snapshot_timestep: str = "24h") -> None:
    """
    Core universe refresh:
      - fetch /mapping
      - upsert Item rows
      - fetch prices for `snapshot_timestep` (24h/5m/1h/6h)
      - upsert PricePoint rows for this snapshot

    Called by:
      - CLI: `refresh-universe`
      - Engine: `run_full_cycle()`
    """
    settings = load_settings()
    client = OSRSWikiClient()

    print("[GE] Refreshing universe (mapping + prices)...")

    mapping_df = client.fetch_mapping()
    _upsert_items_from_mapping(mapping_df)

    # choose the right endpoint
    timestep = snapshot_timestep
    if timestep == "24h":
        price_df = client.fetch_24h()
    elif timestep in ("5m", "1h", "6h"):
        price_df = client._snapshot_to_df(timestep)
    else:
        raise ValueError(f"Unsupported snapshot_timestep: {timestep}")

    _upsert_price_snapshot(price_df, timestep=timestep)

    print("[GE] Universe refresh completed (items + prices upserted).")


def update_timeseries(timestep: str = "1h") -> None:
    """
    High-frequency snapshot updater for all items.

    Uses the OSRS Wiki real-time endpoints:
      - /5m
      - /1h
      - /6h

    This is what you call from `cron-refresh` to continuously
    build a rolling 5m/1h/6h series for every item.
    """
    client = OSRSWikiClient()

    if timestep not in ("5m", "1h", "6h"):
        raise ValueError(f"Unsupported timestep for update_timeseries: {timestep}")

    endpoint = timestep  # they match: "1h" -> "/1h", etc.
    df = client._snapshot_to_df(endpoint)
    _upsert_price_snapshot(df, timestep=timestep)
    # print handled inside _upsert_price_snapshot


USER_AGENT = "osrs-ge-quant/0.1 (contact: Pimpwurt)"


def backfill_history_from_weirdgloop(
    max_items: int | None = None,
    sleep_s: float = 0.01,
    commit_every: int = 10,
) -> None:
    """
    Pull full historical price data for every known OSRS item from the
    WeirdGloop API and store it in the `prices` table as timestep='1d_weirdgloop'.

    WeirdGloop timestamps are **milliseconds since epoch**.
    """
    session = get_session()

    # 1) Clear old WG history so we don't double up
    deleted = (
        session.query(PricePoint)
        .filter(PricePoint.timestep == "1d_weirdgloop")
        .delete()
    )
    session.commit()
    print(f"[WG] Deleted {deleted} existing '1d_weirdgloop' rows")

    # 2) Load universe of items
    q = session.query(Item).order_by(Item.id)
    if max_items is not None:
        items = q.limit(max_items).all()
    else:
        items = q.all()

    total = len(items)
    print(f"[WG] Backfilling WeirdGloop history for {total} items...")

    base_url = "https://api.weirdgloop.org/exchange/history/osrs/all"
    headers = {"User-Agent": USER_AGENT}

    processed = 0
    for idx, item in enumerate(items, start=1):
        url = f"{base_url}?id={item.id}"

        try:
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()

            series = data.get(str(item.id))
            if not series:
                # No history for this item (e.g. untradeable, weird edge case)
                continue

            rows: list[PricePoint] = []
            for dp in series:
                ts_raw = dp.get("timestamp")
                price = dp.get("price")
                volume = dp.get("volume")

                if ts_raw is None or price is None:
                    continue

                # milliseconds since epoch -> UTC datetime
                ts = pd.to_datetime(ts_raw, unit="ms", utc=True)
                # store as naive UTC (to match rest of DB)
                ts = ts.tz_convert(None).to_pydatetime()

                rows.append(
                    PricePoint(
                        item_id=item.id,
                        ts=ts,
                        avg_high=price,
                        avg_low=price,
                        high_vol=volume,
                        low_vol=volume,
                        timestep="1d_weirdgloop",
                    )
                )

            if rows:
                session.bulk_save_objects(rows)

            processed += 1

            if processed % commit_every == 0:
                session.commit()
                print(f"[WG] Committed {processed}/{total} items...")

        except Exception as e:
            session.rollback()
            print(f"[WG] Error on item {item.id} ({item.name}): {e}")

        time.sleep(sleep_s)

    session.commit()
    session.close()
    print("[WG] WeirdGloop backfill complete.")


def backfill_high_resolution_history(timestep: str = "5m", top_n_items: int = 300) -> None:
    """
    Backfill high-resolution (5m, 1h, 6h) historical price data for the top N liquid items.
    Excludes blacklisted items.
    Queries OSRS Wiki prices /timeseries endpoint and upserts database records.
    """
    import time
    session = get_session()
    
    # 1. Load settings & blacklist
    settings = load_settings()
    blacklist = [item.lower() for item in settings.get("analysis", {}).get("blacklisted_items", [])]
    
    # 2. Get the latest price points for timestep '24h' to find top liquid items by 24h volume
    # Let's join Item and PricePoint
    sub = (
        session.query(
            Item.id,
            Item.name,
            (PricePoint.high_vol + PricePoint.low_vol).label("total_vol")
        )
        .join(PricePoint, PricePoint.item_id == Item.id)
        .filter(PricePoint.timestep == "24h")
        .order_by((PricePoint.high_vol + PricePoint.low_vol).desc())
    )
    
    all_liquid = sub.all()
    filtered_items = []
    for item_id, name, vol in all_liquid:
        if name and name.lower() in blacklist:
            continue
        filtered_items.append((item_id, name))
        if len(filtered_items) >= top_n_items:
            break
            
    print(f"[GE] Identified top {len(filtered_items)} liquid items to backfill for timestep '{timestep}'.")
    
    client = OSRSWikiClient()
    
    # Backfill each item
    processed = 0
    for idx, (item_id, name) in enumerate(filtered_items, 1):
        try:
            print(f"[GE] [{idx}/{len(filtered_items)}] Fetching '{timestep}' timeseries for {name} (id={item_id})...")
            df = client.fetch_timeseries_for_item(item_id, timestep=timestep)
            if df.empty:
                print(f"[GE] No timeseries data for {name} (id={item_id})")
                continue
                
            # Delete old points for this item & timestep
            session.query(PricePoint).filter_by(item_id=item_id, timestep=timestep).delete()
            
            rows = []
            for row in df.itertuples():
                avg_high = getattr(row, "avgHighPrice", None)
                avg_low = getattr(row, "avgLowPrice", None)
                high_vol = getattr(row, "highPriceVolume", None)
                low_vol = getattr(row, "lowPriceVolume", None)
                
                # Check for nan values in pandas, replace with None
                if pd.isna(avg_high): avg_high = None
                if pd.isna(avg_low): avg_low = None
                if pd.isna(high_vol): high_vol = None
                if pd.isna(low_vol): low_vol = None
                
                if avg_high is None and avg_low is None:
                    continue
                    
                ts = row.ts.to_pydatetime()
                
                rows.append(PricePoint(
                    item_id=item_id,
                    ts=ts,
                    avg_high=avg_high,
                    avg_low=avg_low,
                    high_vol=high_vol,
                    low_vol=low_vol,
                    timestep=timestep
                ))
            
            if rows:
                session.bulk_save_objects(rows)
                session.commit()
                
            processed += 1
            time.sleep(0.05) # 50ms safety sleep
        except Exception as e:
            session.rollback()
            print(f"[GE] Error backfilling {name} (id={item_id}): {e}")
            
    session.close()
    print(f"[GE] High-resolution backfill for timestep '{timestep}' completed. Processed {processed} items.")
