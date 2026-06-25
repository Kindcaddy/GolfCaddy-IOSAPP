# EC2 instance type upgrade — KindCaddy backend

This document describes how to **resize** the KindCaddy EC2 instance (e.g. `t2.small` → `t3.medium`) with minimal downtime and without any iOS app or DNS changes — **provided** the Elastic IP stays attached.

For code updates, see [EC2-UPDATE-README.md](EC2-UPDATE-README.md). For first-time setup, see [EC2-DEPLOY.md](EC2-DEPLOY.md).

---

## When to use this

- Upgrading instance class (e.g. `t2` → `t3`, `t3` → `t3a`/`m6i`).
- Scaling up for more CPU/RAM (e.g. `t3.small` → `t3.medium`).
- Fixing CPU throttling from `t2` burst credit exhaustion.

Expect **~2–5 minutes of API downtime** during the stop/start. The EBS volume, SQLite database, Caddy TLS cert, `.env`, and all code on disk are **preserved**.

---

## Critical gotcha: Elastic IP can detach on t2 → t3

A plain stop/start of an EC2 instance usually keeps the attached Elastic IP. **However**, when changing from a Xen-based family (`t2`) to a Nitro-based family (`t3`, `t3a`, `m5`, `m6i`, etc.), AWS sometimes detaches the EIP. If that happens:

- The instance gets a **different** auto-assigned public IP.
- `api.yourdomain.com` still resolves to the **old** EIP.
- Requests from the iOS app time out with zero errors in server logs (they never reach the server).

You **must verify the EIP is still attached** after the upgrade. See step 6.

---

## Pre-flight checks

### 1. Verify Nitro compatibility (required for t2 → t3)

SSH into the server:

```bash
ssh -i ~/.ssh/YOUR_KEY.pem ubuntu@api.yourdomain.com
```

Run:

```bash
lsmod | grep -E 'ena|nvme'
sudo ethtool -i ens5 2>/dev/null | head -1 || sudo ethtool -i eth0 | head -1
```

You need the `ena` and `nvme` modules loaded, and the NIC driver should be `ena`. Any modern Ubuntu AMI (20.04+) has these. If `ena` is missing the t3 instance will fail to boot — do not proceed.

From your Mac (with AWS CLI configured):

```bash
aws ec2 describe-instances --instance-ids i-XXXXXXXXXXX \
  --query 'Reservations[].Instances[].[EnaSupport,EbsOptimized,Hypervisor]' \
  --output table
```

You want `EnaSupport = True`. If it's false (rare), enable it first:

```bash
aws ec2 modify-instance-attribute --instance-id i-XXXXXXXXXXX --ena-support
```

### 2. Ensure services auto-start on boot

```bash
sudo systemctl is-enabled kindcaddy
sudo systemctl is-enabled caddy
```

Both should print `enabled`. If either is `disabled`:

```bash
sudo systemctl enable kindcaddy
sudo systemctl enable caddy
```

Without this, the instance comes back up with no API listening and you'll have to SSH in to start services manually.

### 3. Note the current Elastic IP

AWS Console → EC2 → your instance → Details tab → copy the **Elastic IP address**. Paste it somewhere. You'll need this to spot if it got detached.

### 4. Back up the SQLite database

```bash
cp /home/ubuntu/KindCaddy/data/kindcaddy.db \
   /home/ubuntu/kindcaddy-backup-$(date +%Y%m%d-%H%M).db
```

Optional: also take an EBS snapshot (EC2 → Volumes → Actions → Create snapshot). Cheap insurance; restore takes minutes if anything goes sideways.

---

## The upgrade

### Option A — AWS Console (recommended)

1. EC2 → Instances → select the KindCaddy instance.
2. **Instance state → Stop instance.** Wait for state = `Stopped`.
3. **Actions → Instance settings → Change instance type.**
4. Pick the new type (e.g. `t3.medium`) → **Apply**.
5. **Instance state → Start instance.** Wait for state = `Running` **and both status checks = 2/2**.

### Option B — AWS CLI

```bash
INSTANCE=i-XXXXXXXXXXX
NEW_TYPE=t3.medium

aws ec2 stop-instances --instance-ids $INSTANCE
aws ec2 wait instance-stopped --instance-ids $INSTANCE

aws ec2 modify-instance-attribute --instance-id $INSTANCE \
  --instance-type "{\"Value\": \"$NEW_TYPE\"}"

aws ec2 start-instances --instance-ids $INSTANCE
aws ec2 wait instance-running --instance-ids $INSTANCE
```

---

## Post-upgrade verification

### 5. Confirm the type actually changed

SSH in and run:

```bash
curl -s http://169.254.169.254/latest/meta-data/instance-type
```

Should print the new type (e.g. `t3.medium`).

### 6. Verify the Elastic IP is still attached — DO NOT SKIP

AWS Console → EC2 → **Elastic IPs**. Find the EIP you noted in pre-flight step 3.

- **"Associated instance ID"** should equal your KindCaddy instance ID.
- If it's blank or points to something else, the EIP detached during the upgrade. **Re-associate it now**:
  - Select the EIP → **Actions → Associate Elastic IP address** → choose your KindCaddy instance → **Associate**.

### 7. Confirm DNS still resolves to the right IP

From your **Mac**:

```bash
sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder
dig +short api.yourdomain.com
```

Compare the output to the EIP shown on the instance. **They must match.** If they don't:

- Most likely cause: the EIP detached and got replaced. Fix by re-associating (step 6). No DNS edit needed.
- Less common: the EIP was released entirely and the instance has a brand-new EIP. Then update the `A` record for `api` at your DNS provider to the new EIP. See [the DNS change checklist](#if-dns-needs-to-change) below.

### 8. Confirm services are healthy

On the server:

```bash
sudo systemctl status kindcaddy --no-pager -l
sudo systemctl status caddy --no-pager -l
curl -sI --max-time 5 http://127.0.0.1:8000/docs
sudo ss -ltnp | grep -E ':(80|443|8000) '
```

All services `active (running)`, local FastAPI returns `200`, and Caddy is bound to 80 + 443.

### 9. End-to-end HTTPS check

From your **Mac**:

```bash
curl -sI https://api.yourdomain.com/docs
```

Expect `HTTP/2 200`. If you see connection timeout, re-check step 6 (EIP) and step 7 (DNS).

### 10. iOS sanity check

Open the app. The Profile / Stats / Insights / History screens should load. "Start New Round" should get past `POST /session`.

Any in-progress round from before the restart will show "Round Session Expired" (the in-memory caddy `sessions` dict was wiped — this is normal). Finish the stranded round from History and start a new one.

---

## If DNS needs to change

Only needed if step 7 shows a mismatch **and** re-associating the old EIP isn't an option (because it was released).

1. Get the current public IP of the instance: AWS Console → EC2 → your instance → **Public IPv4 address** (or the Elastic IP if you re-allocated one).
2. Go to the DNS provider that hosts `yourdomain.com`. To find which one:
   ```bash
   dig NS yourdomain.com +short
   ```
   `awsdns` → Route 53. `cloudflare.com` → Cloudflare. `domaincontrol.com` → GoDaddy. `registrar-servers.com` → Namecheap.
3. Edit the `A` record for host `api` (type `A`) — change its value to the new IP. Do **not** touch the apex `@` record, `NS`, `MX`, `TXT`, or `CNAME` records.
4. Save. Wait 1–5 min.
5. Verify from your Mac:
   ```bash
   sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder
   dig @1.1.1.1 +short api.yourdomain.com
   curl -sI https://api.yourdomain.com/docs
   ```

No iOS rebuild is required — `Secrets.xcconfig` points at the hostname, not the IP.

---

## Why you don't need to touch the iOS app

The app addresses the backend via hostname:

```
KINDCADDY_BACKEND_URL = api.yourdomain.com
```

(See `ios/KindCaddy/Secrets.xcconfig` → `Info.plist` → `Config.swift` → `APIClient.swift`.)

The IP is resolved via DNS at runtime on every app launch. As long as DNS resolves to a reachable IP where the API is listening, the app doesn't care what type of EC2 instance is behind it.

---

## Rollback

If the new instance type misbehaves (boot failure, driver issues, unexpected cost):

1. Stop the instance.
2. Change instance type back to the original (e.g. `t2.small`).
3. Start the instance.
4. Run steps 6–9 again.

Nothing on the EBS volume is affected by the type change, so rollback is symmetric.

---

## Checklist

- [ ] Nitro driver support verified (`lsmod | grep ena`).
- [ ] `kindcaddy.service` and `caddy.service` both `enabled`.
- [ ] Current EIP noted in a scratch file.
- [ ] `kindcaddy.db` backed up.
- [ ] Instance stopped → type changed → started.
- [ ] 2/2 status checks passing.
- [ ] Instance type verified via metadata endpoint.
- [ ] **Elastic IP still associated with the instance**.
- [ ] `dig +short api.yourdomain.com` matches the instance's EIP.
- [ ] `curl -sI https://api.yourdomain.com/docs` → `200`.
- [ ] iOS app opens and loads all screens.
- [ ] Old/stray EIPs released in AWS (avoid unattached-EIP charges).

---

## Related docs

- [EC2-DEPLOY.md](EC2-DEPLOY.md) — initial deploy, Caddy/HTTPS setup.
- [EC2-UPDATE-README.md](EC2-UPDATE-README.md) — code update runbook.
- [OPERATIONS.md](OPERATIONS.md) — day-to-day ops, logs, troubleshooting.
- [kindcaddy.service](kindcaddy.service) — reference systemd unit file.
