#!/usr/bin/env python3
"""
gw_seat_watch.py — scrape GW's Schedule of Classes and report section status.

Usage:
    python gw_seat_watch.py                              # REL 1010, Spring 2027
    python gw_seat_watch.py --subject MATH --course 2184
    python gw_seat_watch.py --json
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE = "https://my.gwu.edu/mod/pws/courses.cfm"
HEADERS = {"User-Agent": "gw-seat-watch/1.0 (personal course-status checker)"}

STATUSES = ("OPEN", "CLOSED", "WAITLIST", "CANCELLED", "TBA")

# Each section renders as one line of text:
#   CLOSED 77673 REL 1010 10 The New Testament 3.00 Boyce, W MON 353 TR 11:10AM - 12:25PM ...
# Cell splits are inconsistent (course numbers are links, cancelled rows drop
# cells, some rows render as full tables), so match the flattened row text
# rather than fixed <td> positions.
ROW_RE = re.compile(
    r"^(?P<status>" + "|".join(STATUSES) + r")\s+"
    r"(?P<crn>\d{4,6})\s+"
    r"(?P<subject>[A-Z]{2,5})\s*"
    r"(?P<number>\d{4}[A-Z]?)\s+"
    r"(?P<section>[\w.]+)\s+"
    r"(?P<rest>.*)$"
)

# Days and time always sit adjacent ("TR 11:10AM - 12:25PM"); matching them as
# one unit avoids grabbing a nested cross-listed row's meeting info.
MEETING_RE = re.compile(
    r"\b(?P<days>[MTWRFSU]{1,5})\s+"
    r"(?P<time>\d{1,2}:\d{2}[AP]M\s*-\s*\d{1,2}:\d{2}[AP]M)"
)
CREDIT_RE = re.compile(r"\b(\d+\.\d{2})\b")
PAGE_RE = re.compile(r"goToPage\('(\d+)'\)")

LINKED_WORDS = ("recitation", "lab", "laboratory", "discussion")


def fetch_pages(subject, term, camp, session=None):
    """Fetch page 1, discover how many pages exist, then fetch the rest."""
    s = session or requests.Session()
    params = {"campId": camp, "termId": term, "subjId": subject}
    first = s.get(BASE, params=params, headers=HEADERS, timeout=30)
    first.raise_for_status()
    html = [first.text]

    pages = {int(n) for n in PAGE_RE.findall(first.text)}
    for n in sorted(p for p in pages if p > 1):
        r = s.get(BASE, params={**params, "pageNum": n}, headers=HEADERS, timeout=30)
        r.raise_for_status()
        html.append(r.text)
        time.sleep(1)  # don't hammer the registrar
    return html


# Fallback: scan the whole flattened page instead of row elements, capturing
# each section's text up to wherever the next section starts.
LOOSE_RE = re.compile(
    r"(?P<status>" + "|".join(STATUSES) + r")\s+"
    r"(?P<crn>\d{4,6})\s+"
    r"(?P<subject>[A-Z]{2,5})\s*"
    r"(?P<number>\d{4}[A-Z]?)\s+"
    r"(?P<section>[\w.]+)\s+"
    r"(?P<rest>.*?)"
    r"(?=(?:" + "|".join(STATUSES) + r")\s+\d{4,6}\s+[A-Z]{2,5}\s*\d{4}|$)"
)


def _build(m, subject, number):
    rest = m.group("rest")
    cm = CREDIT_RE.search(rest)
    credit = float(cm.group(1)) if cm else None
    # Title is everything before the credit figure. (0.00 is a valid credit
    # value and falsy -- test against None, not truthiness.)
    title = rest[: cm.start()].strip() if cm else rest.strip()
    meeting = MEETING_RE.search(rest)
    return {
        "status": m.group("status"),
        "crn": m.group("crn"),
        "course": f"{subject} {number}",
        "section": m.group("section"),
        "title": title,
        "credit": credit,
        "days": meeting.group("days") if meeting else "",
        "time": meeting.group("time") if meeting else "",
        "is_linked": (credit == 0.0)
        or any(w in title.lower() for w in LINKED_WORDS),
    }


def parse_sections(html_pages, subject, number):
    """Return section dicts in document order, deduped by CRN.

    Primary pass reads <tr> elements. GW renders some rows outside the shape
    that pass expects, so if it finds nothing for this course we re-scan the
    flattened page text before giving up.
    """
    sections, seen = [], set()

    for html in html_pages:
        soup = BeautifulSoup(html, "html.parser")
        for tr in soup.find_all("tr"):
            text = " ".join(tr.get_text(" ", strip=True).split())
            m = ROW_RE.match(text)
            if not m:
                continue
            # Skip cross-listed rows under other subjects (WGSS, CLAS, ...).
            if m.group("subject") != subject or m.group("number") != number:
                continue
            if m.group("crn") in seen:
                continue  # an outer container row already yielded this one
            seen.add(m.group("crn"))
            sections.append(_build(m, subject, number))

    if sections:
        return sections

    for html in html_pages:
        soup = BeautifulSoup(html, "html.parser")
        flat = " ".join(soup.get_text(" ", strip=True).split())
        for m in LOOSE_RE.finditer(flat):
            if m.group("subject") != subject or m.group("number") != number:
                continue
            if m.group("crn") in seen:
                continue
            seen.add(m.group("crn"))
            sections.append(_build(m, subject, number))

    return sections


def group_by_parent(sections):
    """Attach each 0-credit linked section to the lecture row above it."""
    groups, current = [], None
    for sec in sections:
        if sec["is_linked"] and current is not None:
            current["linked"].append(sec)
        else:
            current = {"parent": sec, "linked": []}
            groups.append(current)
    return groups


def render(groups):
    lines = []
    for g in groups:
        p = g["parent"]
        lines.append(
            f"{p['course']}.{p['section']}  [{p['status']}]  CRN {p['crn']}  "
            f"{p['title']}  {p['days']} {p['time']}".rstrip()
        )
        for s in g["linked"]:
            flag = "  <-- OPEN" if s["status"] == "OPEN" else ""
            lines.append(
                f"    .{s['section']}  [{s['status']:<9}] CRN {s['crn']}  "
                f"{s['days']} {s['time']}{flag}"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", default="REL")
    ap.add_argument("--course", default="1010")
    ap.add_argument("--term", default="202701", help="202701 = Spring 2027")
    ap.add_argument("--camp", default="1", help="1 = Main Campus")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pages = fetch_pages(args.subject, args.term, args.camp)
    secs = parse_sections(pages, args.subject.upper(), args.course)
    if not secs:
        print(f"No sections found for {args.subject} {args.course} in term {args.term}.",
              file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(secs, indent=2))
    else:
        print(f"{args.subject} {args.course} — checked {datetime.now():%Y-%m-%d %H:%M}\n")
        print(render(group_by_parent(secs)))
    return 0 if any(s["status"] == "OPEN" for s in secs) else 1


if __name__ == "__main__":
    sys.exit(main())
