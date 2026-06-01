"""Модели данных (SQLAlchemy): пользователи, прогнозы, дневник инвестора."""
import json
from datetime import datetime, date, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    tariff = db.Column(db.String(20), default="free", nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    accepted_privacy = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    forecasts = db.relationship("Forecast", backref="user", lazy=True,
                                cascade="all, delete-orphan")
    diary_entries = db.relationship("DiaryEntry", backref="user", lazy=True,
                                    cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def forecasts_today(self):
        today = date.today()
        return sum(1 for f in self.forecasts
                   if f.created_at and f.created_at.date() == today)


class Forecast(db.Model):
    __tablename__ = "forecasts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    ticker = db.Column(db.String(16), nullable=False)
    period = db.Column(db.String(16), nullable=False)          # short / medium / long
    horizon_days = db.Column(db.Integer, nullable=False)

    direction = db.Column(db.String(8), nullable=False)        # up / down / flat
    predicted_change_pct = db.Column(db.Float, nullable=False)  # ожидаемое изменение, %
    probability = db.Column(db.Float, nullable=False)          # вероятность 0..100, %
    price_at_forecast = db.Column(db.Float, nullable=False)
    target_date = db.Column(db.Date, nullable=False)

    summary_text = db.Column(db.Text)        # человекочитаемое объяснение
    indicators_json = db.Column(db.Text)     # снимок индикаторов
    patterns_json = db.Column(db.Text)       # найденные фигуры

    # Итог прогноза
    status = db.Column(db.String(12), default="awaiting", nullable=False)  # awaiting/success/fail
    result_price = db.Column(db.Float)
    result_change_pct = db.Column(db.Float)
    evaluated_at = db.Column(db.DateTime)

    @property
    def indicators(self):
        return json.loads(self.indicators_json) if self.indicators_json else {}

    @property
    def patterns(self):
        return json.loads(self.patterns_json) if self.patterns_json else []

    @property
    def status_label(self):
        return {"awaiting": "Ожидает", "success": "Сбылся",
                "fail": "Не сбылся"}.get(self.status, self.status)


class DiaryEntry(db.Model):
    __tablename__ = "diary_entries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    ticker = db.Column(db.String(16))
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text)
    emotion = db.Column(db.String(40))   # настроение/эмоция сделки
