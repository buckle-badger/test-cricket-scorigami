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
