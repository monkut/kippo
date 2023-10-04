# kippo/_settings/local.py:
from .base import *  # noqa: F401

STATIC_URL = "/static/"

DEBUG = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "kippo",
        "HOST": "127.0.0.1",
        "PORT": 5432,
        "USER": "postgres",
        "PASSWORD": "mysecretpassword",
    }
}
