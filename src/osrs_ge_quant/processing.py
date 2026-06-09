# src/osrs_ge_quant/processing.py
"""
Processing (skilling) profitability evaluation.

Takes 24h GE price snapshot + your skill levels and evaluates GP/batch
for things like:
- Unicorn horn -> Unicorn dust
- Planks
- Fletching, Smithing, etc.

This is intentionally minimal / safe: it just computes profit_per_batch.
You can extend to GP/hr later.
"""

from typing import Dict, List

import pandas as pd

from .config import load_processing_recipes, load_settings
from .strategy import calculate_osrs_tax


def evaluate_processing_opportunities(df_24h: pd.DataFrame, all_accounts_skills: Dict[str, Dict[str, int]]) -> pd.DataFrame:
    """
    df_24h: DataFrame with at least:
      - 'item_id' (or 'id')
      - 'avgHighPrice'
      - 'avgLowPrice'

    all_accounts_skills: Dict mapping account name -> skill levels dict.
      e.g. {"Pimpwurt": {"Herblore": 70, ...}}

    Returns DataFrame with columns:
      - recipe_name
      - required_skill
      - required_level
      - profit_per_batch
      - eligible_accounts
    """
    settings = load_settings()
    tax = settings["ge"]["tax_rate"]

    # Normalize id column
    work = df_24h.copy()
    if "item_id" in work.columns:
        id_col = "item_id"
    else:
        id_col = "id"
    price_map_high = work.set_index(id_col)["avgHighPrice"].to_dict()
    price_map_low = work.set_index(id_col)["avgLowPrice"].to_dict()

    recipes = load_processing_recipes()
    rows: List[Dict] = []

    for recipe in recipes:
        recipe_name = recipe["name"]
        required_skill = recipe["required_skill"]
        required_level = recipe["required_level"]

        # Check which active accounts can perform this recipe
        eligible_accounts = []
        for name, acc_skills in all_accounts_skills.items():
            player_level = acc_skills.get(required_skill, 1)
            if player_level >= required_level:
                eligible_accounts.append(name)

        if not eligible_accounts:
            # No active account has the required level for this recipe
            continue

        # Compute input cost
        input_cost = 0.0
        missing_price = False
        for inp in recipe["inputs"]:
            item_id = int(inp["item_id"])
            qty = int(inp["qty"])
            price = price_map_high.get(item_id)
            if price is None:
                missing_price = True
                break
            input_cost += price * qty

        if missing_price:
            continue

        # Compute output value (sell at avgLowPrice after tax)
        output_value = 0.0
        for out in recipe["outputs"]:
            item_id = int(out["item_id"])
            qty = int(out["qty"])
            sell_price = price_map_low.get(item_id)
            if sell_price is None:
                missing_price = True
                break
            sell_price_after_tax = sell_price - calculate_osrs_tax(sell_price)
            output_value += sell_price_after_tax * qty

        if missing_price:
            continue

        extra_cost = float(recipe.get("extra_cost_gp", 0) or 0)
        profit_per_batch = output_value - input_cost - extra_cost

        rows.append(
            {
                "recipe_name": recipe_name,
                "required_skill": required_skill,
                "required_level": required_level,
                "profit_per_batch": profit_per_batch,
                "input_cost": input_cost,
                "output_value": output_value,
                "eligible_accounts": ", ".join(eligible_accounts)
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "recipe_name",
                "required_skill",
                "required_level",
                "profit_per_batch",
                "input_cost",
                "output_value",
            ]
        )

    df = pd.DataFrame(rows)
    df.sort_values("profit_per_batch", ascending=False, inplace=True)
    return df
