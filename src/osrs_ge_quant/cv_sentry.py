# src/osrs_ge_quant/cv_sentry.py
import numpy as np
import pyautogui
from PIL import Image

def find_ge_interface_on_screen():
    """
    Scrapes the primary screen to locate the Grand Exchange UI bounding box.
    Returns:
      tuple: (x, y, width, height) of the detected GE window, or None
    """
    try:
        screenshot = pyautogui.screenshot()
        arr = np.array(screenshot)
        
        # OSRS brown border colors range:
        # R: 70-100, G: 60-80, B: 45-65
        r_mask = (arr[:, :, 0] >= 75) & (arr[:, :, 0] <= 95)
        g_mask = (arr[:, :, 1] >= 65) & (arr[:, :, 1] <= 80)
        b_mask = (arr[:, :, 2] >= 50) & (arr[:, :, 2] <= 65)
        
        match_mask = r_mask & g_mask & b_mask
        y_indices, x_indices = np.where(match_mask)
        
        if len(x_indices) < 100:
            return None
            
        min_x, max_x = np.min(x_indices), np.max(x_indices)
        min_y, max_y = np.min(y_indices), np.max(y_indices)
        
        detected_w = max_x - min_x
        detected_h = max_y - min_y
        
        if 400 <= detected_w <= 1200 and 250 <= detected_h <= 900:
            return (int(min_x), int(min_y), int(detected_w), int(detected_h))
    except Exception as e:
        print(f"[CV Sentry] Error scanning screen: {e}")
        
    return None

def detect_ui_coordinates() -> dict:
    """
    Scans screen and returns absolute target coordinate maps for GE slots.
    If scanning fails, falls back to standard offsets on a 1920x1080 screen.
    """
    ge_box = find_ge_interface_on_screen()
    
    screen_w, screen_h = pyautogui.size()
    center_x = screen_w // 2
    center_y = screen_h // 2
    
    coords = {}
    
    if ge_box:
        x, y, w, h = ge_box
        print(f"[CV Sentry] Detected Grand Exchange UI boundary at x={x}, y={y}, w={w}, h={h}")
        slot_w = w // 4
        slot_h = h // 2
        for slot in range(8):
            row = slot // 4
            col = slot % 4
            slot_x = x + col * slot_w + (slot_w // 2)
            slot_y = y + row * slot_h + (slot_h // 2)
            
            coords[f"buy_button_{slot}"] = {"x": slot_x - 30, "y": slot_y, "w": 40, "h": 20}
            coords[f"sell_button_{slot}"] = {"x": slot_x + 30, "y": slot_y, "w": 40, "h": 20}
            
        coords["item_search"] = {"x": x + w // 2, "y": y + h - 50, "w": 150, "h": 30}
        coords["confirm_button"] = {"x": x + w // 2, "y": y + h - 20, "w": 100, "h": 25}
        coords["stats_tab"] = {"x": x + w + 100, "y": y + 50, "w": 30, "h": 30}
        coords["inventory_tab"] = {"x": x + w + 100, "y": y + 200, "w": 30, "h": 30}
    else:
        print("[CV Sentry] Could not locate GE interface. Using screen center coordinates fallback.")
        x = center_x - 250
        y = center_y - 160
        w = 500
        h = 320
        for slot in range(8):
            row = slot // 4
            col = slot % 4
            slot_x = x + col * 120 + 60
            slot_y = y + row * 120 + 60
            coords[f"buy_button_{slot}"] = {"x": slot_x - 25, "y": slot_y, "w": 35, "h": 20}
            coords[f"sell_button_{slot}"] = {"x": slot_x + 25, "y": slot_y, "w": 35, "h": 20}
            
        coords["item_search"] = {"x": center_x, "y": center_y + 80, "w": 150, "h": 30}
        coords["confirm_button"] = {"x": center_x, "y": center_y + 120, "w": 100, "h": 25}
        coords["stats_tab"] = {"x": center_x + 320, "y": center_y + 160, "w": 30, "h": 30}
        coords["inventory_tab"] = {"x": center_x + 320, "y": center_y + 200, "w": 30, "h": 30}
        
    return coords
