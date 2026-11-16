#!/usr/bin/env python3
"""Find the project's MySQL database, creating it and its account when missing.

Run on any device (safe to repeat):

    Windows:      py setup_db.py           (or .venv\\Scripts\\python.exe setup_db.py)
    macOS/Linux:  python3 setup_db.py

The script re-launches itself inside .venv, so any Python 3 works. Run
setup.ps1 / setup.sh first on a new device; that creates .venv and .env.

It reads DB_* settings from .env. If the application account can already use
the database, nothing is changed. Otherwise it connects as a MySQL
administrator (prompted, or MYSQL_ROOT_USER / MYSQL_ROOT_PASSWORD) and:

  - creates DB_NAME (utf8mb4 / utf8mb4_0900_ai_ci) if it does not exist,
  - creates DB_USER for localhost and 127.0.0.1 if it does not exist,
  - grants DB_USER access to DB_NAME and test_DB_NAME (Django's test database).

It never drops anything. An existing account whose password differs from .env
is reset only with --reset-password or after you confirm.

Finally it applies Django migrations, once the custom user model is configured.

Options:
  --check            Report status only; change nothing (exit 0 ready, 2 not ready).
  --reset-password   Reset an existing DB_USER's password to the .env value.
  --no-migrate       Skip migrations.
  --non-interactive  Never prompt (needs MYSQL_ROOT_PASSWORD to provision).
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import setup_dev as ui  # noqa: E402  (stdlib-only helpers shared with setup_dev.py)
from setup_dev import SetupError  # noqa: E402

ACCOUNT_HOSTS = ('localhost', '127.0.0.1')
DISPLAY_TIME_ZONE = 'Asia/Manila'  # must match TIME_ZONE in config/settings.py
TZ_TABLES = ('time_zone', 'time_zone_name', 'time_zone_transition',
             'time_zone_transition_type')
MIN_MYSQL = (8, 0, 11)  # Django 5.2 minimum
CHARSET, COLLATION = 'utf8mb4', 'utf8mb4_0900_ai_ci'


# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------

def relaunch_in_venv() -> None:
    """Re-run this script with the project's .venv Python if needed."""
    try:
        import dotenv  # noqa: F401
        import MySQLdb  # noqa: F401
        return
    except ImportError:
        pass
    venv_python = ui.venv_python()
    if not venv_python.exists():
        raise SetupError('The project virtual environment (.venv) was not found.',
                         'Run setup.ps1 (Windows) or ./setup.sh (macOS/Linux) first.')
    if Path(sys.executable).resolve() == venv_python.resolve():
        raise SetupError('mysqlclient or python-dotenv is missing from .venv.',
                         'Run setup.ps1 / setup.sh again to install dependencies.')
    result = subprocess.run([str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.exit(result.returncode)


def load_settings() -> dict[str, str]:
    from dotenv import dotenv_values

    if not ui.ENV_FILE.exists():
        raise SetupError('.env was not found.',
                         'Run setup.ps1 / setup.sh first; it creates .env from .env.example.')
    raw = dotenv_values(ui.ENV_FILE)
    cfg = {key: (raw.get(key) or '').strip()
           for key in ('DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_HOST', 'DB_PORT')}
    cfg['DB_HOST'] = cfg['DB_HOST'] or '127.0.0.1'
    cfg['DB_PORT'] = cfg['DB_PORT'] or '3306'

    for key in ('DB_NAME', 'DB_USER'):
        if not ui.IDENTIFIER.match(cfg[key]):
            raise SetupError(f'{key} in .env must be 1-64 letters, digits, or underscores.')
    if not cfg['DB_PASSWORD'] or ui.is_placeholder(cfg['DB_PASSWORD']):
        raise SetupError('DB_PASSWORD in .env is empty or a placeholder.',
                         'Set a password, or delete the DB_PASSWORD line and re-run '
                         'setup to generate one.')
    if not cfg['DB_PORT'].isdigit():
        raise SetupError(f'DB_PORT in .env is not a number: {cfg["DB_PORT"]!r}')
    return cfg


def require_server(cfg: dict[str, str]) -> None:
    host, port = cfg['DB_HOST'], cfg['DB_PORT']
    if ui.port_open(host, int(port)):
        ui.ok(f'MySQL server reachable at {host}:{port}')
        return
    detected = ui.detect_mysql_port(host, port)
    if detected != port:
        hint = f'A server is listening on port {detected}; set DB_PORT={detected} in .env and re-run.'
    else:
        hint = ('Install/start MySQL Server 8.x (Windows: Services > MySQL80; Linux: '
                'sudo systemctl start mysql; macOS: brew services start mysql), then '
                'check DB_HOST and DB_PORT in .env.')
    raise SetupError(f'No MySQL server reachable at {host}:{port}.', hint)


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------

def connect(cfg, user, password, database=None):
    import MySQLdb

    options = dict(host=cfg['DB_HOST'], port=int(cfg['DB_PORT']), user=user,
                   passwd=password, charset='utf8mb4', connect_timeout=5)
    if database:
        options['db'] = database
    return MySQLdb.connect(**options)


@dataclass
class Status:
    can_connect: bool = False
    error_code: int | None = None
    error: str = ''
    version: str = ''
    charset: str = ''
    collation: str = ''
    test_grant: bool = False
    timezones_loaded: bool = False

    @property
    def ready(self) -> bool:
        return self.can_connect and self.test_grant and self.timezones_loaded


def app_status(cfg) -> Status:
    import MySQLdb

    try:
        conn = connect(cfg, cfg['DB_USER'], cfg['DB_PASSWORD'], cfg['DB_NAME'])
    except MySQLdb.OperationalError as exc:
        code = exc.args[0] if exc.args else None
        message = exc.args[1] if len(exc.args) > 1 else str(exc)
        return Status(error_code=code, error=message)

    status = Status(can_connect=True)
    try:
        cur = conn.cursor()
        cur.execute('SELECT VERSION()')
        status.version = cur.fetchone()[0]
        cur.execute('SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME '
                    'FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s',
                    (cfg['DB_NAME'],))
        status.charset, status.collation = cur.fetchone()
        cur.execute('SHOW GRANTS FOR CURRENT_USER()')
        grants = [row[0].replace('\\', '') for row in cur.fetchall()]
        test_db = f'`test_{cfg["DB_NAME"]}`'
        status.test_grant = any(
            test_db in g or ('ON *.*' in g and 'ALL PRIVILEGES' in g) for g in grants)
        # Django converts UTC to the display zone in SQL for date filters,
        # truncation, and reports; MySQL returns NULL without zone tables.
        cur.execute("SELECT CONVERT_TZ('2026-01-01 00:00:00', 'UTC', %s)",
                    (DISPLAY_TIME_ZONE,))
        status.timezones_loaded = cur.fetchone()[0] is not None
    finally:
        conn.close()
    return status


def describe_problem(cfg, status: Status) -> str:
    db, user = cfg['DB_NAME'], cfg['DB_USER']
    if status.error_code == 1049:
        return f'Database {db} does not exist.'
    if status.error_code == 1045:
        return f'Account {user!r} does not exist or its password differs from .env.'
    if status.error_code == 1044:
        return f'Account {user!r} has no access to database {db}.'
    if status.can_connect and not status.test_grant:
        return f'Account {user!r} cannot create the test database test_{db}.'
    if status.can_connect and not status.timezones_loaded:
        return ('MySQL time zone tables are not loaded (needed for date filters '
                'and reports).')
    return f'Cannot connect as {user!r}: {status.error}'


def report_ready(cfg, status: Status) -> None:
    ui.ok(f'Database {cfg["DB_NAME"]} found; {cfg["DB_USER"]!r} can connect '
          f'(MySQL {status.version})')
    ui.ok(f'Test database access: test_{cfg["DB_NAME"]}')
    ui.ok(f'MySQL time zone support ({DISPLAY_TIME_ZONE})')
    if status.charset != CHARSET:
        ui.warn(f'Database character set is {status.charset}, expected {CHARSET}. '
                'Convert it before creating tables.')
    numbers = tuple(int(p) for p in status.version.split('-')[0].split('.')[:3])
    if numbers < MIN_MYSQL:
        raise SetupError(f'MySQL {status.version} is too old for Django 5.2 (needs 8.0.11+).')
    if numbers[:2] == (8, 0):
        ui.warn('MySQL 8.0 is end-of-life; the project targets MySQL 8.4 LTS.')


# --------------------------------------------------------------------------
# Provisioning
# --------------------------------------------------------------------------

def admin_connection(cfg, interactive: bool):
    import MySQLdb

    user = os.environ.get('MYSQL_ROOT_USER') or 'root'
    password = os.environ.get('MYSQL_ROOT_PASSWORD')
    if password is None:
        if not interactive:
            raise SetupError('A MySQL administrator password is needed to create the database.',
                             'Set MYSQL_ROOT_PASSWORD (and MYSQL_ROOT_USER if not root), '
                             'or run interactively.')
        password = getpass.getpass(f'        MySQL password for {user!r} (hidden): ')
    try:
        return connect(cfg, user, password)
    except MySQLdb.OperationalError as exc:
        if exc.args and exc.args[0] == 1045:
            raise SetupError(f'MySQL rejected the password for {user!r}.',
                             'Re-run and try again, or set MYSQL_ROOT_USER to another '
                             'administrator account.') from exc
        raise SetupError(f'Could not connect as {user!r}: {exc}') from exc


def provision(cfg, status: Status, args, interactive: bool) -> None:
    db, user, password = cfg['DB_NAME'], cfg['DB_USER'], cfg['DB_PASSWORD']
    conn = admin_connection(cfg, interactive)
    try:
        cur = conn.cursor()

        cur.execute('SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s', (db,))
        if cur.fetchone():
            ui.ok(f'Database {db} already exists (left unchanged)')
        else:
            cur.execute(f'CREATE DATABASE `{db}` CHARACTER SET {CHARSET} COLLATE {COLLATION}')
            ui.ok(f'Created database {db} ({CHARSET})')

        existing = []
        for host in ACCOUNT_HOSTS:
            cur.execute('SELECT 1 FROM mysql.user WHERE User = %s AND Host = %s', (user, host))
            if cur.fetchone():
                existing.append(host)
            else:
                cur.execute('CREATE USER %s@%s IDENTIFIED BY %s', (user, host, password))
                ui.ok(f"Created account '{user}'@'{host}'")

        if existing and status.error_code == 1045:
            accounts = ', '.join(f"'{user}'@'{h}'" for h in existing)
            ui.warn(f'{accounts} already exists with a password different from .env.')
            if not (args.reset_password or ui.ask_yes_no(
                    'Reset its password to the DB_PASSWORD value in .env?', interactive)):
                raise SetupError('Account password does not match .env.',
                                 'Put the account\'s real password in DB_PASSWORD, or '
                                 're-run with --reset-password.')
            for host in existing:
                cur.execute('ALTER USER %s@%s IDENTIFIED BY %s', (user, host, password))
            ui.ok('Account password reset to match .env')

        for host in ACCOUNT_HOSTS:
            for name in (db, f'test_{db}'):
                cur.execute(f'GRANT ALL PRIVILEGES ON `{name}`.* TO %s@%s', (user, host))
        ui.ok(f'Granted {user!r} access to {db} and test_{db}')
        conn.commit()

        if not status.timezones_loaded:
            load_timezones(cur)
            conn.commit()
    finally:
        conn.close()


def find_tzinfo_tool() -> str:
    tool = shutil.which('mysql_tzinfo_to_sql')
    if not tool and ui.IS_WINDOWS:
        found = sorted(Path(os.environ.get('ProgramFiles', r'C:\Program Files'))
                       .glob('MySQL/MySQL Server */bin/mysql_tzinfo_to_sql.exe'))
        tool = str(found[-1]) if found else None
    if not tool:
        raise SetupError('mysql_tzinfo_to_sql was not found; cannot load MySQL time zones.',
                         'It ships with MySQL Server (Linux: mysql-server package). See '
                         'https://dev.mysql.com/doc/refman/8.4/en/time-zone-support.html')
    return tool


def load_timezones(cur) -> None:
    """Fill MySQL's time zone tables from the tzdata package (same data on every
    platform). Zones are converted one file at a time because the directory
    mode of mysql_tzinfo_to_sql does not work on Windows."""
    import tzdata

    tool = find_tzinfo_tool()
    package = Path(tzdata.__file__).parent
    names = [n.strip() for n in (package / 'zones').read_text(encoding='utf-8').splitlines()
             if n.strip()]
    ui.info(f'Loading {len(names)} time zones into MySQL (one-time, may take a minute)')

    def convert(name):
        result = subprocess.run([tool, str(package / 'zoneinfo' / name), name],
                                capture_output=True, text=True)
        return result.stdout if result.returncode == 0 else ''

    with ThreadPoolExecutor(max_workers=8) as pool:
        chunks = list(pool.map(convert, names))
    statements = [s.strip() for chunk in chunks for s in chunk.split(';\n')
                  if s.strip() and s.strip().upper() not in ('START TRANSACTION', 'COMMIT')]
    if not any(c for c in chunks):
        raise SetupError('mysql_tzinfo_to_sql produced no time zone data.')

    cur.execute('USE mysql')
    for table in TZ_TABLES:
        cur.execute(f'TRUNCATE TABLE {table}')
    for statement in statements:
        cur.execute(statement.rstrip(';'))
    cur.execute('SELECT COUNT(*) FROM time_zone_name')
    ui.ok(f'Loaded {cur.fetchone()[0]} MySQL time zones')


# --------------------------------------------------------------------------
# Migrations
# --------------------------------------------------------------------------

def migrate(args) -> None:
    if args.no_migrate:
        ui.skip('Migrations (--no-migrate)')
        return
    result = ui.django_snippet('import django; django.setup(); '
                               'from django.conf import settings; print(settings.AUTH_USER_MODEL)')
    if result.returncode != 0:
        raise SetupError('Could not load Django settings:\n' + result.stderr.strip())
    if result.stdout.strip() == 'auth.User':
        ui.skip('Migrations: AUTH_USER_MODEL is not configured yet. Tables are created '
                'after the custom user model exists (Development Guide, Stage 2).')
        return
    ui.info('Applying migrations')
    if subprocess.run([sys.executable, 'manage.py', 'migrate', '--noinput'],
                      cwd=ROOT).returncode != 0:
        raise SetupError('Applying migrations failed.',
                         'Read the error above. Do not delete migrations or reset a '
                         'database that holds shared data.')
    ui.ok('Migrations applied')

    if subprocess.run([sys.executable, 'manage.py', 'seed_reference_data'],
                      cwd=ROOT).returncode != 0:
        raise SetupError('Seeding roles and reference data failed.')
    ui.ok('Roles and reference data seeded')


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(
        description='Find the MySQL database, creating it and its account when missing.')
    parser.add_argument('--check', action='store_true',
                        help='report status only; exit 0 if ready, 2 if not')
    parser.add_argument('--reset-password', action='store_true',
                        help="reset an existing DB_USER's password to the .env value")
    parser.add_argument('--no-migrate', action='store_true', help='skip migrations')
    parser.add_argument('--non-interactive', action='store_true', help='never prompt')
    parser.add_argument('--embedded', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    interactive = not args.non_interactive and sys.stdin.isatty()

    if not args.embedded:
        print(ui._paint('EVSU recruitment system: database setup', '1'))
    try:
        relaunch_in_venv()
        cfg = load_settings()
        require_server(cfg)

        status = app_status(cfg)
        if not status.ready:
            problem = describe_problem(cfg, status)
            if args.check:
                ui.warn(problem)
                print('\nDatabase is not ready. Run without --check to set it up.')
                return 2
            ui.info(problem + ' Setting it up now.')
            provision(cfg, status, args, interactive)
            status = app_status(cfg)
            if not status.ready:
                raise SetupError('Setup ran but the application account still cannot '
                                 'use the database: ' + describe_problem(cfg, status))
        report_ready(cfg, status)

        if not args.check:
            migrate(args)
    except SetupError as exc:
        print(f'\n  {ui._paint("FAIL", "31")}  {exc}')
        if exc.hint:
            print(f'        {exc.hint}')
        return 1
    except KeyboardInterrupt:
        print('\nCancelled. Nothing further was changed; re-run at any time.')
        return 130

    if not args.embedded:
        print(f'\n{ui._paint("Database ready.", "32;1")}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
