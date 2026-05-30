"""Технический анализ: индикаторы, фигуры и понятные пояснения.

Всё на чистом Python — без pandas/numpy, чтобы приложение запускалось
с минимальным набором зависимостей. Каждый расчёт сопровождается текстовым
объяснением «что мы видим и почему».
"""


# ---------------------------------------------------------------- индикаторы
def sma(values, period):
    """Простая скользящая средняя. Длина результата = len(values)."""
    out = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            window = values[i + 1 - period:i + 1]
            out.append(sum(window) / period)
    return out


def ema(values, period):
    """Экспоненциальная скользящая средняя."""
    out = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values, period=14):
    """RSI по Уайлдеру (0..100)."""
    out = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = [], []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(values)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100 - (100 / (1 + rs))
    return out


def macd(values, fast=12, slow=26, signal=9):
    """MACD: возвращает (macd_line, signal_line, histogram)."""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid = [m for m in macd_line if m is not None]
    sig_valid = ema(valid, signal)
    # выровнять сигнальную линию обратно по индексам macd_line
    signal_line, j = [], 0
    for m in macd_line:
        if m is None:
            signal_line.append(None)
        else:
            signal_line.append(sig_valid[j])
            j += 1
    hist = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, hist


def bollinger(values, period=20, mult=2.0):
    """Полосы Боллинджера: (middle, upper, lower)."""
    mid = sma(values, period)
    upper, lower = [], []
    for i in range(len(values)):
        if mid[i] is None:
            upper.append(None)
            lower.append(None)
            continue
        window = values[i + 1 - period:i + 1]
        mean = mid[i]
        var = sum((x - mean) ** 2 for x in window) / period
        std = var ** 0.5
        upper.append(mean + mult * std)
        lower.append(mean - mult * std)
    return mid, upper, lower


def support_resistance(highs, lows, lookback=40):
    """Простейшие уровни: минимум и максимум за последние lookback свечей."""
    h = highs[-lookback:] if len(highs) > lookback else highs
    low = lows[-lookback:] if len(lows) > lookback else lows
    return {"support": min(low), "resistance": max(h)}


# ------------------------------------------------------------------- фигуры
def _local_extrema(values, window=4):
    """Индексы локальных максимумов и минимумов."""
    maxima, minima = [], []
    for i in range(window, len(values) - window):
        seg = values[i - window:i + window + 1]
        if values[i] == max(seg) and seg.count(values[i]) == 1:
            maxima.append(i)
        if values[i] == min(seg) and seg.count(values[i]) == 1:
            minima.append(i)
    return maxima, minima


def _linreg(points):
    """Линейная регрессия по списку (x, y). Возвращает (slope, intercept)."""
    n = len(points)
    if n < 2:
        return 0.0, points[0][1] if points else 0.0
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0, sy / n
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def _detect_triangle(closes, highs, lows, n):
    """Ищет треугольник на последнем участке: сходящиеся линии по вершинам
    (сопротивление) и впадинам (поддержка)."""
    w = min(55, max(28, n // 4))
    start = n - w
    seg_h = highs[start:]
    seg_l = lows[start:]
    maxima, _ = _local_extrema(seg_h, window=3)
    _, minima = _local_extrema(seg_l, window=3)
    if len(maxima) < 2 or len(minima) < 2:
        return None

    top_pts = [(start + i, seg_h[i]) for i in maxima[-3:]]
    bot_pts = [(start + i, seg_l[i]) for i in minima[-3:]]
    rs_slope, rs_int = _linreg(top_pts)   # линия сопротивления (по вершинам)
    sp_slope, sp_int = _linreg(bot_pts)   # линия поддержки (по впадинам)

    price = closes[-1] or 1
    # ширина диапазона в начале и в конце участка — должна сужаться
    def res(x): return rs_slope * x + rs_int
    def sup(x): return sp_slope * x + sp_int
    width_start = res(start) - sup(start)
    width_end = res(n - 1) - sup(n - 1)
    if width_start <= 0 or width_end <= 0 or width_end >= width_start * 0.85:
        return None  # не сужается — не треугольник

    flat = price * 0.0006  # порог «горизонтальности» наклона за свечу
    if abs(rs_slope) < flat and sp_slope > flat:
        name, typ = "Восходящий треугольник", "bullish"
        explain = ("Сопротивление почти горизонтально, а минимумы растут — "
                   "покупатели поджимают цену к уровню. Чаще пробивается вверх.")
    elif rs_slope < -flat and abs(sp_slope) < flat:
        name, typ = "Нисходящий треугольник", "bearish"
        explain = ("Поддержка почти горизонтальна, а максимумы снижаются — "
                   "продавцы давят цену к уровню. Чаще пробивается вниз.")
    elif rs_slope < -flat and sp_slope > flat:
        name, typ = "Симметричный треугольник", "neutral"
        explain = ("Максимумы снижаются, минимумы растут — цена сжимается в "
                   "клин. Сигнал к скорому сильному движению (направление "
                   "подтверждается пробоем границы).")
    else:
        return None

    shapes = [
        {"type": "line", "i1": start, "y1": res(start), "i2": n - 1, "y2": res(n - 1),
         "label": "Сопротивление", "color": "#ff6b81"},
        {"type": "line", "i1": start, "y1": sup(start), "i2": n - 1, "y2": sup(n - 1),
         "label": "Поддержка", "color": "#2fd98a"},
        {"type": "box", "i1": start, "i2": n - 1, "label": name, "color": "#7b6bff"},
    ]
    return {"name": name, "type": typ, "explain": explain, "shapes_abs": shapes}


def detect_patterns(closes, highs, lows):
    """Распознаёт базовые фигуры и тренды. Возвращает список словарей
    {name, type(bullish/bearish/neutral), explain, shapes_abs}.
    shapes_abs — геометрия для отрисовки на графике (в абсолютных индексах)."""
    patterns = []
    n = len(closes)
    if n < 30:
        return patterns

    maxima, minima = _local_extrema(closes)

    # --- Тренд: линия регрессии по последним ~30 свечам ---
    tw = min(30, n)
    t_start = n - tw
    trend_pts = [(i, closes[i]) for i in range(t_start, n)]
    slope, intercept = _linreg(trend_pts)
    ref = closes[t_start] or 1
    rel = slope * tw / ref   # суммарный наклон за участок, доля
    trend_line = {"type": "line", "i1": t_start, "y1": slope * t_start + intercept,
                  "i2": n - 1, "y2": slope * (n - 1) + intercept,
                  "label": "Линия тренда", "color": "#5b8cff"}
    if rel > 0.03:
        patterns.append({
            "name": "Восходящий тренд", "type": "bullish",
            "explain": "Цена формирует более высокие максимумы и минимумы, линия "
                       "тренда направлена вверх — преобладают покупатели.",
            "shapes_abs": [trend_line]})
    elif rel < -0.03:
        patterns.append({
            "name": "Нисходящий тренд", "type": "bearish",
            "explain": "Линия тренда направлена вниз, рынок делает более низкие "
                       "минимумы — контроль у продавцов.",
            "shapes_abs": [trend_line]})
    else:
        patterns.append({
            "name": "Боковик (флэт)", "type": "neutral",
            "explain": "Цена движется в горизонтальном диапазоне без явного тренда. "
                       "В такие периоды сигналы индикаторов менее надёжны.",
            "shapes_abs": [trend_line]})

    # --- Двойная вершина / двойное дно ---
    if len(maxima) >= 2:
        a, b = maxima[-2], maxima[-1]
        if abs(closes[a] - closes[b]) / closes[a] < 0.03 and b > n - 30:
            level = (closes[a] + closes[b]) / 2
            patterns.append({
                "name": "Двойная вершина", "type": "bearish",
                "explain": "Цена дважды оттолкнулась от близкого уровня сопротивления "
                           "и не смогла его пробить. Классический разворотный сигнал вниз.",
                "shapes_abs": [
                    {"type": "point", "i": a, "y": closes[a], "label": "Вершина 1", "color": "#ff6b81"},
                    {"type": "point", "i": b, "y": closes[b], "label": "Вершина 2", "color": "#ff6b81"},
                    {"type": "line", "i1": a, "y1": level, "i2": b, "y2": level,
                     "label": "Сопротивление", "color": "#ff6b81"}]})
    if len(minima) >= 2:
        a, b = minima[-2], minima[-1]
        if abs(closes[a] - closes[b]) / closes[a] < 0.03 and b > n - 30:
            level = (closes[a] + closes[b]) / 2
            patterns.append({
                "name": "Двойное дно", "type": "bullish",
                "explain": "Цена дважды нашла поддержку на близком уровне и оттолкнулась "
                           "вверх. Часто предшествует развороту вверх.",
                "shapes_abs": [
                    {"type": "point", "i": a, "y": closes[a], "label": "Дно 1", "color": "#2fd98a"},
                    {"type": "point", "i": b, "y": closes[b], "label": "Дно 2", "color": "#2fd98a"},
                    {"type": "line", "i1": a, "y1": level, "i2": b, "y2": level,
                     "label": "Поддержка", "color": "#2fd98a"}]})

    # --- Треугольник (сходящиеся линии) ---
    tri = _detect_triangle(closes, highs, lows, n)
    if tri:
        patterns.append(tri)

    # --- Золотой / мёртвый крест ---
    long_p = 200 if n > 220 else 50
    short_p = 50 if n > 220 else 20
    s_short = sma(closes, short_p)
    s_long = sma(closes, long_p)
    if s_short[-1] is not None and s_long[-1] is not None \
            and s_short[-2] is not None and s_long[-2] is not None:
        cross = {"type": "vline", "i": n - 1, "color": "#f5b14c"}
        if s_short[-2] <= s_long[-2] and s_short[-1] > s_long[-1]:
            cross["label"] = "Золотой крест"
            patterns.append({
                "name": f"Золотой крест (SMA{short_p}↑SMA{long_p})", "type": "bullish",
                "explain": f"Быстрая средняя SMA{short_p} пересекла медленную "
                           f"SMA{long_p} снизу вверх — сильный бычий сигнал.",
                "shapes_abs": [cross]})
        elif s_short[-2] >= s_long[-2] and s_short[-1] < s_long[-1]:
            cross["label"] = "Мёртвый крест"
            patterns.append({
                "name": f"Мёртвый крест (SMA{short_p}↓SMA{long_p})", "type": "bearish",
                "explain": f"SMA{short_p} пересекла SMA{long_p} сверху вниз — "
                           f"медвежий сигнал, риск продолжения снижения.",
                "shapes_abs": [cross]})

    return patterns


def _build_markup(patterns, win_dates, offset, n):
    """Переводит геометрию фигур (абс. индексы) в координаты окна графика.

    x задаём подписью даты (строка) — однозначно для категориальной оси.
    Индексы, не попавшие в окно, отбрасываем/обрезаем по краю.
    """
    wlen = len(win_dates)

    def to_x(i):
        x = i - offset
        if x < 0:
            x = 0
        if x > wlen - 1:
            x = wlen - 1
        return win_dates[x]

    shapes = []
    for p in patterns:
        for sh in p.get("shapes_abs", []):
            t = sh["type"]
            if t == "hline":
                shapes.append({"type": "hline", "y": _round(sh["y"]),
                               "label": sh.get("label"), "color": sh["color"]})
            elif t == "vline":
                shapes.append({"type": "vline", "x": to_x(sh["i"]),
                               "label": sh.get("label"), "color": sh["color"]})
            elif t == "point":
                shapes.append({"type": "point", "x": to_x(sh["i"]), "y": _round(sh["y"]),
                               "label": sh.get("label"), "color": sh["color"]})
            elif t == "line":
                shapes.append({"type": "line",
                               "x1": to_x(sh["i1"]), "y1": _round(sh["y1"]),
                               "x2": to_x(sh["i2"]), "y2": _round(sh["y2"]),
                               "label": sh.get("label"), "color": sh["color"]})
            elif t == "box":
                shapes.append({"type": "box", "x1": to_x(sh["i1"]), "x2": to_x(sh["i2"]),
                               "label": sh.get("label"), "color": sh["color"]})
    return shapes


# --------------------------------------------------- сборка полного анализа
def analyze(candles):
    """Главная функция: считает индикаторы, ищет фигуры и собирает признаки
    для модели прогноза. Возвращает словарь."""
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    dates = [c["date"] for c in candles]

    rsi_vals = rsi(closes)
    macd_line, signal_line, hist = macd(closes)
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    mid, upper, lower = bollinger(closes)
    levels = support_resistance(highs, lows)
    patterns = detect_patterns(closes, highs, lows)

    last_close = closes[-1]
    last_rsi = _last_valid(rsi_vals)
    last_hist = _last_valid(hist)
    prev_hist = _last_valid(hist, skip=1)

    # положение цены в полосах Боллинджера (0 = нижняя, 1 = верхняя)
    bb_pos = None
    if upper[-1] is not None and lower[-1] is not None and upper[-1] != lower[-1]:
        bb_pos = (last_close - lower[-1]) / (upper[-1] - lower[-1])

    # импульс за последние 10 свечей, %
    momentum = None
    if len(closes) > 11:
        momentum = (closes[-1] - closes[-11]) / closes[-11] * 100

    indicators = {
        "rsi": _round(last_rsi),
        "macd_hist": _round(last_hist, 4),
        "macd_line": _round(_last_valid(macd_line), 4),
        "macd_signal": _round(_last_valid(signal_line), 4),
        "sma20": _round(_last_valid(sma20)),
        "sma50": _round(_last_valid(sma50)),
        "bb_pos": _round(bb_pos, 3),
        "support": _round(levels["support"]),
        "resistance": _round(levels["resistance"]),
        "momentum10": _round(momentum, 2),
        "last_close": _round(last_close),
    }

    features = {
        "rsi": last_rsi if last_rsi is not None else 50.0,
        "macd_hist": last_hist if last_hist is not None else 0.0,
        "macd_cross_up": 1.0 if (prev_hist is not None and last_hist is not None
                                 and prev_hist <= 0 < last_hist) else 0.0,
        "macd_cross_down": 1.0 if (prev_hist is not None and last_hist is not None
                                   and prev_hist >= 0 > last_hist) else 0.0,
        "sma_trend": _safe_ratio(_last_valid(sma20), _last_valid(sma50)),
        "bb_pos": bb_pos if bb_pos is not None else 0.5,
        "momentum10": momentum if momentum is not None else 0.0,
        "bull_patterns": sum(1 for p in patterns if p["type"] == "bullish"),
        "bear_patterns": sum(1 for p in patterns if p["type"] == "bearish"),
    }

    explanations = _explain_indicators(indicators, features)

    # данные для графика (последние ~180 точек, чтобы не раздувать страницу)
    window = 180
    n = len(closes)
    offset = max(0, n - window)        # сдвиг абсолютных индексов в окно графика
    cut = slice(-window, None)
    win_dates = dates[cut]

    # разметка фигур: переводим абсолютные индексы в подписи дат окна графика
    markup = _build_markup(patterns, win_dates, offset, len(closes))
    # горизонтальные уровни поддержки/сопротивления — всегда
    markup.append({"type": "hline", "y": indicators["resistance"],
                   "label": "Сопротивление", "color": "#ff6b81"})
    markup.append({"type": "hline", "y": indicators["support"],
                   "label": "Поддержка", "color": "#2fd98a"})

    chart = {
        "dates": win_dates,
        "close": [_round(x) for x in closes[cut]],
        "sma20": [_round(x) for x in sma20[cut]],
        "sma50": [_round(x) for x in sma50[cut]],
        "bb_upper": [_round(x) for x in upper[cut]],
        "bb_lower": [_round(x) for x in lower[cut]],
        "rsi": [_round(x) for x in rsi_vals[cut]],
        "macd_hist": [_round(x, 4) for x in hist[cut]],
        "support": indicators["support"],
        "resistance": indicators["resistance"],
        "markup": markup,
    }

    return {
        "indicators": indicators,
        "features": features,
        "patterns": patterns,
        "explanations": explanations,
        "chart": chart,
        "last_close": last_close,
    }


def _explain_indicators(ind, feat):
    """Понятные текстовые пояснения по каждому индикатору."""
    out = []
    rsi_v = ind["rsi"]
    if rsi_v is not None:
        if rsi_v >= 70:
            out.append(f"RSI = {rsi_v}: зона перекупленности (>70). Актив, возможно, "
                       "перегрет — растёт риск коррекции вниз.")
        elif rsi_v <= 30:
            out.append(f"RSI = {rsi_v}: зона перепроданности (<30). Возможен отскок "
                       "вверх — продавцы могли выдохнуться.")
        else:
            out.append(f"RSI = {rsi_v}: нейтральная зона (30–70). Явного перекоса "
                       "силы покупателей или продавцов нет.")

    if ind["macd_hist"] is not None:
        if feat["macd_cross_up"]:
            out.append("MACD: гистограмма только что перешла выше нуля — бычий сигнал "
                       "(импульс разворачивается вверх).")
        elif feat["macd_cross_down"]:
            out.append("MACD: гистограмма ушла ниже нуля — медвежий сигнал "
                       "(импульс слабеет).")
        elif ind["macd_hist"] > 0:
            out.append("MACD: гистограмма положительна — восходящий импульс сохраняется.")
        else:
            out.append("MACD: гистограмма отрицательна — преобладает нисходящий импульс.")

    if ind["sma20"] and ind["sma50"]:
        if ind["sma20"] > ind["sma50"]:
            out.append(f"SMA20 ({ind['sma20']}) выше SMA50 ({ind['sma50']}) — "
                       "краткосрочный тренд сильнее долгосрочного, картина бычья.")
        else:
            out.append(f"SMA20 ({ind['sma20']}) ниже SMA50 ({ind['sma50']}) — "
                       "краткосрочная слабость, картина медвежья.")

    if ind["bb_pos"] is not None:
        if ind["bb_pos"] > 0.9:
            out.append("Цена у верхней полосы Боллинджера — возможна перекупленность.")
        elif ind["bb_pos"] < 0.1:
            out.append("Цена у нижней полосы Боллинджера — возможна перепроданность.")

    out.append(f"Ближайшая поддержка ~{ind['support']}, сопротивление ~{ind['resistance']}. "
               "Пробой этих уровней обычно усиливает движение.")
    return out


# ------------------------------------------------------------------ helpers
def _last_valid(seq, skip=0):
    seen = 0
    for x in reversed(seq):
        if x is not None:
            if seen == skip:
                return x
            seen += 1
    return None


def _round(x, nd=2):
    return round(x, nd) if isinstance(x, (int, float)) else None


def _safe_ratio(a, b):
    if a is None or b is None or b == 0:
        return 1.0
    return a / b
