# ARI-10: Deployment Investigation Report

**Date**: 2026-03-06
**Investigator**: Worker aristotlebot-slack-w33 (continued by w34)
**Status**: ✅ Complete — all deployment details confirmed

---

## Summary

**aristotlebot-slack is currently deployed and running as a systemd service on the `klaw-controller` GCP Compute Engine VM in `us-central1-a`.** It is actively connected to Slack via Socket Mode and processing events.

---

## Infrastructure Details

### GCP Compute Engine Instance

| Property          | Value                                                              |
| ----------------- | ------------------------------------------------------------------ |
| **Instance Name** | `klaw-controller`                                                  |
| **Zone**          | `us-central1-a`                                                    |
| **GCP Project**   | `klaw-488307` (numeric: `494635512295`)                            |
| **Machine Type**  | `e2-medium`                                                        |
| **OS Image**      | `ubuntu-2404-noble-amd64-v20260218` (Ubuntu 24.04 Noble)           |
| **Internal IP**   | `10.0.0.2`                                                        |
| **FQDN**          | `klaw-controller.us-central1-a.c.klaw-488307.internal`            |
| **Service Acct**  | `klaw-sa@klaw-488307.iam.gserviceaccount.com`                     |
| **Python**        | 3.12.3                                                             |
| **Disk**          | 48 GB root (58% used, ~21 GB free)                                |
| **Memory**        | 3.8 GB total, ~1.3 GB used, ~2.5 GB available                    |

### Service Configuration

The bot runs as a **systemd service** named `aristotlebot.service`.

**Service file**: `/etc/systemd/system/aristotlebot.service`

```ini
[Unit]
Description=Aristotlebot Slack Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/var/lib/openclaw/agents/aristotlebot-slack
EnvironmentFile=/etc/klaw/aristotlebot.env
ExecStart=/var/lib/openclaw/agents/aristotlebot-slack/.venv/bin/python main.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Key properties**:
- **Enabled on boot**: Yes (`WantedBy=multi-user.target`)
- **Auto-restart**: On failure, after 10 seconds
- **Working directory**: `/var/lib/openclaw/agents/aristotlebot-slack`
- **Entry point**: `.venv/bin/python main.py`
- **Runs as**: root (NOTE: should ideally run as a dedicated user)
- **Logs**: journald (`journalctl -u aristotlebot.service`)

### Environment / Secrets

Secrets are stored in `/etc/klaw/aristotlebot.env` (permissions: `-rw-------`, root only):

| Variable           | Source                                      |
| ------------------ | ------------------------------------------- |
| `SLACK_BOT_TOKEN`  | `/etc/klaw/aristotlebot.env`                |
| `SLACK_APP_TOKEN`  | `/etc/klaw/aristotlebot.env`                |
| `ARISTOTLE_API_KEY`| `/etc/klaw/aristotlebot.env`                |
| `LOG_LEVEL`        | `/etc/klaw/aristotlebot.env`                |

Secrets are also stored in GCP Secret Manager:
- `klaw-aristotle-slack-bot-token`
- `klaw-aristotle-slack-app-token`
- `klaw-aristotle-api-key`

---

## Current Runtime Status

As of 2026-03-06 22:51 UTC:

- **Service state**: `active (running)` since 2026-03-05 21:31:55 UTC (~25 hours uptime)
- **PID**: 2188
- **Memory usage**: 60.3 MB
- **CPU usage**: 1m 39s total
- **Socket Mode**: Connected (session active)
- **Health endpoint**: `http://localhost:8080/health` → `{"status": "ok"}`

### Health Check Output

```json
{
  "status": "ok",
  "uptime_seconds": 91178.6,
  "socket_mode_connected": true,
  "events": {
    "total_received": 3,
    "message_events": 3,
    "app_mention_events": 0,
    "ignored_events": 0
  },
  "last_event": {
    "timestamp_iso": "2026-03-06T02:56:55Z",
    "seconds_ago": 71678.9
  },
  "registered_listeners": ["message", "app_mention"]
}
```

### Running Code Version

The service is running from the local git repo at `/var/lib/openclaw/agents/aristotlebot-slack` on the `main` branch. The latest commit on main is:

```
b8cbddb feat: GitHub Lean file intake with import resolution + playground links (#12)
```

**Note**: The service was started on 2026-03-05 21:31:55 UTC but the `main` branch has received commits since then. The running code may be slightly behind the latest `main` if the service hasn't been restarted.

---

## Deployment Architecture

```
┌──────────────────────────────────────────────────────────┐
│  GCP Project: klaw-488307                                 │
│  Zone: us-central1-a                                      │
│                                                           │
│  ┌───────────────────────────────────────────────────┐   │
│  │  VM: klaw-controller (e2-medium)                   │   │
│  │  OS: Ubuntu 24.04 Noble                            │   │
│  │  Internal IP: 10.0.0.2                             │   │
│  │                                                     │   │
│  │  systemd services:                                  │   │
│  │  ├── aristotlebot.service (ACTIVE)                  │   │
│  │  │   └── python main.py                             │   │
│  │  │       ├── Slack Socket Mode (WebSocket)          │   │
│  │  │       ├── Health HTTP server (:8080)             │   │
│  │  │       └── aristotlelib → aristotle.harmonic.fun  │   │
│  │  │                                                   │   │
│  │  ├── /var/lib/openclaw/agents/aristotlebot-slack/   │   │
│  │  │   ├── .venv/ (Python 3.12 virtualenv)            │   │
│  │  │   └── git repo (origin: eastseymour/...)         │   │
│  │  │                                                   │   │
│  │  └── /etc/klaw/aristotlebot.env (secrets)           │   │
│  └───────────────────────────────────────────────────┘   │
│                                                           │
│  GCP Secret Manager:                                      │
│  ├── klaw-aristotle-slack-bot-token                       │
│  ├── klaw-aristotle-slack-app-token                       │
│  └── klaw-aristotle-api-key                               │
└──────────────────────────────────────────────────────────┘
         │
         │ WebSocket (Slack Socket Mode)
         ▼
┌─────────────────┐
│   Slack API      │
└─────────────────┘
         │
         │ HTTPS
         ▼
┌────────────────────────────┐
│ aristotle.harmonic.fun     │
│ (Aristotle API backend)    │
└────────────────────────────┘
```

---

## What Was NOT Found

The following deployment methods are **not** in use:

| Technology        | Status    |
| ----------------- | --------- |
| Cloud Run         | Not used  |
| Cloud Functions   | Not used  |
| Kubernetes / GKE  | Not used  |
| Docker containers | Not used (Dockerfile exists but service runs directly via Python) |
| docker-compose    | Not used  |
| PM2               | Not used  |
| supervisord       | Not used  |
| Terraform         | Not used  |
| GitHub Actions CD | Not used (only CodeQL for security scanning) |
| CI/CD pipeline    | Not used  |

---

## How to Manage the Service

```bash
# Check status
systemctl status aristotlebot.service

# View logs (follow)
journalctl -u aristotlebot.service -f

# View recent logs
journalctl -u aristotlebot.service --since "1 hour ago"

# Restart (after code updates)
systemctl restart aristotlebot.service

# Stop
systemctl stop aristotlebot.service

# Start
systemctl start aristotlebot.service

# Check health
curl http://localhost:8080/health
```

---

## Recommendations

1. **Non-root execution**: The service currently runs as `root`. Consider creating a dedicated `aristotlebot` user.
2. **Automated deployment**: There's no CI/CD pipeline. Code updates require manual `git pull` + `systemctl restart`.
3. **Monitoring**: The health endpoint exists but no external monitoring/alerting is configured.
4. **Code sync**: The running code may drift from `main` — consider a deployment webhook or cron job to keep in sync.
5. **Docker**: A Dockerfile exists but isn't used in production. The service runs directly via Python virtualenv.
