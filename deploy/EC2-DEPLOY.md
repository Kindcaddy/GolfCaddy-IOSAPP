# Deploy KindCaddy to EC2

Run these steps from your **Mac** and then on the **EC2 instance**.

---

## 1. From your Mac: copy project to EC2

Replace `YOUR_KEY.pem` and `EC2_PUBLIC_IP` with your key path and instance public IP (from AWS EC2 console).

```bash
cd ~/KindCaddy

# Create tarball (excludes .git, ios, openclaw, __pycache__)
tar --exclude='.git' --exclude='ios' --exclude='openclaw' --exclude='__pycache__' --exclude='*.pyc' -czvf /tmp/kindcaddy.tar.gz kindcaddy profiles requirements.txt deploy scenarios

# Copy to EC2 (install.sh is inside the tarball under deploy/)
scp -i ~/.ssh/YOUR_KEY.pem /tmp/kindcaddy.tar.gz ubuntu@EC2_PUBLIC_IP:~/
```

---

## 2. SSH into the instance

```bash
ssh -i ~/.ssh/YOUR_KEY.pem ubuntu@EC2_PUBLIC_IP
```

---

## 3. On the EC2 instance: unpack and install

```bash
mkdir -p KindCaddy
tar -xzvf kindcaddy.tar.gz -C KindCaddy
cd KindCaddy
bash deploy/install.sh
```

---

## 4. Set your OpenAI API key

```bash
nano /home/ubuntu/KindCaddy/.env
```

Add your secrets (values are case-sensitive):

```
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxx
KINDCADDY_JWT_SECRET=<output of: openssl rand -hex 32>
KINDCADDY_API_KEY=<your api key>
APPLE_BUNDLE_ID=com.kindcaddy.app
GOOGLE_CLIENT_ID=<your Google OAuth client ID>
```

Save (Ctrl+O, Enter, Ctrl+X).

---

## 5. Start the API

```bash
sudo systemctl start kindcaddy
sudo systemctl status kindcaddy
```

You should see `active (running)`.

---

## 6. Test from your Mac (via SSH tunnel — do NOT open port 8000 publicly)

Do **not** open port 8000 in the EC2 security group. Test via an SSH tunnel instead:

```bash
# Forward local 8001 → server’s 8000
ssh -i ~/.ssh/YOUR_KEY.pem -L 8001:127.0.0.1:8000 ubuntu@EC2_PUBLIC_IP -N &
curl -s http://127.0.0.1:8001/docs
```

If you see HTML, the API is up. Kill the tunnel with `kill %1` when done.

**Quick session test:**

```bash
curl -s -X POST http://127.0.0.1:8001/session \
  -H “Content-Type: application/json” \
  -d ‘{“profile”:{“name”:”Jimmy”,”handicap”:15,”shot_shape”:”fade”,”handed”:”right”,”chat_style”:”casual”,”target_score”:85,”clubs”:{“7i”:{“carry”:155,”total”:165},”PW”:{“carry”:118,”total”:125}}}}’
```

You should get `{“session_id”:”...”}`.

---

## 7. HTTPS with Caddy (required before using the iOS app)

The iOS app requires HTTPS. Port 8000 must **not** be publicly accessible — expose only port 443 via Caddy.

1. Open **port 443** (HTTPS) in the EC2 security group (Inbound: HTTPS, Source 0.0.0.0/0).
2. Point a domain or free [DuckDNS](https://www.duckdns.org) hostname to your EC2 public IP.
3. Install Caddy:
   ```bash
   sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
   curl -1sLf ‘https://dl.cloudsmith.io/public/caddy/stable/gpg.key’ | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
   curl -1sLf ‘https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt’ | sudo tee /etc/apt/sources.list.d/caddy-stable.list
   sudo apt update && sudo apt install caddy
   ```
4. Configure `/etc/caddy/Caddyfile`:
   ```
   your-domain.com {
       reverse_proxy 127.0.0.1:8000
   }
   ```
5. Reload: `sudo systemctl reload caddy`

---

## 8. In the iOS app

The app reads secrets from `ios/KindCaddy/Secrets.xcconfig` (gitignored — never hardcode values in `Config.swift`). Fill in your values:

```
KINDCADDY_API_KEY = <value from server .env>
KINDCADDY_BACKEND_URL = your-domain.com
GOOGLE_CLIENT_ID = <your Google OAuth client ID>
```

If `Secrets.xcconfig` doesn't exist yet, copy the template:

```bash
cp ios/KindCaddy/Secrets.xcconfig.example ios/KindCaddy/Secrets.xcconfig
```

HTTP is not supported — the iOS app requires HTTPS.

---

## Useful commands on the server

| Command | Purpose |
|--------|--------|
| `sudo systemctl status kindcaddy` | Check if API is running |
| `sudo systemctl restart kindcaddy` | Restart after code/config change |
| `sudo journalctl -u kindcaddy -f` | Follow API logs |

---

## Related docs

- [EC2-UPDATE-README.md](EC2-UPDATE-README.md) — push code changes and restart the API safely.
- [EC2-INSTANCE-UPGRADE.md](EC2-INSTANCE-UPGRADE.md) — resize the instance (e.g. `t2.small` → `t3.medium`) without breaking the app.
- [OPERATIONS.md](OPERATIONS.md) — day-to-day ops, logs, troubleshooting.
