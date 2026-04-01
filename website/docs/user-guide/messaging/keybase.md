---
sidebar_position: 12
title: "Keybase Setup"
description: "Chat with Hermes over Keybase — direct messages and team channels via the local keybase CLI"
---

# Keybase Setup

Hermes connects to Keybase via the local `keybase` CLI. No API tokens or external services are required — the adapter calls `keybase chat send` and `keybase chat api-listen` on the host machine using your existing authenticated session.

## Prerequisites

- Keybase CLI installed and logged in on the machine running Hermes
- A Keybase account

## Step 1: Install and log in to Keybase

```bash
# macOS
brew install --cask keybase

# Debian / Ubuntu
curl --remote-name https://prerelease.keybase.io/keybase_amd64.deb
sudo apt install ./keybase_amd64.deb
run_keybase

# Other Linux / Windows: https://keybase.io/download
```

```bash
# Log in
keybase login

# Verify your session
keybase whoami
```

:::info Headless servers
On a server without a GUI, start the Keybase daemon in background mode after login:
```bash
run_keybase -g
```
The daemon must be running for inbound message listening (`keybase chat api-listen`) to work.
:::

## Step 2: Configure Hermes

### Using the setup wizard

```bash
hermes gateway setup
```

Select **Keybase** from the platform list and follow the prompts.

### Manual configuration

Add to your `.env` file:

```bash
# Required to enable the adapter
KEYBASE_ENABLED=true

# Optional: restrict which Keybase users can interact with Hermes
KEYBASE_ALLOWED_USERS=alice,bob

# Optional: default delivery target for cron jobs and notifications
# Use a username for DMs or team#channel for team channels
KEYBASE_HOME_CHANNEL=myteam#general
KEYBASE_HOME_CHANNEL_NAME=Home

# Optional: override the keybase binary path (if not on $PATH)
KEYBASE_BIN=/usr/local/bin/keybase

# Optional: use a non-default Keybase profile/home directory
# (common on servers with separate service accounts)
KEYBASE_HOME=/home/joe/.keybase-prod

# Optional: force Keybase run mode (e.g., prod)
KEYBASE_RUN_MODE=prod
```

## Step 3: Start the gateway

```bash
hermes gateway          # foreground (useful for testing/debugging)
hermes gateway start    # background service (production)
```

## Step 4: Verify it works

Send yourself a Keybase DM and confirm Hermes replies. Replace `yourusername` with the account running Hermes (check with `keybase whoami`):

```bash
keybase chat send yourusername "hello hermes"
```

You should see a response appear in the Keybase app within a few seconds.

## Sending and receiving messages

Hermes listens for inbound messages using `keybase chat api-listen` and replies via `keybase chat send`. Both DMs and team channels are supported.

**Target formats for cron jobs and the send_message tool:**

| Target | Meaning |
|--------|---------|
| `keybase` | Home channel (requires `KEYBASE_HOME_CHANNEL`) |
| `keybase:alice` | DM to user `alice` |
| `keybase:myteam#general` | Team `myteam`, channel `#general` |

## Access control

Hermes applies a default-deny policy to inbound messages:

- `KEYBASE_ALLOWED_USERS=alice,bob` — only the listed usernames can interact
- `KEYBASE_ALLOW_ALL_USERS=true` — any Keybase user can interact (overrides the allowlist)
- Neither set — **all messages are rejected** (safe default; configure one of the above before use)

To open access to everyone:

```bash
KEYBASE_ALLOW_ALL_USERS=true
```

## Troubleshooting

| Problem | Solution |
|---------|---------|
| Gateway logs `keybase CLI not installed or user not logged in` | Run `keybase whoami` — if it fails, log in with `keybase login` |
| Messages not received (gateway starts but no inbound) | Keybase daemon may not be running — run `run_keybase -g` on the server |
| `keybase chat api-listen` hangs or exits immediately | Daemon is in a bad state — run `keybase ctl stop && run_keybase -g` |
| Send succeeds but message never arrives in team channel | Double-check the team name and channel (`myteam#general`, not `myteam/general`) |
| Custom binary path not found | Set `KEYBASE_BIN` to the absolute path of the keybase binary |
| `keybase whoami` works in your shell but Hermes still fails auth/listen | Hermes may be using a different profile. Set `KEYBASE_HOME` to your Keybase home (for example `/home/joe/.keybase-prod`) and optionally `KEYBASE_RUN_MODE=prod` |
| User messages ignored (no reply) | Check `KEYBASE_ALLOWED_USERS` — the sender's username must be listed, or set `KEYBASE_ALLOW_ALL_USERS=true` |

## Environment variables reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `KEYBASE_ENABLED` | Yes | — | Set to `true` to enable the Keybase adapter |
| `KEYBASE_BIN` | No | `keybase` | Path to the keybase CLI binary |
| `KEYBASE_HOME` | No | inherited `$HOME` | Keybase profile home dir Hermes should use (useful when shell and service users differ) |
| `KEYBASE_RUN_MODE` | No | Keybase default | Keybase run mode passed to subprocesses (for example `prod`) |
| `KEYBASE_ALLOWED_USERS` | No | — | Comma-separated Keybase usernames allowed to interact |
| `KEYBASE_ALLOW_ALL_USERS` | No | `false` | Allow any Keybase user without an allowlist |
| `KEYBASE_HOME_CHANNEL` | No | — | Default delivery target (`username` or `team#channel`) |
| `KEYBASE_HOME_CHANNEL_NAME` | No | `Home` | Display name for the home channel |
