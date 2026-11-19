"""Gunicorn settings for the Linux server (see deploy/DEPLOY.md).

One copy runs per site (evsu-recruitment@public and evsu-recruitment@staff), each
on its own local Unix socket that only Nginx uses. Set GUNICORN_WORKERS per site
after measuring memory on the target server.
"""

import multiprocessing
import os

portal = os.environ['SITE_PORTAL']
bind = f'unix:/run/evsu-recruitment-{portal}/gunicorn.sock'
workers = int(os.environ.get('GUNICORN_WORKERS') or min(multiprocessing.cpu_count() + 1, 5))
timeout = 120          # roster PDF/Excel and report generation can take a while
graceful_timeout = 30
max_requests = 1000    # recycle workers to bound memory growth
max_requests_jitter = 100
accesslog = '-'
errorlog = '-'
forwarded_allow_ips = '127.0.0.1'
