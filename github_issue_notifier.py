#!/usr/bin/env python3
"""
GitHub Issue Notifier — Open Source Edition
Retro terminal UI with animated ASCII logo.

Polls GitHub repos and organisations for issues matching configured labels
and emails you a notification for each new match.
"""

import html
import json
import logging
import math
import os
import random
import sys
import time
import argparse
import getpass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

try:
    from dotenv import load_dotenv, set_key
except ImportError:
    print("ERROR: python-dotenv is required. Install: pip install python-dotenv")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.live import Live
    from rich.text import Text
    from rich.align import Align
    from rich.prompt import Prompt
    from rich.markup import escape
    from rich.console import Group
    from rich import box
    RICH = True
except ImportError:
    RICH = False

try:
    from github import Github, RateLimitExceededException, GithubException
except ImportError:
    print("ERROR: PyGithub is required. Install: pip install PyGithub")
    sys.exit(1)

try:
    import yagmail
except ImportError:
    print("ERROR: yagmail is required. Install: pip install yagmail")
    sys.exit(1)

load_dotenv()

# ─── Constants ────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
STATE_FILE  = BASE_DIR / "notifier_state.json"
LOG_FILE    = BASE_DIR / "notifier.log"
ENV_FILE    = BASE_DIR / ".env"

MAX_RETRIES          = 4
BACKOFF_BASE         = 60
ORG_REPO_LIMIT       = 30
INITIAL_LOOKBACK_HRS = 24

DEFAULT_CONFIG: dict = {
    "poll_interval_minutes": 5,
    "watched_repos":  [],
    "watched_orgs":   [],
    "trigger_labels": [],
    "filters": {
        "issue_state":      "open",
        "exclude_assigned": True,
        "exclude_prs":      True,
    },
}

# ─── Logging ──────────────────────────────────────────────────────────────────
def _setup_logging() -> logging.Logger:
    fmt = "%(asctime)s  %(levelname)-8s  %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )
    return logging.getLogger(__name__)

logger = _setup_logging()

# ─── Retro Theme ──────────────────────────────────────────────────────────────
if RICH:
    console = Console()
else:
    console = None

G  = "green"
G2 = "green3"
G3 = "green1"
CY = "cyan"
MG = "magenta"
YL = "yellow"
RD = "red"
DM = "bright_black"

BRIGHTNESS_GREEN = [
    "#001800", "#002800", "#003c00", "#005000",
    "#006800", "#008000", "#009800", "#00b400",
    "#00cc00", "#00ff00",
]

BRIGHTNESS_GREEN_LIGHT = [
    "#004000", "#005000", "#006000", "#007000",
    "#008000", "#009000", "#00a000", "#00b000",
    "#00c000", "#00e000",
]

# ─── Pixel Fonts ──────────────────────────────────────────────────────────────
_PX = {
    'A': ["010","101","111","101","101"],
    'B': ["110","101","110","101","110"],
    'C': ["011","100","100","100","011"],
    'D': ["110","101","101","101","110"],
    'E': ["111","100","110","100","111"],
    'F': ["111","100","110","100","100"],
    'G': ["011","100","101","101","011"],
    'H': ["101","101","111","101","101"],
    'I': ["111","010","010","010","111"],
    'J': ["001","001","001","101","010"],
    'K': ["101","110","100","110","101"],
    'L': ["100","100","100","100","111"],
    'M': ["101","111","111","101","101"],
    'N': ["101","111","111","111","101"],
    'O': ["010","101","101","101","010"],
    'P': ["110","101","110","100","100"],
    'Q': ["010","101","101","110","011"],
    'R': ["110","101","110","101","101"],
    'S': ["011","100","010","001","110"],
    'T': ["111","010","010","010","010"],
    'U': ["101","101","101","101","010"],
    'V': ["101","101","101","010","010"],
    'W': ["101","101","111","111","101"],
    'X': ["101","101","010","101","101"],
    'Y': ["101","101","010","010","010"],
    'Z': ["111","001","010","100","111"],
    ' ': ["000","000","000","000","000"],
    '-': ["000","000","111","000","000"],
    '.': ["000","000","000","000","010"],
}

_OCTOCAT_ASCII = """
         ██▄▄▄▄▄▄▄▄▄▄██
         ██████████████
        ▄██████████████▄
        ████████████████
        ███ ████████ ███
     ▀█████▄████████▄█████▀
     ▀▄▄  ▀██████████▀    ▀
       ▀█▄▄ ▄██████▄
        ▀▀██████████
           ▄█ ████ █▄
          ▄█▀▄████▄▀█▄
             ▀▀  ▀▀
"""

# ─── Animated Logo ────────────────────────────────────────────────────────────
def _render_logo_frame(tick):
    lines = _OCTOCAT_ASCII.strip().split('\n')
    
    # Find the maximum width of non-empty content
    max_width = max(len(line.rstrip()) for line in lines if line.strip())
    
    text = Text()
    for line_idx, line in enumerate(lines):
        # Strip and re-center each line
        stripped = line.strip()
        if stripped:
            # Calculate padding to center this line
            padding = (max_width - len(stripped)) // 2
            centered_line = ' ' * padding + stripped + ' ' * (max_width - len(stripped) - padding)
        else:
            centered_line = ' ' * max_width
        
        for i, ch in enumerate(centered_line):
            if ch == ' ':
                text.append(ch)
            else:
                phase = i * 0.3 + tick * 0.5
                bi = int((math.sin(phase) + 1) / 2 * 9)
                bi = max(0, min(9, bi))
                if ch in '█▀▄':
                    color = BRIGHTNESS_GREEN[bi]
                else:
                    color = BRIGHTNESS_GREEN_LIGHT[bi]
                text.append(ch, style=color)
        if line_idx < len(lines) - 1:
            text.append('\n')
    return text

def _render_title():
    text = Text()
    text.append("I S S U E N O T I F I E R", style=f"bold {G}")
    return text

def _render_banner(tick):
    logo = _render_logo_frame(tick)
    title = _render_title()
    
    result = Text()
    result.append('\n')
    result.append(logo)
    result.append('\n\n')
    result.append(title)
    result.append('\n\n')
    result.append("─── GitHub Issue Monitor ── v2.0 OSS ───", style=DM)
    result.append('\n')
    
    return Align.center(result, vertical="middle")

def show_animated_banner():
    if not RICH:
        print("\n" * 2)
        print("  ╔══════════════════════════════════════════╗")
        print("  ║                                          ║")
        print("  ║       ██████  ██   ██  ██████  ████      ║")
        print("  ║      ██       ██   ██  ██   █  █   █     ║")
        print("  ║      ██  ███  ███████  ██████  ████      ║")
        print("  ║      ██   ██  ██   ██  ██   █  █   █     ║")
        print("  ║       ██████  ██   ██  ██   █  █   █     ║")
        print("  ║                                          ║")
        print("  ║     I S S U E N O T I F I E R            ║")
        print("  ║          v2.0  OSS Edition               ║")
        print("  ║                                          ║")
        print("  ╚══════════════════════════════════════════╝")
        print()
        return

    try:
        with Live(console=console, refresh_per_second=8, transient=True) as live:
            for tick in range(40):
                live.update(_render_banner(tick))
                time.sleep(0.12)
    except KeyboardInterrupt:
        pass

# ─── Credential Management ────────────────────────────────────────────────────
def _ensure_env_file():
    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

def _prompt_credentials():
    if not RICH:
        print("\n=== CREDENTIAL SETUP ===\n")
        print("The following credentials are required.")
        print("They will be saved to .env in this directory.\n")
    else:
        console.print()
        console.print(Panel(
            "[bold]CREDENTIAL SETUP[/bold]\n\n"
            "Configure your API tokens and passwords.\n"
            "Values are stored locally in [bold].env[/bold] — never transmitted anywhere.\n"
            "You can delete [bold].env[/bold] at any time to reset.",
            title="[bold green]⚙  FIRST RUN[/bold green]",
            border_style=G, box=box.DOUBLE_EDGE,
        ))
        console.print()

    _ensure_env_file()

    if RICH:
        console.print(f"  [{CY}]▸ GitHub Personal Access Token[/{CY}]")
        console.print(f"    [{DM}]Classic token with 'public_repo' scope (or 'repo' for private repos)[/{DM}]")
        console.print(f"    [{DM}]Create: https://github.com/settings/tokens[/{DM}]")
        token = Prompt.ask(f"    [{G}]TOKEN[/{G}]", password=True)
    else:
        print("  GitHub Personal Access Token:")
        print("    Classic token with 'public_repo' scope.")
        print("    Create: https://github.com/settings/tokens")
        token = getpass.getpass("    TOKEN: ")

    if not token.strip():
        _cred_error("GitHub token")
    set_key(str(ENV_FILE), "GITHUB_TOKEN", token.strip())

    if RICH:
        console.print()
        console.print(f"  [{CY}]▸ Gmail Address[/{CY}]")
        console.print(f"    [{DM}]The Gmail account used to send notifications[/{DM}]")
        gmail = Prompt.ask(f"    [{G}]EMAIL[/{G}]")
    else:
        print("\n  Gmail Address:")
        print("    The account used to send notifications.")
        gmail = input("    EMAIL: ")

    if not gmail.strip():
        _cred_error("Gmail address")
    set_key(str(ENV_FILE), "GMAIL_USER", gmail.strip())

    if RICH:
        console.print()
        console.print(f"  [{CY}]▸ Gmail App Password[/{CY}]")
        console.print(f"    [{DM}]NOT your normal password — generate an App Password:[/{DM}]")
        console.print(f"    [{DM}]Google Account → Security → 2-Step Verification → App Passwords[/{DM}]")
        console.print(f"    [{DM}]https://myaccount.google.com/apppasswords[/{DM}]")
        app_pw = Prompt.ask(f"    [{G}]APP PASSWORD[/{G}]", password=True)
    else:
        print("\n  Gmail App Password:")
        print("    NOT your normal password. Generate at:")
        print("    https://myaccount.google.com/apppasswords")
        app_pw = getpass.getpass("    APP PASSWORD: ")

    if not app_pw.strip():
        _cred_error("Gmail App Password")
    set_key(str(ENV_FILE), "GMAIL_APP_PASSWORD", app_pw.strip())

    load_dotenv(override=True)

    if RICH:
        console.print()
        console.print(f"  [{G}]✓  Credentials saved to .env[/{G}]")
        console.print()
    else:
        print("\n  ✓  Credentials saved to .env\n")

def _cred_error(name):
    if RICH:
        console.print(f"\n  [{RD}]✗  {name} cannot be empty.[/{RD}]\n")
    else:
        print(f"\n  ✗  {name} cannot be empty.\n")
    sys.exit(1)

def ensure_credentials():
    _ensure_env_file()
    missing = []
    if not os.getenv("GITHUB_TOKEN", "").strip():
        missing.append("GITHUB_TOKEN")
    if not os.getenv("GMAIL_USER", "").strip():
        missing.append("GMAIL_USER")
    if not os.getenv("GMAIL_APP_PASSWORD", "").strip():
        missing.append("GMAIL_APP_PASSWORD")
    if missing:
        _prompt_credentials()

# ─── Retro UI Helpers ─────────────────────────────────────────────────────────
def print_header():
    if not RICH:
        return
    console.clear()
    show_animated_banner()

def print_menu():
    if not RICH:
        print("\nCommands: run, cli, setup, list, add-repo, remove-repo, add-org,")
        print("  remove-org, add-label, remove-label, set-filter, set-interval,")
        print("  test-email, exit/quit/q")
        print("  Use --help for details.\n")
        return

    table = Table(
        show_header=True,
        header_style=f"bold {G}",
        border_style=DM,
        box=box.ROUNDED,
        title=f"[{G}]COMMANDS[/{G}]",
        title_style=f"bold {G}",
    )
    table.add_column("Command", style=G2, width=18)
    table.add_column("Description", style="white")

    cmds = [
        ("run",            "Start polling (Ctrl+C to stop)"),
        ("cli",            "Interactive CLI mode — type commands directly"),
        ("setup",          "Interactive wizard (checkboxes • trending • sorted)"),
        ("list",           "Show current configuration"),
        ("add-repo",       "Watch a specific repository"),
        ("remove-repo",    "Stop watching a repository"),
        ("add-org",        "Watch all repos in an organisation"),
        ("remove-org",     "Stop watching an organisation"),
        ("add-label",      "Add a trigger label"),
        ("remove-label",   "Remove a trigger label"),
        ("set-filter",     "Adjust issue filters"),
        ("set-interval",   "Change poll interval (minutes)"),
        ("test-email",     "Send a test email"),
        ("exit / quit / q","Exit gracefully"),
    ]
    for cmd, desc in cmds:
        table.add_row(f"[bold]{cmd}[/bold]", desc)

    console.print(table)
    console.print()

def print_status(msg, style=G):
    if RICH:
        console.print(f"  [{style}]▸[/{style}] {msg}")
    else:
        print(f"  ▸ {msg}")

def print_error(msg):
    if RICH:
        console.print(f"  [{RD}]✗[/{RD}] {msg}", style=RD)
    else:
        print(f"  ✗ {msg}")

def print_success(msg):
    if RICH:
        console.print(f"  [{G}]✓[/{G}] {msg}")
    else:
        print(f"  ✓ {msg}")

def print_section(title):
    if RICH:
        console.print()
        console.print(f"  [{CY}]─── {title} ───[/{CY}]")
        console.print()
    else:
        print(f"\n  ─── {title} ───\n")

def print_config(config):
    if not RICH:
        print("\n─── Watched Repos ────────────────────────────")
        for r in config["watched_repos"]:
            print(f"  {r}")
        if not config["watched_repos"]:
            print("  (none)")
        print("\n─── Watched Orgs ─────────────────────────────")
        for o in config.get("watched_orgs", []):
            print(f"  {o}")
        if not config.get("watched_orgs"):
            print("  (none)")
        print("\n─── Trigger Labels ───────────────────────────")
        for lbl in config["trigger_labels"]:
            print(f"  {lbl}")
        if not config["trigger_labels"]:
            print("  (none)")
        f = config.get("filters", {})
        print(f"\n─── Issue Filters ────────────────────────────")
        print(f"  state            : {f.get('issue_state', 'open')}")
        print(f"  exclude_assigned : {f.get('exclude_assigned', True)}")
        print(f"  exclude_prs      : {f.get('exclude_prs', True)}")
        print(f"\n  Poll interval    : {config['poll_interval_minutes']} min")
        print(f"  Config file      : {CONFIG_FILE}")
        print(f"  State file       : {STATE_FILE}\n")
        return

    f = config.get("filters", {})
    table = Table(show_header=False, border_style=DM, box=box.ROUNDED)
    table.add_column("Key", style=CY, width=20)
    table.add_column("Value", style="white")

    repos = ", ".join(config["watched_repos"]) if config["watched_repos"] else "(none)"
    orgs = ", ".join(config.get("watched_orgs", [])) if config.get("watched_orgs") else "(none)"
    labels = ", ".join(config["trigger_labels"]) if config["trigger_labels"] else "(none)"

    table.add_row("Watched Repos", repos)
    table.add_row("Watched Orgs", orgs)
    table.add_row("Trigger Labels", labels)
    table.add_row("State Filter", f.get("issue_state", "open"))
    table.add_row("Exclude Assigned", str(f.get("exclude_assigned", True)))
    table.add_row("Exclude PRs", str(f.get("exclude_prs", True)))
    table.add_row("Poll Interval", f"{config['poll_interval_minutes']} min")

    console.print(Panel(
        table,
        title=f"[bold {G}]CURRENT CONFIGURATION[/bold {G}]",
        border_style=G, box=box.DOUBLE_EDGE,
    ))
    console.print()

# ─── Config ───────────────────────────────────────────────────────────────────
def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        for key, val in DEFAULT_CONFIG.items():
            cfg.setdefault(key, val)
        cfg.setdefault("filters", {})
        for k, v in DEFAULT_CONFIG["filters"].items():
            cfg["filters"].setdefault(k, v)
        return cfg
    save_config(DEFAULT_CONFIG.copy())
    return DEFAULT_CONFIG.copy()

def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

# ─── State ────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        return {"notified": {k: set(v) for k, v in raw.get("notified", {}).items()}}
    return {"notified": {}}

def save_state(state: dict) -> None:
    serializable = {"notified": {k: list(v) for k, v in state["notified"].items()}}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)

# ─── Client Builders ─────────────────────────────────────────────────────────
def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        print_error(f"Required environment variable {name} is not set.")
        print_status(f"Run with [bold]--setup[/bold] or delete .env to reconfigure.")
        sys.exit(1)
    return value

def build_github_client() -> Github:
    token = _require_env("GITHUB_TOKEN")
    return Github(token)

def build_mailer() -> tuple:
    user     = _require_env("GMAIL_USER")
    password = _require_env("GMAIL_APP_PASSWORD")
    return yagmail.SMTP(user, password), user

# ─── Issue Filtering ─────────────────────────────────────────────────────────
def passes_filters(issue, filters: dict, first_scan: bool) -> bool:
    if filters.get("exclude_prs", True) and issue.pull_request is not None:
        return False
    if filters.get("exclude_assigned", True) and issue.assignee is not None:
        return False
    if first_scan:
        cutoff  = datetime.now(timezone.utc) - timedelta(hours=INITIAL_LOOKBACK_HRS)
        created = issue.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < cutoff:
            return False
    return True

# ─── Email ────────────────────────────────────────────────────────────────────
_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<style>
  body  {{font-family:Arial,sans-serif;background:#f5f5f5;padding:20px;margin:0}}
  .card {{background:#fff;border-radius:8px;padding:28px;max-width:740px;
          margin:auto;box-shadow:0 2px 10px rgba(0,0,0,.12)}}
  h2    {{color:#24292e;margin-top:0;font-size:20px}}
  .meta {{color:#586069;font-size:14px;margin:4px 0}}
  .meta b {{color:#24292e}}
  a.btn {{display:inline-block;background:#2ea44f;color:#fff!important;
          padding:10px 22px;border-radius:6px;text-decoration:none;
          font-weight:bold;margin-top:12px;font-size:14px}}
  .badge{{display:inline-block;background:#0075ca;color:#fff;border-radius:12px;
           padding:2px 10px;font-size:12px;margin:0 4px 4px 0}}
  .preview{{background:#f6f8fa;border:1px solid #e1e4e8;border-radius:6px;
             padding:14px;font-size:13px;white-space:pre-wrap;
             word-break:break-word;margin-top:16px;color:#24292e}}
  .footer{{color:#6a737d;font-size:12px;margin-top:24px;
           border-top:1px solid #e1e4e8;padding-top:12px}}
</style>
</head>
<body>
<div class="card">
  <h2>&#x1F195; New Issue — {repo}</h2>
  <p class="meta"><b>Title:</b> {title}</p>
  <p class="meta"><b>Author:</b> <a href="https://github.com/{author}">{author}</a></p>
  <p class="meta"><b>Created:</b> {created}</p>
  <p class="meta"><b>Labels:</b> {labels_html}</p>
  <a class="btn" href="{url}">Open Issue on GitHub &rarr;</a>
  <div class="preview">{body_preview}</div>
  <p class="footer">Detected at {timestamp} &middot; GitHub Issue Notifier OSS</p>
</div>
</body>
</html>
"""

def send_email(issue, mailer, recipient: str) -> None:
    label_names = [lbl.name for lbl in issue.labels]
    labels_html = "".join(
        f'<span class="badge">{html.escape(ln)}</span>' for ln in label_names
    ) or "<em>none</em>"

    body_text    = (issue.body or "").strip()
    body_preview = html.escape(body_text[:700]) + ("…" if len(body_text) > 700 else "")

    subject    = f"\U0001F195 [{issue.repository.full_name}] {issue.title[:80]}"
    email_body = _HTML_TEMPLATE.format(
        repo         = html.escape(issue.repository.full_name),
        title        = html.escape(issue.title),
        author       = html.escape(issue.user.login),
        created      = issue.created_at.strftime("%Y-%m-%d %H:%M UTC"),
        labels_html  = labels_html,
        url          = issue.html_url,
        body_preview = body_preview,
        timestamp    = datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    try:
        mailer.send(to=recipient, subject=subject, contents=email_body)
        logger.info("    Email sent for issue #%s", issue.number)
        print_success(f"Email sent for issue #{issue.number}")
    except Exception as exc:
        logger.error("    Email failed for issue #%s: %s", issue.number, exc)
        print_error(f"Email failed for issue #{issue.number}: {exc}")

# ─── Core: Check One Repo ────────────────────────────────────────────────────
def check_repo(
    repo_full_name: str,
    state: dict,
    config: dict,
    g: Github,
    mailer,
    recipient: str,
) -> None:
    filters     = config.get("filters", DEFAULT_CONFIG["filters"])
    issue_state = filters.get("issue_state", "open")

    first_scan = repo_full_name not in state["notified"]
    state["notified"].setdefault(repo_full_name, set())

    try:
        repo = g.get_repo(repo_full_name)
    except GithubException as exc:
        logger.error("  Cannot access %s: %s", repo_full_name, exc)
        print_error(f"Cannot access {repo_full_name}: {exc}")
        return

    for label_name in config["trigger_labels"]:
        retries = 0
        while retries <= MAX_RETRIES:
            try:
                issues = repo.get_issues(state=issue_state, labels=[label_name])
                for issue in issues:
                    if issue.id in state["notified"][repo_full_name]:
                        continue
                    if not passes_filters(issue, filters, first_scan):
                        state["notified"][repo_full_name].add(issue.id)
                        continue
                    logger.info("  New issue: %s", issue.html_url)
                    print_status(f"New issue: {issue.html_url}", CY)
                    send_email(issue, mailer, recipient)
                    state["notified"][repo_full_name].add(issue.id)
                    save_state(state)
                break

            except RateLimitExceededException:
                wait = BACKOFF_BASE * (2 ** retries)
                logger.warning(
                    "  Rate limit hit — waiting %ss (retry %s/%s).",
                    wait, retries + 1, MAX_RETRIES,
                )
                print_status(f"Rate limit — waiting {wait}s (retry {retries+1}/{MAX_RETRIES})", YL)
                time.sleep(wait)
                retries += 1

            except GithubException as exc:
                logger.error(
                    "  GitHub error on %s / label '%s': %s",
                    repo_full_name, label_name, exc,
                )
                print_error(f"GitHub error on {repo_full_name} / '{label_name}': {exc}")
                break

# ─── Core: Expand Org → Repo List ────────────────────────────────────────────
def repos_for_org(org_name: str, g: Github) -> list:
    try:
        org    = g.get_organization(org_name)
        result = []
        for repo in org.get_repos(type="public", sort="pushed", direction="desc"):
            if repo.archived or repo.disabled:
                continue
            result.append(repo.full_name)
            if len(result) >= ORG_REPO_LIMIT:
                break
        return result
    except GithubException as exc:
        logger.error("  Cannot expand org '%s': %s", org_name, exc)
        print_error(f"Cannot expand org '{org_name}': {exc}")
        return []

# ─── Core: Polling Loop ──────────────────────────────────────────────────────
def run_notifier() -> None:
    state  = load_state()
    g      = build_github_client()
    mailer, recipient = build_mailer()

    logger.info("═══ GitHub Issue Notifier started ═══")
    print_section("POLLING STARTED")

    try:
        while True:
            config     = load_config()
            all_repos  = list(config["watched_repos"])

            for org in config.get("watched_orgs", []):
                logger.info("── Expanding org: %s (up to %d repos) ──", org, ORG_REPO_LIMIT)
                print_status(f"Expanding org: {org} (up to {ORG_REPO_LIMIT} repos)", CY)
                all_repos.extend(repos_for_org(org, g))

            seen       = set()
            unique_repos = [r for r in all_repos if not (r in seen or seen.add(r))]

            logger.info(
                "── Poll at %s  (%d repos total, %d labels) ──",
                datetime.now().strftime("%H:%M:%S"),
                len(unique_repos),
                len(config["trigger_labels"]),
            )
            print_status(
                f"Poll at {datetime.now().strftime('%H:%M:%S')} — "
                f"{len(unique_repos)} repos, {len(config['trigger_labels'])} labels",
                G,
            )

            for repo_name in unique_repos:
                logger.info("  Checking %s …", repo_name)
                print_status(f"Checking {repo_name} …", DM)
                check_repo(repo_name, state, config, g, mailer, recipient)

            logger.info("Sleeping %d min …\n", config["poll_interval_minutes"])
            print_status(f"Sleeping {config['poll_interval_minutes']} min …", DM)
            time.sleep(config["poll_interval_minutes"] * 60)

    except KeyboardInterrupt:
        logger.info("Notifier stopped by user (Ctrl+C).")
        save_state(state)
        show_goodbye()
        sys.exit(0)

# ─── CLI Commands ─────────────────────────────────────────────────────────────
def cmd_run(_args) -> None:
    config = load_config()
    
    if not config["watched_repos"] and not config.get("watched_orgs"):
        print_section("NO REPOS CONFIGURED")
        if RICH:
            console.print(f"  [{YL}]⚠[/{YL}]  You haven't configured any repositories or organizations to watch.")
            console.print(f"  [{DM}]  Run the setup wizard to add repos and labels:[/{DM}]")
            console.print(f"  [{G}]  python github_issue_notifier.py setup[/{G}]")
            console.print()
            console.print(f"  [{DM}]  Or add repos manually:[/{DM}]")
            console.print(f"  [{G}]  python github_issue_notifier.py add-repo owner/name[/{G}]")
            console.print()
        else:
            print("  ⚠  You haven't configured any repositories or organizations to watch.")
            print("  Run the setup wizard to add repos and labels:")
            print("  python github_issue_notifier.py setup")
            print()
            print("  Or add repos manually:")
            print("  python github_issue_notifier.py add-repo owner/name")
            print()
        
        if _yn("Run setup wizard now?", default=True):
            cmd_setup(None)
            return
        else:
            print_status("Exiting. Configure repos and try again.", YL)
            sys.exit(0)
    
    if not config["trigger_labels"]:
        print_section("NO LABELS CONFIGURED")
        if RICH:
            console.print(f"  [{YL}]⚠[/{YL}]  You haven't configured any trigger labels.")
            console.print(f"  [{DM}]  Without labels, no issues will match. Run setup to add labels.[/{DM}]")
            console.print()
        else:
            print("  ⚠  You haven't configured any trigger labels.")
            print("  Without labels, no issues will match. Run setup to add labels.")
            print()
        
        if _yn("Run setup wizard now?", default=True):
            cmd_setup(None)
            return
        else:
            print_status("Exiting. Configure labels and try again.", YL)
            sys.exit(0)
    
    run_notifier()

def cmd_list(_args) -> None:
    config = load_config()
    print_config(config)

def cmd_add_repo(args) -> None:
    repo = args.repo.strip()
    if "/" not in repo or repo.count("/") != 1:
        print_error("Repo must be 'owner/name' format, e.g. microsoft/vscode")
        sys.exit(1)
    config = load_config()
    if repo in config["watched_repos"]:
        print_status(f"'{repo}' is already in the watch list.", YL)
        return
    config["watched_repos"].append(repo)
    save_config(config)
    print_success(f"Added repo '{repo}'.")

def cmd_remove_repo(args) -> None:
    repo = args.repo.strip()
    config = load_config()
    if repo not in config["watched_repos"]:
        print_status(f"'{repo}' is not in the watch list.", YL)
        return
    config["watched_repos"].remove(repo)
    save_config(config)
    print_success(f"Removed repo '{repo}'.")

def cmd_add_org(args) -> None:
    org = args.org.strip()
    config = load_config()
    config.setdefault("watched_orgs", [])
    if org in config["watched_orgs"]:
        print_status(f"Org '{org}' is already being watched.", YL)
        return
    config["watched_orgs"].append(org)
    save_config(config)
    print_success(
        f"Added org '{org}'. Next poll will scan up to {ORG_REPO_LIMIT} repos."
    )

def cmd_remove_org(args) -> None:
    org = args.org.strip()
    config = load_config()
    if org not in config.get("watched_orgs", []):
        print_status(f"Org '{org}' is not in the watch list.", YL)
        return
    config["watched_orgs"].remove(org)
    save_config(config)
    print_success(f"Removed org '{org}'.")

def cmd_add_label(args) -> None:
    label = args.label.strip().lower()
    config = load_config()
    if label in config["trigger_labels"]:
        print_status(f"Label '{label}' is already tracked.", YL)
        return
    config["trigger_labels"].append(label)
    save_config(config)
    print_success(f"Added label '{label}'.")

def cmd_remove_label(args) -> None:
    label = args.label.strip().lower()
    config = load_config()
    if label not in config["trigger_labels"]:
        print_status(f"Label '{label}' is not being tracked.", YL)
        return
    config["trigger_labels"].remove(label)
    save_config(config)
    print_success(f"Removed label '{label}'.")

_FILTER_HELP = {
    "state":    "Issue state: 'open', 'closed', or 'all'",
    "assigned": "Exclude assigned issues: 'true' or 'false'",
    "prs":      "Exclude pull-requests: 'true' or 'false'",
}

def cmd_set_filter(args) -> None:
    key   = args.filter_key.strip().lower()
    value = args.value.strip().lower()
    config = load_config()
    config.setdefault("filters", DEFAULT_CONFIG["filters"].copy())

    if key == "state":
        if value not in ("open", "closed", "all"):
            print_error("State must be 'open', 'closed', or 'all'.")
            sys.exit(1)
        config["filters"]["issue_state"] = value
        print_success(f"Issue state set to '{value}'.")
    elif key == "assigned":
        if value not in ("true", "false"):
            print_error("Value must be 'true' or 'false'.")
            sys.exit(1)
        config["filters"]["exclude_assigned"] = (value == "true")
        print_success(f"exclude_assigned set to {value}.")
    elif key == "prs":
        if value not in ("true", "false"):
            print_error("Value must be 'true' or 'false'.")
            sys.exit(1)
        config["filters"]["exclude_prs"] = (value == "true")
        print_success(f"exclude_prs set to {value}.")
    else:
        print_error(f"Unknown filter key '{key}'. Available:")
        for k, desc in _FILTER_HELP.items():
            print_status(f"{k:12s}  {desc}")
        sys.exit(1)

    save_config(config)

def cmd_set_interval(args) -> None:
    try:
        minutes = int(args.minutes)
        if minutes < 1:
            raise ValueError("must be >= 1")
    except ValueError as exc:
        print_error(f"Interval must be a positive integer. {exc}")
        sys.exit(1)
    config = load_config()
    config["poll_interval_minutes"] = minutes
    save_config(config)
    print_success(f"Poll interval set to {minutes} minute(s).")

def cmd_test_email(_args) -> None:
    mailer, recipient = build_mailer()
    try:
        mailer.send(
            to=recipient,
            subject="GitHub Issue Notifier — connection test",
            contents=(
                "<h2>It works!</h2>"
                "<p>Your email setup is configured correctly. "
                "The notifier will send issue alerts to this address.</p>"
            ),
        )
        print_success(f"Test email sent to {recipient}.")
    except Exception as exc:
        print_error(f"Failed to send test email: {exc}")
        sys.exit(1)

def show_goodbye():
    if RICH:
        console.print()
        console.print(Panel(
            f"[{G}]Session terminated.[/{G}]\n"
            f"[{DM}]State saved. Until next time, operator.[/{DM}]",
            title=f"[bold {CY}]GOODBYE[/bold {CY}]",
            subtitle=f"[{DM}]ISSUENOTIFIER v2.0 OSS[/{DM}]",
            border_style=G, box=box.DOUBLE_EDGE,
            width=50,
        ))
        console.print()
    else:
        print()
        print("  ╔══════════════════════════════════════════╗")
        print("  ║            GOODBYE, OPERATOR             ║")
        print("  ║       State saved. Until next time.      ║")
        print("  ║         ISSUENOTIFIER v2.0 OSS           ║")
        print("  ╚══════════════════════════════════════════╝")
        print()

def cmd_exit(_args) -> None:
    try:
        state = load_state()
        save_state(state)
    except Exception:
        pass
    show_goodbye()
    sys.exit(0)

def cmd_cli(_args) -> None:
    ensure_credentials()
    
    if RICH:
        console.print()
        console.print(f"  [{G}]▸[/{G}] Interactive CLI mode. Type commands directly.")
        console.print(f"  [{DM}]  Type 'help' for available commands, 'exit' to quit.[/{DM}]")
        console.print()
    else:
        print()
        print("  ▸ Interactive CLI mode. Type commands directly.")
        print("    Type 'help' for available commands, 'exit' to quit.")
        print()
    
    while True:
        try:
            if RICH:
                user_input = Prompt.ask(f"[{G}]notifier[/{G}]")
            else:
                user_input = input("notifier> ")
            
            user_input = user_input.strip()
            
            if not user_input:
                continue
            
            if user_input.lower() in ("help", "h", "?"):
                print_menu()
                continue
            
            if user_input.lower() in ("exit", "quit", "q"):
                cmd_exit(None)
            
            parts = user_input.split()
            cmd_name = parts[0]
            cmd_args = parts[1:] if len(parts) > 1 else []
            
            if cmd_name not in _COMMAND_MAP:
                print_error(f"Unknown command: {cmd_name}")
                print_status("Type 'help' for available commands.", DM)
                continue
            
            if cmd_name == "cli":
                print_status("Already in CLI mode.", YL)
                continue
            
            try:
                parser = _build_parser()
                args = parser.parse_args([cmd_name] + cmd_args)
                _COMMAND_MAP[cmd_name](args)
            except SystemExit:
                pass
            except Exception as exc:
                print_error(f"Error: {exc}")
        
        except KeyboardInterrupt:
            print()
            show_goodbye()
            sys.exit(0)
        except EOFError:
            print()
            show_goodbye()
            sys.exit(0)

# ─── Interactive Setup Wizard ────────────────────────────────────────────────
def _pick(prompt: str, options: list, multi: bool = False) -> list:
    print(f"\n{prompt}")
    for i, opt in enumerate(options, 1):
        print(f"  {i:>3}.  {opt}")
    hint = "(comma-separated numbers)" if multi else "(enter a number)"
    while True:
        raw = input(f"  Your choice {hint}: ").strip()
        try:
            picks = [int(x.strip()) for x in raw.split(",") if x.strip()]
            if not picks:
                raise ValueError
            if any(p < 1 or p > len(options) for p in picks):
                raise ValueError
            return [p - 1 for p in picks]
        except ValueError:
            print(f"  Please enter valid number(s) between 1 and {len(options)}.")

def _yn(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"  {prompt} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw.startswith("y")

# ─── Interactive Checkbox / Radio Widget ─────────────────────────────────────
_SPARK = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

def _read_key():
    """Read a single keypress without waiting for Enter. Returns a short name.

    Falls back to None if stdin isn't a TTY (caller should fall back to input()).
    """
    try:
        if not sys.stdin.isatty():
            return None
    except Exception:
        return None

    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        try:
            import msvcrt
            ch = msvcrt.getwch()
        except Exception:
            return None

    if ch == "\x1b":
        # escape sequence — read the rest
        seq = ""
        try:
            import termios
            import tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                while len(seq) < 2:
                    nxt = sys.stdin.read(1)
                    if not nxt:
                        break
                    seq += nxt
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            seq = ""
        full = ch + seq
        return {
            "\x1b[A": "up", "\x1b[B": "down",
            "\x1b[C": "right", "\x1b[D": "left",
            "\r": "enter", "\n": "enter",
        }.get(full, "esc")
    return {
        "\r": "enter", "\n": "enter",
        " ": "space", "\t": "tab",
        "\x7f": "backspace", "\x08": "backspace",
        "\x03": "ctrl-c", "\x04": "ctrl-d",
        "q": "q", "Q": "q", "a": "a", "A": "a",
        "n": "n", "N": "n", "p": "p", "P": "p",
    }.get(ch, ch)


def _interactive_select(title, options, multi=True, allow_select_all=True,
                        page_size=10):
    """Arrow-key driven checkbox / radio picker.

    multi=True  -> checkboxes, Space toggles, returns list[int] of chosen indices.
    multi=False -> radio, Enter on the highlighted option returns its index.

    Redraws in place using rich.live.Live (no stacking on each keypress).
    Falls back to _pick() (numbered input) if the terminal can't read raw keys.
    """
    n = len(options)
    if n == 0:
        return [] if multi else -1

    use_fancy = bool(RICH)
    try:
        use_fancy = use_fancy and sys.stdin.isatty()
    except Exception:
        use_fancy = False
    if not use_fancy:
        if multi:
            return _pick(title, options, multi=True)
        idx = _pick(title, options, multi=False)
        return idx[0] if idx else -1

    SELECT_ALL = -1
    total_pages = max(1, (n + page_size - 1) // page_size)
    page = 0
    cursor = SELECT_ALL if (multi and allow_select_all) else 0
    selected = set()

    def page_count():
        return len(options[page * page_size:page * page_size + page_size])

    def render():
        heads = Text()
        heads.append(f"\n{title}\n", style=f"bold {G}")
        hint = ("[↑/↓] move  [Space] toggle  [A] all  "
                "[N]ext page  [P]rev page  [Enter] confirm  [Q] cancel"
                if multi else
                "[↑/↓] move  [Enter] select  [N]ext page  [P]rev page  [Q] cancel")
        heads.append(f"  {hint}\n", style=DM)

        start = page * page_size
        page_opts = options[start:start + page_size]

        table = Table(show_header=False, border_style=DM, box=box.ROUNDED,
                      expand=True)
        table.add_column("", width=2)
        table.add_column("Sel", width=3)
        table.add_column("Option", style="white", ratio=4)

        if multi and allow_select_all:
            mk = "☒" if len(selected) == n else "☐"
            cur = (cursor == SELECT_ALL)
            t = "▶" if cur else " "
            st = G if cur else "white"
            table.add_row(t, f"[{st}]{mk}[/{st}]",
                          f"[bold {G}][SELECT ALL][/bold {G}]")

        for i, opt in enumerate(page_opts):
            abs_idx = start + i
            mk = "☒" if (multi and abs_idx in selected) else "☐"
            cur = (cursor == i)
            t = "▶" if cur else " "
            st = G if cur else "white"
            table.add_row(t, f"[{st}]{mk}[/{st}]",
                          f"[{st}]{escape(str(opt))}[/{st}]")

        foot = Text(f"\n  Page {page + 1}/{total_pages}  —  "
                    f"{len(selected)}/{n} selected\n", style=DM)
        return Group(heads, table, foot)

    with Live(render(), console=console, refresh_per_second=30,
              transient=True, screen=False) as live:
        while True:
            key = _read_key()
            if key is None:
                key = "enter"

            if key in ("q", "esc", "ctrl-c", "ctrl-d"):
                console.print(f"\n  [{YL}]Selection cancelled.[/{YL}]")
                return [] if multi else -1

            if key == "up":
                lower = SELECT_ALL if (multi and allow_select_all) else 0
                cursor -= 1
                if cursor < lower:
                    page = (page - 1) % total_pages
                    cursor = page_count() - 1
            elif key == "down":
                if cursor >= page_count() - 1:
                    page = (page + 1) % total_pages
                    cursor = SELECT_ALL if (multi and allow_select_all) else 0
                else:
                    cursor += 1
            elif key in ("n", "right"):
                page = (page + 1) % total_pages
                cursor = SELECT_ALL if (multi and allow_select_all) else 0
            elif key in ("p", "left"):
                page = (page - 1) % total_pages
                cursor = SELECT_ALL if (multi and allow_select_all) else 0
            elif key == "space":
                if not multi:
                    if 0 <= cursor < page_count():
                        return page * page_size + cursor
                else:
                    if cursor == SELECT_ALL:
                        selected = set() if len(selected) == n else set(range(n))
                    else:
                        abs_idx = page * page_size + cursor
                        selected.discard(abs_idx) if abs_idx in selected else selected.add(abs_idx)
            elif key == "a" and multi:
                selected = set() if len(selected) == n else set(range(n))
            elif key == "enter":
                if not multi:
                    if 0 <= cursor < page_count():
                        return page * page_size + cursor
                else:
                    return sorted(selected)

            live.update(render())


# ─── Repo Activity Sparkline ──────────────────────────────────────────────────
def _repo_sparkline(repo, bins=12) -> str:
    """Build a small activity sparkline from recent issues/PRs (last `bins` weeks)."""
    try:
        counts = [0] * bins
        now = datetime.now(timezone.utc)
        # get_issues covers issues AND PRs in PyGithub for repos; fetch last ~100
        items = repo.get_issues(state="all", sort="created", direction="desc")
        seen = 0
        for it in items:
            seen += 1
            if seen > 100:
                break
            c = it.created_at
            if c.tzinfo is None:
                c = c.replace(tzinfo=timezone.utc)
            weeks_ago = int((now - c).days // 7)
            if 0 <= weeks_ago < bins:
                counts[bins - 1 - weeks_ago] += 1
        mx = max(counts) if counts else 0
        if mx == 0:
            return "".join(_SPARK[0] for _ in range(bins))
        return "".join(_SPARK[min(len(_SPARK) - 1, int(c / mx * (len(_SPARK) - 1)))]
                        for c in counts)
    except Exception:
        return "".join(_SPARK[0] for _ in range(bins))


# ─── Repo Browser (sorted + paginated + trend + checkboxes) ───────────────────
REPO_PAGE_SIZE = 8
_REPO_TREND_CACHE = {}

def _fetch_org_repos_sorted(g, org_name, cap=100):
    """Fetch up to `cap` public non-archived repos for an org, sorted by
    stars desc then open-issues desc."""
    org = g.get_organization(org_name)
    repos = []
    for r in org.get_repos(type="public", sort="pushed", direction="desc"):
        if r.archived or r.disabled:
            continue
        repos.append(r)
        if len(repos) >= cap:
            break
    repos.sort(key=lambda r: (r.stargazers_count, r.open_issues_count), reverse=True)
    return repos


def _compute_sparklines(repos):
    """Compute a 12-week activity sparkline per repo in parallel.
    Returns dict[int idx -> str spark]."""
    out = {}
    if not repos:
        return out

    def work(i):
        return i, _repo_sparkline(repos[i])

    try:
        with ThreadPoolExecutor(max_workers=min(10, len(repos))) as ex:
            for i, spark in ex.map(work, range(len(repos))):
                out[i] = spark
    except Exception:
        for i in range(len(repos)):
            out[i] = "".join(_SPARK[0] for _ in range(12))
    return out


def _repo_browser(g, org_name):
    """Interactive checkbox browser for an org's repos with sorting,
    pagination, stars/open-issues columns and a per-row activity sparkline.

    Shows a loading spinner while fetching + sorting + computing activity
    trends, then redraws a single in-place list (no stacking) via Live."""
    label = f"Fetching and sorting repos for '{org_name}' …"

    if RICH:
        with console.status(f"[{CY}]{label}[/{CY}]", spinner="dots"):
            try:
                repos = _fetch_org_repos_sorted(g, org_name)
            except GithubException as exc:
                print_error(f"Cannot fetch org '{org_name}': {exc}")
                return [], []
        if not repos:
            print_status("No public repos found.")
            return [], []
        with console.status(f"[{CY}]Computing activity trends …[/{CY}]",
                             spinner="dots"):
            sparks = _compute_sparklines(repos)
    else:
        print(f"\n  {label}")
        try:
            repos = _fetch_org_repos_sorted(g, org_name)
        except GithubException as exc:
            print_error(f"Cannot fetch org '{org_name}': {exc}")
            return [], []
        if not repos:
            print_status("No public repos found.")
            return [], []
        sparks = _compute_sparklines(repos)

    # Non-interactive fallback (no TTY / no rich): numbered multi-select.
    use_fancy = bool(RICH)
    try:
        use_fancy = use_fancy and sys.stdin.isatty()
    except Exception:
        use_fancy = False
    if not use_fancy:
        names = [r.full_name for r in repos]
        opts = [f"[ENTIRE ORG]  {org_name}"] + names
        idxs = _pick(f"Select repos to watch from '{org_name}':", opts, multi=True)
        chosen = []
        chosen_set = set()
        for i in idxs:
            if i == 0:
                continue
            chosen.append(names[i - 1])
            chosen_set.add(i - 1)
        return chosen, chosen_set

    n = len(repos)
    total_pages = max(1, (n + REPO_PAGE_SIZE - 1) // REPO_PAGE_SIZE)
    page = 0
    cursor = -1
    selected = set()

    def render():
        heads = Text()
        heads.append(f"\nBrowse repos in '{escape(org_name)}' — {n} repos\n",
                     style=f"bold {G}")
        heads.append("  [↑/↓] move  [Space] toggle  [A] select-all  "
                     "[N]ext page  [P]rev page  [Enter] confirm  [Q] cancel\n",
                     style=DM)

        table = Table(show_header=True, header_style=f"bold {G}",
                      border_style=DM, box=box.ROUNDED, expand=True)
        table.add_column("", width=2)
        table.add_column("Sel", width=3)
        table.add_column("Repo", style="white", ratio=3)
        table.add_column("★ Stars", style=YL, justify="right", width=8)
        table.add_column("⚠ Open", style=RD, justify="right", width=7)
        table.add_column("Activity (12w)", style=G, width=14)

        start = page * REPO_PAGE_SIZE
        page_repos = repos[start:start + REPO_PAGE_SIZE]

        all_on = len(selected) == n
        mark = "☒" if all_on else "☐"
        cur = (cursor == -1)
        tag = "▶" if cur else " "
        table.add_row(tag, f"[{G}]{mark}[/{G}]",
                      f"[bold {G}]SELECT ALL[/bold {G}]", "", "", "")

        for i, r in enumerate(page_repos):
            abs_idx = start + i
            checked = abs_idx in selected
            mk = "☒" if checked else "☐"
            pos = (cursor == i)
            t = "▶" if pos else " "
            spk = sparks.get(abs_idx, "".join(_SPARK[0] for _ in range(12)))
            style = G if pos else "white"
            table.add_row(
                t, f"[{style}]{mk}[/{style}]",
                f"[{style}]{escape(r.full_name)}[/{style}]",
                f"{r.stargazers_count:,}", f"{r.open_issues_count:,}",
                f"[{G}]{spk}[/{G}]",
            )

        foot = Text(f"\n  Page {page + 1}/{total_pages}  —  "
                    f"{len(selected)}/{n} selected\n", style=DM)
        return Group(heads, table, foot)

    with Live(render(), console=console, refresh_per_second=30,
              transient=True, screen=False) as live:
        while True:
            key = _read_key()
            if key is None:
                key = "enter"

            if key in ("q", "esc", "ctrl-c", "ctrl-d"):
                console.print(f"\n  [{YL}]Browser cancelled.[/{YL}]")
                return [], []

            if key == "up":
                cursor -= 1
                if cursor < -1:
                    page = (page - 1) % total_pages
                    pc = len(repos[page * REPO_PAGE_SIZE:page * REPO_PAGE_SIZE + REPO_PAGE_SIZE])
                    cursor = pc - 1
            elif key == "down":
                pc = len(repos[page * REPO_PAGE_SIZE:page * REPO_PAGE_SIZE + REPO_PAGE_SIZE])
                if cursor >= pc - 1:
                    page = (page + 1) % total_pages
                    cursor = -1
                else:
                    cursor += 1
            elif key in ("n", "right"):
                page = (page + 1) % total_pages
                cursor = -1
            elif key in ("p", "left"):
                page = (page - 1) % total_pages
                cursor = -1
            elif key == "space":
                if cursor == -1:
                    if len(selected) == n:
                        selected.clear()
                    else:
                        selected = set(range(n))
                else:
                    abs_idx = page * REPO_PAGE_SIZE + cursor
                    if abs_idx in selected:
                        selected.discard(abs_idx)
                    else:
                        selected.add(abs_idx)
            elif key == "a":
                if len(selected) == n:
                    selected.clear()
                else:
                    selected = set(range(n))
            elif key == "enter":
                chosen_repos = [repos[i].full_name for i in sorted(selected)]
                return chosen_repos, selected

            live.update(render())


ISSUE_STATES = ["open", "closed", "all"]

def _remove_menu(section: str, items: list, config: dict, key: str) -> None:
    if not items:
        print(f"  (no {section} configured)")
        return
    print(f"\n  Current {section}:")
    for i, item in enumerate(items, 1):
        print(f"    {i:>3}.  {item}")
    raw = input("  Enter numbers to REMOVE (comma-separated) or press Enter to keep all: ").strip()
    if not raw:
        return
    try:
        to_remove = {int(x.strip()) - 1 for x in raw.split(",") if x.strip()}
        removed = [items[i] for i in sorted(to_remove) if 0 <= i < len(items)]
        config[key] = [item for i, item in enumerate(items) if i not in to_remove]
        for r in removed:
            print(f"  Removed: {r}")
    except ValueError:
        print("  Invalid input — nothing removed.")

def cmd_setup(_args) -> None:
    print_section("INTERACTIVE SETUP")

    if RICH:
        console.print(Panel(
            "Configure repos, orgs, labels, and filters.\n"
            "Credentials are handled separately via .env.",
            title=f"[bold {G}]SETUP WIZARD[/bold {G}]",
            border_style=G, box=box.DOUBLE_EDGE,
        ))

    g      = build_github_client()
    config = load_config()

    # Step 1: Orgs
    print_section("STEP 1 — Organisations")
    _remove_menu("watched orgs", config.get("watched_orgs", []), config, "watched_orgs")

    while True:
        org_name = input("\n  Add an org to browse (or press Enter to skip): ").strip()
        if not org_name:
            break
        config.setdefault("watched_orgs", [])
        if org_name in config["watched_orgs"]:
            print(f"  '{org_name}' is already being watched (entire org).")
            continue

        chosen_repos, chosen_idx_set = _repo_browser(g, org_name)
        config.setdefault("watched_repos", [])
        added_any = False
        for repo in chosen_repos:
            if repo not in config["watched_repos"]:
                config["watched_repos"].append(repo)
                print(f"  Added repo '{repo}'.")
                added_any = True
        # If the user selected every visible repo, also register the whole org
        # so it stays in sync on future polls.
        if chosen_idx_set and len(chosen_idx_set) == 100 and org_name not in config["watched_orgs"]:
            config["watched_orgs"].append(org_name)
            print(f"  Added entire org '{org_name}' (all listed repos selected).")
        elif added_any:
            pass

    # Step 2: Individual repos
    print_section("STEP 2 — Individual repos")
    _remove_menu("watched repos", config.get("watched_repos", []), config, "watched_repos")

    while True:
        repo = input("\n  Add a repo (owner/name) or press Enter to skip: ").strip()
        if not repo:
            break
        if "/" not in repo or repo.count("/") != 1:
            print("  Format must be owner/name, e.g. microsoft/vscode")
            continue
        config.setdefault("watched_repos", [])
        if repo not in config["watched_repos"]:
            config["watched_repos"].append(repo)
            print(f"  Added '{repo}'.")
        else:
            print(f"  '{repo}' already in list.")

    # Step 3: Labels
    print_section("STEP 3 — Trigger labels")
    _remove_menu("labels", config.get("trigger_labels", []), config, "trigger_labels")

    COMMON_LABELS = [
        "good first issue", "help wanted", "beginner friendly",
        "easy", "starter", "bug", "documentation", "enhancement",
        "feature request", "hacktoberfest", "up for grabs", "first-timers-only",
    ]
    already = set(config.get("trigger_labels", []))
    available = [l for l in COMMON_LABELS if l not in already]

    if available:
        print("\n  Common trigger labels — toggle the ones you want to watch:")
        idxs = _interactive_select(
            "Select trigger labels",
            available,
            multi=True,
            allow_select_all=True,
            page_size=len(available) or 1,
        )
        config.setdefault("trigger_labels", [])
        for i in idxs:
            lbl = available[i]
            config["trigger_labels"].append(lbl)
            print(f"  Added '{lbl}'.")

    while True:
        label = input("\n  Type a custom label to add (or press Enter to finish): ").strip().lower()
        if not label:
            break
        config.setdefault("trigger_labels", [])
        if label not in config["trigger_labels"]:
            config["trigger_labels"].append(label)
            print(f"  Added label '{label}'.")
        else:
            print(f"  '{label}' already tracked.")

    # Step 4: Filters
    print_section("STEP 4 — Issue filters")
    print(f"  Current state filter  : {config['filters']['issue_state']}")
    print(f"  Exclude assigned      : {config['filters']['exclude_assigned']}")
    print(f"  Exclude PRs           : {config['filters']['exclude_prs']}")

    if _yn("\n  Change issue filters?", default=False):
        state_idx = _interactive_select(
            "Issue state to fetch",
            ISSUE_STATES,
            multi=False,
            allow_select_all=False,
        )
        if 0 <= state_idx < len(ISSUE_STATES):
            config["filters"]["issue_state"] = ISSUE_STATES[state_idx]
        config["filters"]["exclude_assigned"] = not _yn(
            "Include issues that already have an assignee?", default=False
        )
        config["filters"]["exclude_prs"] = not _yn(
            "Include pull-requests in results?", default=False
        )

    # Step 5: Poll interval
    print_section("STEP 5 — Poll interval")
    raw_interval = input(
        f"  Poll interval in minutes [{config['poll_interval_minutes']}]: "
    ).strip()
    if raw_interval.isdigit() and int(raw_interval) >= 1:
        config["poll_interval_minutes"] = int(raw_interval)

    save_config(config)
    print_section("CONFIGURATION SAVED")
    cmd_list(None)

    if _yn("Start polling now?", default=True):
        run_notifier()

# ─── Argument Parser ──────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github_issue_notifier",
        description="GitHub Issue Notifier OSS — Retro terminal edition.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python github_issue_notifier.py cli
  python github_issue_notifier.py run
  python github_issue_notifier.py setup
  python github_issue_notifier.py add-repo   facebook/react
  python github_issue_notifier.py add-label  "beginner friendly"
  python github_issue_notifier.py set-filter state open
  python github_issue_notifier.py set-interval 10
  python github_issue_notifier.py test-email
  python github_issue_notifier.py exit
  python github_issue_notifier.py --setup run
""",
    )

    parser.add_argument(
        "--setup", action="store_true",
        help="Prompt for credentials before running the command",
    )

    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sub.add_parser("run",        help="Start polling (runs until Ctrl+C)")
    sub.add_parser("setup",      help="Interactive wizard: repos, labels, filters")
    sub.add_parser("list",       help="Show current config")
    sub.add_parser("test-email", help="Send a test email to verify SMTP setup")
    sub.add_parser("cli",        help="Start interactive CLI mode")
    sub.add_parser("exit",       help="Exit the notifier gracefully")
    sub.add_parser("quit",       help="Alias for exit")
    sub.add_parser("q",          help="Alias for exit")

    p = sub.add_parser("add-repo",    help="Add a specific repo to watch")
    p.add_argument("repo", help="owner/name  e.g. microsoft/vscode")

    p = sub.add_parser("remove-repo", help="Remove a specific repo")
    p.add_argument("repo", help="owner/name  e.g. microsoft/vscode")

    p = sub.add_parser("add-org",    help="Watch all public repos in an organisation")
    p.add_argument("org", help="GitHub org name  e.g. microsoft")

    p = sub.add_parser("remove-org", help="Stop watching an organisation")
    p.add_argument("org", help="GitHub org name  e.g. microsoft")

    p = sub.add_parser("add-label",   help="Add a trigger label")
    p.add_argument("label", help='e.g. "good first issue"')

    p = sub.add_parser("remove-label", help="Remove a trigger label")
    p.add_argument("label", help="Label name to stop tracking")

    p = sub.add_parser("set-filter", help="Adjust issue filters")
    p.add_argument("filter_key", metavar="KEY",   help="state | assigned | prs")
    p.add_argument("value",      metavar="VALUE", help="state->open/closed/all  assigned/prs->true/false")

    p = sub.add_parser("set-interval", help="Change the poll interval (minutes)")
    p.add_argument("minutes", help="Positive integer")

    return parser

_COMMAND_MAP = {
    "run":           cmd_run,
    "setup":         cmd_setup,
    "list":          cmd_list,
    "add-repo":      cmd_add_repo,
    "remove-repo":   cmd_remove_repo,
    "add-org":       cmd_add_org,
    "remove-org":    cmd_remove_org,
    "add-label":     cmd_add_label,
    "remove-label":  cmd_remove_label,
    "set-filter":    cmd_set_filter,
    "set-interval":  cmd_set_interval,
    "test-email":    cmd_test_email,
    "cli":           cmd_cli,
    "exit":          cmd_exit,
    "quit":          cmd_exit,
    "q":             cmd_exit,
}

# ─── Entry Point ──────────────────────────────────────────────────────────────
def main() -> None:
    try:
        parser = _build_parser()
        args   = parser.parse_args()

        if RICH:
            console.clear()
            show_animated_banner()
            print_menu()

        if args.setup:
            ensure_credentials()

        if args.command in ("run", "test-email", "cli"):
            ensure_credentials()

        _COMMAND_MAP[args.command](args)

    except KeyboardInterrupt:
        print()
        show_goodbye()
        sys.exit(0)
    except EOFError:
        print()
        show_goodbye()
        sys.exit(0)


if __name__ == "__main__":
    main()
