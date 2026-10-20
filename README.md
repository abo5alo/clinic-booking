# Clinic Booking

A Django app where patients book 30-minute appointments with a clinic's doctors, through web pages or a REST API. Staff manage doctors, working hours and appointments in the Django admin.

## Features

- **Booking pages:** sign up, filter doctors by specialty, read what each doctor treats, pick a day and time, say what you're coming in for, and cancel your own appointments.
- **Reason for visit:** required, at most 500 characters, visible only to the patient and clinic staff, and never written to the logs.
- **REST API** (Django REST Framework) with token or session login.
- **No double booking:** guaranteed by a database constraint, so it holds even when two requests arrive at the same moment.
- **Rate limiting:** on login, signup, booking and cancelling, for both the web pages and the API.
- **Abuse limits:** at most 5 upcoming appointments per patient, bookings only inside working hours and only in the future.
- **Production setup:** settings from environment variables, Postgres, gunicorn, WhiteNoise for static files, HTTPS-only cookies and HSTS, a Docker image that runs as a non-root user, a health check, and CI on GitHub Actions.

## Design decisions

- **The database enforces "one booking per slot".** A partial unique constraint on `(doctor, start)` that ignores cancelled appointments (`clinic/models.py`). Checking availability in Python first is not enough: two requests can both pass the check before either one saves. `Appointment.book()` validates first for a friendly message, then catches the database error if it loses that race.
- **One booking function for both front ends.** The web pages and the API both call `Appointment.book()` and `Appointment.cancel()`, so the rules can't drift apart.
- **Patients only ever see their own data.** Every query for appointments is filtered by the logged-in user, so someone else's appointment ID returns 404, not 403. That doesn't reveal whether the appointment exists.
- **Rate limits are counted per client IP.** The web pages use a small decorator (`clinic/ratelimit.py`), and the API uses DRF throttling. Both read the IP the same way and honour `DJANGO_NUM_PROXIES`, so a forged `X-Forwarded-For` header can't dodge the limit.
- **Known limit:** the counters live in an in-process cache, so each gunicorn worker counts on its own. Switch the cache to Redis before running more than one server.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:DJANGO_DEBUG = "1"
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py createsuperuser
.\.venv\Scripts\python manage.py runserver
```

Open http://127.0.0.1:8000, then add doctors and their working hours at http://127.0.0.1:8000/admin.

## Run with Docker (Postgres)

```bash
cp .env.example .env   # then fill in DJANGO_SECRET_KEY and POSTGRES_PASSWORD
docker compose up --build
docker compose exec web python manage.py createsuperuser
```

## Deploy (Render)

`render.yaml` describes the whole setup: a Docker web service plus a Postgres database, both on the free plan.

1. In Render: **New → Blueprint**, then pick this repository.
2. Fill in the values Render asks for: `DJANGO_DEMO_PASSWORD` (the public demo login), and `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL` and `DJANGO_SUPERUSER_PASSWORD` (your admin account).
3. On every start, the container applies migrations, seeds the demo doctors (`seed_demo`, only when `DJANGO_DEMO=1`), creates the admin if it doesn't exist, and starts gunicorn.

The demo shows a banner asking visitors not to enter real medical information, because the demo patient account is shared.

## Tests

```powershell
$env:DJANGO_DEBUG = "1"; .\.venv\Scripts\python manage.py test
```

CI runs the same tests against Postgres, plus `check --deploy` and a check that no migrations are missing.

## API

| Method | URL | Auth | Description |
|---|---|---|---|
| GET | `/api/doctors/` | none | List doctors |
| GET | `/api/doctors/<id>/slots/?date=YYYY-MM-DD` | none | Free slots on a day |
| POST | `/api/token/` | none | Exchange `username` and `password` for a token (5/min) |
| GET | `/api/appointments/` | token | Your appointments |
| POST | `/api/appointments/` | token | Book: `{"doctor": 1, "start": "2030-01-07T10:00:00Z", "reason": "Rash on my arm"}` (10/min) |
| POST | `/api/appointments/<id>/cancel/` | token | Cancel one of your appointments |

Send the token as `Authorization: Token <token>`.

## Credits

- [Geist](https://github.com/vercel/geist-font) typeface by Vercel, SIL Open Font License 1.1. It's self-hosted in `clinic/static/clinic/fonts/`, so patients' browsers never contact a font CDN.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DJANGO_SECRET_KEY` | required unless `DJANGO_DEBUG=1` | Django's signing key |
| `DJANGO_DEBUG` | off | `1` for local development only |
| `DJANGO_ALLOWED_HOSTS` | none | Comma-separated host names |
| `DJANGO_SSL_REDIRECT` | `1` | Redirect HTTP to HTTPS when not in debug |
| `DJANGO_NUM_PROXIES` | `0` | Reverse proxies in front of the app (usually `1` on a host like Render) |
| `DJANGO_TIME_ZONE` | `UTC` | The clinic's local time zone |
| `POSTGRES_HOST`, `_DB`, `_USER`, `_PASSWORD`, `_PORT` | SQLite if no host | Database connection |
