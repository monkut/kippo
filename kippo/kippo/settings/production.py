from .base import *

INSTALLED_APPS.append('storages')

# refer to:
# https://django-storages.readthedocs.io/en/latest/backends/amazon-S3.html
DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
STATICFILES_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
STATICFILES_LOCATION = 'static'

# S3 Bucket Config
# -- for static files
#    (For django-storages)
AWS_STORAGE_BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', 'kippo-staticfiles')
AWS_S3_CUSTOM_DOMAIN = '{}.s3.amazonaws.com'.format(AWS_STORAGE_BUCKET_NAME)
STATIC_URL = 'https://{}/'.format(AWS_S3_CUSTOM_DOMAIN)

# zappa deploy url prefix
URL_PREFIX = '/production'


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'kippo',
        'HOST': os.getenv('DB_HOST'),
        'PORT': 5432,
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
    }
}
