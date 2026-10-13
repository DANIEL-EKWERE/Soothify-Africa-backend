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

`settings.py` is not in git, so it goes up by hand. Check the copy on the
server has **`DEBUG = False`** — it also turns on hashed static filenames,
which is what stops browsers holding a week-old stylesheet.

## 2. Install the packages

In cPanel → **Setup Python App** → your app → there is a command at the top
to enter the virtualenv. Run it, then:

```bash
pip install djangorestframework==3.15.2 \
            djangorestframework-simplejwt==5.3.1 \
            django-cors-headers==4.3.1 \
            django-filter==24.3 \
            pillow \
            cryptography
```

`cryptography` signs the ZEGOCLOUD call tokens. The other four are the API
itself. Celery, boto3 and django-storages are **not** needed here.

## 3. Migrate and seed

```bash
python manage.py migrate
python manage.py seed_content        # plans, categories, shelves
python manage.py show_webhook_urls   # what to paste where
```

`seed_content` is safe to run twice; it creates nothing that exists.

Then restart the app from the Setup Python App page.

## 4. The nightly cron

cPanel → **Cron Jobs** → add one entry, once a day:

```
30 3 * * *  /home/wdivczxg/virtualenv/SoothifyAfrica/3.9/bin/python /home/wdivczxg/SoothifyAfrica/manage.py run_due_jobs >> /home/wdivczxg/cron.log 2>&1
```

Check those two paths against your own Setup Python App page — the
virtualenv path in particular is specific to the app.

That one line covers everything the worker would do: releasing unpaid
booking holds, reconciling subscriptions against Bachs, session reminders,
and deleting recordings past their thirty days.

Run it by hand once to see it work:

```bash
python manage.py run_due_jobs
```

## 5. Point the outside world at it

`python manage.py show_webhook_urls` prints these, filled in:

- **Bachs** → `https://soothifyafrica.com.ng/api/v1/billing/webhook/`
- **ZEGOCLOUD** → `https://soothifyafrica.com.ng/api/v1/recordings/callback/`
- **Flutter** → base URL `https://soothifyafrica.com.ng/api/v1/`

Then, once `BACHS_SECRET_KEY` is in `settings.py`:

```bash
python manage.py setup_bachs_products
```

## 6. Check it

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
