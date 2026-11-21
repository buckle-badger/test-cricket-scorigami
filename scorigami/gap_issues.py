"""Turn out/gaps.json (written by the build) into GitHub issues, without ever making duplicates.

  * one issue per problem, titled "Missing Test #2644" or "Test #2644 is used twice", labelled missing-test
  * already open: left alone
  * closed by hand as "not planned": treated as acknowledged and never re-opened
  * fixed (the number is no longer missing or doubled, e.g. after a hand-added match): commented on and closed

Runs in the daily workflow with the built-in GITHUB_TOKEN. Without a token it only prints what it would do.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import date
from pathlib import Path

LABEL = "missing-test"
GAPS = Path(__file__).resolve().parent.parent / "out" / "gaps.json"


def title(g: dict) -> str:
    return f"Missing Test #{g['test']}" if g["kind"] == "missing" else f"Test #{g['test']} is used twice"


def body(g: dict) -> str:
    if g["kind"] == "missing":
        a, b = g["after"], g["before"]
        return (f"Test #{g['test']} isn't on the site. It falls between\n\n"
                f"- Test #{a['test']}: {a['teams']}, {a['venue']}, {a['date']}\n"
                f"- Test #{b['test']}: {b['teams']}, {b['venue']}, {b['date']}\n\n"
                "Cricsheet doesn't publish Afghanistan men's matches, so it's most likely one of theirs. "
                "To fix it, add the match to `data/cricinfo_new.json` with its Test number, checked against "
                "the ESPNcricinfo scorecard. This issue closes itself on the next rebuild once the gap is filled.\n\n"
                "If the number is genuinely unused, close this issue as **not planned** and it won't come back.")
    rows = "\n".join(f"- {m['teams']}, {m['venue']}, {m['date']} (match id {m['match_id']})" for m in g["matches"])
    return (f"Two matches on the site carry Test #{g['test']}:\n\n{rows}\n\n"
            "One of the numbers in `data/cricinfo_new.json` is probably wrong. "
            "This issue closes itself once only one match has the number.")


class GitHub:
    def __init__(self, token: str, repo: str):
        self.token, self.base = token, f"https://api.github.com/repos/{repo}"

    def call(self, method: str, path: str, data: dict | None = None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                                              "User-Agent": "scorigami-gap-check"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def issues(self):
        out, page = [], 1
        while True:
            got = self.call("GET", f"/issues?labels={LABEL}&state=all&per_page=100&page={page}")
            out += [i for i in got if "pull_request" not in i]
            if len(got) < 100:
                return out
            page += 1

    def create(self, t, b):
        return self.call("POST", "/issues", {"title": t, "body": b, "labels": [LABEL]})

    def close(self, n, note):
        self.call("POST", f"/issues/{n}/comments", {"body": note})
        self.call("PATCH", f"/issues/{n}", {"state": "closed", "state_reason": "completed"})


def plan(gaps: list[dict], issues: list[dict]):
    """What to open and what to close. Pure, so it can be tested without GitHub."""
    want = {title(g): g for g in gaps}
    by_title = {}
    for i in issues:                       # an open issue beats a closed one with the same title
        if i["title"] not in by_title or i["state"] == "open":
            by_title[i["title"]] = i
    to_open = [g for t, g in want.items()
               if not (t in by_title and (by_title[t]["state"] == "open" or by_title[t].get("state_reason") == "not_planned"))]
    to_close = [i for i in issues if i["state"] == "open" and i["title"] not in want]
    return to_open, to_close


def main():
    gaps = json.loads(GAPS.read_text()) if GAPS.exists() else []
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print(f"Gap check (no GitHub token, so nothing is posted): {len(gaps)} problem(s)")
        for g in gaps:
            print("  would open:", title(g))
        return
    gh = GitHub(token, repo)
    to_open, to_close = plan(gaps, gh.issues())
    for g in to_open:
        print("Opening issue:", title(g)); gh.create(title(g), body(g))
    for i in to_close:
        print("Closing issue:", i["title"])
        gh.close(i["number"], f"Fixed: the numbering no longer has this problem (checked {date.today().isoformat()}).")
    if not to_open and not to_close:
        print(f"Gap check: nothing to change ({len(gaps)} known problem(s)).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:      # never let the alert break the rebuild
        print(f"! Gap check couldn't reach GitHub: {e}", file=sys.stderr)
