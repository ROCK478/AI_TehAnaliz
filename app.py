"""ТехАнализ — образовательная платформа прогнозов по акциям Мосбиржи.

Flask + Jinja + SQLAlchemy. Точка входа и все маршруты.
Запуск:  python app.py   (см. README.md)
"""
import json
import random
from datetime import date, timedelta

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, abort)
from flask_login import (login_user, logout_user, login_required, current_user)

from config import (Config, STOCKS, STOCK_TICKERS, PERIODS, TARIFFS,
                    DISCLAIMER, RISK_TIPS, stock_name)
from extensions import db, login_manager
from models import User, Forecast, DiaryEntry
import moex
import analysis
import predictor


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # Глобальные переменные для всех шаблонов
    @app.context_processor
    def inject_globals():
        return {
            "DISCLAIMER": DISCLAIMER,
            "risk_tip": random.choice(RISK_TIPS),
            "TARIFFS": TARIFFS,
            "current_year": date.today().year,
        }

    register_routes(app)

    with app.app_context():
        db.create_all()

    return app


def register_routes(app):

    # ----------------------------------------------------------- лендинг
    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        return render_template("index.html", stocks=STOCKS)

    @app.route("/privacy")
    def privacy():
        return render_template("privacy.html")

    # --------------------------------------------------------- регистрация
    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            password2 = request.form.get("password2") or ""
            accepted = request.form.get("accept_privacy")

            errors = []
            if "@" not in email or "." not in email:
                errors.append("Введите корректный e-mail.")
            if len(password) < 6:
                errors.append("Пароль должен быть не короче 6 символов.")
            if password != password2:
                errors.append("Пароли не совпадают.")
            if not accepted:
                errors.append("Необходимо принять политику конфиденциальности.")
            if User.query.filter_by(email=email).first():
                errors.append("Пользователь с таким e-mail уже существует.")

            if errors:
                for e in errors:
                    flash(e, "error")
                return render_template("register.html", email=email)

            user = User(email=email, tariff="free", accepted_privacy=True)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("Добро пожаловать! Аккаунт создан.", "success")
            return redirect(url_for("dashboard"))

        return render_template("register.html")

    # --------------------------------------------------------------- вход
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                login_user(user)
                return redirect(url_for("dashboard"))
            flash("Неверный e-mail или пароль.", "error")
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("index"))

    # ---------------------------------------------------------- дашборд
    @app.route("/dashboard")
    @login_required
    def dashboard():
        limit = TARIFFS[current_user.tariff]["daily_limit"]
        used = current_user.forecasts_today()
        recent = (Forecast.query.filter_by(user_id=current_user.id)
                  .order_by(Forecast.created_at.desc()).limit(5).all())
        return render_template("dashboard.html", stocks=STOCKS, periods=PERIODS,
                               limit=limit, used=used, remaining=max(0, limit - used),
                               recent=recent)

    # ------------------------------------------------------ создать прогноз
    @app.route("/forecast", methods=["POST"])
    @login_required
    def make_forecast():
        ticker = request.form.get("ticker")
        period = request.form.get("period")

        if ticker not in STOCK_TICKERS or period not in PERIODS:
            flash("Выберите акцию и период из списка.", "error")
            return redirect(url_for("dashboard"))

        # лимит тарифа
        limit = TARIFFS[current_user.tariff]["daily_limit"]
        if current_user.forecasts_today() >= limit:
            flash(f"Достигнут дневной лимит прогнозов для тарифа "
                  f"«{TARIFFS[current_user.tariff]['label']}» ({limit}/день). "
                  f"Загляните во вкладку «Тарифы».", "error")
            return redirect(url_for("tariffs"))

        cfg = PERIODS[period]
        candles = moex.fetch_candles(ticker, cfg["history_days"])
        if not candles or len(candles) < 30:
            flash("Не удалось получить достаточно данных по акции. Попробуйте позже.",
                  "error")
            return redirect(url_for("dashboard"))

        res = analysis.analyze(candles)
        pred = predictor.predict(res["features"], cfg["horizon_days"], period)
        summary = predictor.build_summary(
            stock_name(ticker), cfg["label"], pred,
            res["patterns"], res["explanations"])

        target = date.today() + timedelta(days=cfg["horizon_days"])
        fc = Forecast(
            user_id=current_user.id, ticker=ticker, period=period,
            horizon_days=cfg["horizon_days"], direction=pred["direction"],
            predicted_change_pct=pred["change_pct"], probability=pred["probability"],
            price_at_forecast=res["last_close"], target_date=target,
            summary_text=summary,
            indicators_json=json.dumps(res["indicators"], ensure_ascii=False),
            patterns_json=json.dumps(res["patterns"], ensure_ascii=False),
        )
        db.session.add(fc)
        db.session.commit()
        # график не храним в БД — кладём во временный кэш на время запроса
        app.config.setdefault("_chart_cache", {})[fc.id] = res["chart"]
        return redirect(url_for("forecast_detail", fc_id=fc.id))

    @app.route("/forecast/<int:fc_id>")
    @login_required
    def forecast_detail(fc_id):
        fc = db.session.get(Forecast, fc_id)
        if not fc or fc.user_id != current_user.id:
            abort(404)
        # пересобираем график (в БД не храним ради компактности)
        chart = app.config.get("_chart_cache", {}).get(fc.id)
        if chart is None:
            cfg = PERIODS[fc.period]
            candles = moex.fetch_candles(fc.ticker, cfg["history_days"])
            chart = analysis.analyze(candles)["chart"] if candles else None
        return render_template("forecast_detail.html", fc=fc,
                               stock_name=stock_name(fc.ticker),
                               period=PERIODS[fc.period],
                               chart_json=json.dumps(chart, ensure_ascii=False))

    # ------------------------------------------------------- история
    @app.route("/history")
    @login_required
    def history():
        forecasts = (Forecast.query.filter_by(user_id=current_user.id)
                     .order_by(Forecast.created_at.desc()).all())
        stats = {
            "total": len(forecasts),
            "success": sum(1 for f in forecasts if f.status == "success"),
            "fail": sum(1 for f in forecasts if f.status == "fail"),
            "awaiting": sum(1 for f in forecasts if f.status == "awaiting"),
        }
        done = stats["success"] + stats["fail"]
        stats["accuracy"] = round(stats["success"] / done * 100, 1) if done else None
        return render_template("history.html", forecasts=forecasts, stats=stats,
                               stock_name=stock_name)

    @app.route("/history/refresh", methods=["POST"])
    @login_required
    def refresh_history():
        """Оценивает прогнозы, у которых наступила дата исполнения."""
        updated = evaluate_forecasts(current_user.id)
        flash(f"Обновлено прогнозов: {updated}." if updated
              else "Нет прогнозов, готовых к оценке (срок ещё не наступил).",
              "success" if updated else "info")
        return redirect(url_for("history"))

    # --------------------------------------------------------- тарифы
    @app.route("/tariffs")
    @login_required
    def tariffs():
        return render_template("tariffs.html", current=current_user.tariff)

    @app.route("/tariffs/select", methods=["POST"])
    @login_required
    def select_tariff():
        tariff = request.form.get("tariff")
        if tariff in TARIFFS:
            current_user.tariff = tariff
            db.session.commit()
            flash(f"Тариф изменён на «{TARIFFS[tariff]['label']}» (демо-режим, "
                  f"без реальной оплаты).", "success")
        return redirect(url_for("tariffs"))

    # --------------------------------------------------------- доп. фишки
    @app.route("/news")
    @login_required
    def news():
        return render_template("news.html", news=DEMO_NEWS)

    @app.route("/news/<news_id>")
    @login_required
    def news_detail(news_id):
        item = next((n for n in DEMO_NEWS if n["id"] == news_id), None)
        if item is None:
            abort(404)
        return render_template("news_detail.html", n=item)

    @app.route("/textbook")
    def textbook():
        return render_template("textbook.html", lessons=TEXTBOOK)

    @app.route("/diary", methods=["GET", "POST"])
    @login_required
    def diary():
        if request.method == "POST":
            title = (request.form.get("title") or "").strip()
            if not title:
                flash("Заголовок записи обязателен.", "error")
            else:
                entry = DiaryEntry(
                    user_id=current_user.id, title=title,
                    ticker=(request.form.get("ticker") or "").strip().upper(),
                    body=(request.form.get("body") or "").strip(),
                    emotion=(request.form.get("emotion") or "").strip())
                db.session.add(entry)
                db.session.commit()
                flash("Запись добавлена в дневник.", "success")
            return redirect(url_for("diary"))
        entries = (DiaryEntry.query.filter_by(user_id=current_user.id)
                   .order_by(DiaryEntry.created_at.desc()).all())
        return render_template("diary.html", entries=entries, stocks=STOCKS)

    @app.route("/diary/<int:entry_id>/delete", methods=["POST"])
    @login_required
    def delete_diary(entry_id):
        entry = db.session.get(DiaryEntry, entry_id)
        if entry and entry.user_id == current_user.id:
            db.session.delete(entry)
            db.session.commit()
            flash("Запись удалена.", "success")
        return redirect(url_for("diary"))


def evaluate_forecasts(user_id):
    """Проставляет статус (сбылся/не сбылся) прогнозам с наступившим сроком."""
    pending = Forecast.query.filter_by(user_id=user_id, status="awaiting").all()
    updated = 0
    for fc in pending:
        if fc.target_date > date.today():
            continue
        result_price = moex.close_on_or_after(fc.ticker, fc.target_date)
        if result_price is None:
            continue
        change = (result_price - fc.price_at_forecast) / fc.price_at_forecast * 100
        fc.result_price = round(result_price, 2)
        fc.result_change_pct = round(change, 2)
        fc.evaluated_at = date.today()
        fc.status = "success" if _is_hit(fc.direction, change) else "fail"
        updated += 1
    if updated:
        db.session.commit()
    return updated


def _is_hit(direction, actual_change):
    if direction == "up":
        return actual_change > 0
    if direction == "down":
        return actual_change < 0
    return abs(actual_change) <= 2.0   # flat


# --------------------------------------------------- демо-контент (заглушки)
DEMO_NEWS = [
    {"id": "key-rate", "date": "2026-05-29", "title": "ЦБ сохранил ключевую ставку",
     "tag": "Макро", "source": "Демо-лента",
     "summary": "Регулятор оставил ставку без изменений; рынок акций отреагировал "
                "умеренным ростом голубых фишек.",
     "body": [
         "Банк России по итогам заседания принял решение сохранить ключевую "
         "ставку на прежнем уровне. Регулятор отметил, что инфляционные ожидания "
         "остаются повышенными, но постепенно стабилизируются.",
         "Для рынка акций сохранение ставки — умеренно позитивный сигнал: высокая "
         "ставка делает облигации и депозиты привлекательнее акций, поэтому пауза "
         "в ужесточении поддерживает котировки.",
         "Чему здесь учиться: ключевая ставка — один из главных макрофакторов. "
         "Рост ставки обычно давит на акции (особенно закредитованных компаний), "
         "снижение — поддерживает. Это фундаментальный фон, на котором работает "
         "технический анализ."]},
    {"id": "sber-report", "date": "2026-05-28", "title": "Сбербанк отчитался о прибыли",
     "tag": "SBER", "source": "Демо-лента",
     "summary": "Квартальная прибыль выше ожиданий аналитиков, акции прибавили на "
                "открытии торгов.",
     "body": [
         "Сбербанк опубликовал финансовые результаты за квартал. Чистая прибыль "
         "превысила консенсус-прогноз аналитиков, рентабельность капитала осталась "
         "на высоком уровне.",
         "На открытии торгов бумаги SBER прибавили на повышенных объёмах — реакция "
         "рынка на сильную отчётность.",
         "Чему здесь учиться: отчётности — пример «события-катализатора». На графике "
         "это часто видно как гэп (разрыв) и всплеск объёма. Технический трейдер "
         "следит за тем, удержится ли цена выше уровня до отчёта."]},
    {"id": "brent-80", "date": "2026-05-27", "title": "Нефть Brent выше $80",
     "tag": "Сырьё", "source": "Демо-лента",
     "summary": "Рост нефтяных котировок поддержал бумаги нефтегазового сектора — "
                "Лукойл и Роснефть в плюсе.",
     "body": [
         "Котировки нефти марки Brent поднялись выше отметки $80 за баррель на фоне "
         "сокращения запасов и геополитической премии в цене.",
         "Бумаги нефтегазового сектора отреагировали ростом: Лукойл (LKOH) и "
         "Роснефть (ROSN) прибавили вслед за сырьём.",
         "Чему здесь учиться: акции нефтяников сильно коррелируют с ценой нефти. "
         "Понимание таких межрыночных связей помогает интерпретировать движения "
         "и не принимать рост сектора за «случайность» на графике."]},
    {"id": "div-season", "date": "2026-05-26", "title": "Дивидендный сезон на Мосбирже",
     "tag": "Дивиденды", "source": "Демо-лента",
     "summary": "Ряд компаний объявили рекомендации по дивидендам за прошлый год.",
     "body": [
         "Несколько крупных эмитентов объявили рекомендации совета директоров по "
         "дивидендам за прошлый год. Инвесторы оценивают дивидендную доходность "
         "и даты закрытия реестра.",
         "После дивидендной отсечки цена акции, как правило, открывается с гэпом "
         "вниз примерно на размер дивиденда — это называется дивидендный гэп.",
         "Чему здесь учиться: дивидендный гэп — нормальное явление, а не «обвал». "
         "Технический анализ учитывает такие разрывы, чтобы не путать их с "
         "разворотом тренда."]},
]

TEXTBOOK = [
    {"id": "trend", "title": "Тренды и их виды",
     "body": "Тренд — основное направление движения цены. Бывает восходящим "
             "(серия более высоких максимумов и минимумов), нисходящим и боковым "
             "(флэт). Главное правило теханализа: «тренд — твой друг». Торговля "
             "против тренда требует опыта и строгого риск-менеджмента."},
    {"id": "support", "title": "Поддержка и сопротивление",
     "body": "Поддержка — уровень, от которого цена отскакивает вверх (много "
             "покупателей). Сопротивление — уровень, где цена разворачивается вниз "
             "(много продавцов). Пробой уровня с ростом объёма часто продолжает "
             "движение в сторону пробоя."},
    {"id": "rsi", "title": "Индикатор RSI",
     "body": "RSI (индекс относительной силы) измеряет скорость и величину движения "
             "цены по шкале 0–100. Выше 70 — перекупленность (риск снижения), ниже "
             "30 — перепроданность (возможен отскок). RSI хорош для поиска разворотов, "
             "но в сильном тренде может долго оставаться в крайней зоне."},
    {"id": "macd", "title": "Индикатор MACD",
     "body": "MACD строится на разнице двух скользящих средних (обычно EMA12 и EMA26) "
             "и сигнальной линии. Пересечение гистограммой нулевой линии снизу вверх — "
             "бычий сигнал, сверху вниз — медвежий. MACD показывает смену импульса."},
    {"id": "figures", "title": "Графические фигуры",
     "body": "Фигуры — повторяющиеся узнаваемые формы на графике. «Двойная вершина» и "
             "«двойное дно» сигналят о развороте. «Голова и плечи» — о смене тренда. "
             "«Треугольники» и «флаги» — о продолжении движения. Фигуры подтверждают "
             "сигналы индикаторов, но не дают гарантий."},
    {"id": "risk", "title": "Риск-менеджмент",
     "body": "Самая важная глава. Определяйте размер позиции так, чтобы убыток по "
             "стоп-лоссу не превышал 1–2% депозита. Всегда фиксируйте план сделки: "
             "точку входа, стоп и цель. Без управления риском даже точные прогнозы "
             "приводят к потерям."},
]


app = create_app()

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=True, port=port)
