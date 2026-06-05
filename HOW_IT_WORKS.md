# Как всё работает

Подробное описание функциональной части платформы «ТехАнализ»: подключение к базе
данных, получение котировок, расчёт индикаторов, построение графиков, прогнозная
модель и её обучение. Все примеры кода — реальные фрагменты из проекта.

---

## 1. Общая картина: путь одного прогноза

Когда пользователь на дашборде нажимает «Построить прогноз», запрос проходит
через всю систему насквозь:

```
POST /forecast (app.py)
   │
   ├─ 1. moex.fetch_candles(ticker, history_days)   ← API Мосбиржи (ISS)
   │        └─ при недоступности: синтетический ряд (оффлайн-фолбэк)
   │
   ├─ 2. analysis.analyze(candles)                  ← чистый Python
   │        ├─ индикаторы: RSI, MACD, SMA20/50, Bollinger, уровни
   │        ├─ фигуры: тренд, двойная вершина/дно, треугольник, кресты
   │        ├─ features  — 9 чисел для модели
   │        └─ chart     — данные + разметка для Chart.js
   │
   ├─ 3. predictor.predict(features, horizon, period)
   │        ├─ есть model.pkl  → ML (RandomForest clf + reg)
   │        └─ нет model.pkl   → rule-based заглушка
   │
   ├─ 4. Forecast(...) → db.session.commit()        ← запись в SQLite
   │
   └─ 5. redirect → /forecast/<id>                  ← страница с графиками
```

Ключевой код маршрута (`app.py:133`):

```python
@app.route("/forecast", methods=["POST"])
@login_required
def make_forecast():
    ...
    cfg = PERIODS[period]                                    # история и горизонт
    candles = moex.fetch_candles(ticker, cfg["history_days"])  # 1. котировки
    res = analysis.analyze(candles)                            # 2. теханализ
    pred = predictor.predict(res["features"],                  # 3. прогноз
                             cfg["horizon_days"], period)
    summary = predictor.build_summary(...)                     # текст-обоснование

    fc = Forecast(user_id=current_user.id, ticker=ticker, ...,
                  indicators_json=json.dumps(res["indicators"], ensure_ascii=False),
                  patterns_json=json.dumps(res["patterns"], ensure_ascii=False))
    db.session.add(fc)
    db.session.commit()                                        # 4. в БД
    return redirect(url_for("forecast_detail", fc_id=fc.id))   # 5. на страницу
```

---

## 2. База данных: SQLite + SQLAlchemy

### 2.1. Как происходит коннект

Используется **Flask-SQLAlchemy** (ORM поверх SQLAlchemy). Никаких «ручных»
соединений в коде нет — всё настраивается одной строкой URI в `config.py`:

```python
class Config:
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "tehanaliz.db")
    )
```

- По умолчанию это файл **`tehanaliz.db`** (SQLite) рядом с `app.py` — сервер БД
  не нужен вообще.
- Через переменную окружения `DATABASE_URL` можно подключить любую другую БД
  (PostgreSQL, MySQL) без изменения кода: SQLAlchemy сам выберет драйвер по URI.

Объект `db` создаётся в `extensions.py` отдельно от приложения (чтобы избежать
циклических импортов между `app.py` и `models.py`):

```python
# extensions.py
from flask_sqlalchemy import SQLAlchemy
db = SQLAlchemy()
```

…и привязывается к приложению в фабрике `create_app()` (`app.py:23`):

```python
def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)   # здесь подхватывается URI базы
    db.init_app(app)                 # привязка ORM к приложению

    with app.app_context():
        db.create_all()              # создаёт таблицы при первом запуске
    return app
```

`db.create_all()` смотрит на все классы-наследники `db.Model` и выполняет
`CREATE TABLE IF NOT EXISTS` — поэтому ни миграций, ни ручного SQL для старта
не требуется: удалили `tehanaliz.db` → при следующем запуске база пересоздастся
пустой.

Само соединение SQLAlchemy открывает **лениво**: физический коннект к SQLite
создаётся при первом запросе и управляется пулом соединений; в коде мы работаем
только с сессией `db.session` (паттерн Unit of Work — изменения копятся и
записываются одним `commit()`).

### 2.2. Схема данных (models.py)

Три таблицы:

| Таблица | Класс | Что хранит |
|---|---|---|
| `users` | `User` | e-mail, хэш пароля, тариф, флаг админа |
| `forecasts` | `Forecast` | сам прогноз + снимок индикаторов и фигур |
| `diary_entries` | `DiaryEntry` | записи дневника инвестора |

```python
class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    tariff = db.Column(db.String(20), default="free", nullable=False)
    ...
    forecasts = db.relationship("Forecast", backref="user", lazy=True,
                                cascade="all, delete-orphan")
```

Важные решения:

- **Пароли не хранятся в открытом виде** — только хэш через
  `werkzeug.security.generate_password_hash` / `check_password_hash`:

  ```python
  def set_password(self, password):
      self.password_hash = generate_password_hash(password)
  ```

- **`cascade="all, delete-orphan"`** — при удалении пользователя (из админки)
  автоматически удаляются все его прогнозы и записи дневника.
- **JSON-колонки** `indicators_json` / `patterns_json` в `Forecast` — снимок
  индикаторов и найденных фигур на момент прогноза сериализуется в текст
  (`json.dumps`), а наружу отдаётся через `@property`:

  ```python
  @property
  def indicators(self):
      return json.loads(self.indicators_json) if self.indicators_json else {}
  ```

  Это сделано сознательно: индикаторы нужны странице прогноза «как было тогда»,
  а нормализовать их в отдельные таблицы незачем.
- **График в БД не хранится** (сотни точек на прогноз). Он кладётся в кэш на
  время запроса, а при повторном открытии страницы пересобирается заново из
  свежих котировок (`app.py:186`):

  ```python
  chart = app.config.get("_chart_cache", {}).get(fc.id)
  if chart is None:                       # кэш пуст (например, после рестарта)
      candles = moex.fetch_candles(fc.ticker, cfg["history_days"])
      chart = analysis.analyze(candles)["chart"]
  ```

### 2.3. Типичные запросы

Все обращения к БД идут через ORM, без сырого SQL:

```python
User.query.filter_by(email=email).first()                       # логин
Forecast.query.filter_by(user_id=current_user.id) \
    .order_by(Forecast.created_at.desc()).limit(5).all()         # последние прогнозы
User.query.filter(User.email.ilike(f"%{q}%"))                    # поиск в админке
```

Аутентификация — **Flask-Login**: сессионная кука хранит `user.id`, а на каждый
запрос пользователь восстанавливается колбэком:

```python
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))
```

---

## 3. Внешний API: котировки Мосбиржи (ISS)

### 3.1. Какой API и как используется

Единственный внешний API в проекте — **ISS (Informational & Statistical Server)
Московской биржи**. Он публичный, без ключей и регистрации. Используется один
endpoint — дневные свечи по акции:

```
https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}/candles.json
```

Параметры запроса (`moex.py:59`):

| Параметр | Значение | Смысл |
|---|---|---|
| `from` / `till` | ISO-даты | период истории |
| `interval` | `24` | дневные свечи (24 = 1 день в нотации ISS) |
| `start` | 0, 500, 1000… | смещение для пагинации |

### 3.2. Пагинация

ISS отдаёт максимум **~500 свечей за запрос**, поэтому клиент листает страницы
через параметр `start`, пока данные не закончатся:

```python
def _fetch_from_moex(ticker, frm, till):
    out, start = [], 0
    for _ in range(40):                    # предохранитель (~20000 свечей максимум)
        params = {"from": frm.isoformat(), "till": till.isoformat(),
                  "interval": 24, "start": start}
        resp = requests.get(ISS_URL.format(ticker=ticker), params=params, timeout=10)
        payload = resp.json()
        ...
        if len(rows) < 500:                # пришло меньше страницы — конец
            break
        start += len(rows)
    return out
```

Ответ ISS — JSON вида `{"candles": {"columns": [...], "data": [[...], ...]}}`,
т.е. колонки и строки отдельно. Клиент строит индекс по именам колонок и
превращает каждую строку в обычный словарь:

```python
idx = {c: i for i, c in enumerate(cols)}     # {"open": 0, "close": 1, ...}
out.append({
    "date":  str(r[idx["begin"]])[:10],
    "open":  float(r[idx["open"]]),
    "high":  float(r[idx["high"]]),
    "low":   float(r[idx["low"]]),
    "close": float(r[idx["close"]]),
    "volume": float(r[idx.get("volume", -1)] or 0),
})
```

Дальше **всё приложение работает только с этим унифицированным форматом** —
список свечей `[{date, open, high, low, close, volume}]` от старых к новым.

### 3.3. Оффлайн-фолбэк

Если сети нет, биржа недоступна или ответ пустой, `fetch_candles` подменяет
данные **детерминированным синтетическим рядом** — приложение остаётся полностью
рабочим оффлайн:

```python
def fetch_candles(ticker, history_days):
    candles = _fetch_from_moex(ticker, frm, till)
    if not candles:
        candles = _synthetic_series(ticker, history_days)   # фолбэк
    return candles
```

Синтетика генерируется без `random` — из синусов с сидом от тикера, поэтому ряд
**воспроизводим** (один тикер → всегда один и тот же график):

```python
seed = sum(ord(ch) for ch in ticker)
drift = math.sin(t + seed) * base * 0.0015                       # тренд
wave  = math.sin(i / 9.0 + seed) * base * 0.01                   # волны
noise = (math.sin(i * 12.9898 + seed) * 43758.5453) % 1 - 0.5    # псевдошум
price = max(base * 0.4, price + drift + wave * 0.3 + noise * base * 0.012)
```

### 3.4. Три функции-обёртки

| Функция | Зачем |
|---|---|
| `fetch_candles(ticker, days)` | история для анализа и графика |
| `latest_close(ticker)` | текущая цена (последнее закрытие) |
| `close_on_or_after(ticker, date)` | цена на дату исполнения прогноза — для проверки «сбылся / не сбылся» (дата может попасть на выходной, поэтому берётся ближайший следующий торговый день) |

---

## 4. Технический анализ (analysis.py)

Весь расчёт — **на чистом Python, без pandas/numpy**, чтобы приложение
запускалось с минимумом зависимостей. Главная функция:

```python
analysis.analyze(candles) -> {
    "indicators":   {...},   # значения для отображения (RSI, SMA, уровни…)
    "features":     {...},   # 9 чисел — вход модели прогноза
    "patterns":     [...],   # найденные фигуры с пояснениями и геометрией
    "explanations": [...],   # человекочитаемые тексты «что мы видим»
    "chart":        {...},   # готовые данные для Chart.js
    "last_close":   float,
}
```

### 4.1. Индикаторы

Каждый индикатор — отдельная функция, возвращающая список той же длины, что и
цены (где значение ещё не определено — `None`; Chart.js просто не рисует такие
точки).

**SMA** — обычное скользящее среднее окном `period`.

**EMA** — экспоненциальное среднее, основа MACD:

```python
def ema(values, period):
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period      # первое значение — SMA
    ...
    prev = values[i] * k + prev * (1 - k)     # рекуррентная формула
```

**RSI (14)** — по классической формуле Уайлдера со сглаживанием средних
прироста/падения:

```python
avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
rs = avg_gain / avg_loss
out[i] = 100 - (100 / (1 + rs))
```

**MACD (12, 26, 9)** — `macd_line = EMA12 − EMA26`, сигнальная — EMA9 от
macd-линии, гистограмма — их разность. Главные сигналы — пересечение гистограммой
нуля (`macd_cross_up` / `macd_cross_down`).

**Полосы Боллинджера (20, 2σ)** — SMA20 ± 2 стандартных отклонения. Из них
считается признак `bb_pos` — положение цены внутри полос (0 = нижняя, 1 = верхняя):

```python
bb_pos = (last_close - lower[-1]) / (upper[-1] - lower[-1])
```

**Уровни поддержки/сопротивления** — минимум low и максимум high за последние
40 свечей (`support_resistance`).

### 4.2. Распознавание фигур (detect_patterns)

Все фигуры строятся на двух примитивах:

- `_local_extrema(values, window)` — индексы локальных максимумов/минимумов
  (точка выше/ниже всех соседей в окне ±4);
- `_linreg(points)` — линейная регрессия методом наименьших квадратов
  (наклон + смещение).

Что распознаётся:

1. **Тренд** — регрессия по последним ~30 свечам; если суммарный наклон за
   участок > +3% — «Восходящий тренд», < −3% — «Нисходящий», иначе «Боковик».
2. **Двойная вершина / двойное дно** — два последних локальных экстремума на
   близком уровне (разница < 3%) и недалеко от правого края графика.
3. **Треугольники** (`_detect_triangle`) — по вершинам и впадинам последнего
   участка строятся две регрессионные линии (сопротивление и поддержка); если
   диапазон между ними **сужается** (ширина в конце < 85% начальной), по знакам
   наклонов определяется тип: восходящий (бычий), нисходящий (медвежий) или
   симметричный.
4. **Золотой / мёртвый крест** — пересечение быстрой SMA медленной
   (SMA50×SMA200 на длинной истории, SMA20×SMA50 на короткой) на последней свече.

Каждая фигура возвращается с **пояснением на русском** и **геометрией для
отрисовки** (`shapes_abs`) — список примитивов `line / point / hline / vline / box`
в абсолютных индексах свечей:

```python
{"name": "Восходящий треугольник", "type": "bullish",
 "explain": "Сопротивление почти горизонтально, а минимумы растут — ...",
 "shapes_abs": [
     {"type": "line", "i1": start, "y1": ..., "i2": n-1, "y2": ...,
      "label": "Сопротивление", "color": "#ff6b81"},
     {"type": "box",  "i1": start, "i2": n-1, "label": name, "color": "#7b6bff"},
 ]}
```

### 4.3. Признаки для модели (features)

Из индикаторов и фигур собирается фиксированный вектор из 9 чисел — **единый
контракт между приложением и моделью** (`FEATURE_ORDER` в `predictor.py`):

| Признак | Что это |
|---|---|
| `rsi` | RSI(14), 0–100 |
| `macd_hist` | значение MACD-гистограммы |
| `macd_cross_up` / `macd_cross_down` | 1.0, если гистограмма только что пересекла ноль |
| `sma_trend` | отношение SMA20/SMA50 (>1 — бычий тренд) |
| `bb_pos` | положение в полосах Боллинджера, 0–1 |
| `momentum10` | изменение цены за 10 свечей, % |
| `bull_patterns` / `bear_patterns` | число бычьих/медвежьих фигур на графике |

Если данных не хватает, подставляются нейтральные значения (RSI=50, bb_pos=0.5
и т.д.) — модель никогда не получает `None`.

---

## 5. Графики: бэкенд → Chart.js

### 5.1. Подготовка данных на сервере

В конце `analyze()` собирается объект `chart` — последние **180 точек** (чтобы не
раздувать страницу), все серии уже выровнены по одному массиву дат:

```python
chart = {
    "dates": win_dates,                        # ось X (категориальная, строки дат)
    "close": [...], "sma20": [...], "sma50": [...],
    "bb_upper": [...], "bb_lower": [...],      # серии цены и средних
    "rsi": [...], "macd_hist": [...],          # для двух нижних графиков
    "support": ..., "resistance": ...,
    "markup": [...],                           # разметка фигур (см. ниже)
}
```

**Разметка фигур.** Геометрия фигур из `detect_patterns` задана в абсолютных
индексах всей истории, а график показывает только окно из 180 свечей. Функция
`_build_markup` переводит индексы в подписи дат окна (и обрезает по краям):

```python
def to_x(i):
    x = i - offset                # offset = сколько свечей слева отрезано
    x = max(0, min(x, wlen - 1))  # клип по границам окна
    return win_dates[x]           # x-координата = строка-дата (категориальная ось)
```

Готовый объект сериализуется в шаблон одной строкой:

```python
return render_template("forecast_detail.html", ...,
                       chart_json=json.dumps(chart, ensure_ascii=False))
```

### 5.2. Отрисовка на клиенте

Используются две библиотеки с CDN (`forecast_detail.html:97`):

- **Chart.js 4.4.1** — сами графики;
- **chartjs-plugin-annotation 3.0.1** — линии тренда, уровни, точки и зоны фигур
  поверх ценового графика.

JSON с сервера попадает прямо в JS-константу:

```html
<script>
const C = {{ chart_json|safe }};
```

Рисуются **три canvas**:

1. **Цена** (`priceChart`) — линейный график из 5 датасетов: цена, SMA20, SMA50
   и две пунктирные полосы Боллинджера:

   ```js
   new Chart(document.getElementById('priceChart'), {
     type: 'line',
     data: {labels: C.dates, datasets: [
       {label:'Цена',  data:C.close, borderColor:'#5b8cff', borderWidth:2, pointRadius:0},
       {label:'SMA20', data:C.sma20, borderColor:'#2fd98a', borderWidth:1},
       ...
     ]},
     options: {plugins: {annotation: {annotations: buildAnnotations(C.markup)}}}
   });
   ```

2. **RSI** (`rsiChart`) — линия с фиксированной шкалой Y `min:0, max:100`.
3. **MACD** (`macdChart`) — bar-chart, столбики красятся по знаку прямо в JS:

   ```js
   backgroundColor: C.macd_hist.map(v => (v||0) >= 0 ? '#39d98a' : '#ff6b6b')
   ```

**Разметка фигур** превращается в аннотации функцией `buildAnnotations`: каждый
примитив из `C.markup` отображается в тип плагина —
`hline`/`vline` → annotation-`line`, `line` → наклонная линия (линия тренда,
границы треугольника), `point` → точка (вершины двойной вершины),
`box` → полупрозрачная зона фигуры:

```js
if (m.type === 'line'){
  ann[k] = {type:'line', xMin:m.x1, xMax:m.x2, yMin:m.y1, yMax:m.y2,
            borderColor:m.color, borderWidth:2, label: lbl(m.label,'end')};
} else if (m.type === 'box'){
  ann[k] = {type:'box', xMin:m.x1, xMax:m.x2,
            backgroundColor:m.color+'1f', borderColor:m.color+'66', ...};
}
```

Т.е. **вся «интеллектуальная» разметка считается на сервере** (Python), а клиент
только рисует готовые координаты — JS не содержит никакой логики теханализа.

---

## 6. Прогнозная модель (predictor.py + train_model.py)

### 6.1. Единый интерфейс

Прогноз изолирован за одной функцией — благодаря этому реализацию можно менять,
не трогая остальное приложение:

```python
predict(features, horizon_days, period) -> {
    "direction":   "up" | "down" | "flat",
    "change_pct":  float,    # ожидаемое изменение, %
    "probability": float,    # уверенность, 0..100
    "model":       "ml" | "rule-based",
}
```

Выбор реализации (`predictor.py:34`):

```python
def predict(features, horizon_days, period="short"):
    bundle = _load_model()                       # ленивая загрузка model.pkl
    if bundle: sub = bundle["models"].get(period)
    if sub is not None:
        try:
            return _ml_predict(sub, features, horizon_days)   # ML-путь
        except Exception:
            pass                                              # любая ошибка →
    return _rule_based_predict(features, horizon_days)        # фолбэк на правила
```

- `model.pkl` есть и читается → **ML-прогноз**;
- файла нет / нет sklearn / ошибка инференса → **rule-based** (приложение не
  падает никогда).

Загрузка модели кэшируется в модульной переменной (`_load_model`) — `joblib.load`
выполняется один раз за жизнь процесса.

### 6.2. Как обучена модель (train_model.py)

Под **каждый из трёх периодов** обучается своя пара моделей, потому что горизонты
несравнимы (неделя ≠ два года):

| Период | Горизонт (календ.) | Горизонт (торговых дней) |
|---|---|---|
| short | 7 дней | ~5 |
| medium | 120 дней | ~86 |
| long | 500 дней | ~357 |

Пайплайн обучения:

**Шаг 1 — данные.** ~5 лет (1800 дней) дневных свечей по 5 акциям через тот же
`moex.fetch_candles` (с пагинацией ISS).

**Шаг 2 — генерация примеров.** Скользим по истории: в каждой точке `i`
считаем признаки **той же функцией `analyze()`, что работает в приложении**
(никакого train/serve skew), а метку берём из будущего — что случилось через
`horizon` торговых дней:

```python
for i in range(MIN_WINDOW, len(candles) - horizon, 2):     # шаг 2 — меньше корреляции соседних примеров
    feats = analyze(candles[:i + 1])["features"]            # только прошлое до дня i
    change = (closes[i + horizon] - closes[i]) / closes[i] * 100
    X.append([feats[k] for k in FEATURE_ORDER])
    y_dir.append(1 if change >= 0 else 0)                   # метка классификатора
    y_chg.append(change)                                    # метка регрессора
```

`MIN_WINDOW = 220` — признаки не считаются, пока не накопится история для длинных
средних (SMA200).

**Шаг 3 — обучение.** Признаки нормируются `StandardScaler`, выборка делится
80/20, обучаются два **RandomForest**:

```python
scaler = StandardScaler().fit(X)
Xtr, Xte, ydtr, ydte, yctr, ycte = train_test_split(Xs, y_dir, y_chg, test_size=0.2)

clf = RandomForestClassifier(n_estimators=250, max_depth=8,
                             min_samples_leaf=5, random_state=42)   # направление
reg = RandomForestRegressor(n_estimators=250, max_depth=8,
                            min_samples_leaf=5, random_state=42)    # величина, %
```

- **Классификатор** отвечает на вопрос «вверх или вниз?» и даёт вероятность
  (`predict_proba`).
- **Регрессор** предсказывает величину изменения в процентах **сразу на свой
  горизонт** — масштабировать результат не нужно.
- Ограничения `max_depth=8`, `min_samples_leaf=5` — защита от переобучения на
  шумных рыночных данных.

**Шаг 4 — сохранение.** Все три периода складываются в один bundle:

```python
bundle = {"features": FEATURE_ORDER,
          "models": {"short":  {"clf": ..., "reg": ..., "scaler": ...},
                     "medium": {...},
                     "long":   {...}}}
joblib.dump(bundle, "model.pkl")
```

Достигнутое качество (направление, на отложенной выборке): ~0.54 на неделе
(почти случайное блуждание), ~0.62 на 2–5 месяцах, ~0.65 на 1–2 годах. Числа
честные — именно поэтому на каждом прогнозе стоит дисклеймер «не ИИР».

### 6.3. Инференс (_ml_predict)

```python
x = np.array([[features[k] for k in FEATURE_ORDER]])   # 1×9 в правильном порядке
x = model["scaler"].transform(x)                       # та же нормировка, что при обучении

proba_up = clf.predict_proba(x)[0][index_of_class_1]   # вероятность роста
change_pct = float(reg.predict(x)[0])                  # ожидаемое изменение, %

flat_thr = max(0.5, 0.45 * (horizon_days ** 0.5))      # порог «боковика» ~ √времени
direction = "up" if change_pct > flat_thr else \
            "down" if change_pct < -flat_thr else "flat"
probability = round(max(proba_up, 1 - proba_up) * 100, 1)
```

Порог боковика растёт как корень из горизонта: ±0.5% за неделю — шум, а вот
±10% за два года — уже движение.

### 6.4. Rule-based фолбэк

Если модели нет — работает прозрачная заглушка: «бычий счёт» в диапазоне
[−1..+1], куда каждый индикатор вносит вклад с понятной логикой:

```python
score = 0.0
if rsi >= 70:   score -= 0.25        # перекупленность → риск вниз
elif rsi <= 30: score += 0.25        # перепроданность → отскок
else:           score += (rsi - 50) / 100.0

if f["macd_cross_up"]:   score += 0.30
elif f["macd_cross_down"]: score -= 0.30
...
score += 0.15 * f["bull_patterns"] - 0.15 * f["bear_patterns"]
```

Счёт переводится в проценты через ту же модель «√времени»
(`change_pct = score * 0.45 * √horizon`), а уверенность — линейно от силы
сигнала (`50 + |score| * 42`, т.е. 50–92%).

### 6.5. Как подключить свою нейросеть

Контракт прост: сохранить в `model.pkl` bundle формата
`{"features": FEATURE_ORDER, "models": {period: {"clf", "reg", "scaler"?}}}`,
где `clf` имеет `predict_proba`, а `reg` — `predict`. Либо заменить тело
`_ml_predict()`. Больше ничего в приложении менять не нужно.

---

## 7. Оценка прогнозов «сбылся / не сбылся»

При создании прогноза фиксируются `price_at_forecast` и
`target_date = today + horizon_days`. Кнопка «Проверить исполнение» на странице
истории вызывает `evaluate_forecasts` (`app.py:414`):

```python
for fc in pending:                                   # все awaiting-прогнозы юзера
    if fc.target_date > date.today():
        continue                                     # срок ещё не наступил
    result_price = moex.close_on_or_after(fc.ticker, fc.target_date)
    change = (result_price - fc.price_at_forecast) / fc.price_at_forecast * 100
    fc.status = "success" if _is_hit(fc.direction, change) else "fail"
```

Критерий попадания:

```python
def _is_hit(direction, actual_change):
    if direction == "up":   return actual_change > 0
    if direction == "down": return actual_change < 0
    return abs(actual_change) <= 2.0                 # flat: коридор ±2%
```

Т.е. оценивается **направление**, а не точная величина: «вверх» сбылся, если
цена выросла хоть на сколько-то; «боковик» — если уложилась в ±2%. На странице
истории из этого считается личная точность пользователя
(`success / (success + fail)`).

---

## 8. Тарифы и лимиты

Тарифы — словарь в `config.py` (free: 1 прогноз/день, PRO: 25, Premium: 1000).
Лимит проверяется в момент создания прогноза подсчётом сегодняшних записей
пользователя:

```python
def forecasts_today(self):                            # models.py
    today = date.today()
    return sum(1 for f in self.forecasts
               if f.created_at and f.created_at.date() == today)

# app.py, make_forecast:
if current_user.forecasts_today() >= TARIFFS[current_user.tariff]["daily_limit"]:
    return redirect(url_for("tariffs"))               # на страницу тарифов
```

Смена тарифа — демо (без оплаты): `POST /tariffs/select` просто пишет новое
значение в `user.tariff`.

---

## 9. Используемые технологии — сводка

| Слой | Технология | Где |
|---|---|---|
| Веб-фреймворк | Flask (фабрика `create_app`, blueprints не используются) | `app.py` |
| ORM / БД | Flask-SQLAlchemy + SQLite (`tehanaliz.db`) | `extensions.py`, `models.py` |
| Аутентификация | Flask-Login (сессионные куки) + хэши Werkzeug | `extensions.py`, `models.py` |
| Шаблоны | Jinja2 (`templates/`), серверный рендеринг | `templates/*.html` |
| Котировки | MOEX ISS API (`requests`, пагинация по 500 свечей) + синтетический фолбэк | `moex.py` |
| Теханализ | чистый Python (RSI, MACD, SMA, Bollinger, фигуры) | `analysis.py` |
| ML | scikit-learn RandomForest (clf + reg) × 3 периода, joblib → `model.pkl` | `train_model.py`, `predictor.py` |
| Графики | Chart.js 4 + chartjs-plugin-annotation (CDN), данные готовит сервер | `forecast_detail.html` |
| Тесты | smoke-тест на Flask test client | `test_app.py` |

Принципиальные архитектурные решения:

1. **Деградация без падений** на каждом уровне: нет сети → синтетика; нет
   `model.pkl` → правила; нет кэша графика → пересборка из котировок.
2. **Единый контракт признаков** (`FEATURE_ORDER`) и общая функция `analyze()`
   для обучения и инференса — модель в проде видит ровно те же признаки, что на
   обучении.
3. **Вся логика на сервере**: фронтенд получает готовые числа и координаты,
   JS только рисует.
