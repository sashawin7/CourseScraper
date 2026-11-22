# CourseScraper

Watches the GW Schedule of Classes and emails when a specific CRN opens.

**Currently watching:** REL 1010 section 10 — CRN `77673` — The New Testament (Spring 2027)

## How it works

| File | Role |
|---|---|
| `gw_seat_watch.py` | Fetches and parses the Schedule of Classes page |
| `watch_crns.py` | Compares against saved state, emails on a flip to OPEN |
| `.github/workflows/watch.yml` | Runs the check on a schedule |
| `state.json` | Last seen status per CRN (created by the first run) |
| `last_checked.txt` | Heartbeat — proves the watcher is alive |

Email fires only on a *transition* into OPEN, so a section that stays open
doesn't send repeatedly. Delete `state.json` to reset.

## Setup

Three repository secrets under Settings → Secrets and variables → Actions:

- `MAIL_TO` — where alerts go
- `MAIL_FROM` — the Gmail address sending them
- `MAIL_PASS` — 16-char Google app password, spaces stripped

## Watching something else

Edit the `env:` block in `.github/workflows/watch.yml` (`CRNS`, `SUBJECT`,
`COURSE`, `TERM`), then delete `state.json`.

## Run locally

```bash
pip install requests beautifulsoup4
python gw_seat_watch.py                  # print all REL 1010 sections
python watch_crns.py --dry-run           # show the email without sending
```

## Known limits

- GitHub throttles scheduled workflows heavily — expect a handful of checks
  per day, not one every 7 minutes.
- Scheduled workflows on public repos are disabled after 60 days of repo
  inactivity, silently. The heartbeat commit on every run prevents this.
