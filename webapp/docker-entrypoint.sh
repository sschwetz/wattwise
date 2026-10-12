#!/bin/sh
set -eu

: "${API_BASE_URL:?API_BASE_URL must be set (e.g. https://core-api.yourdomain.example)}"

cat > /usr/share/nginx/html/config.js <<EOF
window.__APP_CONFIG__ = {
  apiBaseUrl: "${API_BASE_URL}"
};
EOF
chmod 644 /usr/share/nginx/html/config.js

exec nginx -g 'daemon off;'
