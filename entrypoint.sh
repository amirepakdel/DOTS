#!/bin/sh
chmod +x "$0" 2>/dev/null || true

set -e

echo "Waiting for PostgreSQL at $DB_HOST:$DB_PORT..."
while ! nc -z "$DB_HOST" "$DB_PORT"; do
  sleep 0.5
done
echo "PostgreSQL is up"

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Running migrations..."
python manage.py migrate --noinput

echo "Running init_db..."
python manage.py init_db

echo "Starting Daphne (ASGI Server)..."
exec daphne -b 0.0.0.0 -p 8000 dtos.asgi:application