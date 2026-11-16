#!/usr/bin/env python3
"""Development environment setup for the EVSU recruitment system.

Run after cloning (safe to run any number of times):

    Windows:      .\\setup.ps1          or   py -3.13 setup_dev.py
    macOS/Linux:  ./setup.sh            or   python3 setup_dev.py

What it does, skipping anything already done:
  1. Verifies Python, Git, and MySQL tooling.
  2. Creates .venv and installs requirements.txt.
  3. Creates .env from .env.example (generated secrets, detected MySQL port),
     or adds any keys missing from an existing .env. Existing values are never
     overwritten.
  4. Creates private storage directories.
  5. Runs Django system checks.
  6. Runs setup_db.py: finds the MySQL database, creates it and its account
     when missing (asks for the MySQL root password once), and applies
     migrations once the custom user model is configured.

Options:
  --skip-db          Skip all database steps.
  --recreate-venv    Delete and rebuild .venv.
  --non-interactive  Never prompt (for CI/scripts).

Only the Python standard library is used here, because this runs before the
virtual environment exists.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import socket
import string
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / '.venv'
ENV_FILE = ROOT / '.env'
ENV_EXAMPLE = ROOT / '.env.example'
REQUIREMENTS = ROOT / 'requirements.txt'
PRIVATE_DIRS = ('private/documents', 'private/exports', 'logs')

SUPPORTED_PY = ((3, 12), (3, 13))   # Tested range
ALLOWED_PY = ((3, 12), (3, 14))     # Django 5.2 range; 3.14 warns as untested
MYSQL_PORT_CANDIDATES = (3306, 3307, 3308)
IS_WINDOWS = os.name == 'nt'
IDENTIFIER = re.compile(r'^[A-Za-z0-9_]{1,64}$')


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

def _supports_color() -> bool:
    if os.environ.get('NO_COLOR') or not sys.stdout.isatty():
        return False
    if IS_WINDOWS:
        os.system('')  # Enables ANSI escape processing in Windows 10+ consoles.
    return True


COLOR = _supports_color()


def _paint(text: str, code: str) -> str:
    return f'\033[{code}m{text}\033[0m' if COLOR else text


def ok(msg: str) -> None:
    print(f'  {_paint("OK", "32")}    {msg}')


def info(msg: str) -> None:
    print(f'  {_paint("..", "36")}    {msg}')


def warn(msg: str) -> None:
    print(f'  {_paint("WARN", "33")}  {msg}')


def skip(msg: str) -> None:
    print(f'  {_paint("SKIP", "90")}  {msg}')


class SetupError(Exception):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


# --------------------------------------------------------------------------
# Process helpers
# --------------------------------------------------------------------------

def venv_python() -> Path:
    return VENV_DIR / ('Scripts/python.exe' if IS_WINDOWS else 'bin/python')


def run(cmd, *, capture=False, env=None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [str(part) for part in cmd], cwd=ROOT, env=env, text=True,
            capture_output=capture,
        )
    except FileNotFoundError as exc:
        raise SetupError(f'Command not found: {cmd[0]}') from exc


def run_or_fail(cmd, message: str, hint: str | None = None) -> None:
    if run(cmd).returncode != 0:
        raise SetupError(message, hint)


def django_snippet(code: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    env.update(extra_env or {})
    return run([venv_python(), '-c', code], capture=True, env=env)


def ask_yes_no(question: str, interactive: bool) -> bool:
    if not interactive:
        return False
    return input(f'        {question} [y/N] ').strip().lower() in {'y', 'yes'}


# --------------------------------------------------------------------------
# .env handling
# --------------------------------------------------------------------------

def parse_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def is_placeholder(value: str) -> bool:
    return value.startswith('<') and value.endswith('>')


def port_open(host: str, port: int, timeout: float = 0.75) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def detect_mysql_port(host: str, default: str) -> str:
    candidates = [int(default)] if default.isdigit() else []
    candidates += [p for p in MYSQL_PORT_CANDIDATES if p not in candidates]
    for port in candidates:
        if port_open(host, port):
            return str(port)
    return default


def generated_value(key: str, example_value: str, current: dict[str, str]) -> str:
    """Value for a key being newly written to .env."""
    if key == 'DJANGO_SECRET_KEY':
        return secrets.token_urlsafe(50)
    if key == 'DB_PASSWORD':
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(24))
    if key == 'DB_PORT':
        return detect_mysql_port(current.get('DB_HOST', '127.0.0.1'), example_value)
    return example_value


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------

def step_tools(args) -> None:
    version = sys.version_info[:2]
    label = f'{version[0]}.{version[1]}'
    if not (ALLOWED_PY[0] <= version <= ALLOWED_PY[1]):
        raise SetupError(
            f'Python {label} is not supported (need 3.12 or 3.13).',
            'Install Python 3.13 from https://www.python.org/downloads/ and run '
            '"py -3.13 setup_dev.py" (Windows) or "python3.13 setup_dev.py".',
        )
    if version > SUPPORTED_PY[1]:
        warn(f'Python {label} works with Django 5.2 but this project is tested on 3.12-3.13.')
    else:
        ok(f'Python {sys.version.split()[0]} ({sys.executable})')

    git = shutil.which('git')
    if git:
        ok(f'Git: {run([git, "--version"], capture=True).stdout.strip()}')
    else:
        warn('Git not found on PATH. Install it from https://git-scm.com/downloads.')

    mysql = shutil.which('mysql')
    if not mysql and IS_WINDOWS:
        found = sorted(Path(os.environ.get('ProgramFiles', r'C:\Program Files'))
                       .glob('MySQL/MySQL Server */bin/mysql.exe'))
        mysql = str(found[-1]) if found else None
    if mysql:
        ok(f'MySQL client: {mysql}')
    else:
        info('MySQL command-line client not found (optional; the server is checked later).')

    if not IS_WINDOWS and not venv_python().exists():
        missing = [tool for tool in ('pkg-config',) if not shutil.which(tool)]
        if missing:
            warn('mysqlclient compiles from source here and needs pkg-config and the '
                 'MySQL client headers (Debian/Ubuntu: sudo apt install pkg-config '
                 'python3-dev default-libmysqlclient-dev build-essential; macOS: '
                 'brew install pkg-config mysql-client).')


def step_venv(args) -> None:
    if args.recreate_venv and VENV_DIR.exists():
        info('Removing existing .venv (--recreate-venv)')
        shutil.rmtree(VENV_DIR)

    if VENV_DIR.exists():
        probe = run([venv_python(), '-c',
                     'import sys; print("%d.%d" % sys.version_info[:2])'], capture=True) \
            if venv_python().exists() else None
        if probe is None or probe.returncode != 0:
            raise SetupError('The existing .venv is broken or incomplete.',
                             'Re-run with --recreate-venv to rebuild it.')
        major, minor = (int(p) for p in probe.stdout.strip().split('.'))
        if not (ALLOWED_PY[0] <= (major, minor) <= ALLOWED_PY[1]):
            raise SetupError(f'.venv uses unsupported Python {major}.{minor}.',
                             'Re-run with --recreate-venv using Python 3.12 or 3.13.')
        ok(f'.venv already exists (Python {major}.{minor})')
        return

    info(f'Creating .venv with Python {sys.version.split()[0]}')
    try:
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)
    except Exception as exc:  # ensurepip missing on some Linux distributions
        shutil.rmtree(VENV_DIR, ignore_errors=True)
        raise SetupError(f'Could not create the virtual environment: {exc}',
                         'On Debian/Ubuntu install the venv package, e.g. '
                         'sudo apt install python3.12-venv') from exc
    run_or_fail([venv_python(), '-m', 'pip', 'install', '--upgrade', '--quiet', 'pip'],
                'Could not upgrade pip in .venv.', 'Check your internet connection.')
    ok('.venv created')


def step_dependencies(args) -> None:
    if not REQUIREMENTS.exists():
        raise SetupError('requirements.txt is missing.')
    hint = ('Check your internet connection. ' + (
        '' if IS_WINDOWS else
        'If mysqlclient failed to build, install pkg-config and the MySQL client '
        'development headers (see the warning above), then re-run.'))
    info('Installing requirements.txt (already-installed packages are skipped)')
    run_or_fail([venv_python(), '-m', 'pip', 'install', '--disable-pip-version-check',
                 '--quiet', '-r', REQUIREMENTS],
                'Dependency installation failed.', hint)
    result = run([venv_python(), '-m', 'pip', 'check'], capture=True)
    if result.returncode != 0:
        raise SetupError('Installed packages have conflicts:\n' + result.stdout.strip(),
                         'Fix requirements.txt, then re-run with --recreate-venv.')
    ok('Dependencies installed and consistent')


def step_env_file(args) -> None:
    if not ENV_EXAMPLE.exists():
        raise SetupError('.env.example is missing; cannot create .env.')
    example = parse_env(ENV_EXAMPLE)

    if not ENV_FILE.exists():
        lines, written = [], {}
        for raw in ENV_EXAMPLE.read_text(encoding='utf-8').splitlines():
            stripped = raw.strip()
            if stripped and not stripped.startswith('#') and '=' in stripped:
                key = stripped.split('=', 1)[0].strip()
                written[key] = generated_value(key, example[key], written)
                lines.append(f'{key}={written[key]}')
            elif stripped.startswith('# Copy to .env'):
                lines.append('# Local development settings. Private: never commit this file.')
            else:
                lines.append(raw)
        ENV_FILE.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        ok(f'Created .env (generated secret key and DB password; DB_PORT={written.get("DB_PORT")})')
    else:
        current = parse_env(ENV_FILE)
        missing = [key for key in example if key not in current]
        if missing:
            additions = []
            for key in missing:
                current[key] = generated_value(key, example[key], current)
                additions.append(f'{key}={current[key]}')
            text = ENV_FILE.read_text(encoding='utf-8')
            if text and not text.endswith('\n'):
                text += '\n'
            ENV_FILE.write_text(text + '\n'.join(additions) + '\n', encoding='utf-8')
            ok(f'.env exists; added missing keys: {", ".join(missing)}')
        else:
            ok('.env already exists with all keys (left unchanged)')

    unresolved = [k for k, v in parse_env(ENV_FILE).items() if is_placeholder(v)]
    if unresolved:
        warn(f'Replace placeholder values in .env: {", ".join(unresolved)}')


def step_directories(args) -> None:
    created = []
    for rel in PRIVATE_DIRS:
        path = ROOT / rel
        if not path.exists():
            path.mkdir(parents=True)
            created.append(rel)
    ok(f'Created {", ".join(created)}' if created else 'Private storage directories exist')


def step_django(args) -> None:
    run_or_fail([venv_python(), 'manage.py', 'check'], 'Django system check failed.')
    ok('Django system check passed')


def step_database(args) -> None:
    if args.skip_db:
        skip('Database steps (--skip-db)')
        return
    cmd = [venv_python(), ROOT / 'setup_db.py', '--embedded']
    if args.non_interactive:
        cmd.append('--non-interactive')
    if run(cmd).returncode != 0:
        raise SetupError('Database setup did not complete.',
                         'Fix the issue reported above, then re-run this script '
                         '(or just setup_db.py).')


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> int:
    # Keep our messages in order with subprocess output, even when piped.
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description='Set up the local development environment.')
    parser.add_argument('--skip-db', action='store_true', help='skip database steps')
    parser.add_argument('--recreate-venv', action='store_true', help='rebuild .venv')
    parser.add_argument('--non-interactive', action='store_true', help='never prompt')
    args = parser.parse_args()

    steps = [
        ('Checking required tools', lambda: step_tools(args)),
        ('Preparing virtual environment', lambda: step_venv(args)),
        ('Installing dependencies', lambda: step_dependencies(args)),
        ('Configuring .env', lambda: step_env_file(args)),
        ('Creating private directories', lambda: step_directories(args)),
        ('Running Django checks', lambda: step_django(args)),
        ('Setting up database', lambda: step_database(args)),
    ]

    print(_paint('EVSU recruitment system: development setup', '1'))
    try:
        for number, (title, action) in enumerate(steps, 1):
            print(f'\n[{number}/{len(steps)}] {title}')
            action()
    except SetupError as exc:
        print(f'\n  {_paint("FAIL", "31")}  {exc}')
        if exc.hint:
            print(f'        {exc.hint}')
        print('\nSetup stopped. Fix the issue above and re-run; completed steps are kept.')
        return 1
    except KeyboardInterrupt:
        print('\nSetup cancelled. Re-run at any time to continue.')
        return 130

    py = r'.venv\Scripts\python.exe' if IS_WINDOWS else '.venv/bin/python'
    print(f'\n{_paint("Setup complete.", "32;1")} Start the development server with:')
    print(f'    {py} manage.py runserver')
    return 0


if __name__ == '__main__':
    sys.exit(main())
