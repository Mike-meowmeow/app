"""Run as root on the server: builds /srv/evsu-prod/.env (deploy/TUNNEL.md step 4).
Prints only key names, never values."""
import os
import pwd
import re
import secrets

EXAMPLE = '/srv/evsu-prod/.env.production.example'
TARGET = '/srv/evsu-prod/.env'
DEV = '/srv/evsu-recruitment/.env'
SECRETS = '/etc/evsu-prod/db.secrets'


def read(path):
    values = {}
    for line in open(path, encoding='utf-8'):
        if '=' in line and not line.lstrip().startswith('#'):
            key, _, value = line.rstrip('\n').partition('=')
            values[key.strip()] = value
    return values


db = read(SECRETS)
dev = read(DEV)
copied = {k: dev[k] for k in ('EMAIL_HOST', 'EMAIL_PORT', 'EMAIL_USE_TLS', 'EMAIL_HOST_USER',
                              'DEFAULT_FROM_EMAIL', 'PRIVACY_NOTICE_VERSION', 'MYSQL_BIN_DIR',
                              'NOTIFICATION_EMAIL_ENABLED', 'CORRECTION_WINDOW_DAYS')
          if k in dev}
values = {
    'DJANGO_SECRET_KEY': secrets.token_urlsafe(64),
    'DJANGO_DEBUG': 'False',
    'PUBLIC_SITE_URL': 'https://apply.evsuocc-recruitment.online',
    'STAFF_SITE_URL': 'https://staff.evsuocc-recruitment.online',
    'BEHIND_PROXY': 'True',
    'TRUSTED_PROXIES': '127.0.0.1,unix',
    'PRIVATE_FILE_DELIVERY': 'nginx',
    'DB_NAME': 'evsu_recruitment',
    'DB_USER': 'evsu_app',
    'DB_PASSWORD': db['DB_APP_PASSWORD'],
    'DB_HOST': '127.0.0.1',
    'DB_PORT': '3306',
    'PRIVATE_STORAGE_ROOT': '/srv/evsu-prod/private',
    'STATIC_ROOT': '/srv/evsu-prod/staticfiles',
    'BACKUP_ROOT': '/srv/evsu-prod-backups',
    'LOG_DIR': '/var/log/evsu-prod',
    'EMAIL_BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
    'EMAIL_HOST_PASSWORD': '',  # set by the project owner (evsu-prod-set-smtp)
    'SECURE_HSTS_SECONDS': '3600',
    'MALWARE_SCAN_COMMAND': 'clamdscan --no-summary --fdpass',
    'HF_HOME': '/srv/evsu-models',
    'HF_HUB_OFFLINE': '1',
    'TRANSFORMERS_OFFLINE': '1',
    'SBERT_MODEL': 'sentence-transformers/all-MiniLM-L6-v2',
    'SPACY_MODEL': 'en_core_web_sm',
    'AI_EXTRACTION_ENABLED': 'True',
    'AI_QUALIFICATION_CHECKS_ENABLED': 'True',
    'AI_COMPETENCY_MATCHING_ENABLED': 'True',
    'ASSISTANT_ENABLED': 'True',
    **copied,
}

lines, seen = [], set()
for line in open(EXAMPLE, encoding='utf-8'):
    match = re.match(r'#?\s*([A-Z][A-Z0-9_]*)=', line)
    if match and match.group(1) in values and match.group(1) not in seen:
        key = match.group(1)
        lines.append(f'{key}={values[key]}\n')
        seen.add(key)
    else:
        lines.append(line)
missing = [k for k in values if k not in seen]
if missing:
    lines.append('\n# Added for this server (deploy/TUNNEL.md)\n')
    lines += [f'{k}={values[k]}\n' for k in missing]

if os.path.exists(TARGET):
    raise SystemExit(f'{TARGET} already exists; not overwritten.')
old = os.umask(0o077)
with open(TARGET, 'w', encoding='utf-8') as f:
    f.writelines(lines)
os.umask(old)
user = pwd.getpwnam('evsuprod')
os.chown(TARGET, user.pw_uid, user.pw_gid)
os.chmod(TARGET, 0o600)
placeholders = [l.split('=')[0] for l in lines if '=<' in l and not l.startswith('#')]
print('written:', TARGET)
print('keys set:', ', '.join(sorted(values)))
print('copied from the development copy:', ', '.join(sorted(copied)) or 'none')
print('still placeholders:', ', '.join(placeholders) or 'none')
