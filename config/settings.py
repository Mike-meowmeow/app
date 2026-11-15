"""
Django settings for the EVSU recruitment system (local development baseline).

Values come from app/.env, loaded explicitly with python-dotenv.
Split into base/local/test/production modules before deployment.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise ImproperlyConfigured(f'Missing required environment variable: {name}')
    return value


def env_bool(name, default=False):
    return str(env(name, str(default))).strip().lower() in {'1', 'true', 'yes', 'on'}


def env_list(name, default=''):
    return [item.strip() for item in env(name, default).split(',') if item.strip()]


SECRET_KEY = env('DJANGO_SECRET_KEY', required=True)
DEBUG = env_bool('DJANGO_DEBUG', False)
ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1')


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Project apps
    'core.apps.CoreConfig',
    'accounts.apps.AccountsConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database: MySQL, InnoDB, utf8mb4, strict mode, READ COMMITTED.

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': env('DB_NAME', required=True),
        'USER': env('DB_USER', required=True),
        'PASSWORD': env('DB_PASSWORD', required=True),
        'HOST': env('DB_HOST', '127.0.0.1'),
        'PORT': env('DB_PORT', '3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'isolation_level': 'read committed',
            'init_command': (
                "SET sql_mode='STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,"
                "ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION', "
                "default_storage_engine=INNODB"
            ),
        },
        'TEST': {
            'NAME': f"test_{env('DB_NAME', 'evsu_recruitment_dev')}",
            'CHARSET': 'utf8mb4',
            'COLLATION': 'utf8mb4_0900_ai_ci',
        },
    }
}

# Custom user model: set in Stage 2, before the first migrate.
# AUTH_USER_MODEL = 'accounts.User'


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization: store UTC, display Asia/Manila.

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Manila'
USE_I18N = True
USE_TZ = True


# Static files

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'


# Private file locations (never served as public static/media).

PRIVATE_STORAGE_ROOT = BASE_DIR / 'private'
DOCUMENT_STORAGE_ROOT = PRIVATE_STORAGE_ROOT / 'documents'
EXPORT_STORAGE_ROOT = PRIVATE_STORAGE_ROOT / 'exports'


# Email: console backend for local development.

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
