from .base import *

INSTALLED_APPS.append('storages')

# refer to:
# https://django-storages.readthedocs.io/en/latest/backends/amazon-S3.html
DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
STATICFILES_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
STATICFILES_LOCATION = 'static'
STATIC_ROOT = '/static/'

# S3 Bucket Config
# -- for static files
#    (For django-storages)
AWS_STORAGE_BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', 'kippo-staticfiles')
AWS_S3_CUSTOM_DOMAIN = '{}.s3.amazonaws.com'.format(AWS_STORAGE_BUCKET_NAME)
STATIC_URL = 'https://{}/'.format(AWS_S3_CUSTOM_DOMAIN)

# zappa deploy url prefix
URL_PREFIX = '/prod'
SOCIAL_AUTH_LOGIN_REDIRECT_URL = f'{URL_PREFIX}/admin/'

# double-check, this is hard coded domain restriction. (Also filtered on User creation Login
SOCIAL_AUTH_GOOGLE_OAUTH2_DOMAINS_RAW = os.getenv('SOCIAL_AUTH_GOOGLE_OAUTH2_DOMAIN', None)
if SOCIAL_AUTH_GOOGLE_OAUTH2_DOMAINS_RAW:
    SOCIAL_AUTH_GOOGLE_OAUTH2_DOMAINS = [i.strip() for i in SOCIAL_AUTH_GOOGLE_OAUTH2_DOMAINS_RAW.split(',')]
    SOCIAL_AUTH_GOOGLE_OAUTH2_WHITELISTED_DOMAINS = SOCIAL_AUTH_GOOGLE_OAUTH2_DOMAINS  # TODO: Confirm that this works...

ALLOWED_HOSTS.append(os.getenv('ALLOWED_HOST', '*'))

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'kippo'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': 5432,
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
    }
}

DISPLAY_ADMIN_AUTH_FOR_MODELBACKEND = False
