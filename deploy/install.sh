#!/bin/bash
# Run this script ON the EC2 instance (Ubuntu) after copying KindCaddy there.
# Usage: cd KindCaddy && bash deploy/install.sh

set -e
echo "=== KindCaddy EC2 install ==="

# Project root = parent of deploy/
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
echo "Project root: $APP_DIR"

# System deps
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv

# Venv and Python deps (skip sounddevice on headless server - optional)
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# .env for OPENAI_API_KEY (you must create this yourself with the real key)
if [ ! -f .env ]; then
  echo "Creating .env template. YOU MUST EDIT .env and set OPENAI_API_KEY=sk-..."
  install -m 600 /dev/null .env
  printf "OPENAI_API_KEY=\nKINDCADDY_API_KEY=\n" >> .env
fi
chmod 600 .env

# Systemd service (replace default path with actual APP_DIR)
sudo cp "$APP_DIR/deploy/kindcaddy.service" /etc/systemd/system/
sudo sed -i "s|/home/ubuntu/KindCaddy|$APP_DIR|g" /etc/systemd/system/kindcaddy.service
sudo systemctl daemon-reload
sudo systemctl enable kindcaddy

echo "=== Done. Next steps ==="
echo "1. Edit $APP_DIR/.env and set OPENAI_API_KEY=sk-..."
echo "2. Start the API: sudo systemctl start kindcaddy"
echo "3. Check status: sudo systemctl status kindcaddy"
echo "4. For HTTPS, install Caddy and proxy to 127.0.0.1:8000"
