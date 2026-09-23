# D2 Solo Bot Operations

This is a separate D2 Solo bot process for Seal Team Six solo/flawless dungeon posts only.

## Files

- Entry point: sts_bot.py
- Package: sts_solo_bot/
- Config data: sts_server_config.json

## Environment

Required:

- BUNGIE_API_KEY
- BUNGIE_CLAN_ID
- STS_DISCORD_TOKEN (recommended)

Fallback token:

- DISCORD_TOKEN (used only if STS_DISCORD_TOKEN is missing)

## Run Manually

```bash
cd /home/wh33ls/Documents/Welcome\ Bot
source .venv/bin/activate
python sts_bot.py
```

## Install As Service

```bash
cd /home/wh33ls/Documents/Welcome\ Bot
chmod +x scripts/install_sts_service.sh
./scripts/install_sts_service.sh
```

Useful service commands:

```bash
sudo systemctl --no-pager --full status sts-solo-bot
sudo systemctl restart sts-solo-bot
sudo journalctl -u sts-solo-bot -f
```

## Verified Ops Commands

Use these when you want to confirm exactly what is running and from where.

Check service status (shows `Main PID` and active state):

```bash
systemctl --no-pager status sts-solo-bot.service | head -n 40
```

Restart service after code/config changes:

```bash
sudo systemctl restart sts-solo-bot.service
systemctl --no-pager status sts-solo-bot.service | head -n 30
```

View recent startup logs after restart:

```bash
journalctl -u sts-solo-bot.service --since '2 minutes ago' --no-pager | tail -n 40
```

Confirm the service code path maps to this workspace:

```bash
ls -la /home/wh33ls/Documents/Welcome\ Bot
```

Expected result: the project files are present at `/home/wh33ls/Documents/Welcome Bot`.

## Slash Commands

- /set_dungeon_channel
- /set_dungeon_channel_by_id
- /unset_dungeon_channel

These commands are currently usable by any member in the allowed Seal Team Six servers.

Notes:
- `/set_dungeon_channel` sets announcements to the channel where the command is run (no picker list).
- `/set_dungeon_channel_by_id` accepts a channel ID, #mention, or channel name.

## Behavior

- Restricted to Seal Team Six guild IDs.
- Tracks the full configured Bungie clan roster (`STS_BUNGIE_CLAN_ID` when set, otherwise `BUNGIE_CLAN_ID`).
- Tracks only solo/flawless dungeon completions.
- Does not post team dungeon or team raid announcements.
