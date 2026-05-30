"""Расширения Flask, вынесены отдельно во избежание циклических импортов."""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Войдите, чтобы продолжить."
