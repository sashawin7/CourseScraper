#!/usr/bin/env python3
"""
watch_crns.py — check specific CRNs and email when one flips to OPEN.

Environment variables:
    CRNS         comma-separated, e.g. "77673"            (default 77673)
    SUBJECT      default REL
    COURSE       default 1010
    TERM         default 202701  (Spring 2027)
    CAMP         default 1       (Main Campus)
    MAIL_TO      where to send the alert
    MAIL_FROM    the Gmail address sending it
    MAIL_PASS    16-char Google app password, no spaces
    SMTP_HOST    default smtp.gmail.com
    SMTP_PORT    default 587
    STATE_FILE   default state.json

Emails only on a transition into OPEN, so a section that sits open for hours
doesn't spam you. Delete state.json to reset.

Run with --dry-run to print what it would send without sending anything.
"""

import json
import os
import smtplib
import ssl
import sys
from datetime import datetime
from email.message import EmailMessage

from gw_seat_watch import fetch_pages, parse_sections

REGISTER_URL = "https://banweb.gwu.edu/"

# A seat you can actually take. WAITLIST means a queue exists, not a free seat,
# so it is deliberately NOT an alert condition.
ALERT_STATUSES = {"OPEN"}


def env(name, default=None):
    v = os.environ.get(name, default)
    return v.strip() if isinstance(v, str) else v


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path, state):
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def build_email(opened, mail_from, mail_to):
    names = ", ".join(f"{s['course']}.{s['section']}" for s in opened)
    msg = EmailMessage()
    msg["Subject"] = f"[SEAT OPEN] {names} — register now"
    msg["From"] = mail_from
    msg["To"] = mail_to

    body = ["A section you're watching just opened:\n"]
    for s in opened:
        body.append(
            f"  {s['course']}.{s['section']}  CRN {s['crn']}\n"
            f"    {s['title']}\n"
            f"    status : {s['status']}\n"
            f"    meets  : {s['days']} {s['time']}\n"
        )
    body.append(f"\nRegister: {REGISTER_URL}")
    body.append(f"Checked at {datetime.now():%Y-%m-%d %H:%M}")
    msg.set_content("\n".join(body))
    return msg


def send(msg, host, port, user, password):
    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls(context=context)
        server.login(user, password)
        server.send_message(msg)


def main():
    dry_run = "--dry-run" in sys.argv

    crns = {c.strip() for c in env("CRNS", "77673").split(",") if c.strip()}
    subject = env("SUBJECT", "REL").upper()
    course = env("COURSE", "1010")
    term = env("TERM", "202701")
    camp = env("CAMP", "1")
    state_file = env("STATE_FILE", "state.json")

    pages = fetch_pages(subject, term, camp)
    sections = parse_sections(pages, subject, course)
    watched = [s for s in sections if s["crn"] in crns]

    missing = crns - {s["crn"] for s in watched}
    if missing:
        print(f"WARNING: CRNs not found on the page: {', '.join(sorted(missing))}",
              file=sys.stderr)
    if not watched:
        print("Nothing to check — exiting without touching state.", file=sys.stderr)
        return 2

    state = load_state(state_file)
    opened = []
    for s in watched:
        was = state.get(s["crn"])
        print(f"{s['course']}.{s['section']} CRN {s['crn']}: {was or '?'} -> {s['status']}")
        # Alert on the transition into OPEN, not every run while it stays open.
        if s["status"] in ALERT_STATUSES and was not in ALERT_STATUSES:
            opened.append(s)
        state[s["crn"]] = s["status"]

    if opened:
        # Flag the CI job so it can also file a GitHub issue as a backup alert.
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            with open(gh_out, "a") as f:
                f.write("opened=true\n")

        mail_to, mail_from = env("MAIL_TO"), env("MAIL_FROM")
        mail_pass = env("MAIL_PASS")
        msg = build_email(opened, mail_from or "me", mail_to or "me")
        if dry_run or not all([mail_to, mail_from, mail_pass]):
            print("\n--- would send ---")
            print(f"Subject: {msg['Subject']}\n")
            print(msg.get_content())
            if not dry_run:
                print("Mail env vars not set; skipped sending.", file=sys.stderr)
        else:
            send(msg, env("SMTP_HOST", "smtp.gmail.com"),
                 int(env("SMTP_PORT", "587")), mail_from, mail_pass)
            print(f"Emailed {mail_to}.")

    if not dry_run:
        save_state(state_file, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
