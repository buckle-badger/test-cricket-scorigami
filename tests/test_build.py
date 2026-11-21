import csv
from pathlib import Path

from scorigami import build

HERE = Path(__file__).parent


def run(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "OUT", tmp_path / "out")
    monkeypatch.setattr(build, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(build, "DATA", tmp_path)  # keep the real Cricinfo check and overrides out of the tests
    build.main(["--no-download", "--csv", str(HERE.parent / "data" / "test_matches_kaggle.csv"), "--cricsheet", str(HERE / "fixture_cricsheet.zip")])
    rows = list(csv.DictReader(open(tmp_path / "out" / "innings.csv")))
    checks = list(csv.DictReader(open(tmp_path / "out" / "checks.csv")))
    return {(r["match_id"], r["innings"]): r for r in rows}, checks


def test_new_match_from_cricsheet(tmp_path, monkeypatch):
    rows, _ = run(tmp_path, monkeypatch)
    r = rows[("9999991", "2")]  # synthetic Multan-style Test after the CSV ends
    assert (r["runs"], r["wickets"], r["ending"]) == ("823", "7", "declared")
    assert rows[("9999991", "3")]["ending"] == "allout"


def test_retired_hurt_is_short_handed_all_out(tmp_path, monkeypatch):
    rows, checks = run(tmp_path, monkeypatch)
    assert rows[("1419831", "4")]["ending"] == "allout_short"
    assert any(c["match_id"] == "1419831" and c["field"] == "how" for c in checks)


def test_page_written(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch)
    html = (tmp_path / "docs" / "index.html").read_text()
    assert "__DATA__" not in html and '"meta"' in html


def test_awarded_and_other_endings():
    """The Oval 2006 innings is 'awarded'; any other unexplained ending stays 'other' (shown only if one turns up)."""
    from scorigami.build import how_final
    assert how_final("other", "England awarded the match (opposition refused to play)") == "awarded"
    assert how_final("other", "Match abandoned") == "other"
    assert how_final("draw", "England awarded the match") == "draw"



def test_gap_check_finds_holes_and_doubles():
    from scorigami import build
    s = build.Store()
    for mid, n, d in [("a", 1, "2026-01-01"), ("b", 2, "2026-01-08"), ("d", 4, "2026-01-20"), ("e", 4, "2026-01-21")]:
        s.add_match(mid, test=n, date=d, venue="X")
        s.put(mid, 1, {"bat": "England", "opp": "India", "inn": 1, "runs": 100, "wkts": 10, "how": "allout", "why": None}, "csv")
    g = build.find_gaps(s)
    assert [(x["kind"], x["test"]) for x in g] == [("missing", 3), ("duplicate", 4)]
    assert g[0]["after"]["test"] == 2 and g[0]["before"]["test"] == 4


def test_gap_issues_are_idempotent():
    from scorigami.gap_issues import plan, title
    gaps = [{"kind": "missing", "test": 3}, {"kind": "missing", "test": 7}]
    issues = [{"number": 1, "title": "Missing Test #3", "state": "open"},              # already open: leave it
              {"number": 2, "title": "Missing Test #7", "state": "closed", "state_reason": "not_planned"},  # silenced
              {"number": 3, "title": "Missing Test #5", "state": "open"},              # filled since: close it
              {"number": 4, "title": "Missing Test #9", "state": "closed", "state_reason": "completed"}]
    to_open, to_close = plan(gaps, issues)
    assert to_open == [] and [i["number"] for i in to_close] == [3]
    # a gap that comes back after being fixed gets a fresh issue
    to_open, _ = plan([{"kind": "missing", "test": 9}], issues)
    assert [title(g) for g in to_open] == ["Missing Test #9"]
    assert plan([], [])[0] == []
