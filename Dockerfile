FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    netcat-traditional \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code + entrypoint
COPY . .

# Make sure entrypoint is executable (this must come AFTER COPY . .)
RUN chmod +x /app/entrypoint.sh

# Static files directory
RUN mkdir -p /var/www/static

# Create non-root user and fix permissions
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app /var/www/static

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]