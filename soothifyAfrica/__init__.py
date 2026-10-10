"""Soothify Africa: the landing site and the mobile API in one project."""

# Celery, when this deployment has a worker. Wrapped because cPanel has
# neither Celery installed nor a broker to talk to, and importing it there
# would break every management command. api/jobs.py falls back to running
# background work inline when this import does not happen.
try:
    from .celery import app as celery_app
except Exception:  # pragma: no cover - depends on the deployment, not the code
    celery_app = None

__all__ = ('celery_app',)
