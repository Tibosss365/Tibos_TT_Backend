# Gunicorn config for Azure App Service (production).
#
# To use it:
#   1. Azure Portal -> App Service `tibos-tt-api` -> Settings ->
#      Configuration -> General settings:
#        - Startup Command:  gunicorn main:app -c gunicorn.conf.py
#        - Always On:        On   <-- CRITICAL for a polling/scheduler app
#   2. Save + Restart.
#
# Why this matters: the background schedulers (report digests, SLA-breach
# detector, escalation, email poller), the real-time SSE relay, and the
# startup auto-migration all run inside the ASGI *lifespan*. The lifespan only
# runs under an ASGI worker (UvicornWorker) AND stays alive only when
# "Always On" is enabled — otherwise Azure unloads the app when idle and all of
# the above stop.
import os

# Azure injects the port via $PORT; fall back to 8000 for local runs.
bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

# ASGI worker so FastAPI's lifespan (startup/shutdown) actually runs.
worker_class = "uvicorn.workers.UvicornWorker"

# IMPORTANT: exactly one worker. Each worker runs its own copy of the
# schedulers, so 2+ workers would send duplicate report emails / SLA alerts
# and poll the mailbox multiple times. The app is designed as a single process.
workers = 1

timeout = 600            # long timeout for slow cold starts / large operations
graceful_timeout = 120
keepalive = 5
