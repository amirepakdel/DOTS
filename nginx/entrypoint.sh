#!/bin/sh
set -e

DOMAIN="${CERTBOT_DOMAIN:-su.forclosure.ai}"
EMAIL="${CERTBOT_EMAIL:-admin@su.forclosure.ai}"
STAGING="${CERTBOT_STAGING:-0}"

STAGING_ARG=""
if [ "$STAGING" = "1" ]; then
    STAGING_ARG="--staging"
fi

certs_exist() {
    [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ] && \
    [ -f "/etc/letsencrypt/live/$DOMAIN/privkey.pem" ]
}

generate_config() {
    sed -e "s|\${DOMAIN}|$DOMAIN|g" "$1" > /etc/nginx/conf.d/default.conf
}

# Bootstrap: if no certs, start HTTP-only nginx temporarily for ACME
if ! certs_exist; then
    echo "=== No SSL certificates found. Bootstrapping HTTP mode ==="
    
    generate_config /etc/nginx/templates/http.conf.template
    
    echo "Starting nginx in HTTP mode for ACME challenge..."
    nginx
    
    echo "Waiting for nginx to be ready..."
    sleep 2
    
    echo "Requesting certificate from Let's Encrypt for $DOMAIN..."
    
    # Use email if provided, otherwise register unsafely without email
    if [ -n "$EMAIL" ] && [ "$EMAIL" != "admin@su.forclosure.ai" ]; then
        EMAIL_ARG="--email $EMAIL"
    else
        EMAIL_ARG="--register-unsafely-without-email"
    fi
    
    certbot certonly --webroot \
        -w /var/www/certbot \
        -d "$DOMAIN" \
        $EMAIL_ARG \
        --agree-tos \
        --non-interactive \
        --no-eff-email \
        $STAGING_ARG || {
            echo "WARNING: Certbot failed. Will continue with HTTP-only."
            echo "Make sure $DOMAIN points to this server's IP."
        }
    
    echo "Stopping temporary nginx..."
    nginx -s stop
    sleep 1
fi

# Final config: HTTPS if certs exist, otherwise HTTP fallback
if certs_exist; then
    echo "=== Starting nginx with HTTPS ==="
    generate_config /etc/nginx/templates/https.conf.template
else
    echo "=== Starting nginx with HTTP-only (no certs available) ==="
    generate_config /etc/nginx/templates/http.conf.template
fi

# Start nginx in foreground
echo "Starting nginx..."
nginx -g "daemon off;" &
NGINX_PID=$!

# Auto-renewal loop (runs every 12 hours)
(
    while true; do
        echo "Waiting 12h before next renewal check..."
        sleep 12h
        
        if certs_exist; then
            echo "Checking certificate renewal..."
            certbot renew --quiet --no-random-sleep-on-renew $STAGING_ARG
            echo "Reloading nginx..."
            nginx -s reload
        fi
    done
) &

# Wait for nginx (main process)
wait $NGINX_PID