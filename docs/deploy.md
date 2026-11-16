# Deployment Guide — ShortsEngine

This guide covers how to run ShortsEngine on a cloud server so the pipeline
runs automatically and the dashboard is accessible from anywhere.

## Recommended setup (Phase 7)

| Component | Choice | Why |
|-----------|--------|-----|
| Server | DigitalOcean Droplet, $6/mo | Cheapest option that runs 24/7 |
| Containers | Docker + Docker Compose | Same setup locally and on the server |
| Scheduler | Linux cron | Built into every server; no extra service |
| SSL/HTTPS | Caddy (optional) | Free auto-renewing certs via Let's Encrypt |

---

## Step 1 — Create a server

1. Sign up at [digitalocean.com](https://digitalocean.com).
2. Create a Droplet: **Ubuntu 24.04 LTS, Basic, $6/month** (1 GB RAM, 25 GB SSD).
3. Add your SSH key when creating it so you can log in without a password.

---

## Step 2 — Install Docker on the server

```bash
# Log into your server
ssh root@YOUR_SERVER_IP

# Install Docker and Docker Compose (one command)
curl -fsSL https://get.docker.com | sh
```

---

## Step 3 — Upload the project

On your local machine:

```bash
# Copy the project to the server (replace YOUR_SERVER_IP)
scp -r /path/to/shortsengine root@YOUR_SERVER_IP:/opt/shortsengine
```

Or clone from GitHub:

```bash
ssh root@YOUR_SERVER_IP
git clone https://github.com/Tejeshp27/ShortsEngine.git /opt/shortsengine
cd /opt/shortsengine
```

---

## Step 4 — Add your secrets

```bash
cd /opt/shortsengine
cp .env.example .env
nano .env   # fill in your API keys
```

The dashboard also needs a `SESSION_SECRET` (it signs the login cookie).
Create a random one and paste it into `.env`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Never commit .env to git.**

---

## Step 5 — Create your login and start the dashboard

```bash
cd /opt/shortsengine

# Create your username and password (stored hashed in the database).
docker compose run --rm dashboard python -m src.dashboard.set_password

docker compose up -d dashboard
```

The dashboard now runs on the server, but on purpose it is **not** reachable
from the internet: it only listens on the server itself (`127.0.0.1:8000`), so
your password never crosses the internet unencrypted. To open it from your
laptop, use an encrypted tunnel:

```bash
# On your own computer — then open http://localhost:8000
ssh -L 8000:localhost:8000 root@YOUR_SERVER_IP
```

For always-on access from your phone, put it behind HTTPS (Caddy, below) and set
`SESSION_COOKIE_SECURE=true` in `.env`, or use a private network such as Tailscale.

> **Server size:** rendering an animated video peaked at about 1.1 GB of memory
> when measured on a Mac, so the 1 GB server above is too small to render videos.
> Use a server with at least 2 GB (4 GB is comfortable), or render on your own
> computer. The database lives in `data/` and videos in `output/` on the server's disk.

---

## Step 6 — Schedule the pipeline with cron

The pipeline should run once a day. On the server:

```bash
# Open the cron editor
crontab -e

# Add this line to run the news pipeline at 7am UTC every day:
0 7 * * * cd /opt/shortsengine && docker compose run --rm pipeline >> /var/log/shortsengine.log 2>&1
```

To run on multiple channels, add one line per channel.

---

## Step 7 — View logs

```bash
# Dashboard logs
docker compose logs -f dashboard

# Pipeline logs (from cron)
tail -f /var/log/shortsengine.log
```

---

## (Optional) HTTPS with Caddy

If you have a domain name pointed at your server:

```bash
apt install -y caddy
```

Create `/etc/caddy/Caddyfile`:
```
yourdomain.com {
    reverse_proxy localhost:8000
}
```

```bash
systemctl restart caddy
```

Caddy handles SSL certificates automatically via Let's Encrypt.

---

## Updating the code

```bash
cd /opt/shortsengine
git pull
docker compose build
docker compose up -d dashboard
```
