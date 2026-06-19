import time
import random
import requests
import numpy as np
import pyautogui

# Set PyAutoGUI fail-safe to corner to allow safety interrupt
pyautogui.FAILSAFE = True

def wind_mouse(start_x, start_y, dest_x, dest_y, G_0=9.0, W_0=3.0, M_0=15.0, D_0=12.0):
    """
    Generates a realistic path of points from (start_x, start_y) to (dest_x, dest_y)
    simulating human-like speed, gravity, wind (noise), and overshoot.
    """
    current_x, current_y = start_x, start_y
    wind_x, wind_y = 0.0, 0.0
    velocity_x, velocity_y = 0.0, 0.0
    path = []
    
    dist = np.hypot(dest_x - start_x, dest_y - start_y)
    while dist > 1.0:
        wind_x = wind_x / np.sqrt(3.0) + (random.random() * 2.0 - 1.0) * W_0 / np.sqrt(5.0)
        wind_y = wind_y / np.sqrt(3.0) + (random.random() * 2.0 - 1.0) * W_0 / np.sqrt(5.0)
        
        gravity_x = (dest_x - current_x) * G_0 / dist
        gravity_y = (dest_y - current_y) * G_0 / dist
        
        acc_x = wind_x + gravity_x
        acc_y = wind_y + gravity_y
        
        velocity_x += acc_x
        velocity_y += acc_y
        
        speed = np.hypot(velocity_x, velocity_y)
        if speed > M_0:
            velocity_x = (velocity_x / speed) * M_0
            velocity_y = (velocity_y / speed) * M_0
            
        current_x += velocity_x
        current_y += velocity_y
        
        path.append((int(current_x), int(current_y)))
        dist = np.hypot(dest_x - current_x, dest_y - current_y)
        
    path.append((dest_x, dest_y))
    return path

def human_move_to(dest_x, dest_y, w=0, h=0):
    """
    Moves mouse using WindMouse spline paths with custom random box boundaries offset.
    """
    start_x, start_y = pyautogui.position()
    # Apply minor offset randomized inside the bounds of the target widget
    ox = random.randint(-w // 4, w // 4) if w > 4 else random.randint(-2, 2)
    oy = random.randint(-h // 4, h // 4) if h > 4 else random.randint(-2, 2)
    
    path = wind_mouse(start_x, start_y, dest_x + ox, dest_y + oy)
    for pt in path:
        pyautogui.moveTo(pt[0], pt[1])
        time.sleep(random.uniform(0.001, 0.004))
    time.sleep(random.uniform(0.05, 0.15))

def human_click():
    pyautogui.click()
    time.sleep(random.uniform(0.1, 0.25))

def human_type(text):
    for char in text:
        pyautogui.write(char)
        # Gaussian distribution centered at 65ms with 20ms std dev, clipped at [20ms, 150ms]
        delay = max(0.02, min(0.15, random.gauss(0.065, 0.02)))
        time.sleep(delay)
    # End of string delay (Gaussian centered at 220ms, clipped [100ms, 450ms])
    end_delay = max(0.1, min(0.45, random.gauss(0.220, 0.05)))
    time.sleep(end_delay)

def perform_micro_distractions(coords=None):
    """
    Simulates random human-like micro-distractions to defeat anti-cheat filters:
    - Slowly drifts the mouse pointer randomly.
    - Moves the mouse pointer off-screen/idle.
    - Clicks a game tab (like Stats/Inventory) if coordinates are provided, and returns.
    """
    roll = random.random()
    if roll > 0.30: # 30% chance of distraction when called
        return
        
    print("[Automation] Simulating human-like micro-distraction...")
    if not coords:
        # Simple mouse drift
        try:
            cur_x, cur_y = pyautogui.position()
            drift_x = cur_x + random.randint(-150, 150)
            drift_y = cur_y + random.randint(-150, 150)
            screen_w, screen_h = pyautogui.size()
            drift_x = max(10, min(screen_w - 10, drift_x))
            drift_y = max(10, min(screen_h - 10, drift_y))
            
            path = wind_mouse(cur_x, cur_y, drift_x, drift_y)
            for pt in path:
                pyautogui.moveTo(pt[0], pt[1])
                time.sleep(random.uniform(0.002, 0.005))
            time.sleep(random.uniform(0.8, 2.2))
        except Exception:
            pass
        return

    # If coordinates are available, randomly click tabs or drift
    action = random.choice(["drift", "idle", "check_tabs"])
    if action == "drift":
        try:
            cur_x, cur_y = pyautogui.position()
            drift_x = cur_x + random.randint(-200, 200)
            drift_y = cur_y + random.randint(-200, 200)
            screen_w, screen_h = pyautogui.size()
            drift_x = max(10, min(screen_w - 10, drift_x))
            drift_y = max(10, min(screen_h - 10, drift_y))
            
            path = wind_mouse(cur_x, cur_y, drift_x, drift_y)
            for pt in path:
                pyautogui.moveTo(pt[0], pt[1])
                time.sleep(random.uniform(0.002, 0.005))
            time.sleep(random.uniform(0.5, 1.5))
        except Exception:
            pass
        
    elif action == "idle":
        try:
            screen_w, screen_h = pyautogui.size()
            path = wind_mouse(*pyautogui.position(), screen_w - 5, random.randint(100, screen_h - 100))
            for pt in path:
                pyautogui.moveTo(pt[0], pt[1])
                time.sleep(random.uniform(0.001, 0.003))
            time.sleep(random.uniform(1.5, 3.5))
        except Exception:
            pass
        
    elif action == "check_tabs":
        stats_w = coords.get("stats_tab")
        inv_w = coords.get("inventory_tab")
        if stats_w and inv_w:
            print("[Automation] Distraction: checking stats tab...")
            human_move_to(stats_w["x"], stats_w["y"], stats_w.get("w", 0), stats_w.get("h", 0))
            human_click()
            time.sleep(random.uniform(1.2, 2.5))
            
            print("[Automation] Distraction: returning to inventory tab...")
            human_move_to(inv_w["x"], inv_w["y"], inv_w.get("w", 0), inv_w.get("h", 0))
            human_click()
            time.sleep(random.uniform(0.5, 1.0))
        else:
            time.sleep(random.uniform(1.0, 2.0))

class GeAutomationBot:
    def __init__(self, api_url="http://127.0.0.1:8050"):
        self.api_url = api_url

    def fetch_coordinates(self):
        try:
            r = requests.get(f"{self.api_url}/api/runelite/automation-state")
            if r.status_code == 200:
                coords = r.json().get("coordinates", {})
                if coords:
                    return coords
        except Exception as e:
            print(f"Error fetching coordinates from server: {e}")
            
        print("[Automation] Falling back to CV screen coordinate scan...")
        try:
            from .cv_sentry import detect_ui_coordinates
            return detect_ui_coordinates()
        except Exception as cv_err:
            print(f"[Automation] CV coordinate scan failed: {cv_err}")
        return {}

    def execute_click(self, widget_key, coords=None):
        if coords is None:
            coords = self.fetch_coordinates()
        
        w_data = coords.get(widget_key)
        if not w_data:
            print(f"Widget coordinate '{widget_key}' not found or active.")
            return False
            
        print(f"Moving to click '{widget_key}' at ({w_data['x']}, {w_data['y']}).")
        human_move_to(w_data['x'], w_data['y'], w_data.get('w', 0), w_data.get('h', 0))
        human_click()
        return True

    def place_offer(self, side, slot, item_name, qty, price, trailing_stop_loss_pct=None, is_snipe=False):
        """
        Full workflow to place a GE offer:
        1. Click buy/sell button on specific slot.
        2. If buy: search item and click results.
        3. Click quantity box, type quantity, hit enter.
        4. Click price box, type price, hit enter.
        5. Click confirm button.
        """
        # Sentry check before placing the buy offer
        if side.lower() == "buy":
            try:
                from .db import get_session
                from .models import Item
                from .strategy import check_order_book_walls_and_velocity
                session = get_session()
                item = session.query(Item).filter(Item.name.ilike(item_name)).first()
                session.close()
                if item:
                    sentry = check_order_book_walls_and_velocity(item.id)
                    if not sentry["is_safe"]:
                        print(f"[Automation] Aborting buy order for {item_name} due to sentry alert: {sentry['reason']}")
                        return False
            except Exception as e:
                print(f"[Automation] Failed to perform sentry check before buy order: {e}")

        # Check if is_snipe or trailing stop-loss is specified
        if is_snipe:
            print(f"[Automation] Initiating Snipe order for {item_name} at price {price}")
        if trailing_stop_loss_pct is not None:
            print(f"[Automation] Setting trailing stop-loss at {trailing_stop_loss_pct * 100:.1f}% for {item_name}")

        coords = self.fetch_coordinates()
        if not coords:
            print("Cannot automate: client coordinates not synced.")
            return False

        if side.lower() == "buy":
            # 1. Click Buy Button
            if not self.execute_click(f"buy_button_{slot}", coords):
                return False
            time.sleep(random.uniform(0.5, 0.8))
            
            # 2. Type Item Name
            human_type(item_name)
            time.sleep(random.uniform(0.8, 1.2))
            
            # Perform a micro-distraction to mimic human delay
            perform_micro_distractions(coords)
            
            # 3. Select Item from Results (the search highlights item_search)
            if not self.execute_click("item_search", coords):
                return False
            time.sleep(random.uniform(0.5, 0.8))
            
        else: # sell
            # 1. Click Sell Button
            if not self.execute_click(f"sell_button_{slot}", coords):
                return False
            time.sleep(random.uniform(0.5, 0.8))
            
            # Note: For selling, the user must click the item in their inventory.
            # We assume they clicked the item or it is already active.
            # Or the plugin coordinates search for the active item.

        # 3. Set Quantity
        if not self.execute_click("quantity_input", coords):
            return False
        time.sleep(random.uniform(0.3, 0.5))
        human_type(str(qty))
        pyautogui.press('enter')
        time.sleep(random.uniform(0.4, 0.6))

        # 4. Set Price
        if not self.execute_click("price_input", coords):
            return False
        time.sleep(random.uniform(0.3, 0.5))
        human_type(str(price))
        pyautogui.press('enter')
        time.sleep(random.uniform(0.4, 0.6))

        # Perform another micro-distraction before confirming the trade
        perform_micro_distractions(coords)

        # 5. Confirm Trade
        if not self.execute_click("confirm_button", coords):
            return False
        print(f"Trade offer placed successfully in slot {slot}!")
        return True


def check_trailing_stop_losses(trailing_pct: float = 0.05) -> list:
    """
    Checks trailing stop-loss condition for all active holdings:
    - Finds the highest average high price since the purchase timestamp.
    - If current price is below peak by more than trailing_pct, triggers liquidation alert/action.
    """
    from datetime import datetime
    from .ledger import get_consolidated_ledger
    from .db import get_session
    from .models import Trade, PricePoint
    
    session = get_session()
    ledger = get_consolidated_ledger()
    holdings = ledger.get("holdings", [])
    
    triggered = []
    
    for h in holdings:
        item_id = h["item_id"]
        acc_id = h["account_id"]
        qty = h["qty"]
        avg_cost = h["avg_cost"]
        current_price = h["current_price"]
        
        # Get last buy trade to find starting timestamp
        last_buy = (
            session.query(Trade)
            .filter(
                Trade.account_id == acc_id,
                Trade.item_id == item_id,
                Trade.side == "buy"
            )
            .order_by(Trade.ts.desc())
            .first()
        )
        if not last_buy:
            continue
            
        # Get peak price since last_buy.ts
        peak_row = (
            session.query(PricePoint.avg_high)
            .filter(
                PricePoint.item_id == item_id,
                PricePoint.ts >= last_buy.ts,
                PricePoint.timestep == "5m"
            )
            .order_by(PricePoint.avg_high.desc())
            .first()
        )
        if not peak_row:
            peak_row = (
                session.query(PricePoint.avg_high)
                .filter(
                    PricePoint.item_id == item_id,
                    PricePoint.ts >= last_buy.ts
                )
                .order_by(PricePoint.avg_high.desc())
                .first()
            )
        
        # If no peak is found in DB, use avg_cost as initial peak
        peak_price = float(peak_row[0]) if peak_row and peak_row[0] is not None else float(avg_cost)
        
        # Update peak price to be at least current_price (since trailing peak cannot be lower than current)
        peak_price = max(peak_price, current_price)
        
        stop_loss_price = peak_price * (1.0 - trailing_pct)
        
        if current_price < stop_loss_price:
            # Trailing stop loss triggered!
            triggered.append({
                "account_id": acc_id,
                "account_name": h["account_name"],
                "item_id": item_id,
                "item_name": h["item_name"],
                "qty": qty,
                "avg_cost": avg_cost,
                "current_price": current_price,
                "peak_price": peak_price,
                "stop_loss_price": stop_loss_price,
                "action": "LIQUIDATE_SELL"
            })
            
    session.close()
    return triggered
