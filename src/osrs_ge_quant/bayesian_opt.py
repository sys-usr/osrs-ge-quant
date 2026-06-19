# src/osrs_ge_quant/bayesian_opt.py
import numpy as np
from scipy.stats import norm
from scipy.optimize import minimize
from typing import Callable, Tuple, List

class BayesianOptimizer:
    def __init__(self, objective: Callable[[float, float], float], 
                 bounds: List[Tuple[float, float]], 
                 n_init: int = 5, 
                 xi: float = 0.01, 
                 l: float = 0.5, 
                 sigma_f: float = 1.0, 
                 sigma_n: float = 1e-4):
        self.objective = objective
        self.bounds = np.array(bounds)
        self.n_init = n_init
        self.xi = xi
        self.l = l
        self.sigma_f = sigma_f
        self.sigma_n = sigma_n
        
        self.X_sample = []
        self.y_sample = []

    def _rbf_kernel(self, x1: np.ndarray, x2: np.ndarray) -> np.ndarray:
        # Distance squared matrix calculation
        dist_sq = np.sum(x1**2, axis=1).reshape(-1, 1) + np.sum(x2**2, axis=1) - 2 * np.dot(x1, x2.T)
        return (self.sigma_f**2) * np.exp(-0.5 * dist_sq / (self.l**2))

    def _gp_predict(self, X_new: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        X_sample = np.array(self.X_sample)
        y_sample = np.array(self.y_sample).reshape(-1, 1)
        
        K = self._rbf_kernel(X_sample, X_sample) + (self.sigma_n**2) * np.eye(len(X_sample))
        K_inv = np.linalg.inv(K)
        
        K_s = self._rbf_kernel(X_sample, X_new)
        K_ss = self._rbf_kernel(X_new, X_new) + 1e-8 * np.eye(len(X_new))
        
        mu = np.dot(K_s.T, np.dot(K_inv, y_sample)).flatten()
        cov = K_ss - np.dot(K_s.T, np.dot(K_inv, K_s))
        sigma = np.sqrt(np.diag(cov))
        
        return mu, sigma

    def _expected_improvement(self, x: np.ndarray) -> float:
        x = x.reshape(1, -1)
        mu, sigma = self._gp_predict(x)
        
        y_max = np.max(self.y_sample)
        
        with np.errstate(divide='ignore', invalid='ignore'):
            imp = mu - y_max - self.xi
            Z = imp / (sigma + 1e-9)
            ei = imp * norm.cdf(Z) + sigma * norm.pdf(Z)
            ei[sigma == 0.0] = 0.0
            
        return -ei[0]

    def propose_next_point(self) -> np.ndarray:
        best_x = None
        best_ei = float('inf')
        
        for _ in range(10):
            x0 = np.random.uniform(self.bounds[:, 0], self.bounds[:, 1])
            res = minimize(self._expected_improvement, x0, bounds=self.bounds, method='L-BFGS-B')
            if res.fun < best_ei:
                best_ei = res.fun
                best_x = res.x
                
        return best_x

    def run_optimization(self, n_iter: int = 15) -> Tuple[np.ndarray, float]:
        for _ in range(self.n_init):
            x = np.random.uniform(self.bounds[:, 0], self.bounds[:, 1])
            y = self.objective(x[0], x[1])
            self.X_sample.append(x)
            self.y_sample.append(y)
            
        for _ in range(n_iter - self.n_init):
            next_x = self.propose_next_point()
            next_y = self.objective(next_x[0], next_x[1])
            self.X_sample.append(next_x)
            self.y_sample.append(next_y)
            
        best_idx = np.argmax(self.y_sample)
        return self.X_sample[best_idx], self.y_sample[best_idx]
