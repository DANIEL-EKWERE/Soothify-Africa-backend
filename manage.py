#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    # settings.py is the live server's configuration: DEBUG off, which turns on
    # the https redirect and fingerprinted static files. The development server
    # speaks plain HTTP and has no collectstatic manifest, so it cannot serve
    # the site with those; "runserver" therefore defaults to settings_dev.
    # Production runs through wsgi.py, which always uses settings.py.
    default_settings = 'soothifyAfrica.settings'
    if 'runserver' in sys.argv and not any(a.startswith('--settings') for a in sys.argv):
        default_settings = 'soothifyAfrica.settings_dev'
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', default_settings)
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
