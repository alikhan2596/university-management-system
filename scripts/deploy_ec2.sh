#!/bin/bash
set -e

APP_DIR="/opt/ums"
ARTIFACT="/tmp/ums-build.zip"
BUCKET="alikhan2596-ums-cicd-artifacts"

echo "=== UMS deployment started ==="

mkdir -p "$APP_DIR"

aws s3 cp \
"s3://${BUCKET}/releases/latest.zip" \
"$ARTIFACT"

rm -rf "${APP_DIR:?}"/*

unzip -o "$ARTIFACT" -d "$APP_DIR"

cd "$APP_DIR"

python3 -m venv venv

venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

    cat > .env <<EOF
SECRET_KEY=$SECRET
ADMIN_USERNAME=admin
ADMIN_PASSWORD=AdminTemp@2026!
TEACHER_PASSWORD=TeacherTemp@2026!
STUDENT_PASSWORD=StudentTemp@2026!
SESSION_MINUTES=30
COOKIE_SECURE=0
EOF
fi

if [ ! -f university.db ]; then
    venv/bin/python seed.py
fi

cat > /etc/systemd/system/ums.service <<EOF
[Unit]
Description=University Management System
After=network.target

[Service]
User=root
WorkingDirectory=/opt/ums
EnvironmentFile=/opt/ums/.env
ExecStart=/opt/ums/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/nginx/conf.d/ums.conf <<'EOF'
server {
    listen 80 default_server;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

systemctl daemon-reload

systemctl enable ums
systemctl restart ums

nginx -t

systemctl enable nginx
systemctl restart nginx

sleep 3

curl --fail http://127.0.0.1/login

echo "=== UMS deployment successful ==="
