# Running the API on cPanel, beside the landing site

No new hosting, no new domain. `/api/v1/` sits next to the landing pages on
`soothifyafrica.com.ng`, outside the language prefix, served by the Python
app that already runs the site.

What you give up by not being on AWS: background work happens inside the
request or on a nightly cron instead of in a worker, and transcription is
off. Recording still works, because ZEGOCLOUD records in their cloud and
uploads straight to your bucket — cPanel never handles the audio.

Move to AWS (see `README.md` in this folder) when transcription earns its
keep or a nightly cron is too slow. One line of settings changes.

## 1. Upload

Everything that is new since the site last went up:

```
accounts/   api/   billing/   content/   corporate/   experts/
notifications/   recordings/   spaces/   wellbeing/   community/
soothifyAfrica/settings.py
soothifyAfrica/urls.py
soothifyAfrica/__init__.py
soothifyAfrica/celery.py          (harmless here; imported defensively)
requirements.txt
```

`settings.py` is not in git, so it goes up by hand, and the copy already on
the server is the old site-only one — one installed app, no API. **Replace
it**, do not merge into it; the passwords and the secret key in the new copy
are the same ones.

Debug mode is no longer a line to remember: `DEBUG` reads `DJANGO_DEBUG`
from the environment, which cPanel does not set, so the server is always in
production mode (hashed static filenames and all, which is what stops
browsers holding a week-old stylesheet). Locally, `DJANGO_DEBUG=1 python
manage.py runserver`.

## 2. Install the packages

In cPanel → **Setup Python App** → your app → there is a command at the top
to enter the virtualenv. Run it, then:

```bash
pip install djangorestframework==3.15.2 \
            djangorestframework-simplejwt==5.3.1 \
            django-cors-headers==4.3.1 \
            django-filter==24.3 \
            psycopg2-binary==2.9.10 \
            pillow \
            cryptography
```

`psycopg2-binary` is the PostgreSQL driver (see step 3). `cryptography`
signs the ZEGOCLOUD call tokens. The other four are the API itself. Celery,
boto3 and django-storages are **not** needed here.

## 3. The database

The site used to run on SQLite. The new settings point cPanel at the
PostgreSQL database instead — `wdivczxg_soothifyAfrica-db`, through
`wdivczxg_soothifyAfrica-user` on localhost. SQLite lets one writer in at a
time, and the API now has a cron writing charges and reminders while people
are mid-request; that is where "database is locked" comes from.

Whatever is in the old `db.sqlite3` — the waitlist — goes across before you
migrate, or it is left behind:

```bash
# with the OLD settings.py still in place, or DJANGO_DEBUG=1
python manage.py dumpdata home --indent 2 > waitlist.json
```

Then, after the new `settings.py` is up and `migrate` has run:

```bash
python manage.py loaddata waitlist.json
```

Check in cPanel → **PostgreSQL Databases** that the user is actually granted
on the database. A fresh cPanel database has no privileges until you add the
user to it, and the error you get instead is a bare authentication failure.

## 4. Migrate and seed

```bash
python manage.py migrate
python manage.py seed_content        # plans, categories, shelves
python manage.py show_webhook_urls   # what to paste where
```

`seed_content` is safe to run twice; it creates nothing that exists.

Then restart the app from the Setup Python App page.

## 5. Push notifications (optional)

The app registers its FCM token on every launch and the server files it;
what sends the push is `notifications/push.py`, and it needs a key.

Firebase console → **Project settings → Service accounts → Generate new
private key**. Upload the JSON beside `settings.py` as
`firebase-service-account.json`, then:

```bash
chmod 600 firebase-service-account.json
pip install firebase-admin==6.6.0
```

Skip all of it and nothing breaks: every notification is still written to
the in-app feed and shows the next time the app is opened. The only thing
missing is the line the phone draws when the app is shut.

**The key committed in `4f48680` must be rotated** before any of this —
generate a new one and delete the old from the console's service-account
page. It is in the repository's history, where deleting the file does not
reach it.

## 6. Error pages

400, 403, 404, 429 and 500 are Django's to serve, and it does, with no setup
— the handlers are wired in `soothifyAfrica/urls.py`. A request under
`/api/` gets JSON instead of a page, so the app never has to parse HTML to
find out what went wrong.

502, 503, 504 and 508 are different: by the time a visitor sees one, the
Python app is not answering, so Django cannot apologise for itself.
LiteSpeed has to serve those from disk. After `collectstatic`:

```bash
python manage.py make_error_pages
```

That writes `500.html`, `502.html`, `503.html`, `504.html` and `508.html`
into an `errors/` folder beside the collected static files — inside the
document root, so the web server can reach them. Then, in the document
root's `.htaccess`:

```apache
ErrorDocument 500 /static/errors/500.html
ErrorDocument 502 /static/errors/502.html
ErrorDocument 503 /static/errors/503.html
ErrorDocument 504 /static/errors/504.html
ErrorDocument 508 /static/errors/508.html
```

508 is the one worth having: on cPanel it means the account hit its
resource limit, which is the failure a growing site meets first.

Re-run `make_error_pages` whenever you re-run `collectstatic` — the pages
link to the stylesheet by its hashed name, and the old name stops existing.

## 7. Keep the private uploads private

Practising certificates and government IDs are stored under
`media/private/`. Nothing serves that folder: files are read through signed,
short-lived links that Django checks (`api/media.py`).

If you ever add an alias so cPanel serves `media/` straight from disk, add a
rule refusing `media/private/` in the same breath. A certificate reachable by
URL is the one mistake on this host that cannot be taken back.

## 8. The nightly cron

cPanel → **Cron Jobs** → add one entry, once a day:

```
30 3 * * *  /home/wdivczxg/virtualenv/SoothifyAfrica/3.9/bin/python /home/wdivczxg/SoothifyAfrica/manage.py run_due_jobs >> /home/wdivczxg/cron.log 2>&1
```

Check those two paths against your own Setup Python App page — the
virtualenv path in particular is specific to the app.

That one line covers everything the worker would do: releasing unpaid
booking holds, reconciling subscriptions against Bachs, session reminders,
and deleting recordings past their thirty days.

It also carries the whole renewal cycle while memberships are sold one
period at a time (`MEMBERSHIP_BILLING=one_off`). Bachs run no subscription
here, so this cron is what charges the saved cards when a period ends,
warns whoever has to pay by hand, and ends what nobody paid for.
**Without it no membership ever renews and none ever lapses** — the library
stays open to anyone who stopped paying, and nobody who meant to carry on
is charged.

Run it by hand once to see it work:

```bash
python manage.py run_due_jobs
```

## 9. Point the outside world at it

`python manage.py show_webhook_urls` prints these, filled in:

- **Bachs** → `https://soothifyafrica.com.ng/api/v1/billing/webhook/`
- **ZEGOCLOUD** → `https://soothifyafrica.com.ng/api/v1/recordings/callback/`
- **Flutter** → base URL `https://soothifyafrica.com.ng/api/v1/`

Then, once `BACHS_SECRET_KEY` is in `settings.py`:

```bash
python manage.py setup_bachs_products
```

## 10. Check it

```bash
curl https://soothifyafrica.com.ng/api/v1/health/
# {"status":"ok","deployment":"cpanel"}

curl https://soothifyafrica.com.ng/api/v1/billing/plans/
curl https://soothifyafrica.com.ng/api/v1/content/categories/
```

The site itself should be untouched: the API lives under `/api/`, and the
language middleware leaves that prefix alone.

## What runs where

| | cPanel (now) | AWS (later) |
|---|---|---|
| Database | SQLite | PostgreSQL |
| Media and recordings | Disk, signed per request | S3/R2, signed by the bucket |
| Background work | Inline, plus the nightly cron | Celery worker and beat |
| Transcription | Off | AWS Transcribe |
| Switch | `DEPLOY_TARGET=cpanel` (the default) | `DEPLOY_TARGET=aws` |

## Things that bite

**SQLite and concurrency.** It locks the whole file on a write. Fine for a
waitlist and early users; it is the first thing that will hurt as sessions
and playback events pile up. Watch for "database is locked" in the logs —
that is the signal to move.

**Recordings need a bucket.** ZEGOCLOUD uploads to S3 or R2, not to cPanel.
Until `AWS_STORAGE_BUCKET_NAME` and the keys are set, recording is off and
calls still work — `start_recording` logs and the session connects anyway.

**Static files.** After uploading, run `collectstatic` as before. With
`DEBUG = False` a missing file is a build-time error rather than a silent
404 at runtime.
