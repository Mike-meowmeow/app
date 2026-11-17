"""Gunicorn settings for the Linux server (see deploy/DEPLOY.md).

Workers: start with 2 x CPU cores + 1 and measure memory on the target server
before raising it. Gunicorn listens on a local Unix socket that only Nginx uses.
"""

import multiprocessing

bind = 'unix:/run/evsu-recruitment/gunicorn.sock'
workers = min(multiprocessing.cpu_count() * 2 + 1, 9)
timeout = 120          # roster PDF/Excel and report generation can take a while
graceful_timeout = 30
max_requests = 1000    # recycle workers to bound memory growth
max_requests_jitter = 100
accesslog = '-'
errorlog = '-'
forwarded_allow_ips = '127.0.0.1'
