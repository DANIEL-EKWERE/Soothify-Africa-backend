"""Work that should not happen inside a request -- sending mail, thumbnailing
an upload, calling Paystack back.

The same call has to work on both deployments:

    cPanel   no Redis and no worker process, so the function runs inline and
             the request waits for it. Failures are logged, never raised: a
             signup must not be lost because mail was slow.
    AWS      a Celery worker picks it up and the request returns immediately.

Callers write `jobs.run(send_welcome, signup.id)` and never learn which of the
two happened. Celery is imported lazily and defensively, so cPanel does not
need it installed at all.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_celery_app = None
_celery_checked = False


def _celery():
    """The Celery app, or None when there is no worker to hand work to."""
    global _celery_app, _celery_checked
    if _celery_checked:
        return _celery_app
    _celery_checked = True
    if not getattr(settings, 'CELERY_BROKER_URL', ''):
        return None
    try:
        from soothifyAfrica.celery import app  # noqa: WPS433 (deliberately lazy)
    except Exception:
        # Celery is not installed (cPanel) or is misconfigured. Inline is a
        # correct answer here, just a slower one.
        logger.warning('celery unavailable; background work will run inline')
        return None
    _celery_app = app
    return _celery_app


def run(func, *args, **kwargs):
    """Do `func(*args, **kwargs)` now or soon, whichever this machine can.

    Returns True when the work was handed to a worker, False when it ran here.
    """
    app = _celery()
    if app is not None:
        task = getattr(func, 'delay', None)
        if task is not None:
            task(*args, **kwargs)
            return True

    try:
        func(*args, **kwargs)
    except Exception:
        # Whatever this was, it is not worth failing the caller's request for.
        logger.exception('background job %s failed', getattr(func, '__name__', func))
    return False


def task(func):
    """Mark a function as background work.

    On AWS it becomes a Celery task (so `jobs.run` can queue it); elsewhere it
    is returned untouched and stays an ordinary function.
    """
    app = _celery()
    if app is None:
        return func
    return app.task(name='{0}.{1}'.format(func.__module__, func.__name__))(func)
