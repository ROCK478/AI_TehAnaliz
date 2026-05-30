"""Клиент Московской биржи (ISS API).

Тянет дневные свечи по тикеру. Если биржа недоступна (нет сети / выходной /
лимиты), отдаёт детерминированно сгенерированный ряд, чтобы приложение
оставалось работоспособным для разработки и тестирования.
"""
import math
from datetime import date, timedelta

try:
    import requests
except ImportError:  # пакет ставится из requirements.txt
    requests = None

ISS_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/"
    "securities/{ticker}/candles.json"
)

# Базовые цены для синтетического фолбэка (примерные уровни)
_BASE_PRICE = {
    "SBER": 300.0, "GAZP": 160.0, "LKOH": 7000.0,
    "GMKN": 150.0, "ROSN": 560.0,
}


def fetch_candles(ticker, history_days):
    """Список свечей [{date, open, high, low, close, volume}], старые -> новые."""
    till = date.today()
    frm = till - timedelta(days=history_days)
    candles = _fetch_from_moex(ticker, frm, till)
    if not candles:
        candles = _synthetic_series(ticker, history_days)
    return candles


def latest_close(ticker):
    """Текущая (последняя доступная) цена закрытия."""
    candles = fetch_candles(ticker, 14)
    return candles[-1]["close"] if candles else None


def close_on_or_after(ticker, target_date):
    """Цена закрытия на дату target_date (или ближайшую следующую торговую).

    Используется для оценки сбылся прогноз или нет.
    """
    if target_date > date.today():
        return None
    span = (date.today() - target_date).days + 10
    candles = fetch_candles(ticker, span)
    for c in candles:
        if _parse_date(c["date"]) >= target_date:
            return c["close"]
    return candles[-1]["close"] if candles else None


# ----------------------------------------------------------------------------
def _fetch_from_moex(ticker, frm, till):
    """Тянет свечи постранично: ISS отдаёт максимум ~500 строк за запрос,
    поэтому листаем через параметр start, пока приходят новые данные."""
    if requests is None:
        return []
    out = []
    start = 0
    for _ in range(40):  # предохранитель от бесконечного цикла (до ~20000 свечей)
        params = {"from": frm.isoformat(), "till": till.isoformat(),
                  "interval": 24, "start": start}
        try:
            resp = requests.get(ISS_URL.format(ticker=ticker), params=params, timeout=10)
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            break

        block = payload.get("candles", {})
        cols = block.get("columns", [])
        rows = block.get("data", [])
        if not cols or not rows:
            break

        idx = {c: i for i, c in enumerate(cols)}
        for r in rows:
            try:
                out.append({
                    "date": str(r[idx["begin"]])[:10],
                    "open": float(r[idx["open"]]),
                    "high": float(r[idx["high"]]),
                    "low": float(r[idx["low"]]),
                    "close": float(r[idx["close"]]),
                    "volume": float(r[idx.get("volume", -1)] or 0),
                })
            except (KeyError, TypeError, ValueError):
                continue

        if len(rows) < 500:    # последняя страница
            break
        start += len(rows)
    return out


def _synthetic_series(ticker, history_days):
    """Псевдослучайный, но детерминированный ряд цен (без зависимости от сети)."""
    base = _BASE_PRICE.get(ticker, 100.0)
    seed = sum(ord(ch) for ch in ticker)
    n = max(60, min(history_days, 1200))
    today = date.today()
    out = []
    price = base
    for i in range(n):
        t = i / 18.0
        # тренд + волны + детерминированный «шум»
        drift = math.sin(t + seed) * base * 0.0015
        wave = math.sin(i / 9.0 + seed) * base * 0.01
        noise = (math.sin(i * 12.9898 + seed) * 43758.5453) % 1 - 0.5
        price = max(base * 0.4, price + drift + wave * 0.3 + noise * base * 0.012)
        d = today - timedelta(days=(n - i))
        high = price * 1.012
        low = price * 0.988
        out.append({
            "date": d.isoformat(),
            "open": round(price * 0.999, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(price, 2),
            "volume": round(100000 + (noise + 0.5) * 50000, 0),
        })
    return out


def _parse_date(s):
    return date.fromisoformat(str(s)[:10])
