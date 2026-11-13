"""Build the Men's Test scorigami from up to three sources.

Sources, applied in this order (later sources overwrite earlier ones for the same innings):
  1. data/test_matches_kaggle.csv   historical base to March 2024. Declarations are INFERRED.
  2. Cricsheet tests_male_json.zip  men's Tests from 2001, updated after every match. Exact.
  3. data/statsguru*.csv            optional manual export from Cricinfo Statsguru. Exact, full history.
  3b. data/cricinfo_check.json      scorecard check read from Cricinfo in the browser. Exact.
  4. data/overrides.csv             your own corrections (match_id, innings, ending[, runs, wickets], note).

Every overwrite that changes runs, wickets or the ending is logged to out/checks.csv,
which is how the inferred CSV data gets checked against the exact sources.

Usage:
  python -m scorigami.build                 # download Cricsheet and build
  python -m scorigami.build --no-download   # use data/tests_male_json.zip if present
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "out"
DOCS = ROOT / "docs"
CRICSHEET_URL = "https://cricsheet.org/downloads/tests_male_json.zip"

HOW = ["allout", "allout_short", "declared", "chase", "draw", "other", "forfeit"]
# Why a side was all out with fewer than 10 wickets down
WHY = ["absent_hurt", "retired_hurt", "ill", "other", "several", "unknown"]


def why_from_text(note: str) -> str:
    """Classify Cricinfo dismissal notes such as 'absent hurt', 'retired ill', 'absent (family)'."""
    found = set()
    for part in re.split(r"[;,]", (note or "").lower()):
        part = part.strip()
        if not part: continue
        if "hurt" in part or "injur" in part: found.add("absent_hurt" if "absent" in part else "retired_hurt")
        elif "ill" in part: found.add("ill")
        else: found.add("other")
    return "unknown" if not found else (found.pop() if len(found) == 1 else "several")
SRC = ["csv", "cricsheet", "statsguru", "manual", "cricinfo"]

TEAM_ALIASES = {
    "ENG": "England", "AUS": "Australia", "SA": "South Africa", "WI": "West Indies",
    "NZ": "New Zealand", "INDIA": "India", "IND": "India", "PAK": "Pakistan", "SL": "Sri Lanka",
    "ZIM": "Zimbabwe", "BAN": "Bangladesh", "IRE": "Ireland", "AFG": "Afghanistan",
    "ICC": "ICC World XI", "World-XI": "ICC World XI", "ICC XI": "ICC World XI",
}


RES = ["won", "lost", "drawn", "tied"]
NOTES = {("63864", 2): "Forfeited. The Laws then didn’t provide for a first-innings forfeit, so the scorers recorded it as 0/0 declared",
         ("63864", 3): "Forfeited, counted here on 0"}
FO = ["following_on", "follow_on_match", "no_follow_on"]
TOSS = ["won_bat", "won_bowl", "lost_opp_batted", "lost_sent_in", "unknown"]


def result_for(bat: str, result_text: str) -> str:
    """What happened to the batting side, read from the match result text."""
    t = (result_text or "").strip()
    low = t.lower()
    if "tied" in low or low.startswith("tie"): return "tied"
    m = re.match(r"^(.+?) (?:won|awarded)\b", t)
    if m: return "won" if team(m.group(1)) == bat else "lost"
    return "drawn"  # drawn, abandoned, no result


def team(name: str) -> str:
    name = str(name).strip()
    name = re.sub(r"^v\s+", "", name)
    return TEAM_ALIASES.get(name, name)


class Store:
    """Innings records keyed by (Cricinfo match id, innings number in the match)."""

    def __init__(self):
        self.inn: dict[tuple[str, int], dict] = {}
        self.matches: dict[str, dict] = {}
        self.by_date: dict[tuple[str, str, int], tuple[str, int]] = {}
        self.checks: list[dict] = []

    def add_match(self, mid, **kw):
        m = self.matches.setdefault(mid, {"mid": mid, "test": None, "date": None, "year": None, "toss": None, "toss_dec": None,
                                          "venue": "", "result": ""})
        for k, v in kw.items():  # first source to know a field wins; later ones only fill gaps
            if v not in (None, "") and not m.get(k):
                m[k] = v
        if m["date"] and not m["year"]:
            m["year"] = m["date"][:4]

    def put(self, mid, inn, rec, src):
        key = (mid, inn)
        old = self.inn.get(key)
        if old is None:
            m = self.matches.get(mid, {})
            if m.get("date"):  # fall back to date + team + innings when ids disagree
                alt = self.by_date.get((m["date"], rec["bat"], inn))
                if alt and alt in self.inn:
                    key, old = alt, self.inn[alt]
        rec = {**rec, "src": src}
        if old is not None:
            for f in ("runs", "wkts", "how"):
                if rec.get(f) is None:
                    rec[f] = old.get(f)
                elif old.get(f) is not None and old[f] != rec[f]:
                    self.checks.append({
                        "match_id": key[0], "test": self.matches.get(key[0], {}).get("test"),
                        "date": self.matches.get(key[0], {}).get("date"), "team": rec["bat"],
                        "innings": inn, "field": f, "old": old[f], "old_source": old["src"],
                        "new": rec[f], "new_source": src})
            rec = {**old, **{k: v for k, v in rec.items() if v is not None}}
        if rec.get("how") != "allout_short":
            rec["why"] = None
        elif not rec.get("why"):
            rec["why"] = "unknown"
        self.inn[key] = rec
        m = self.matches.get(key[0], {})
        if m.get("date"):
            self.by_date[(m["date"], rec["bat"], inn)] = key


# ---------------------------------------------------------------- 1. Kaggle CSV
def load_csv(store: Store, path: Path):
    d = pd.read_csv(path)

    def num(x):
        return None if pd.isna(x) else int(x)

    for _, r in d.iterrows():
        t1, t2 = team(r["Team1 Name"]), team(r["Team2 Name"])
        I = {k: (num(r[f"Innings{n} Team{t} Runs Scored"]), num(r[f"Innings{n} Team{t} Wickets Fell"]))
             for k, (n, t) in {"A1": (1, 1), "B1": (1, 2), "A2": (2, 1), "B2": (2, 2)}.items()}
        res = str(r["Match Result Text"])
        win = team(r["Match Winner"]) if not pd.isna(r["Match Winner"]) else None
        bywk, byinn = "wicket" in res, "innings" in res
        byruns = (" run" in res) and not byinn
        has = lambda k: I[k][0] is not None
        # Team1 always batted first in this file. Work out whether team 2 followed on.
        fo = False
        if has("B2") and not has("A2"):
            fo = True
        elif has("A2") and has("B2"):
            if (win == t1 and bywk) or (win == t2 and byruns):
                fo = True
            elif "tied" in res:
                fo = I["A2"][1] == 10 and I["B2"][1] != 10  # a tie ends with the side batting last all out
            elif win is None:
                # A drawn match can't end with the last side already past its target.
                need_normal = I["A1"][0] + I["A2"][0] - I["B1"][0] + 1   # B batting 4th
                need_fo = I["B1"][0] + I["B2"][0] - I["A1"][0] + 1       # A batting 4th
                normal_ok = I["B2"][0] < need_normal
                fo_ok = I["A2"][0] < need_fo
                if normal_ok != fo_ok:
                    fo = fo_ok
                else:
                    lead = I["A1"][0] - I["B1"][0]
                    fo = lead >= 100 and I["B2"][1] == 10 and I["A2"][0] < need_fo
        order = [k for k in ["A1", "B1"] + (["B2", "A2"] if fo else ["A2", "B2"]) if has(k)]

        mid = str(int(r["Match ID"]))
        dt = None if pd.isna(r["Match Start Date"]) else str(r["Match Start Date"])[:10]
        yr = dt[:4] if dt else (re.search(r"(18|19|20)\d\d", str(r["Series Name"])) or [None])[0]
        store.add_match(mid, test=int(r["TEST Match No"]), date=dt, year=yr,
                        toss=team(r["Toss Winner"]) if isinstance(r["Toss Winner"], str) else None,
                        toss_dec={"bat": "bat", "bowl": "field", "field": "field"}.get(str(r["Toss Winner Choice"]).strip().lower()),
                        venue=r["Match Venue (City)"] if isinstance(r["Match Venue (City)"], str) else "",
                        result=res)
        for pos, k in enumerate(order):
            runs, w = I[k]
            bat = t1 if k[0] == "A" else t2
            opp = t2 if bat == t1 else t1
            last = pos == len(order) - 1
            if w == 10:
                how = "allout"
            elif last:
                if win == bat and bywk: how = "chase"
                elif "drawn" in res: how = "draw"
                elif win == opp: how = "allout_short"
                else: how = "other"
            else:
                if runs == 0 and w == 0:
                    # Centurion 2000: the 2nd-innings forfeit is shown as a forfeit on 0; the first-innings
                    # "forfeit" was officially recorded as 0/0 declared
                    how = "forfeit"  # Centurion 2000: both forfeits (England's was scored as 0/0 declared)
                    store.put(mid, pos + 1, {"bat": bat, "opp": opp, "inn": pos + 1, "runs": 0, "wkts": 0,
                                             "how": how}, "csv")
                    continue
                how = "allout_short" if (win == opp and byinn and k[1] == "1" and w == 9) else "declared"
            store.put(mid, pos + 1, {"bat": bat, "opp": opp, "inn": pos + 1, "runs": runs, "wkts": w,
                                     "how": how}, "csv")


# ---------------------------------------------------------------- 2. Cricsheet
def outcome_text(info) -> str:
    o = info.get("outcome", {})
    if o.get("result") == "draw": return "Match drawn"
    if o.get("result") == "tie": return "Match tied"
    if o.get("result") == "no result": return "No result"
    w, by = o.get("winner"), o.get("by", {})
    if not w: return ""
    if "innings" in by: return f"{w} won by an innings and {by.get('runs', 0)} runs"
    if "wickets" in by: return f"{w} won by {by['wickets']} wicket{'s' if by['wickets'] != 1 else ''}"
    if "runs" in by: return f"{w} won by {by['runs']} run{'s' if by['runs'] != 1 else ''}"
    return f"{w} won"


NOT_DISMISSALS = {"retired hurt", "retired not out"}


def load_cricsheet(store: Store, zpath: Path) -> int:
    n = 0
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            if not name.endswith(".json"):
                continue
            j = json.loads(z.read(name))
            info = j.get("info", {})
            if info.get("match_type") != "Test" or info.get("gender", "male") != "male":
                continue
            mid = Path(name).stem  # Cricsheet file names are Cricinfo match ids
            teams = [team(t) for t in info.get("teams", [])]
            dt = (info.get("dates") or [None])[0]
            store.add_match(mid, test=info.get("match_type_number"), date=dt,
                            toss=team(info["toss"]["winner"]) if info.get("toss", {}).get("winner") else None,
                            toss_dec=info.get("toss", {}).get("decision"),
                            venue=info.get("city") or info.get("venue", ""), result=outcome_text(info))
            o = info.get("outcome", {})
            winner, by = (team(o["winner"]) if o.get("winner") else None), o.get("by", {})
            played = [i for i in j.get("innings", []) if not i.get("super_over")]
            real = [i for i in played if not i.get("forfeited")]
            for pos, inn in enumerate(played):
                if inn.get("forfeited"):
                    bat = team(inn["team"]); first = all(team(x["team"]) != bat for x in played[:pos])
                    store.put(mid, pos + 1, {"bat": bat, "opp": next((t for t in teams if t != bat), ""),
                                             "inn": pos + 1, "runs": 0, "wkts": 0,
                                             "how": "forfeit"}, "cricsheet")
                    continue
                bat = team(inn["team"])
                opp = next((t for t in teams if t != bat), "")
                runs, wk, retired = 0, 0, False
                for ov in inn.get("overs", []):
                    for dlv in ov.get("deliveries", []):
                        runs += dlv.get("runs", {}).get("total", 0)
                        wk += sum(1 for w in dlv.get("wickets", []) if w.get("kind") not in NOT_DISMISSALS)
                        retired |= any(w.get("kind") == "retired hurt" for w in dlv.get("wickets", []))
                pr = inn.get("penalty_runs", {})
                runs += pr.get("pre", 0) + pr.get("post", 0)
                last = inn is real[-1]
                if inn.get("declared"): how = "declared"
                elif wk >= 10: how = "allout"
                elif not last: how = "allout_short"
                elif winner == bat and "wickets" in by: how = "chase"
                elif o.get("result") == "draw": how = "draw"
                elif winner and winner != bat: how = "allout_short"
                else: how = "other"
                absent = bool(inn.get("absent_hurt"))
                why = ("several" if absent and retired else "absent_hurt" if absent
                       else "retired_hurt" if retired else "unknown")
                store.put(mid, pos + 1, {"bat": bat, "opp": opp, "inn": pos + 1, "runs": runs,
                                         "wkts": min(wk, 10), "how": how, "why": why}, "cricsheet")
            n += 1
    return n


def fetch_cricsheet(dest: Path) -> Path:
    req = urllib.request.Request(CRICSHEET_URL, headers={"User-Agent": "test-scorigami/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        f.write(r.read())
    return dest


# ---------------------------------------------------------------- 3. Statsguru export
SCORE_RE = re.compile(r"^(\d+)(?:/(\d+))?(d)?$")


def load_statsguru(store: Store, path: Path) -> int:
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m = re.search(r"/match/(\d+)", row.get("match_link", "")) or re.search(r"(\d{4,})", row.get("match_link", ""))
            sc = SCORE_RE.match(row.get("Score", "").strip())
            if not m or not sc or not row.get("Inns", "").strip().isdigit():
                continue  # DNB, forfeited, abandoned
            mid, inn = m.group(1), int(row["Inns"])
            runs = int(sc.group(1))
            wk = int(sc.group(2)) if sc.group(2) else None
            res = row.get("Result", "").strip().lower()
            if sc.group(3): how = "declared"
            elif sc.group(2) is None: how = "allout" if (wk is None or wk == 10) else "allout_short"
            elif res == "won": how = "chase"
            elif res == "draw": how = "draw"
            else: how = "other"
            bat, opp = team(row["Team"]), team(row["Opposition"])
            try:
                dt = datetime.strptime(row["Start Date"].strip(), "%d %b %Y").date().isoformat()
            except (KeyError, ValueError):
                dt = None
            store.add_match(mid, date=dt, venue=row.get("Ground", ""),
                            result={"won": f"{bat} won", "lost": f"{opp} won", "draw": "Match drawn",
                                    "tied": "Match tied"}.get(res, ""))
            rec = {"bat": bat, "opp": opp, "inn": inn, "runs": runs, "wkts": wk, "how": how}
            if how == "allout" and wk is None:
                # Statsguru shows all-outs without a wicket count; take it from an earlier source
                old = store.inn.get((mid, inn))
                if old and old.get("wkts") is not None:
                    rec["wkts"] = old["wkts"]
                    rec["how"] = "allout" if old["wkts"] >= 10 else "allout_short"
            store.put(mid, inn, rec, "statsguru")
            n += 1
    return n


# ---------------------------------------------------------------- 2b. Tests after the CSV, read from Cricinfo
def load_cricinfo_new(store: Store, path: Path) -> int:
    """{match_id: {test, date, town, state, status, toss, choice (1 bat, 2 bowl), teams,
    inn: [[team, runs, wickets, event, absent/retired note], ...]}}. Only finished matches are used."""
    n = 0
    for mid, m in json.loads(path.read_text()).items():
        if m.get("state") != "POST" or not m.get("inn"):
            continue
        teams = [team(t) for t in m["teams"]]
        res = m.get("status") or ""
        store.add_match(mid, test=m.get("test"), date=m.get("date"), venue=m.get("town", ""), result=res,
                        toss=team(m["toss"]) if m.get("toss") else None,
                        toss_dec={1: "bat", 2: "field"}.get(m.get("choice")))
        innings = m["inn"]
        for k, (t, runs, wk, ev, note) in enumerate(innings):
            bat = team(t); opp = next((x for x in teams if x != bat), "")
            last = k == len(innings) - 1
            if ev == 4: how = "forfeit"
            elif ev == 1: how = "allout" if wk == 10 else "allout_short"
            elif ev == 2: how = "declared"
            elif ev == 3: how = "chase"
            elif last and ("drawn" in res.lower() or "draw" in res.lower()): how = "draw"
            else: how = "other"
            store.put(mid, k + 1, {"bat": bat, "opp": opp, "inn": k + 1, "runs": runs or 0, "wkts": wk or 0,
                                   "how": how, "why": why_from_text(note) if how == "allout_short" else None},
                      "cricinfo")
        n += 1
    return n


# ---------------------------------------------------------------- 3b. Cricinfo scorecard check
def load_cricinfo_check(store: Store, path: Path) -> int:
    """{match_id: [[team, innings, runs, wickets, event, absent/retired note], ...]}.
    Cricinfo's innings event: 1 = all out, 2 = declared, 3 = target reached, null = not out."""
    n = 0
    for mid, innings in json.loads(path.read_text()).items():
        # Match each Cricinfo innings to the CSV's record for the same team's 1st/2nd innings,
        # so a wrongly guessed batting order (follow-ons) gets put right.
        olds = sorted([(k[1], r) for k, r in store.inn.items() if k[0] == mid], key=lambda x: x[0])
        by_team = defaultdict(list)
        for _, r in olds:
            by_team[r["bat"]].append(r)
        seen_team = Counter()
        pairs = []
        # Forfeited innings (event 4, or a 0/0 "declaration" the CSV doesn't have) aren't innings here
        have = Counter(r["bat"] for _, r in olds)
        want = Counter(team(i[0]) for i in innings)
        innings = [i for i in innings if not (
            i[3] == 0 and i[4] == 2 and want[team(i[0])] > have[team(i[0])])]
        for t, inn, runs, wk, ev, note in innings:
            t = team(t); k = seen_team[t]; seen_team[t] += 1
            old = by_team[t][k] if k < len(by_team.get(t, [])) else store.inn.get((mid, inn))
            pairs.append((old, t, inn, runs, wk, ev, note))
        if pairs and all(o is not None for o, *_ in pairs) and len(pairs) == len(olds):
            moved = [(o["inn"], inn) for o, _, inn, *_ in pairs if o["inn"] != inn]
            if moved:
                store.checks.append({"match_id": mid, "test": store.matches[mid]["test"],
                                     "date": store.matches[mid]["date"], "team": "",
                                     "innings": "", "field": "batting order",
                                     "old": " ".join(f"{a}" for a, _ in sorted(moved)), "old_source": "csv",
                                     "new": " ".join(f"{b}" for _, b in sorted(moved)), "new_source": "cricinfo"})
                for o, *_ in pairs:
                    store.inn.pop((mid, o["inn"]), None)
                for o, t, inn, *_ in pairs:
                    store.inn[(mid, inn)] = {**o, "inn": inn}
        for old, t, inn, runs, wk, ev, note in pairs:
            old = store.inn.get((mid, inn))
            if old is None:
                continue
            if ev == 4 or (old["how"] == "forfeit" and wk == 0 and ev == 2): how = "forfeit"
            elif ev == 1: how = "allout" if wk == 10 else "allout_short"
            elif ev == 2: how = "declared"
            elif ev == 3: how = "chase"
            elif old["how"] in ("chase", "draw", "other"): how = old["how"]
            else:  # not out, but not the innings the CSV thought ended the match
                m = store.matches[mid]; res = m["result"] or ""
                how = ("chase" if res.startswith(old["bat"]) and "wicket" in res
                       else "draw" if "drawn" in res else "other")
            store.put(mid, inn, {"bat": old["bat"], "opp": old["opp"], "inn": inn, "runs": runs,
                                 "wkts": wk, "how": how, "why": why_from_text(note)}, "cricinfo")
            n += 1
    return n


# ---------------------------------------------------------------- 4. manual corrections
def load_overrides(store: Store, path: Path) -> int:
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mid, inn = row["match_id"].strip(), int(row["innings"])
            old = store.inn.get((mid, inn))
            if old is None:
                print(f"! override for match {mid} innings {inn} matches no innings", file=sys.stderr)
                continue
            if row["ending"].strip() not in HOW:
                print(f"! override ending must be one of {HOW}: {row['ending']}", file=sys.stderr)
                continue
            rec = {"bat": old["bat"], "opp": old["opp"], "inn": inn, "how": row["ending"].strip(),
                   "runs": int(row["runs"]) if row.get("runs") else None,
                   "wkts": int(row["wickets"]) if row.get("wickets") else None,
                   "why": (row.get("reason") or "").strip() or None}
            store.put(mid, inn, rec, "manual")
            n += 1
    return n


# ---------------------------------------------------------------- outputs
def write_outputs(store: Store, counts_src: dict):
    OUT.mkdir(exist_ok=True); DOCS.mkdir(exist_ok=True)
    mlist = sorted(store.matches.values(),
                   key=lambda m: (m["date"] or f"{m['year'] or '0000'}-00-00", m["test"] or 0))
    midx = {m["mid"]: i for i, m in enumerate(mlist)}
    recs = sorted(store.inn.items(), key=lambda kv: (midx[kv[0][0]], kv[0][1]))

    # Follow-on: the same side batting 2nd and 3rd. Its 3rd innings is the one played following on.
    bat_by = defaultdict(dict)
    for (mid, inn), r in recs:
        bat_by[mid][inn] = r["bat"]

    def toss_of(mid, bat):
        m = store.matches[mid]; t, d = m.get("toss"), m.get("toss_dec")
        if not t or d not in ("bat", "field"): return "unknown"
        if t == bat: return "won_bat" if d == "bat" else "won_bowl"
        return "lost_opp_batted" if d == "bat" else "lost_sent_in"

    def fo_of(mid, inn):
        b = bat_by[mid]
        if b.get(2) and b.get(2) == b.get(3):
            return "following_on" if inn == 3 else "follow_on_match"
        return "no_follow_on"

    with open(OUT / "innings.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["test", "match_id", "date", "team", "opposition", "innings", "runs", "wickets",
                    "ending", "short_handed_reason", "team_result", "follow_on", "toss", "source", "result"])
        for (mid, inn), r in recs:
            m = store.matches[mid]
            w.writerow([m["test"], mid, m["date"] or m["year"], r["bat"], r["opp"], inn, r["runs"],
                        r["wkts"], r["how"], r.get("why") or "", result_for(r["bat"], m["result"]), fo_of(mid, inn), toss_of(mid, r["bat"]),
                        r["src"], m["result"]])

    counts = defaultdict(Counter)
    for _, r in recs:
        counts[r["runs"]][r["how"]] += 1
    with open(OUT / "scorigami_counts.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["total", "innings", *HOW, "status"])
        for s in range(1000):
            c = counts.get(s, Counter())
            n = sum(c.values())
            st = "all out" if c["allout"] + c["allout_short"] else ("not out only" if n else "never")
            w.writerow([s, n, *[c[h] for h in HOW], st])

    with open(OUT / "checks.csv", "w", newline="") as f:
        cols = ["match_id", "test", "date", "team", "innings", "field", "old", "old_source", "new", "new_source"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); w.writerows(store.checks)

    teams = sorted({r["bat"] for _, r in recs} | {r["opp"] for _, r in recs})
    ti = {t: i for i, t in enumerate(teams)}
    used = sorted({midx[mid] for (mid, _) in store.inn})
    remap = {old: new for new, old in enumerate(used)}
    page = {
        "teams": teams, "how": HOW, "why": WHY, "res": RES,
        "notes": {f"{remap[midx[mid]]}-{inn}": t for (mid, inn), t in NOTES.items() if (mid, inn) in store.inn},
        "matches": [[int(mlist[i]["mid"]) if mlist[i]["mid"].isdigit() else 0, mlist[i]["year"] or "",
                     mlist[i]["venue"] or "", mlist[i]["result"] or "", mlist[i]["test"]] for i in used],
        "inn": [[remap[midx[mid]], ti[r["bat"]], ti.get(r["opp"], 0), inn, r["runs"], r["wkts"] or 0,
                 HOW.index(r["how"]), SRC.index(r["src"]),
                 WHY.index(r["why"]) if r.get("why") else -1,
                 RES.index(result_for(r["bat"], store.matches[mid]["result"])),
                 FO.index(fo_of(mid, inn)), TOSS.index(toss_of(mid, r["bat"]))] for (mid, inn), r in recs],
        "meta": {
            "built": date.today().isoformat(),
            "first": mlist[used[0]]["year"], "last": max(m["date"] or "" for m in mlist),
            "tests": len(used), "innings": len(recs),
            "src": {s: sum(1 for _, r in recs if r["src"] == s) for s in SRC},
            "checked": counts_src,
        },
    }
    tpl = (ROOT / "scorigami" / "template.html").read_text(encoding="utf-8")
    (DOCS / "index.html").write_text(tpl.replace("__DATA__", json.dumps(page, separators=(",", ":"))),
                                     encoding="utf-8")
    return page["meta"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-download", action="store_true", help="don't fetch Cricsheet; use data/tests_male_json.zip")
    ap.add_argument("--csv", default=DATA / "test_matches_kaggle.csv", type=Path)
    ap.add_argument("--cricsheet", default=DATA / "tests_male_json.zip", type=Path)
    ap.add_argument("--statsguru", default=str(DATA / "statsguru*.csv"),
                    help="Statsguru export(s); a glob, so several partial exports can sit side by side")
    a = ap.parse_args(argv)

    store = Store()
    load_csv(store, a.csv)
    seen = {}
    cn = DATA / "cricinfo_new.json"
    if cn.exists():
        seen["cricinfo_new_matches"] = load_cricinfo_new(store, cn)
    if not a.no_download:
        try:
            fetch_cricsheet(a.cricsheet)
        except Exception as e:  # keep building from whatever is on disk
            print(f"! Cricsheet download failed: {e}", file=sys.stderr)
    if a.cricsheet.exists():
        seen["cricsheet_matches"] = load_cricsheet(store, a.cricsheet)
    sg = sorted(Path(a.statsguru).parent.glob(Path(a.statsguru).name))
    if sg:
        seen["statsguru_innings"] = sum(load_statsguru(store, p) for p in sg)
    cc = DATA / "cricinfo_check.json"
    if cc.exists():
        seen["cricinfo_checked"] = load_cricinfo_check(store, cc)
    ov = DATA / "overrides.csv"
    if ov.exists():
        seen["manual_overrides"] = load_overrides(store, ov)
    meta = write_outputs(store, seen)
    by_field = Counter(c["field"] for c in store.checks)
    print(f"{meta['tests']} Tests, {meta['innings']} innings, latest {meta['last']}. "
          f"Sources: {meta['src']}. Corrections logged: {dict(by_field) or 'none'}")


if __name__ == "__main__":
    main()
