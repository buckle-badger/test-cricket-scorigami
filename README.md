# Men's Test Scorigami

Builds the scorigami page (`docs/index.html`) and three CSVs (`out/`) from:

| Source | Covers | Endings | How it arrives |
|---|---|---|---|
| `data/test_matches_kaggle.csv` ([Kaggle: Test Cricket Matches Dataset (1877–2024)](https://www.kaggle.com/datasets/bhuvaneshprasad/test-matches-dataset-1877-2023), by Bhuvanesh Prasad, Apache License 2.0, see `data/LICENSE-kaggle-dataset.txt`) | Tests 1–2537 (to March 2024) | inferred from batting order and result | already here |
| Cricsheet `tests_male_json.zip` | men's Tests from 2001, updated after each match | exact (`declared`, `forfeited`, retirements) | downloaded on every build |
| `data/statsguru*.csv` | every Test, if you export it | exact (`d` suffix, all-outs shown without a slash) | you export it once with `tools/statsguru_export.js` |

`data/overrides.csv` holds your own corrections (match_id, innings, ending, optional runs/wickets, note) and is applied last. Use it for anything the sources can't tell apart, such as a declaration that ended a drawn match (Sri Lanka 952/6d, 1997).

Later sources overwrite earlier ones for the same innings. Every change to runs, wickets or ending is logged in `out/checks.csv`, which is the record of where the inferred data was wrong.

## Outputs

- `docs/index.html`: the interactive page (Grids, Ranked innings, Table, Wagon wheel, 3D)
- `docs/og.png`, `docs/icon.png`: the link-preview picture and home-screen icon, redrawn on every build (needs Pillow). Set the `SITE_URL` environment variable if the site moves to a new address.
- `out/scorigami_counts.csv`: one row per total 0–999 with counts per ending and a status column. Paste or link this into your Excel sheet to colour it.
- `out/innings.csv`: every innings with its ending and source
- `out/checks.csv`: corrections made by Cricsheet or Statsguru

## Put it online (about 15 minutes, free)

1. Make a free GitHub account if you don't have one, then **New repository** → name it (e.g. `test-scorigami`) → **Public** → Create.
2. On the new repo page click **uploading an existing file** and drag in everything from this folder, including the hidden `.github` folder (on a Mac, press Cmd+Shift+. in Finder to show hidden folders). Commit.
3. **Settings → Actions → General → Workflow permissions** → *Read and write permissions* → Save.
4. **Settings → Pages** → Source: *Deploy from a branch*, Branch `main`, folder `/docs` → Save. After a minute the site is live at `https://<your-username>.github.io/test-scorigami/`.
5. **Actions → Update scorigami → Run workflow** once to check it all works.

From then on the workflow runs every morning. When Cricsheet has added a new Test (usually within a few days of it finishing) it rebuilds the page and the site updates itself. If nothing new has arrived it changes nothing.

Optional: **Settings → Pages → Custom domain** if you buy a domain such as `testscorigami.com`.

### What gets published

Only `docs/index.html` (the page) is served. The repo itself also holds the data files and CSVs; if you'd rather keep those private, use a private repo with a paid GitHub plan, or move just `docs/` to a separate public repo.

### Sources and credit

Keep the credits section in the page footer. Cricsheet data is shared under the Open Data Commons Attribution License, which requires attribution. ESPNcricinfo is credited and linked for every scorecard.

## Check the whole history against Cricinfo (one-off)

1. Open the Statsguru link at the top of `tools/statsguru_export.js` in your normal browser.
2. Open the console (Cmd+Option+J / Ctrl+Shift+J), paste the file, press Enter. It reads every page a couple of seconds apart and downloads `statsguru_team_innings.csv`.
3. Put that file in `data/` and run the build (or push it to GitHub, which runs it for you).
4. Read `out/checks.csv`.

## Run it locally

```
pip install -r requirements.txt
python -m scorigami.build            # downloads Cricsheet
python -m scorigami.build --no-download
python -m pytest -q tests            # fixture tests (the fixtures are synthetic)
```

## Copyright

© 2026 BuckleBadger. All rights reserved; see `LICENSE`. Match data comes from Bhuvanesh Prasad's Kaggle dataset (to March 2024, Apache License 2.0) and from Cricsheet, used under the Open Data Commons Attribution License.
