#!/bin/sh
set -eu

: "${API_BASE_URL:?API_BASE_URL must be set (e.g. https://core-api.yourdomain.example)}"

cat > /usr/share/nginx/html/config.js <<EOF
window.__APP_CONFIG__ = {
  apiBaseUrl: "${API_BASE_URL}"
};
EOF
chmod 644 /usr/share/nginx/html/config.js

# connect-src must name the API origin, which is only known at container start.
# The live WebSocket uses the same origin with the ws/wss scheme.
WS_API_BASE_URL="ws${API_BASE_URL#http}"
cat > /etc/nginx/security-headers.conf <<EOF
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self' ${API_BASE_URL} ${WS_API_BASE_URL}; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'" always;
add_header X-Frame-Options "DENY" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
EOF

exec nginx -g 'daemon off;'
