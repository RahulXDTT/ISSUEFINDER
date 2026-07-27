# GitHub Issue Notifier — OSS Edition

> Retro terminal UI with animated Invertocat pixel-art logo.  
> Polls GitHub repos/orgs for labelled issues and emails you a notification.

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║          000000111111                                                        ║
║        22222000000022222                                                     ║
║       2222222222222222222                                                    ║
║       2220022222220022222   ← animated Invertocat                           ║
║       2222222222222222222      (digits cycle, brightness pulses)             ║
║       2222220000222222222                                                    ║
║        22222222222222222                                                     ║
║         22222222222222                                                       ║
║        20022222222002                                                        ║
║        20000222200002                                                        ║
║         020000000020                                                         ║
║          0222222220                                                          ║
║           000000000                                                          ║
║                                                                              ║
║   ██  ███████  ███████  ██    ██  ███████  ███    ██  ██████  ██████        ║
║   ██  ██       ██       ██    ██  ██       ██ ██  ██  ██      ██            ║
║   ██  ███████  ███████  ██    ██  █████    ██  ██ ██  █████   █████         ║
║   ██       ██       ██  ██    ██  ██       ██   ████  ██      ██            ║
║   ██  ███████  ███████   ██████   ███████  ██    ███  ███████ ██████        ║
║   ╚══════════════════════════════════════════════════════════════╝           ║
║                                                                              ║
║   ─── GitHub Issue Monitor ── v2.0 OSS ───                                   ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## Features

- **Retro terminal UI** — green-on-black, box-drawing characters, animated Invertocat logo
- **Animated Invertocat pixel-art** — GitHub Octocat silhouette built from digit blocks with independent brightness pulsing and color (blue circle + white cat)
- **Gradient pixel-art title** — "ISSUENOTIFIER" rendered in 8-bit block characters with cyan-to-blue gradient
- **Interactive credential setup** — prompts for tokens on first run, stores in `.env`
- **Polls GitHub repos & orgs** — configurable labels, filters, and intervals
- **HTML email notifications** — clean card-style emails via Gmail SMTP
- **State persistence** — never sends duplicate notifications
- **Rate limit handling** — exponential backoff with up to 4 retries
- **Live config reload** — changes apply on next poll cycle, no restart needed
- **Graceful exit** — `exit`, `quit`, or `q` commands with retro goodbye message
- **Full CLI** — 15 subcommands for managing repos, orgs, labels, and filters

---

## Requirements

- Python 3.10+
- A GitHub Personal Access Token
- A Gmail account with an App Password

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/ISSUEFINDER-OSS.git
cd ISSUEFINDER-OSS
pip install -r requirements.txt
```

### 2. Run (credentials prompted automatically)

```bash
python github_issue_notifier.py run
```

On first run, you'll be prompted for:

| Credential | Where to get it |
|---|---|
| `GITHUB_TOKEN` | [github.com/settings/tokens](https://github.com/settings/tokens) — classic token, `public_repo` scope |
| `GMAIL_USER` | Your Gmail address |
| `GMAIL_APP_PASSWORD` | Google Account → Security → [App Passwords](https://myaccount.google.com/apppasswords) |

Credentials are saved to a local `.env` file — never transmitted or logged.

### 3. Configure repos and labels

```bash
# Interactive wizard
python github_issue_notifier.py setup

# Or use individual commands
python github_issue_notifier.py add-repo facebook/react
python github_issue_notifier.py add-label "good first issue"
python github_issue_notifier.py set-interval 5
```

### 4. Start polling

```bash
python github_issue_notifier.py run
```

---

## CLI Reference

```
python github_issue_notifier.py <command> [args]
```

| Command | Description |
|---|---|
| `run` | Start polling (runs until Ctrl+C) |
| `setup` | Interactive configuration wizard |
| `list` | Show current config |
| `add-repo <owner/name>` | Add a repo to the watch list |
| `remove-repo <owner/name>` | Remove a repo from the watch list |
| `add-org <org>` | Watch all public repos in an organisation |
| `remove-org <org>` | Stop watching an organisation |
| `add-label "<label>"` | Add a trigger label |
| `remove-label "<label>"` | Stop tracking a label |
| `set-filter <key> <value>` | Adjust filters (state/assigned/prs) |
| `set-interval <minutes>` | Change the poll interval |
| `test-email` | Send a test email to verify SMTP setup |
| `exit` / `quit` / `q` | Exit gracefully with retro goodbye |

### Global flags

| Flag | Description |
|---|---|
| `--setup` | Prompt for credentials before running the command |

---

## Configuration Files

| File | Purpose |
|---|---|
| `.env` | Secrets (tokens, passwords) — auto-created on first run, never commit |
| `config.json` | Repos, labels, poll interval — auto-created, editable via CLI |
| `notifier_state.json` | Already-seen issue IDs — prevents duplicate emails |
| `notifier.log` | Rolling log of all activity |

---

## How It Works

1. Every N minutes the script fetches open issues for each watched repo + label via the GitHub REST API.
2. Issues whose ID is not in `notifier_state.json` are considered new.
3. For each new issue, an HTML email is sent via Gmail SMTP.
4. The issue ID is saved to state so it is never emailed again.

**Polling vs. Webhooks**: polling is used intentionally — you typically don't have admin access on third-party repos to register webhooks.

---

## Rate Limits

The GitHub REST API allows 5,000 requests/hour for authenticated requests.  
Polling 20 repos × 2 labels every 5 minutes ≈ 480 requests/hour — well within limits.

If the rate limit is hit, the script waits with exponential backoff (up to 4 retries).

---

## Retro UI

The terminal interface uses the [rich](https://github.com/Textualize/rich) library for:

- **Animated Invertocat pixel-art** — GitHub Octocat silhouette built from a grid of digit blocks, with deep blue for the circle background and bright white for the cat shape. Each block independently pulses its brightness and cycles its digit.
- **Gradient pixel-art title** — "ISSUENOTIFIER" rendered in 8-bit block characters with a smooth cyan-to-blue horizontal gradient and decorative box-drawing underline
- **Green-on-black theme** — classic terminal aesthetic with cyan/magenta accents
- **Styled panels and tables** — box-drawing characters throughout
- **Smooth animations** — 8 FPS refresh rate, runs for ~5 seconds on startup
- **Retro goodbye** — styled exit message with double-edge box borders

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `GITHUB_TOKEN not set` | Delete `.env` and re-run to re-enter credentials |
| `GMAIL_APP_PASSWORD not set` | Same — delete `.env` or run with `--setup` |
| Email not arriving | Check spam folder; verify App Password was generated for "Mail" |
| Duplicate emails after restart | Don't delete `notifier_state.json` unless you want a full re-scan |
| Animation not showing | Ensure your terminal supports ANSI colors; install `rich` |

---

## License

MIT — see [LICENSE](LICENSE).

---

## Contributing

Contributions welcome! Open an issue or submit a PR.

### Ideas for future versions

- [ ] Discord / Slack / Telegram notifications
- [ ] Streamlit web dashboard
- [ ] Docker + docker-compose deployment
- [ ] Digest mode — batch issues into one daily email
- [ ] `reset-repo <owner/name>` command to re-scan a single repo
- [ ] More retro themes (amber, CRT scanlines, etc.)
