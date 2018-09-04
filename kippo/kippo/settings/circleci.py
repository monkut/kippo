from .base import *

STATIC_URL = '/static/'

DEBUG = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'circle_test',
        'HOST': '127.0.0.1',
        'PORT': 5432,
        'USER': 'circleci',
        'PASSWORD': '',
    }
}
