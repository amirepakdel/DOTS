FROM nginx:alpine

RUN apk add --no-cache certbot openssl bash

RUN mkdir -p /var/www/certbot /etc/nginx/templates

COPY templates/ /etc/nginx/templates/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]