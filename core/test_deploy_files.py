"""Consistency of the Cloudflare Tunnel deployment files (deploy/tunnel/, TUNNEL.md).

Nginx cannot run in the test suite, so these tests check that the pieces
agree: host names, socket paths, the proxy headers Django relies on, and the
client address settings.
"""

import os
import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from audit.context import client_ip

DEPLOY = Path(settings.BASE_DIR) / 'deploy'
TUNNEL = DEPLOY / 'tunnel'


def env_file(path):
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.startswith('#') and '=' in line:
            key, _, value = line.partition('=')
            values[key.strip()] = value.strip()
    return values


class TunnelFilesTests(SimpleTestCase):
    def setUp(self):
        self.nginx = (TUNNEL / 'nginx-tunnel.conf').read_text(encoding='utf-8')
        self.snippet = (TUNNEL / 'nginx-tunnel-proxy.conf').read_text(encoding='utf-8')
        self.unit = (TUNNEL / 'systemd' / 'evsu-prod@.service').read_text(encoding='utf-8')

    def test_each_site_host_is_served_by_nginx_and_trusted_by_django(self):
        for site in ('public', 'staff'):
            values = env_file(TUNNEL / f'{site}.env')
            host = values['DJANGO_ALLOWED_HOSTS']
            self.assertIn(f'server_name {host};', self.nginx)
            self.assertEqual(values['CSRF_TRUSTED_ORIGINS'], f'https://{host}')

    def test_nginx_reaches_the_sockets_the_units_create(self):
        bind = re.search(r'GUNICORN_BIND=unix:(\S+)', self.unit).group(1)
        for site in ('public', 'staff'):
            self.assertIn(f'server unix:{bind.replace("%i", site)} ', self.nginx)
        self.assertIn('RuntimeDirectory=evsu-prod-%i', self.unit)
        self.assertTrue(bind.startswith('/run/evsu-prod-%i/'))

    def test_nginx_is_local_only_and_uses_the_cloudflare_address(self):
        listens = re.findall(r'^\s*listen\s+([^;]+);', self.nginx, re.M)
        self.assertTrue(listens)
        self.assertTrue(all(value.split()[0] == '127.0.0.1:8080' for value in listens), listens)
        self.assertIn('real_ip_header CF-Connecting-IP;', self.nginx)
        self.assertIn('set_real_ip_from 127.0.0.1;', self.nginx)
        self.assertIn('proxy_set_header X-Forwarded-Proto https;', self.snippet)
        self.assertIn('proxy_set_header X-Forwarded-For $remote_addr;', self.snippet)
        self.assertIn('/etc/nginx/snippets/evsu-tunnel-proxy.conf', self.nginx)

    def test_production_example_trusts_the_unix_socket(self):
        values = env_file(Path(settings.BASE_DIR) / '.env.production.example')
        self.assertIn('unix', values['TRUSTED_PROXIES'].split(','))
        self.assertIn('TRUSTED_PROXIES=127.0.0.1,unix', (DEPLOY / 'TUNNEL.md').read_text(
            encoding='utf-8'))

    def test_gunicorn_uses_the_bind_from_the_unit(self):
        source = (DEPLOY / 'gunicorn.conf.py').read_text(encoding='utf-8')
        for extra, expected in (({}, 'unix:/run/evsu-recruitment-staff/gunicorn.sock'),
                                ({'GUNICORN_BIND': 'unix:/run/evsu-prod-staff/gunicorn.sock'},
                                 'unix:/run/evsu-prod-staff/gunicorn.sock')):
            with mock.patch.dict(os.environ, {'SITE_PORTAL': 'staff', **extra}):
                if 'GUNICORN_BIND' not in extra:
                    os.environ.pop('GUNICORN_BIND', None)
                namespace = {}
                exec(compile(source, 'gunicorn.conf.py', 'exec'), namespace)
            self.assertEqual(namespace['bind'], expected)

    @override_settings(TRUSTED_PROXIES=['127.0.0.1', 'unix'])
    def test_a_request_through_the_tunnel_records_the_visitor(self):
        from django.test import RequestFactory
        # Gunicorn on the socket: no REMOTE_ADDR; Nginx sends the CF-Connecting-IP value.
        request = RequestFactory().get('/', REMOTE_ADDR='', HTTP_X_FORWARDED_FOR='198.51.100.23',
                                       HTTP_X_FORWARDED_PROTO='https')
        self.assertEqual(client_ip(request), '198.51.100.23')
