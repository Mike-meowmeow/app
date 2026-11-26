"""
Django settings for the EVSU recruitment system.

One environment-driven module: values come from app/.env (python-dotenv).
Development uses DJANGO_DEBUG=True; production sets DJANGO_DEBUG=False, which
turns on the HTTPS/cookie hardening below unless a key overrides it. See
.env.production.example and deploy/DEPLOY.md.
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
    'matrix.apps.MatrixConfig',
    'committee.apps.CommitteeConfig',
    'notifications.apps.NotificationsConfig',
    'analytics.apps.AnalyticsConfig',
    'operations.apps.OperationsConfig',
    'ai_processing.apps.AiProcessingConfig',
    'assistant.apps.AssistantConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'accounts.portal.PortalMiddleware',
    'accounts.middleware.TwoFactorMiddleware',
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
                'notifications.context_processors.unread',
                'assistant.context_processors.assistant',
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
CSRF_FAILURE_VIEW = 'accounts.views.csrf_failure'

# Which site this process serves (see accounts/portal.py): "public" (applicants),
# "staff" (staff accounts only), or "all" (development and tests only).
SITE_PORTAL = (env('SITE_PORTAL') or 'all').strip().lower()
if SITE_PORTAL not in ('all', 'public', 'staff'):
    raise ImproperlyConfigured('SITE_PORTAL must be "all", "public", or "staff".')
# A production web process refuses to start without a specific portal.
PORTAL_ENFORCED = not DEBUG
# Staff two-factor sign-in (accounts/two_factor.py). On by default in
# production; in development and tests it applies only to staff who set it up.
STAFF_TWO_FACTOR_REQUIRED = env_bool('STAFF_TWO_FACTOR_REQUIRED', PORTAL_ENFORCED)
# Name shown for this system in the authenticator app.
TWO_FACTOR_ISSUER = env('TWO_FACTOR_ISSUER') or 'EVSU Recruitment'
if SITE_PORTAL != 'all':
    SESSION_COOKIE_NAME = f'evsu_{SITE_PORTAL}_session'
    CSRF_COOKIE_NAME = f'evsu_{SITE_PORTAL}_csrf'

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
STATIC_ROOT = Path(env('STATIC_ROOT') or BASE_DIR / 'staticfiles')


# Private file locations (never served as public static/media).

PRIVATE_STORAGE_ROOT = Path(env('PRIVATE_STORAGE_ROOT') or BASE_DIR / 'private')
DOCUMENT_STORAGE_ROOT = PRIVATE_STORAGE_ROOT / 'documents'
EXPORT_STORAGE_ROOT = PRIVATE_STORAGE_ROOT / 'exports'
REPORT_STORAGE_ROOT = PRIVATE_STORAGE_ROOT / 'reports'

# Optional malware scanner command; the file path is appended. Exit code
# 0 = clean, 1 = infected. Empty = files are recorded as "not scanned".
MALWARE_SCAN_COMMAND = env('MALWARE_SCAN_COMMAND', '')
# When on, an upload is refused if it could not actually be scanned. Default:
# on for the production public site.
MALWARE_SCAN_REQUIRED = env_bool('MALWARE_SCAN_REQUIRED',
                                 PORTAL_ENFORCED and SITE_PORTAL == 'public')

# Default correction window offered to HR when requesting corrections.
CORRECTION_WINDOW_DAYS = int(env('CORRECTION_WINDOW_DAYS', '5'))

# Official roster (FR-021 to FR-023). Whether the preparer may approve their
# own roster is an institutional decision; it is refused until HR confirms.
ROSTER_ALLOW_SELF_APPROVAL = env_bool('ROSTER_ALLOW_SELF_APPROVAL', False)
# Recruitment type codes allowed to use the PART-TIME roster form.
ROSTER_PART_TIME_TYPES = ('PTI',)

# Committees and decisions (Stage 7). Development defaults until HR confirms
# shortlisting, final-decision, release, and conflict-resolution authority
# (FR-026). 'HR Personnel' additionally needs an active HR assignment on the
# vacancy; the other roles act on every vacancy they can see.
DECISION_RECORDING_ROLES = {
    'shortlisting': ('HR Personnel',),
    'committee_recommendation': ('HR Personnel',),
    'final_outcome': ('HR Head / Approver',),
    'appointment': ('HR Head / Approver',),
}
DECISION_RELEASE_ROLES = {
    'shortlisting': ('HR Personnel', 'HR Head / Approver'),
    'final_outcome': ('HR Head / Approver',),
    'appointment': ('HR Head / Approver',),
}
CONFLICT_RESOLUTION_ROLES = ('HR Head / Approver',)
# Roles a user must hold to be added to a committee.
COMMITTEE_MEMBER_ROLES = ('PSB Member', 'Department Head')


# Email: console backend for local development; set EMAIL_BACKEND to
# django.core.mail.backends.smtp.EmailBackend plus the EMAIL_* keys for SMTP.

EMAIL_BACKEND = env('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = env('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(env('EMAIL_PORT', '25'))
EMAIL_HOST_USER = env('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', False)
EMAIL_TIMEOUT = int(env('EMAIL_TIMEOUT', '20'))
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', 'EVSU Recruitment <no-reply@localhost>')

# Notifications (Stage 8): email copies of in-app notifications.
NOTIFICATION_EMAIL_ENABLED = env_bool('NOTIFICATION_EMAIL_ENABLED', True)
NOTIFICATION_MAX_ATTEMPTS = int(env('NOTIFICATION_MAX_ATTEMPTS', '3'))
# Base address used for links in emails.
SITE_URL = env('SITE_URL', 'http://127.0.0.1:8000')
# Applicants receive public-site links; staff receive school-site links.
PUBLIC_SITE_URL = env('PUBLIC_SITE_URL') or SITE_URL
STAFF_SITE_URL = env('STAFF_SITE_URL') or SITE_URL


# AI processing (Stage 10). Advisory only: it proposes items from verified
# documents for HR review and never changes statuses, shortlists, or rosters.
# The runtime (Tesseract, pdfplumber, pypdfium2, spaCy) is installed only where
# the worker runs (requirements-ai.txt, deploy/AI-SERVER.md). With this off,
# nothing is queued and the AI controls are hidden; manual review always works.
AI_EXTRACTION_ENABLED = env_bool('AI_EXTRACTION_ENABLED', False)
# Document types processed after verification (codes from seed_reference_data).
AI_EXTRACTION_TYPES = env_list('AI_EXTRACTION_TYPES',
                               'PDS,RESUME,TOR,DIPLOMA,PRC,TRAINING,EMPLOYMENT')
AI_MAX_ATTEMPTS = int(env('AI_MAX_ATTEMPTS', '3'))
AI_RETRY_SECONDS = int(env('AI_RETRY_SECONDS', '60'))
# A running attempt without a heartbeat for this long is treated as stopped.
AI_JOB_STALE_SECONDS = int(env('AI_JOB_STALE_SECONDS', '900'))
AI_WORKER_POLL_SECONDS = int(env('AI_WORKER_POLL_SECONDS', '5'))
AI_OCR_DPI = int(env('AI_OCR_DPI', '300'))
AI_OCR_LANGUAGES = env('AI_OCR_LANGUAGES', 'eng')
AI_MAX_PAGES = int(env('AI_MAX_PAGES', '30'))
# A PDF page with less digital text than this is read with OCR instead.
AI_MIN_PAGE_CHARACTERS = int(env('AI_MIN_PAGE_CHARACTERS', '40'))
SPACY_MODEL = env('SPACY_MODEL', 'en_core_web_sm')
# Advisory qualification checks (13.4): fixed rules comparing HR-accepted facts
# with the published standards. No AI runtime needed, so they work anywhere.
AI_QUALIFICATION_CHECKS_ENABLED = env_bool('AI_QUALIFICATION_CHECKS_ENABLED', True)
# SBERT competency matching (13.5): runs in the AI worker and needs the model
# downloaded on that computer (deploy/AI-SERVER.md step 5.5), so it is off by
# default. SBERT_MODEL is a model name in HF_HOME or a local folder.
AI_COMPETENCY_MATCHING_ENABLED = env_bool('AI_COMPETENCY_MATCHING_ENABLED', False)
SBERT_MODEL = env('SBERT_MODEL', 'sentence-transformers/all-MiniLM-L6-v2')

# Staff help assistant (floating button for HR and System Administrators).
# Answers only from assistant/help_guide.md, using models in a local Ollama
# service (deploy/AI-SERVER.md step 8.2); never reads applicant data. Off by
# default; without Ollama it falls back to a word search of the guide.
ASSISTANT_ENABLED = env_bool('ASSISTANT_ENABLED', False)
ASSISTANT_OLLAMA_URL = env('ASSISTANT_OLLAMA_URL', 'http://127.0.0.1:11434')
ASSISTANT_CHAT_MODEL = env('ASSISTANT_CHAT_MODEL', 'qwen2.5:3b')
ASSISTANT_EMBED_MODEL = env('ASSISTANT_EMBED_MODEL', 'all-minilm')
# Articles less similar than this to the question are not used (0-1 scale).
ASSISTANT_MIN_SIMILARITY = float(env('ASSISTANT_MIN_SIMILARITY', '0.35'))
ASSISTANT_TIMEOUT_SECONDS = int(env('ASSISTANT_TIMEOUT_SECONDS', '90'))
ASSISTANT_MAX_QUESTIONS_PER_10_MINUTES = int(env('ASSISTANT_MAX_QUESTIONS_PER_10_MINUTES', '30'))


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Production hardening (Stage 9). Secure defaults whenever DEBUG is False;
# each key can be overridden in .env. Behind Nginx, the proxy terminates HTTPS
# and sends X-Forwarded-Proto, so SECURE_PROXY_SSL_HEADER is trusted only when
# BEHIND_PROXY is on.

SECURE_DEFAULTS = not DEBUG
SESSION_COOKIE_SECURE = env_bool('SESSION_COOKIE_SECURE', SECURE_DEFAULTS)
CSRF_COOKIE_SECURE = env_bool('CSRF_COOKIE_SECURE', SECURE_DEFAULTS)
SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', SECURE_DEFAULTS)
SECURE_HSTS_SECONDS = int(env('SECURE_HSTS_SECONDS', '31536000' if SECURE_DEFAULTS else '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', SECURE_DEFAULTS)
SECURE_HSTS_PRELOAD = env_bool('SECURE_HSTS_PRELOAD', False)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') \
    if env_bool('BEHIND_PROXY', SECURE_DEFAULTS) else None
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS', '')
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = False  # Django's default; forms read the token from the page.
# HSTS preload is a long-term commitment for the whole domain; the institution
# decides it, so check --deploy's preload reminder is silenced deliberately.
SILENCED_SYSTEM_CHECKS = ['security.W021']

# Client IP for audit and login throttling: X-Forwarded-For is used only when
# the direct peer is one of these proxy addresses (e.g. 127.0.0.1 for Nginx).
TRUSTED_PROXIES = env_list('TRUSTED_PROXIES', '')

# Login throttling (FR-001): failed sign-ins per identifier or per IP address
# within the window lock further attempts until the window passes.
LOGIN_MAX_FAILURES = int(env('LOGIN_MAX_FAILURES') or 5)
LOGIN_LOCKOUT_MINUTES = int(env('LOGIN_LOCKOUT_MINUTES') or 15)
# Higher per-address limit: many users may share one campus network address.
LOGIN_MAX_FAILURES_PER_IP = int(env('LOGIN_MAX_FAILURES_PER_IP') or 20)
# Separate per-address limit for the staff site (production example sets 20;
# raise it when many staff computers share one network address).
STAFF_LOGIN_MAX_FAILURES_PER_IP = int(env('STAFF_LOGIN_MAX_FAILURES_PER_IP') or 100)

# Private downloads: 'django' streams files from the app; 'nginx' returns an
# X-Accel-Redirect to an internal-only location after the permission check.
PRIVATE_FILE_DELIVERY = env('PRIVATE_FILE_DELIVERY') or 'django'
PRIVATE_FILE_INTERNAL_PREFIX = env('PRIVATE_FILE_INTERNAL_PREFIX', '/protected/')

# Backups (FR-020): consistent sets of database + private files.
BACKUP_ROOT = Path(env('BACKUP_ROOT') or BASE_DIR / 'backups')
MYSQL_BIN_DIR = env('MYSQL_BIN_DIR', '')
BACKUP_MAX_AGE_HOURS = int(env('BACKUP_MAX_AGE_HOURS') or 26)
# Isolated database used by restore checks; never the live database.
RESTORE_CHECK_DATABASE = env('RESTORE_CHECK_DATABASE') or f"{DATABASES['default']['NAME']}_restore"

# Tests use temporary storage and backup folders, never private/ or backups/.
TEST_RUNNER = 'core.test_runner.IsolatedStorageRunner'

# Logging: warnings and errors to the console and logs/app.log.
LOG_DIR = Path(env('LOG_DIR') or BASE_DIR / 'logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'plain': {'format': '{asctime} {levelname} {name}: {message}',
                             'style': '{'}},
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'},
        'file': {'class': 'logging.handlers.RotatingFileHandler',
                 'filename': str(LOG_DIR / 'app.log'), 'maxBytes': 5 * 1024 * 1024,
                 'backupCount': 5, 'formatter': 'plain', 'encoding': 'utf-8',
                 'delay': True},
    },
    'root': {'handlers': ['console', 'file'], 'level': env('LOG_LEVEL', 'WARNING')},
    # 403/404 responses are normal access-control outcomes (already audited where
    # relevant); log server errors only. django.security warnings still appear.
    'loggers': {'django.request': {'level': 'ERROR'}},
}
