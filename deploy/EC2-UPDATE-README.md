# EC2 update guide — KindCaddy backend

This document describes how to **push code changes** from your Mac to an existing KindCaddy server on AWS EC2 and restart the API safely.

For first-time setup, use [EC2-DEPLOY.md](EC2-DEPLOY.md) and [OPERATIONS.md](OPERATIONS.md).

---

## What must be true on the server

The systemd unit expects:

| Item | Typical value |
|------|----------------|
| Working directory | `/home/ubuntu/KindCaddy` |
| Python app module | `kindcaddy.api:app` (package lives in **`~/KindCaddy/kindcaddy/`**) |
| Virtualenv | `~/KindCaddy/venv` |
| Environment file | `~/KindCaddy/.env` (loaded by systemd) |

**Important:** Uvicorn imports `kindcaddy.api`. That means `api.py`, `db.py`, `auth.py`, and the rest of the package must be under **`/home/ubuntu/KindCaddy/kindcaddy/`**, not only in `/home/ubuntu/KindCaddy/` (repo root). If you unpack a tarball incorrectly, you can end up with modules at the wrong path and get `404` on new routes or import errors in logs.

### Fix: modules ended up in `~/KindCaddy/` (repo root)

If a deploy left `auth.py`, `db.py`, `api.py`, or `api_models.py` next to `venv/` and `requirements.txt` but **outside** the `kindcaddy` package folder, copy them into the package (then restart):

```bash
cp ~/KindCaddy/auth.py ~/KindCaddy/db.py ~/KindCaddy/api.py ~/KindCaddy/api_models.py ~/KindCaddy/kindcaddy/
sudo systemctl restart kindcaddy
sudo systemctl status kindcaddy
```

(`~` is `/home/ubuntu` when you SSH as `ubuntu`.) After the service is healthy, you can delete the stray copies in `~/KindCaddy/` if you want to avoid confusion—Python loads the package from `kindcaddy/` when you run `uvicorn kindcaddy.api:app`.

---

## Update flow (recommended)

### 1. On your Mac — create the archive

```bash
cd ~/KindCaddy

tar --exclude='__pycache__' \
    --exclude='*.pyc' \
    -czvf /tmp/kindcaddy.tar.gz \
    kindcaddy
```

### 2. On your Mac — copy to EC2 and SSH in

Replace the key path and host.

```bash
scp -i ~/.ssh/YOUR_KEY.pem /tmp/kindcaddy.tar.gz ubuntu@EC2_PUBLIC_IP:~/
ssh -i ~/.ssh/YOUR_KEY.pem ubuntu@EC2_PUBLIC_IP
```

### 3. On EC2 — unpack into the app directory

```bash
cd /home/ubuntu/KindCaddy
tar -xzvf ~/kindcaddy.tar.gz
```

### 4. On EC2 — install new Python dependencies (when `requirements.txt` changed)

```bash
source /home/ubuntu/KindCaddy/venv/bin/activate
pip install -r /home/ubuntu/KindCaddy/requirements.txt
```

If email auth raises bcrypt / passlib errors, the known-good pin is:

```bash
pip install "bcrypt==4.0.1"
```

### 5. On EC2 — environment variables

Secrets and config live in **`/home/ubuntu/KindCaddy/.env`** (referenced by `kindcaddy.service`). After adding auth, rounds, or other features, ensure the server has at least:

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | OpenAI API |
| `KINDCADDY_JWT_SECRET` | Signing JWTs (use a long random secret; `openssl rand -hex 32`) |
| `APPLE_BUNDLE_ID` | Must be `com.kindcaddy.app` (case-sensitive) — must match iOS bundle ID exactly for Apple Sign-In token checks |
| `GOOGLE_CLIENT_ID` | iOS OAuth client ID for Google ID token audience validation |
| `KINDCADDY_API_KEY` | Optional legacy header auth |
| `KINDCADDY_DB_PATH` | Optional; default is under `data/kindcaddy.db` relative to the app |

Edit with `nano /home/ubuntu/KindCaddy/.env`, save, then restart (step 6).

### 6. On EC2 — restart the API

```bash
sudo systemctl restart kindcaddy
sudo systemctl status kindcaddy
```

You want **`active (running)`**. If not:

```bash
sudo journalctl -u kindcaddy --no-pager -n 80
```

Fix any `ModuleNotFoundError` (install missing packages in the **venv**) or traceback, then restart again.

### 7. Quick verification

From the server (or via SSH tunnel to `127.0.0.1:8000`):

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/docs
```

Expect `200`. For authenticated features, test with a valid `Authorization: Bearer …` header or your configured `X-API-Key`.

---

## “Quick patch” — copy only a few files

If you only changed backend modules and want to avoid a full tarball:

1. On your Mac, `scp` the changed files into **`ubuntu@host:/home/ubuntu/KindCaddy/kindcaddy/`** (same names as in the repo).
2. On EC2: `sudo systemctl restart kindcaddy`.

Example:

```bash
scp -i ~/.ssh/YOUR_KEY.pem \
  ~/KindCaddy/kindcaddy/api.py \
  ~/KindCaddy/kindcaddy/db.py \
  ubuntu@EC2_PUBLIC_IP:/home/ubuntu/KindCaddy/kindcaddy/
```

---

## SQLite database and round history

Phase 2 creates **`rounds`**, **`round_scores`**, and **`round_shots`** tables on first startup (`init_db()`). The database file is typically:

`/home/ubuntu/KindCaddy/data/kindcaddy.db`

Back it up before risky experiments:

```bash
cp /home/ubuntu/KindCaddy/data/kindcaddy.db "/home/ubuntu/kindcaddy-backup-$(date +%Y%m%d).db"
```

---

## Harmless tar warning on Linux

If you see:

```text
tar: Ignoring unknown extended header keyword 'LIBARCHIVE.xattr.com.apple.provenance'
```

That is **normal** when extracting on Linux archives created on macOS. It does not mean the extract failed.

---

## Checklist after each deploy

- [ ] Files landed under `/home/ubuntu/KindCaddy/kindcaddy/` (not only repo root).
- [ ] `pip install -r requirements.txt` in the project venv if dependencies changed.
- [ ] `.env` contains any **new** variables required by the release.
- [ ] `sudo systemctl restart kindcaddy` and status is **running**.
- [ ] `journalctl -u kindcaddy -n 50` shows no traceback on startup.
- [ ] Optional: backup `data/kindcaddy.db` before major upgrades.

---

## Related docs

- [EC2-DEPLOY.md](EC2-DEPLOY.md) — initial deploy tarball layout and Caddy/HTTPS.
- [EC2-INSTANCE-UPGRADE.md](EC2-INSTANCE-UPGRADE.md) — resize/change instance type (e.g. `t2.small` → `t3.medium`) without breaking the app.
- [OPERATIONS.md](OPERATIONS.md) — day-to-day ops, logs, troubleshooting, training data.
- [kindcaddy.service](kindcaddy.service) — reference unit file (paths and `EnvironmentFile`).
