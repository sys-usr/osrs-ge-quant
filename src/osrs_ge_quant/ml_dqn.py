# src/osrs_ge_quant/ml_dqn.py
import os
import random
from collections import deque
from typing import Dict, Any

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

class QNetwork(nn.Module):
    def __init__(self, state_dim: int, action_dim: int):
        super(QNetwork, self).__init__()
        self.fc1 = nn.Linear(state_dim, 64)
        self.relu = nn.ReLU()
        self.fc2 = nn.Linear(64, 32)
        self.fc3 = nn.Linear(32, action_dim)
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.relu(self.fc1(x))
        x = self.relu(self.fc2(x))
        return self.fc3(x)

class ReplayBuffer:
    def __init__(self, capacity: int = 10000):
        self.buffer = deque(maxlen=capacity)
        
    def push(self, state: np.ndarray, action: int, reward: float, next_state: np.ndarray, done: bool):
        self.buffer.append((state, action, reward, next_state, done))
        
    def sample(self, batch_size: int):
        state, action, reward, next_state, done = zip(*random.sample(self.buffer, batch_size))
        return (np.array(state, dtype=np.float32),
                np.array(action, dtype=np.int64),
                np.array(reward, dtype=np.float32),
                np.array(next_state, dtype=np.float32),
                np.array(done, dtype=np.uint8))
                
    def __len__(self) -> int:
        return len(self.buffer)

def construct_state(current_price: float, spread: float, order_book_depth: dict) -> np.ndarray:
    buy_depth = float(order_book_depth.get("buy_depth", 0.0))
    sell_depth = float(order_book_depth.get("sell_depth", 0.0))
    
    norm_spread = spread / (current_price + 1e-9)
    log_buy = np.log1p(buy_depth)
    log_sell = np.log1p(sell_depth)
    imbalance = (buy_depth - sell_depth) / (buy_depth + sell_depth + 1e-9)
    
    return np.array([norm_spread, log_buy, log_sell, imbalance, 1.0], dtype=np.float32)

class DQNPricingAgent:
    def __init__(self, state_dim: int = 5, action_dim: int = 10, lr: float = 1e-3, gamma: float = 0.99,
                 epsilon_start: float = 1.0, epsilon_end: float = 0.01, epsilon_decay: float = 0.995):
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.gamma = gamma
        self.epsilon = epsilon_start
        self.epsilon_end = epsilon_end
        self.epsilon_decay = epsilon_decay
        
        self.q_network = QNetwork(state_dim, action_dim)
        self.target_network = QNetwork(state_dim, action_dim)
        self.target_network.load_state_dict(self.q_network.state_dict())
        self.target_network.eval()
        
        self.optimizer = optim.Adam(self.q_network.parameters(), lr=lr)
        self.replay_buffer = ReplayBuffer()
        self.batch_size = 64
        
    def select_action(self, state: np.ndarray, evaluate: bool = False) -> int:
        if not evaluate and random.random() < self.epsilon:
            return random.randint(0, self.action_dim - 1)
        
        state_t = torch.FloatTensor(state).unsqueeze(0)
        with torch.no_grad():
            q_values = self.q_network(state_t)
        return q_values.argmax(dim=1).item()
        
    def get_optimal_bid(self, current_price: float, spread: float, order_book_depth: dict) -> float:
        state = construct_state(current_price, spread, order_book_depth)
        action = self.select_action(state, evaluate=True)
        
        low_price = current_price - spread / 2.0
        high_price = current_price + spread / 2.0
        
        # Map action to bid price
        if action == 0:
            bid = low_price - 100
        elif action == 1:
            bid = low_price - 10
        elif action == 2:
            bid = low_price - 1
        elif action == 3:
            bid = low_price
        elif action == 4:
            bid = low_price + 1
        elif action == 5:
            bid = low_price + 2
        elif action == 6:
            bid = low_price + 5
        elif action == 7:
            bid = low_price + 0.1 * spread
        elif action == 8:
            bid = low_price + 0.5 * spread
        elif action == 9:
            bid = high_price
        else:
            bid = low_price + 1
            
        bid = max(1.0, np.floor(bid))
        bid = min(high_price, bid)
        return float(bid)
        
    def update_epsilon(self):
        self.epsilon = max(self.epsilon_end, self.epsilon * self.epsilon_decay)
        
    def train_step(self) -> float or None:
        if len(self.replay_buffer) < self.batch_size:
            return None
            
        states, actions, rewards, next_states, dones = self.replay_buffer.sample(self.batch_size)
        
        states_t = torch.FloatTensor(states)
        actions_t = torch.LongTensor(actions).unsqueeze(1)
        rewards_t = torch.FloatTensor(rewards)
        next_states_t = torch.FloatTensor(next_states)
        dones_t = torch.FloatTensor(dones)
        
        q_values = self.q_network(states_t).gather(1, actions_t).squeeze(1)
        
        with torch.no_grad():
            next_q_values = self.target_network(next_states_t).max(dim=1)[0]
            target_q_values = rewards_t + self.gamma * next_q_values * (1 - dones_t)
            
        loss = nn.MSELoss()(q_values, target_q_values)
        
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()
        
        return loss.item()
        
    def update_target_network(self):
        self.target_network.load_state_dict(self.q_network.state_dict())
        
    def save_model(self, path: str):
        torch.save(self.q_network.state_dict(), path)
        
    def load_model(self, path: str):
        if os.path.exists(path):
            self.q_network.load_state_dict(torch.load(path))
            self.update_target_network()


def record_experience_to_db(state: np.ndarray, action: int, reward: float, next_state: np.ndarray, done: bool, account_id: int):
    """
    Serializes and uploads a single experience tuple to the database for federated learning.
    """
    import json
    from .db import get_session
    from .models import DQNExperience
    
    session = get_session()
    try:
        exp = DQNExperience(
            state=json.dumps(state.tolist()),
            action=action,
            reward=reward,
            next_state=json.dumps(next_state.tolist()),
            done=done,
            account_id=account_id
        )
        session.add(exp)
        session.commit()
    except Exception as e:
        print(f"[DQN DB] Failed to record experience to DB: {e}")
        session.rollback()
    finally:
        session.close()


def sync_replay_buffer_from_db(agent: DQNPricingAgent, limit: int = 5000):
    """
    Fetches DQN experiences from all alts stored in the database and updates the agent's replay buffer.
    """
    import json
    from .db import get_session
    from .models import DQNExperience
    
    session = get_session()
    try:
        exps = (
            session.query(DQNExperience)
            .order_by(DQNExperience.ts.desc())
            .limit(limit)
            .all()
        )
        
        count = 0
        for e in exps:
            try:
                state = np.array(json.loads(e.state), dtype=np.float32)
                next_state = np.array(json.loads(e.next_state), dtype=np.float32)
                agent.replay_buffer.push(state, e.action, e.reward, next_state, e.done)
                count += 1
            except Exception:
                continue
                
        print(f"[DQN DB] Synced {count} experiences from database into local replay buffer.")
    except Exception as e:
        print(f"[DQN DB] Failed to sync replay buffer: {e}")
    finally:
        session.close()
