# Destiny 2 Clan Welcome Bot

A simple Discord bot that welcomes new members to your Destiny 2 clan server.

## Features
- Sends a welcome message to new members when they join the server.

## Which Bot Does What?

This repo runs two Discord bots as separate services:

- `welcome-bot` service: main community bot (`bot.py`) for welcome/rules/roles and broader clan features.
- `sts-solo-bot` service: D2 Solo bot (`sts_bot.py`) for solo/flawless dungeon announcements in allowed Seal Team Six servers.

Quick troubleshooting guide:

- If it is a welcome/rules/raid role issue, check `welcome-bot`.
- If it is a solo/flawless post issue, check `sts-solo-bot`.

## Setup
1. Clone this repository or copy the files to your server.
2. Run `npm install` to install dependencies.
3. Copy `.env.example` to `.env` and add your Discord bot token.
4. Run the bot with `node index.js`.

## Requirements
- Node.js v18 or higher
- A Discord bot token (create one at https://discord.com/developers/applications)

## Environment Variables
See `.env.example` for required variables.

## Usage
```
npm install
cp .env.example .env # Add your token to .env
node index.js
```

## Customization
You can edit the welcome message in `index.js`.
