"""Слой прогнозирования.

Здесь сознательно разделён ИНТЕРФЕЙС и РЕАЛИЗАЦИЯ, чтобы позже легко
подменить заглушку на настоящую нейросеть:

    predict(features, horizon_days, period) -> {
        direction, change_pct, probability, model
    }

Логика выбора реализации:
  1. Если рядом лежит обученная модель (model.pkl) и установлен scikit-learn —
     используем её (см. train_model.py).
  2. Иначе работает прозрачная rule-based заглушка на основе индикаторов.

Чтобы вставить свою нейронку — достаточно реализовать класс с методом
predict_proba/predict признаков FEATURE_ORDER и сохранить его в MODEL_PATH,
либо заменить функцию _ml_predict().
"""
import os

CONFIG_DIR = os.path.abspath(os.path.dirname(__file__))
MODEL_PATH = os.path.join(CONFIG_DIR, "model.pkl")

# Порядок признаков — общий контракт между обучением и инференсом.
FEATURE_ORDER = [
    "rsi", "macd_hist", "macd_cross_up", "macd_cross_down",
    "sma_trend", "bb_pos", "momentum10", "bull_patterns", "bear_patterns",
]

_model_cache = None
_model_loaded = False


def predict(features, horizon_days, period="short"):
    """Главная точка входа. Возвращает прогноз для горизонта horizon_days."""
    bundle = _load_model()
    sub = None
    if bundle is not None:
        # новый формат: {"features":..., "models": {period: {...}}}
        models = bundle.get("models") if isinstance(bundle, dict) else None
        if models:
            sub = models.get(period) or next(iter(models.values()), None)
        elif isinstance(bundle, dict) and "clf" in bundle:
            sub = bundle  # старый одно-модельный формат
    if sub is not None:
        try:
            return _ml_predict(sub, features, horizon_days)
        except Exception:
            pass  # при любой ошибке откатываемся на правила
    return _rule_based_predict(features, horizon_days)


# ----------------------------------------------------- ML-реализация (опц.)
def _load_model():
    """Лениво загружает обученную модель, если она есть."""
    global _model_cache, _model_loaded
    if _model_loaded:
        return _model_cache
    _model_loaded = True
    if os.path.exists(MODEL_PATH):
        try:
            import joblib
            _model_cache = joblib.load(MODEL_PATH)
        except Exception:
            _model_cache = None
    return _model_cache


def _ml_predict(model, features, horizon_days):
    """Инференс модели нужного периода. model — словарь {clf, reg, scaler?}.

    Каждая модель обучена под свой горизонт, поэтому величину масштабировать
    не нужно — регрессор уже выдаёт изменение на этот горизонт.
    """
    import numpy as np
    x = np.array([[features[k] for k in FEATURE_ORDER]], dtype=float)
    if model.get("scaler") is not None:
        x = model["scaler"].transform(x)

    clf = model["clf"]   # классификатор направления (вверх/вниз)
    reg = model["reg"]   # регрессор величины изменения, %

    proba_up = float(clf.predict_proba(x)[0][list(clf.classes_).index(1)]) \
        if 1 in clf.classes_ else 0.5
    change_pct = float(reg.predict(x)[0])

    # порог «боковика» масштабируем под длину горизонта
    flat_thr = max(0.5, 0.45 * (horizon_days ** 0.5))
    if change_pct > flat_thr:
        direction = "up"
    elif change_pct < -flat_thr:
        direction = "down"
    else:
        direction = "flat"
    probability = round(max(proba_up, 1 - proba_up) * 100, 1)
    return {"direction": direction, "change_pct": round(change_pct, 2),
            "probability": probability, "model": "ml"}


# --------------------------------------------- rule-based заглушка (по умолч.)
def _rule_based_predict(f, horizon_days):
    """Прозрачный прогноз из индикаторов. Замена настоящей нейросети.

    Считаем «бычий счёт» в диапазоне [-1..1], переводим в ожидаемое изменение
    и оценку уверенности. Логика намеренно объяснимая.
    """
    score = 0.0

    # RSI: отклонение от 50 даёт направленный вклад (с разворотом в крайностях)
    rsi = f["rsi"]
    if rsi >= 70:
        score -= 0.25            # перекупленность -> риск вниз
    elif rsi <= 30:
        score += 0.25            # перепроданность -> отскок вверх
    else:
        score += (rsi - 50) / 100.0  # 50..70 умеренно бычий и т.п.

    # MACD
    if f["macd_cross_up"]:
        score += 0.30
    elif f["macd_cross_down"]:
        score -= 0.30
    else:
        score += 0.15 if f["macd_hist"] > 0 else -0.15

    # Тренд по SMA
    if f["sma_trend"] > 1.01:
        score += 0.20
    elif f["sma_trend"] < 0.99:
        score -= 0.20

    # Боллинджер: крайние положения как контр-сигнал
    if f["bb_pos"] > 0.9:
        score -= 0.10
    elif f["bb_pos"] < 0.1:
        score += 0.10

    # Импульс
    score += max(-0.2, min(0.2, f["momentum10"] / 100.0))

    # Фигуры
    score += 0.15 * f["bull_patterns"] - 0.15 * f["bear_patterns"]

    score = max(-1.0, min(1.0, score))

    # Ожидаемая дневная амплитуда ~ горизонт. Базовая «волатильность» 0.45%/день.
    daily_vol = 0.45
    max_move = daily_vol * (horizon_days ** 0.5)   # √времени
    change_pct = round(score * max_move, 2)

    if change_pct > 0.7:
        direction = "up"
    elif change_pct < -0.7:
        direction = "down"
    else:
        direction = "flat"

    # Уверенность тем выше, чем сильнее (по модулю) сигнал.
    probability = round(50 + abs(score) * 42, 1)   # 50..92%
    return {"direction": direction, "change_pct": change_pct,
            "probability": probability, "model": "rule-based"}


def build_summary(stock_name, period_label, prediction, patterns, explanations):
    """Собирает финальный человекочитаемый вывод-обоснование прогноза."""
    d = prediction["direction"]
    sign = "вырастет" if d == "up" else ("снизится" if d == "down"
                                         else "останется в боковике")
    pct = abs(prediction["change_pct"])
    head = (f"По совокупности сигналов модель ожидает, что {stock_name} "
            f"в рамках выбранного горизонта ({period_label.lower()}) скорее всего "
            f"{sign}" + (f" примерно на {pct}%." if d != "flat" else "."))
    conf = f"Оценочная вероятность сценария — {prediction['probability']}%."

    pat_txt = ""
    if patterns:
        names = ", ".join(p["name"] for p in patterns)
        pat_txt = f"На графике выделены фигуры: {names}. "

    reasons = " ".join(explanations)
    model_note = ("Прогноз построен обученной моделью."
                  if prediction["model"] == "ml"
                  else "Прогноз построен на правилах теханализа (демо-модель; "
                       "интерфейс готов к подключению нейросети).")
    return f"{head} {conf}\n\n{pat_txt}Почему так: {reasons}\n\n{model_note}"
