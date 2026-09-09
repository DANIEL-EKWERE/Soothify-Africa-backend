#!/usr/bin/env bash
# Render build step. Runs on every deploy, before the service starts.
#
# Kept POSIX-compatible on purpose: Render may invoke this as "sh build.sh",
# and /bin/sh is dash there, which has no "set -o pipefail". There are no
# pipelines here, so errexit + nounset is all the safety this needs.
set -o errexit    # abort the deploy on the first failing command
set -o nounset    # treat an unset variable as an error

pip install --upgrade pip
pip install -r requirements.txt

# Collect into STATIC_ROOT, where WhiteNoise serves from. Also produces the
# hashed manifest, so a missing file fails the build here rather than at runtime.
python manage.py collectstatic --no-input

# Idempotent: a no-op when there is nothing new to apply.
python manage.py migrate

# NOTE: compilemessages is deliberately NOT run here. Render's image has no
# gettext, and locale/pcm/LC_MESSAGES/django.mo is committed already. If you
# edit the .po, recompile locally and commit the .mo alongside it.
