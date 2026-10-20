FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN useradd --create-home app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# collectstatic imports settings, which need some key; this one exists only during the build.
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

USER app
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 3 --access-logfile -"]
