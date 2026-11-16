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
    'audit.apps.AuditConfig',
    'documents.apps.DocumentsConfig',
    'recruitment.apps.RecruitmentConfig',
    'applicants.apps.ApplicantsConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'audit.middleware.AuditContextMiddleware',
    'accounts.middleware.PrivacyAcknowledgmentMiddleware',
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
                'accounts.context_processors.workspaces',
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

# Custom user model (must be in accounts/0001_initial.py).
AUTH_USER_MODEL = 'accounts.User'


# Authentication and sessions (FR-001, FR-003)

AUTHENTICATION_BACKENDS = ['accounts.backends.EmailOrUsernameBackend']
LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'core:dashboard'
LOGOUT_REDIRECT_URL = 'accounts:login'

# Inactivity timeout: the session expiry is renewed on every request.
SESSION_IDLE_TIMEOUT_MINUTES = int(env('SESSION_IDLE_TIMEOUT_MINUTES', '30'))
SESSION_COOKIE_AGE = SESSION_IDLE_TIMEOUT_MINUTES * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True

PASSWORD_RESET_TIMEOUT = int(env('PASSWORD_RESET_TIMEOUT_MINUTES', '60')) * 60
EMAIL_VERIFICATION_TIMEOUT = int(env('EMAIL_VERIFICATION_TIMEOUT_HOURS', '48')) * 3600

# Version of the privacy notice applicants must acknowledge. The wording is a
# draft until HR confirms it; changing the version requires re-acknowledgment.
PRIVACY_NOTICE_VERSION = env('PRIVACY_NOTICE_VERSION', 'DRAFT-2026-11')


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

# Optional malware scanner command; the file path is appended. Exit code
# 0 = clean, 1 = infected. Empty = files are recorded as "not scanned".
MALWARE_SCAN_COMMAND = env('MALWARE_SCAN_COMMAND', '')

# Default correction window offered to HR when requesting corrections.
CORRECTION_WINDOW_DAYS = int(env('CORRECTION_WINDOW_DAYS', '5'))


# Email: console backend for local development.

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', 'EVSU Recruitment <no-reply@localhost>')


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
