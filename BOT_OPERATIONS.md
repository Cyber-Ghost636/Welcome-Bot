# Welcome Bot Operations

This file is a quick reference for running and maintaining the `welcome-bot` systemd service.

## Service Name

- `welcome-bot`

## Common Commands

### Check status

```bash
sudo systemctl --no-pager --full status welcome-bot
```

### Restart bot

```bash
sudo systemctl restart welcome-bot
```

### Restart + quick status (recommended)

```bash
sudo systemctl restart welcome-bot && sudo systemctl --no-pager --full status welcome-bot | head -n 20
```

### Start bot

```bash
sudo systemctl start welcome-bot
```

### Stop bot

```bash
sudo systemctl stop welcome-bot
```

### Enable on boot

```bash
sudo systemctl enable welcome-bot
```

### Disable on boot

```bash
sudo systemctl disable welcome-bot
```

## Logs

### Follow live logs

```bash
sudo journalctl -u welcome-bot -f
```

### Show latest 200 lines

```bash
sudo journalctl -u welcome-bot -n 200 --no-pager
```

### Show logs since last boot

```bash
sudo journalctl -u welcome-bot -b --no-pager
```

## Deploying Code Changes

Run these after editing code:

```bash
cd /home/wh33ls/Documents/Welcome\ Bot
./scripts/pre_restart_check.sh
sudo systemctl restart welcome-bot
sudo systemctl --no-pager --full status welcome-bot | head -n 20
```

Then verify in Discord by running your test commands.

## Team Raid/Dungeon Post Speed

Team raid and team dungeon checks now run on a dedicated interval so posts can appear faster than solo checks.

- Env var: `TEAM_ACTIVITY_CHECK_INTERVAL_MINUTES`
- Default: `5`
- Lower value = faster posting but more Bungie API traffic

## Pre-Restart Sanity Check

Run this before restart to catch common startup issues:

```bash
cd /home/wh33ls/Documents/Welcome\ Bot
./scripts/pre_restart_check.sh
```

What it checks:

- Invalid slash-command decorator namespace typos (`@app.commands.*`)
- Python syntax/compile issues across top-level `*.py` files

## One-Command Health Check

Use this to verify both services, recent logs, and the canonical symlink path:

```bash
cd /home/wh33ls/Documents/Welcome\ Bot
./scripts/health_check_services.sh
```

Optional: show more logs by passing a line count:

```bash
./scripts/health_check_services.sh 50
```

## If Service Fails To Start

1. Check status:

```bash
sudo systemctl --no-pager --full status welcome-bot
```

2. Check recent logs:

```bash
sudo journalctl -u welcome-bot -n 200 --no-pager
```

3. If you edited the service file (`/etc/systemd/system/welcome-bot.service`), reload systemd:

```bash
sudo systemctl daemon-reload
sudo systemctl restart welcome-bot
```

## Optional: Manual Run (Debug)

If you want to run directly in shell for debugging:

```bash
cd /home/wh33ls/Documents/Welcome\ Bot
source .venv/bin/activate
python bot.py
```

Stop with `Ctrl+C`.
