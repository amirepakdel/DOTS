import multiprocessing
import os

bind = "0.0.0.0:8000"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000

# CRITICAL: Must be longer than the STT wait timeout (55s in views.py) + margin.
# If you have very long audio files, increase this further or switch to gevent.
timeout = 120
keepalive = 2
preload_app = True

errorlog = "-"
accesslog = "-"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'
capture_output = True
enable_stdio_inheritance = True

limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# Optional: use gevent for WebSocket/long-polling support without blocking workers.
# Requires: pip install gevent
# worker_class = "gevent"
# worker_connections = 1000