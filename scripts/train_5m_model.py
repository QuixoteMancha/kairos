#!/usr/bin/env python3
"""
Entrenamiento baseline para predecir P(Up) en intervalos 5m crypto (Polymarket).

Lee el dataset recolectado en la tabla `Crypto5mInterval` y entrena una regresión
logística (numpy puro, sin dependencias nuevas) sobre features tempranas del
intervalo: probabilidad YES en distintos momentos, momentum, rango, liquidez y
volumen. Reporta accuracy / log-loss / AUC y guarda el modelo en JSON.

Uso:
    python scripts/train_5m_model.py
    python scripts/train_5m_model.py --symbol BTC --min-samples 50
    python scripts/train_5m_model.py --out data/models/5m_btc.json

El modelo guardado es portable (pesos + normalización) y puede usarse luego para
inferencia en tiempo real cotejando la probabilidad del mercado vs. la del modelo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np

# Permitir ejecutar desde la raíz del repo
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


FEATURE_NAMES = [
    "yes_t30",
    "yes_t60",
    "yes_t150",
    "yes_t240",
    "high_yes",
    "low_yes",
    "range_yes",
    "momentum_60",
    "log_liquidity",
    "log_volume",
]


def _row_features(row) -> list[float]:
    open_yes = row.open_yes if row.open_yes is not None else 0.5
    yes_t30 = row.yes_t30 if row.yes_t30 is not None else open_yes
    yes_t60 = row.yes_t60 if row.yes_t60 is not None else yes_t30
    yes_t150 = row.yes_t150 if row.yes_t150 is not None else yes_t60
    yes_t240 = row.yes_t240 if row.yes_t240 is not None else yes_t150
    high_yes = row.high_yes if row.high_yes is not None else max(open_yes, yes_t60)
    low_yes = row.low_yes if row.low_yes is not None else min(open_yes, yes_t60)
    range_yes = high_yes - low_yes
    momentum_60 = yes_t60 - open_yes
    log_liquidity = np.log10(max(1.0, float(row.liquidity or 0.0)))
    log_volume = np.log10(max(1.0, float(row.volume_24h or 0.0)))
    return [
        yes_t30, yes_t60, yes_t150, yes_t240,
        high_yes, low_yes, range_yes, momentum_60,
        log_liquidity, log_volume,
    ]


def load_dataset(symbol: str | None):
    from src.database.database import init_db, Crypto5mInterval

    init_db()
    q = Crypto5mInterval.select().where(
        (Crypto5mInterval.resolved == True) & (Crypto5mInterval.label.is_null(False))  # noqa: E712
    )
    if symbol:
        q = q.where(Crypto5mInterval.symbol == symbol.upper())
    q = q.order_by(Crypto5mInterval.end_ts.asc())

    X, y = [], []
    for row in q:
        X.append(_row_features(row))
        y.append(int(row.label))
    return np.array(X, dtype=float), np.array(y, dtype=float)


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def train_logistic(X, y, epochs=4000, lr=0.1, l2=1e-3):
    # Normalización (z-score) para estabilidad
    mu = X.mean(axis=0)
    sigma = X.std(axis=0)
    sigma[sigma == 0] = 1.0
    Xn = (X - mu) / sigma

    n, d = Xn.shape
    w = np.zeros(d)
    b = 0.0
    for _ in range(epochs):
        z = Xn @ w + b
        p = _sigmoid(z)
        grad_w = (Xn.T @ (p - y)) / n + l2 * w
        grad_b = float(np.mean(p - y))
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b, mu, sigma


def _metrics(y_true, p_pred):
    eps = 1e-9
    p = np.clip(p_pred, eps, 1 - eps)
    log_loss = float(-np.mean(y_true * np.log(p) + (1 - y_true) * np.log(1 - p)))
    acc = float(np.mean((p_pred >= 0.5).astype(float) == y_true))
    auc = _auc(y_true, p_pred)
    return {"accuracy": round(acc, 4), "log_loss": round(log_loss, 4), "auc": round(auc, 4)}


def _auc(y_true, scores):
    # AUC por rank (Mann-Whitney U). Robusto sin sklearn.
    pos = scores[y_true == 1]
    neg = scores[y_true == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(scores)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1)
    sum_pos = ranks[y_true == 1].sum()
    auc = (sum_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))
    return float(auc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None, help="BTC/ETH/SOL/XRP/DOGE/HYPE (default: todos)")
    ap.add_argument("--min-samples", type=int, default=40)
    ap.add_argument("--test-frac", type=float, default=0.25)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    X, y = load_dataset(args.symbol)
    n = len(y)
    print(f"[5M-ML] dataset: {n} intervalos resueltos"
          + (f" (symbol={args.symbol})" if args.symbol else " (todos los símbolos)"))

    if n < args.min_samples:
        print(f"[5M-ML] Datos insuficientes (n={n} < min={args.min_samples}).")
        print("        El recolector sigue acumulando intervalos en la tabla crypto_5m_interval.")
        print("        Vuelve a ejecutar cuando haya más datos.")
        return

    # Split temporal (los primeros = train, los últimos = test)
    cut = int(n * (1 - args.test_frac))
    Xtr, ytr = X[:cut], y[:cut]
    Xte, yte = X[cut:], y[cut:]

    w, b, mu, sigma = train_logistic(Xtr, ytr)

    def predict(M):
        return _sigmoid(((M - mu) / sigma) @ w + b)

    train_m = _metrics(ytr, predict(Xtr))
    test_m = _metrics(yte, predict(Xte)) if len(yte) else {}
    base_rate = float(np.mean(y))

    print(f"[5M-ML] base rate Up = {base_rate:.3f}")
    print(f"[5M-ML] train: {train_m}")
    print(f"[5M-ML] test:  {test_m}")
    print("[5M-ML] pesos (feature → weight):")
    for name, weight in sorted(zip(FEATURE_NAMES, w), key=lambda kv: -abs(kv[1])):
        print(f"          {name:>16}: {weight:+.4f}")

    model = {
        "type": "logistic_regression",
        "trained_at": datetime.utcnow().isoformat() + "Z",
        "symbol": args.symbol or "ALL",
        "n_samples": n,
        "base_rate_up": round(base_rate, 4),
        "features": FEATURE_NAMES,
        "weights": [round(float(x), 6) for x in w],
        "bias": round(float(b), 6),
        "mu": [round(float(x), 6) for x in mu],
        "sigma": [round(float(x), 6) for x in sigma],
        "metrics": {"train": train_m, "test": test_m},
    }

    out_path = args.out or os.path.join(
        "data", "models", f"5m_{(args.symbol or 'all').lower()}.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(model, f, indent=2)
    print(f"[5M-ML] modelo guardado en {out_path}")


if __name__ == "__main__":
    main()
