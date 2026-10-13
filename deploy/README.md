# Running the API on AWS, with the site still on Truehost

The landing site stays where it is. Only `api.soothifyafrica.com.ng` moves,
and DNS is what sends it there — the two have nothing else to do with each
other, and nothing about the cPanel site changes.

## 1. Point the subdomain at AWS

Your nameservers are Truehost's (`ns1.cloudoon.com` and friends), so the
record is made in cPanel.

In **cPanel → Zone Editor → Manage**, add one record:

| Type | Name | Points to | TTL |
|---|---|---|---|
| `A` | `api` | the Elastic IP of the AWS box | 300 |

If you put the API behind a load balancer instead of a single box, it is a
`CNAME` to the balancer's hostname rather than an `A` to an address.

**Do not use cPanel's "Subdomains" page.** That creates a folder on the
cPanel server and an A record pointing at it, which is the opposite of what
you want. If you already made one, delete it, then add the record above.

A short TTL (300) while you set this up means a mistake costs five minutes
rather than a day. Raise it later.

Nothing about mail changes: MX records belong to the parent domain, so
support@ and noreply@ keep working from cPanel exactly as now.

Check it with `dig +short api.soothifyafrica.com.ng` — it should answer with
the AWS address, while the bare domain still answers with Truehost's.

## 2. The box

One instance is enough to start: the API, a worker, beat, and Redis on the
same machine. A `t3.small` (2 GB) in `eu-west-1` is the usual choice.
`af-south-1` (Cape Town) is physically closer to Nigeria and noticeably
faster from Lagos, but costs more and does not carry every service — check
AWS Transcribe is available there before choosing it, since the recording
pipeline needs it.

Give the instance an **Elastic IP**, or the address changes when it reboots
and the DNS record goes stale.

Security group: 80 and 443 from anywhere, 22 from your own address only.

## 3. Install

```bash
sudo adduser --system --group --home /srv/soothify soothify
sudo apt update && sudo apt install -y python3-venv nginx redis-server postgresql-client git

sudo -u soothify git clone <your repo> /srv/soothify
cd /srv/soothify
sudo -u soothify python3 -m venv venv
sudo -u soothify venv/bin/pip install -r requirements.txt
sudo -u soothify venv/bin/pip install gunicorn celery redis 'django-storages[s3]' boto3
```

`settings.py` is not in the repo (it holds the Zego secret), so copy your
local one up by hand — and make sure the copy on the server has
`DEBUG = False`.

## 4. Configure

```bash
sudo mkdir -p /etc/soothify
sudo cp deploy/env.example /etc/soothify/env
sudo nano /etc/soothify/env          # fill in every blank
sudo chmod 600 /etc/soothify/env     # it holds every secret
```

Then the services and the web server:

```bash
sudo cp deploy/*.service /etc/systemd/system/
sudo cp deploy/nginx-api.conf /etc/nginx/sites-available/soothify-api
sudo ln -s /etc/nginx/sites-available/soothify-api /etc/nginx/sites-enabled/
sudo systemctl daemon-reload
sudo systemctl enable --now soothify-api soothify-worker soothify-beat
sudo nginx -t && sudo systemctl reload nginx
```

## 5. Database, static files, certificate

```bash
cd /srv/soothify
sudo -u soothify venv/bin/python manage.py migrate
sudo -u soothify venv/bin/python manage.py collectstatic --no-input
sudo -u soothify venv/bin/python manage.py createsuperuser

sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.soothifyafrica.com.ng
```

Certbot needs the DNS record to be live first, because it proves you control
the name by answering a request to it.

## 6. Tell the outside world where to call

- **Bachs** → webhook endpoint `https://api.soothifyafrica.com.ng/api/v1/billing/webhook/`
- **ZEGOCLOUD** → recording callback `https://api.soothifyafrica.com.ng/api/v1/recordings/callback/`
- **The Flutter app** → base URL `https://api.soothifyafrica.com.ng/api/v1/`

Then `python manage.py setup_bachs_products` once, to create the recurring
products the plans sell.

## 7. Check it

```bash
curl https://api.soothifyafrica.com.ng/api/v1/health/
# {"status":"ok","deployment":"aws"}
```

If it says `cpanel`, `DEPLOY_TARGET` did not reach the process — it is in
`/etc/soothify/env` and the services were not restarted.

## Deploying a change later

```bash
cd /srv/soothify
sudo -u soothify git pull
sudo -u soothify venv/bin/pip install -r requirements.txt
sudo -u soothify venv/bin/python manage.py migrate
sudo -u soothify venv/bin/python manage.py collectstatic --no-input
sudo systemctl restart soothify-api soothify-worker soothify-beat
```

## Things that bite

**Port 25 is blocked on EC2 by default.** The welcome email goes out over
SMTP to `mail.soothifyafrica.com.ng` on 465, which is fine — but do not
switch it to 25, and do not expect a local sendmail to work.

**Beat must run exactly once.** Two instances means recordings deleted twice
and reminders sent twice. If you ever run two API boxes, beat belongs on one
of them only.

**The site and the API share one database only if you point them at one.**
Right now the cPanel site has its own SQLite with the waitlist in it. Moving
to Postgres on AWS means either migrating that data across or leaving the
site where it is. The waitlist signups are the only thing in there that
matters.
