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

**Never commit .env to git.**

---

## Step 5 — Start the dashboard

```bash
cd /opt/shortsengine
docker compose up -d dashboard
```

The dashboard is now running at `http://YOUR_SERVER_IP:8000`.

To set a password, add `DASHBOARD_PASSWORD=yourpassword` to `.env` and restart:
```bash
docker compose restart dashboard
```

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
