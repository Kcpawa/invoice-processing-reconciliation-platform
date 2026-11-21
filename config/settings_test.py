"""Test settings.

Tests run on an in-memory SQLite database by default so the suite needs no
running services. Set TEST_USE_POSTGRES=1 to run them against the PostgreSQL
instance configured through the DB_* variables instead.
"""
from .settings import *  # noqa: F401,F403
from .settings import env_bool

if not env_bool("TEST_USE_POSTGRES", False):
    DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    }

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
