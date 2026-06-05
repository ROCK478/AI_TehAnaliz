"""Сквозной smoke-тест: регистрация → прогноз → лимит → оценка → дневник → тарифы.

Запуск:  python test_app.py
Использует отдельную БД test_app.db и тестовый клиент Flask (сеть не требуется —
moex.py отдаёт синтетику оффлайн).
"""
import os
import datetime

os.environ["DATABASE_URL"] = "sqlite:///test_app.db"
os.environ["EMAIL_CHECK_DELIVERABILITY"] = "0"  # без сети DNS-проверка домена недоступна

from app import create_app, evaluate_forecasts  # noqa: E402
from extensions import db                        # noqa: E402
from models import User, Forecast                # noqa: E402


def run():
    app = create_app()
    c = app.test_client()

    # публичные страницы
    for url in ("/", "/textbook", "/privacy", "/login", "/register"):
        assert c.get(url).status_code == 200, f"GET {url}"

    # регистрация: слабый пароль и некорректный e-mail должны отклоняться
    r = c.post("/register", data={"email": "a@b.com", "password": "secret1",
                                  "password2": "secret1", "accept_privacy": "on"},
               follow_redirects=True)
    assert "Пароль должен" in r.get_data(as_text=True), "слабый пароль прошёл"
    r = c.post("/register", data={"email": "ff@ff", "password": "Secret#123",
                                  "password2": "Secret#123", "accept_privacy": "on"},
               follow_redirects=True)
    assert "корректный e-mail" in r.get_data(as_text=True), "некорректный e-mail прошёл"

    # регистрация с нормальными данными
    r = c.post("/register", data={"email": "a@b.com", "password": "Secret#123",
                                  "password2": "Secret#123", "accept_privacy": "on"},
               follow_redirects=True)
    assert r.status_code == 200

    # дашборд
    assert c.get("/dashboard").status_code == 200

    # первый прогноз
    r = c.post("/forecast", data={"ticker": "SBER", "period": "short"},
               follow_redirects=True)
    assert "Прогноз и обоснование" in r.get_data(as_text=True), "нет страницы прогноза"

    # лимит бесплатного тарифа (1/день)
    r = c.post("/forecast", data={"ticker": "GAZP", "period": "short"},
               follow_redirects=True)
    assert "лимит" in r.get_data(as_text=True).lower(), "лимит не сработал"

    # история
    assert c.get("/history").status_code == 200

    # оценка исполнения: «состарим» прогноз и проверим статус
    with app.app_context():
        fc = Forecast.query.first()
        fc.target_date = datetime.date.today() - datetime.timedelta(days=1)
        db.session.commit()
        n = evaluate_forecasts(User.query.first().id)
        fc = Forecast.query.first()
        assert n == 1 and fc.status in ("success", "fail"), "оценка не сработала"
        print(f"  оценка: статус={fc.status}, факт={fc.result_change_pct}%")

    # дневник
    r = c.post("/diary", data={"title": "Test trade", "ticker": "SBER",
                               "body": "note", "emotion": "calm"},
               follow_redirects=True)
    assert "Test trade" in r.get_data(as_text=True)

    # смена тарифа
    r = c.post("/tariffs/select", data={"tariff": "pro"}, follow_redirects=True)
    assert r.status_code == 200

    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    try:
        run()
    finally:
        for p in ("test_app.db", os.path.join("instance", "test_app.db")):
            if os.path.exists(p):
                os.remove(p)
