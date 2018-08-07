from .base import *

STATIC_URL = '/static/'


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'kippo_local_development',
        'HOST': '127.0.0.1',
        'PORT': 5432,
        'USER': 'postgres',
        'PASSWORD': 'mysecretpassword',
    }
}
