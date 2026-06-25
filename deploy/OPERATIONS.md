# KindCaddy Operations Guide

Complete reference for deploying, managing, and updating KindCaddy on AWS EC2.

---

## Prerequisites

- AWS account with an EC2 instance (Ubuntu, t2.micro free tier)
- SSH key file (`.pem`) on your Mac
- A registered domain pointing to the instance (Route 53 or other registrar)
- Elastic IP assigned to the instance (so the IP survives restarts)

---

## 1. First-Time Deployment

### 1a. Package code on your Mac

```bash
cd ~/Desktop && tar czf /tmp/kindcaddy.tar.gz KindCaddy/
```

### 1b. Copy to EC2

```bash
scp -i ~/KindCaddy-key.pem /tmp/kindcaddy.tar.gz ubuntu@YOUR_EC2_IP:~/
```

### 1c. SSH into the instance

```bash
ssh -i ~/KindCaddy-key.pem ubuntu@YOUR_EC2_IP
```

### 1d. Unpack and install

```bash
tar xzf kindcaddy.tar.gz
cd KindCaddy
bash deploy/install.sh
```

### 1e. Set environment variables

```bash
sudo nano /etc/systemd/system/kindcaddy.service
```

Find the `Environment` line and set both keys:

```
Environment="OPENAI_API_KEY=sk-your-openai-key" "KINDCADDY_API_KEY=your-secret-api-key"
```

Generate a KINDCADDY_API_KEY with: `openssl rand -hex 24`

Save: `Ctrl+O`, `Enter`, `Ctrl+X`

```bash
sudo systemctl daemon-reload
sudo systemctl start kindcaddy
sudo systemctl status kindcaddy
```

Should show `active (running)`.

---

## 2. HTTPS Setup (Caddy + Let's Encrypt)

### 2a. Point your domain to the instance

In Route 53 (or your registrar), create an A record:

```
Record name: api (or @ for root)
Record type: A
Value: YOUR_ELASTIC_IP
TTL: 300
Routing: Simple
```

### 2b. Open ports in EC2 Security Group

In AWS Console > EC2 > Security Groups, add inbound rules:

```
HTTP    Port 80    Source: 0.0.0.0/0
HTTPS   Port 443   Source: 0.0.0.0/0
SSH     Port 22    Source: Your IP only
```

Remove the old port 8000 rule if it exists.

### 2c. Install Caddy on EC2

```bash
sudo apt update && sudo apt install -y caddy
```

### 2d. Configure Caddy

```bash
sudo nano /etc/caddy/Caddyfile
```

Replace the entire file with (use your actual domain):

```
api.yourdomain.com {
    reverse_proxy localhost:8000
}
```

Save: `Ctrl+O`, `Enter`, `Ctrl+X`

### 2e. Start Caddy

```bash
sudo systemctl enable caddy
sudo systemctl restart caddy
```

Caddy automatically gets a free Let's Encrypt certificate. Wait 30 seconds.

### 2f. Verify HTTPS works

From the EC2 instance:

```bash
curl https://api.yourdomain.com/docs
```

From your Mac (new terminal):

```bash
curl https://api.yourdomain.com/docs
```

If you see HTML output, HTTPS is working.

### 2g. Update the iOS app

In `ios/KindCaddy/KindCaddy/Config.swift`:

```swift
static let backendBaseURL = "https://api.yourdomain.com"
static let apiKey = "your-secret-api-key"
```

Rebuild in Xcode and reinstall on your phone.

---

## 3. Day-to-Day Operations

### Connecting to the server

```bash
ssh -i ~/KindCaddy-key.pem ubuntu@YOUR_EC2_IP
```

Closing your terminal does NOT stop the server. Everything keeps running.

### Checking service status

```bash
sudo systemctl status kindcaddy    # KindCaddy backend
sudo systemctl status caddy        # HTTPS proxy
```

### Viewing live logs

```bash
sudo journalctl -u kindcaddy -f    # Follow KindCaddy logs (Ctrl+C to stop)
sudo journalctl -u caddy -f        # Follow Caddy logs
```

### Restarting services

```bash
sudo systemctl restart kindcaddy   # After code or config changes
sudo systemctl restart caddy       # After Caddyfile changes
```

---

## 4. Pushing Code Updates

When you make changes on your Mac (new features, bug fixes, security updates):

### 4a. First, pull any logs from the server (so they don't get overwritten)

```bash
scp -i ~/KindCaddy-key.pem ubuntu@YOUR_EC2_IP:/home/ubuntu/KindCaddy/data/*.jsonl ~/Desktop/KindCaddy/data/
```

### 4b. Package and upload

```bash
cd ~/Desktop && tar czf /tmp/kindcaddy.tar.gz KindCaddy/
scp -i ~/KindCaddy-key.pem /tmp/kindcaddy.tar.gz ubuntu@YOUR_EC2_IP:~/
```

### 4c. SSH in, unpack, restart

```bash
ssh -i ~/KindCaddy-key.pem ubuntu@YOUR_EC2_IP
tar xzf kindcaddy.tar.gz
sudo systemctl restart kindcaddy
```

---

## 5. Working with Training Data Logs

### View how many interactions are logged

```bash
wc -l /home/ubuntu/KindCaddy/data/advice_logs_*.jsonl
```

### Read the conversations

```bash
python3 -c "
import json
with open('/home/ubuntu/KindCaddy/data/advice_logs_2026-03.jsonl') as f:
    for i, line in enumerate(f, 1):
        r = json.loads(line)
        hole = r['round_context']['hole']
        print(f'--- Interaction {i} (Hole {hole}) ---')
        print(f'You:   {r[\"user_input\"]}')
        resp = r['assistant_response']
        print(f'Caddy: {resp[:200]}...' if len(resp) > 200 else f'Caddy: {resp}')
        print()
"
```

### View one interaction in full detail

```bash
python3 -c "
import json
with open('/home/ubuntu/KindCaddy/data/advice_logs_2026-03.jsonl') as f:
    lines = f.readlines()
print(f'Total interactions: {len(lines)}')
print(json.dumps(json.loads(lines[0]), indent=2))
"
```

### Pull logs to your Mac for backup / fine-tuning

```bash
scp -i ~/KindCaddy-key.pem ubuntu@YOUR_EC2_IP:/home/ubuntu/KindCaddy/data/*.jsonl ~/Desktop/KindCaddy/data/
```

---

## 6. Stopping and Starting the Instance

### Stop the instance (to save free tier hours)

1. AWS Console > EC2 > Instances > select your instance
2. Instance state > **Stop instance**

Everything on disk is preserved. No data is lost.

### Start the instance

1. AWS Console > EC2 > Instances > select your instance
2. Instance state > **Start instance**
3. Wait for Status check: **2/2 checks passed**

If you have an Elastic IP, the IP stays the same. If not, check the new public IP and update your DNS A record.

After starting, SSH in and verify services are running:

```bash
ssh -i ~/KindCaddy-key.pem ubuntu@YOUR_EC2_IP
sudo systemctl status kindcaddy
sudo systemctl status caddy
```

Both should be `active (running)` automatically (they're enabled on boot).

---

## 7. Troubleshooting

| Problem | Check | Fix |
|---------|-------|-----|
| Can't SSH in | Security Group has port 22 open to your IP? | Update inbound rule with your current IP |
| SSH "Permission denied" | Correct .pem file? Permissions set? | `chmod 400 ~/KindCaddy-key.pem` |
| SSH "Operation timed out" | Instance running? Correct IP? | Check AWS Console, verify IP |
| App says "Could not connect" | KindCaddy service running? | `sudo systemctl restart kindcaddy` |
| App says "401 Invalid API key" | API key in Config.swift matches server? | Check both match exactly |
| HTTPS certificate error | Caddy running? DNS pointing to correct IP? | `sudo systemctl restart caddy` and check `dig api.yourdomain.com +short` |
| "OPENAI_API_KEY not set" | Key in systemd service file? | `sudo nano /etc/systemd/system/kindcaddy.service`, add key, `sudo systemctl daemon-reload && sudo systemctl restart kindcaddy` |
| Instance not found in AWS Console | Correct region selected? | Check region dropdown (top right of AWS Console) |
| Can't find log files | Data directory exists? | `ls -la /home/ubuntu/KindCaddy/data/` |

---

## 8. Where Secrets Live

| Secret | Location | File |
|--------|----------|------|
| OPENAI_API_KEY | EC2 server only | `/etc/systemd/system/kindcaddy.service` |
| KINDCADDY_API_KEY | EC2 server only | `/etc/systemd/system/kindcaddy.service` |
| API key (matching) | iOS app | `Config.swift` |
| Backend URL | iOS app | `Config.swift` |
| SSH private key | Your Mac only | `~/KindCaddy-key.pem` |

Never commit secrets to git. Never share your `.pem` file.

---

## Quick Reference Commands

```bash
# Connect to server
ssh -i ~/KindCaddy-key.pem ubuntu@YOUR_EC2_IP

# Check services
sudo systemctl status kindcaddy
sudo systemctl status caddy

# Restart after changes
sudo systemctl restart kindcaddy

# View logs
sudo journalctl -u kindcaddy --no-pager -n 50

# Pull training data to Mac
scp -i ~/KindCaddy-key.pem ubuntu@YOUR_EC2_IP:/home/ubuntu/KindCaddy/data/*.jsonl ~/Desktop/KindCaddy/data/

# Push code updates (run on Mac)
cd ~/Desktop && tar czf /tmp/kindcaddy.tar.gz KindCaddy/
scp -i ~/KindCaddy-key.pem /tmp/kindcaddy.tar.gz ubuntu@YOUR_EC2_IP:~/

# Unpack on server (run after SSH)
tar xzf kindcaddy.tar.gz && sudo systemctl restart kindcaddy
```
