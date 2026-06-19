# src/osrs_ge_quant/ml_lstm.py
import os
import torch
import torch.nn as nn
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Tuple, Any

from .db import get_session
from .models import PricePoint, Item

MODEL_PATH = os.path.join("data", "lstm_model.pth")
METRICS_PATH = os.path.join("data", "lstm_metrics.json")

class LSTMForecaster(nn.Module):
    def __init__(self, input_dim: int = 1, hidden_dim: int = 64, num_layers: int = 2, output_dim: int = 4):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_dim, output_dim)
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x shape: (batch, seq_len, input_dim)
        out, _ = self.lstm(x)
        # out shape: (batch, seq_len, hidden_dim)
        out = out[:, -1, :]  # Take last time step output
        out = self.fc(out)   # Output shape: (batch, output_dim)
        return out

def prepare_sequences(prices: List[float], seq_len: int = 24, forecast_len: int = 4) -> Tuple[np.ndarray, np.ndarray]:
    """
    Prepares input sequences and targets from a series of prices.
    Normalized relative to the last price of the input window:
    x_i = p_i / p_last - 1
    """
    X, Y = [], []
    if len(prices) < seq_len + forecast_len:
        return np.array([]), np.array([])
        
    for i in range(len(prices) - seq_len - forecast_len + 1):
        x_window = prices[i : i + seq_len]
        y_window = prices[i + seq_len : i + seq_len + forecast_len]
        
        p_last = x_window[-1]
        if p_last <= 0:
            continue
            
        # Scale-invariant normalization
        x_norm = [p / p_last - 1.0 for p in x_window]
        y_norm = [p / p_last - 1.0 for p in y_window]
        
        X.append(x_norm)
        Y.append(y_norm)
        
    return np.array(X), np.array(Y)

def load_training_data(timestep: str = "1h", limit_items: int = 150, seq_len: int = 24, forecast_len: int = 4) -> Tuple[np.ndarray, np.ndarray]:
    """
    Fetches historical price data from the database and prepares it for training.
    """
    session = get_session()
    # Find active tradeable items with price points
    items = session.query(Item).filter(Item.tradeable == True).limit(limit_items).all()
    
    all_X, all_Y = [], []
    
    for item in items:
        price_points = (
            session.query(PricePoint.avg_high, PricePoint.avg_low)
            .filter(PricePoint.item_id == item.id, PricePoint.timestep == timestep)
            .order_by(PricePoint.ts.asc())
            .all()
        )
        if len(price_points) < seq_len + forecast_len:
            continue
            
        prices = []
        for p in price_points:
            if p[0] and p[1]:
                prices.append((p[0] + p[1]) / 2.0)
            elif p[0] or p[1]:
                prices.append(p[0] or p[1])
                
        # Clean null values
        prices = [x for x in prices if x is not None and x > 0]
        if len(prices) < seq_len + forecast_len:
            continue
            
        X, Y = prepare_sequences(prices, seq_len, forecast_len)
        if X.size > 0:
            all_X.append(X)
            all_Y.append(Y)
            
    session.close()
    
    if not all_X:
        return np.array([]), np.array([])
        
    return np.concatenate(all_X, axis=0), np.concatenate(all_Y, axis=0)

def train_lstm(epochs: int = 15, batch_size: int = 128, lr: float = 0.001, timestep: str = "1h") -> Dict[str, Any]:
    """
    Trains the LSTM forecaster model and saves it.
    """
    print(f"[LSTM] Loading training data for timestep={timestep}...")
    X, Y = load_training_data(timestep=timestep)
    if X.size == 0:
        return {"error": "Insufficient training data in DB. Run backfill-timeseries first."}
        
    print(f"[LSTM] Loaded {X.shape[0]} training samples.")
    
    # Shuffle and split into train/val
    indices = np.arange(X.shape[0])
    np.random.shuffle(indices)
    split = int(0.85 * len(indices))
    
    train_idx, val_idx = indices[:split], indices[split:]
    X_train, Y_train = X[train_idx], Y[train_idx]
    X_val, Y_val = X[val_idx], Y[val_idx]
    
    # Convert to PyTorch tensors
    X_train_t = torch.tensor(X_train, dtype=torch.float32).unsqueeze(-1)
    Y_train_t = torch.tensor(Y_train, dtype=torch.float32)
    X_val_t = torch.tensor(X_val, dtype=torch.float32).unsqueeze(-1)
    Y_val_t = torch.tensor(Y_val, dtype=torch.float32)
    
    # Init model, loss, optimizer
    model = LSTMForecaster(input_dim=1, hidden_dim=64, num_layers=2, output_dim=4)
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    
    best_val_loss = float("inf")
    os.makedirs("data", exist_ok=True)
    
    print("[LSTM] Starting training loop...")
    for epoch in range(epochs):
        model.train()
        permutation = torch.randperm(X_train_t.size(0))
        epoch_loss = 0.0
        
        for i in range(0, X_train_t.size(0), batch_size):
            indices = permutation[i:i+batch_size]
            batch_x, batch_y = X_train_t[indices], Y_train_t[indices]
            
            optimizer.zero_grad()
            predictions = model(batch_x)
            loss = criterion(predictions, batch_y)
            loss.backward()
            optimizer.step()
            
            epoch_loss += loss.item() * batch_x.size(0)
            
        epoch_loss /= X_train_t.size(0)
        
        # Validation loss
        model.eval()
        with torch.no_grad():
            val_preds = model(X_val_t)
            val_loss = criterion(val_preds, Y_val_t).item()
            
        print(f"Epoch {epoch+1:02d}/{epochs:02d} | Train MSE: {epoch_loss:.6f} | Val MSE: {val_loss:.6f}")
        
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            torch.save(model.state_dict(), MODEL_PATH)
            
    # Load best model for error analysis
    model.load_state_dict(torch.load(MODEL_PATH))
    model.eval()
    with torch.no_grad():
        preds = model(X_val_t).numpy()
        errors = preds - Y_val
        rmse = np.sqrt(np.mean(errors ** 2))
        mae = np.mean(np.abs(errors))
        # Save residual standard deviation for confidence bands
        std_residuals = np.std(errors, axis=0).tolist()
        
    import json
    metrics = {
        "rmse": float(rmse),
        "mae": float(mae),
        "std_residuals": std_residuals,
        "trained_at": datetime.utcnow().isoformat(),
        "timestep": timestep
    }
    with open(METRICS_PATH, "w") as f:
        json.dump(metrics, f, indent=4)
        
    print(f"[LSTM] Model saved to {MODEL_PATH}.")
    print(f"[LSTM] Out-of-sample MAE: {mae:.4f} (relative returns scale). Std of residuals: {std_residuals}")
    return metrics

def predict_lstm(item_id: int, timestep: str = "1h", seq_len: int = 24, forecast_len: int = 4, k_std: float = 1.96) -> Dict[str, Any]:
    """
    Predicts the t+4 hr price path for a specific item using the trained LSTM model.
    """
    if not os.path.exists(MODEL_PATH):
        return {"error": "No trained LSTM model found. Run 'train-lstm' command."}
        
    session = get_session()
    # Fetch recent price points
    price_points = (
        session.query(PricePoint.avg_high, PricePoint.avg_low)
        .filter(PricePoint.item_id == item_id, PricePoint.timestep == timestep)
        .order_by(PricePoint.ts.desc())
        .limit(seq_len)
        .all()
    )
    session.close()
    
    # Reverse to restore chronological order
    price_points = price_points[::-1]
    
    if len(price_points) < seq_len:
        return {"error": f"Insufficient recent data for item {item_id}. Need {seq_len} steps, got {len(price_points)}."}
        
    prices = []
    for p in price_points:
        if p[0] and p[1]:
            prices.append((p[0] + p[1]) / 2.0)
        elif p[0] or p[1]:
            prices.append(p[0] or p[1])
            
    prices = [x for x in prices if x is not None and x > 0]
    if len(prices) < seq_len:
        return {"error": "Price points contain invalid values."}
        
    p_last = prices[-1]
    if p_last <= 0:
        return {"error": "Invalid base price (zero or negative)."}
        
    # Prepare input sequence
    x_norm = [p / p_last - 1.0 for p in prices]
    x_tensor = torch.tensor([x_norm], dtype=torch.float32).unsqueeze(-1)
    
    # Load model and predict
    model = LSTMForecaster(input_dim=1, hidden_dim=64, num_layers=2, output_dim=forecast_len)
    model.load_state_dict(torch.load(MODEL_PATH))
    model.eval()
    
    with torch.no_grad():
        pred_norm = model(x_tensor).numpy()[0]
        
    # Load metrics for bounds calculation
    import json
    std_residuals = [0.02, 0.03, 0.04, 0.05] # default falls back
    if os.path.exists(METRICS_PATH):
        try:
            with open(METRICS_PATH, "r") as f:
                metrics = json.load(f)
                std_residuals = metrics.get("std_residuals") or std_residuals
        except Exception:
            pass
            
    # Reconstruct prices
    predicted_prices = [float(p_last * (val + 1.0)) for val in pred_norm]
    lower_bounds = []
    upper_bounds = []
    
    for idx, pred_p in enumerate(predicted_prices):
        sd = std_residuals[idx]
        lower_bounds.append(float(p_last * (pred_norm[idx] - k_std * sd + 1.0)))
        upper_bounds.append(float(p_last * (pred_norm[idx] + k_std * sd + 1.0)))
        
    return {
        "current_price": float(p_last),
        "forecasted_prices": predicted_prices,
        "lower_bounds": lower_bounds,
        "upper_bounds": upper_bounds,
        "timestep": timestep
    }
