"""Обучение моделей прогноза на исторических данных Мосбиржи.

Запуск:  python train_model.py

Обучается ОТДЕЛЬНАЯ модель под каждый период прогноза (краткосрок / среднесрок /
долгосрок), потому что горизонты сильно различаются (неделя против двух лет).
Для каждого периода:
  1. тянем длинную историю по 5 акциям (moex.py; при отсутствии сети — синтетика);
  2. на каждом дне считаем те же признаки, что и в analysis.py;
  3. размечаем: что было через N торговых дней (рост/падение и величина %);
  4. обучаем RandomForest-классификатор (направление) и регрессор (величину).
Все модели складываются в один model.pkl: {period: {clf, reg, scaler}, ...}.

Требуется: numpy, scikit-learn, joblib (см. requirements.txt).
"""
import sys

from config import STOCKS, PERIODS
from moex import fetch_candles
from analysis import analyze
from predictor import FEATURE_ORDER, MODEL_PATH

MIN_WINDOW = 220          # минимум свечей для длинных средних
HISTORY_DAYS = 1800       # сколько календарных дней истории тянем под обучение


def trading_horizon(calendar_days):
    """Грубый перевод календарных дней в торговые (≈5 из 7)."""
    return max(3, round(calendar_days * 5 / 7))


def build_dataset(horizon, candles_by_ticker):
    X, y_dir, y_chg = [], [], []
    per_ticker = {}
    for ticker, candles in candles_by_ticker.items():
        if len(candles) < MIN_WINDOW + horizon + 10:
            continue
        closes = [c["close"] for c in candles]
        cnt = 0
        for i in range(MIN_WINDOW, len(candles) - horizon, 2):
            feats = analyze(candles[:i + 1])["features"]
            change = (closes[i + horizon] - closes[i]) / closes[i] * 100
            X.append([feats[k] for k in FEATURE_ORDER])
            y_dir.append(1 if change >= 0 else 0)
            y_chg.append(change)
            cnt += 1
        per_ticker[ticker] = cnt
    return X, y_dir, y_chg, per_ticker


def main():
    try:
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
        from sklearn.preprocessing import StandardScaler
        from sklearn.model_selection import train_test_split
        import joblib
    except ImportError:
        print("Не установлены ML-зависимости. Выполните:\n"
              "  pip install numpy scikit-learn joblib")
        sys.exit(1)

    print("Загрузка истории Мосбиржи по 5 акциям...")
    candles_by_ticker = {}
    for s in STOCKS:
        c = fetch_candles(s["ticker"], HISTORY_DAYS)
        candles_by_ticker[s["ticker"]] = c
        print(f"  {s['ticker']}: {len(c)} свечей")

    bundle = {"features": FEATURE_ORDER, "models": {}}

    for period, cfg in PERIODS.items():
        horizon = trading_horizon(cfg["horizon_days"])
        print(f"\n=== Период «{cfg['label']}» (горизонт {horizon} торг. дней) ===")
        X, y_dir, y_chg, per_ticker = build_dataset(horizon, candles_by_ticker)
        print("  примеров по тикерам:", per_ticker, "→ всего", len(X))
        if len(X) < 100:
            print("  мало данных, период пропущен")
            continue

        X = np.array(X, dtype=float)
        y_dir = np.array(y_dir)
        y_chg = np.array(y_chg, dtype=float)

        scaler = StandardScaler().fit(X)
        Xs = scaler.transform(X)
        Xtr, Xte, ydtr, ydte, yctr, ycte = train_test_split(
            Xs, y_dir, y_chg, test_size=0.2, random_state=42)

        clf = RandomForestClassifier(n_estimators=250, max_depth=8,
                                     min_samples_leaf=5, random_state=42, n_jobs=-1)
        clf.fit(Xtr, ydtr)
        reg = RandomForestRegressor(n_estimators=250, max_depth=8,
                                    min_samples_leaf=5, random_state=42, n_jobs=-1)
        reg.fit(Xtr, yctr)

        print(f"  accuracy направления: {clf.score(Xte, ydte):.3f}")
        print(f"  R^2 величины:         {reg.score(Xte, ycte):.3f}")

        bundle["models"][period] = {
            "clf": clf, "reg": reg, "scaler": scaler, "horizon": horizon}

    if not bundle["models"]:
        print("\nНе удалось обучить ни одной модели.")
        sys.exit(1)

    joblib.dump(bundle, MODEL_PATH)
    print(f"\nГотово. Модель сохранена в {MODEL_PATH}")
    print("Перезапустите приложение — прогноз пойдёт через ML.")


if __name__ == "__main__":
    main()
