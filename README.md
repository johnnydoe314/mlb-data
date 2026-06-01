# MLB Analysis Automation

Automates daily collection of SP probable pitchers, Statcast pitcher data,
and team batting data so you never need to be at your PC to run the model.

---

## How it works

```
GitHub Actions (daily 10am CT)
    ↓
collect_data.py
    ├── MLB Stats API → probable_pitchers.csv
    ├── Baseball Savant → stats.csv (pitcher Statcast)
    └── Baseball Savant → statcast_hitting.csv (team batting)
    ↓
Commits to GitHub repo
    ↓
raw.githubusercontent.com (publicly readable)
    ↓
run_analysis.py (on any device, in Claude)
    └── auto-fetches fresh files → runs composite model
```

---

## Setup (one time)

### 1. Create a GitHub repo

Create a **public** repo named e.g. `mlb-data`. Public is required so
`raw.githubusercontent.com` serves the files without authentication.

```
github.com/YOUR_USERNAME/mlb-data
```

### 2. Add these files to the repo

```
.github/workflows/daily_data.yml   ← the GitHub Actions workflow
scripts/collect_data.py             ← the data collection script
data/                               ← auto-created by the workflow
run_analysis.py                     ← updated analysis script
```

### 3. Update GITHUB_RAW_BASE in run_analysis.py

```python
GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/YOUR_USERNAME/mlb-data/main/data"
)
```

### 4. Enable GitHub Actions

In your repo → Settings → Actions → General → Allow all actions.
The workflow triggers automatically at 10am CT daily.

You can also trigger it manually:
- Go to Actions tab → "MLB Daily Data Collection" → Run workflow

---

## Usage

### On any device (including mobile via Claude)

Once the repo is set up, just run:
```
python run_analysis.py
```

The script auto-fetches fresh data from your GitHub repo and runs the
composite model. No manual file updates needed.

### Options
```
python run_analysis.py                  # full run, auto-fetch if stale
python run_analysis.py --fetch-only     # just update local data files
python run_analysis.py --force-fetch    # force re-fetch even if fresh
python run_analysis.py --no-fetch       # use local files only
python run_analysis.py --composite-only # skip XLS matchup data
python run_analysis.py --auto           # skip validation gate
```

---

## What gets automated

| Data | Source | Refresh |
|---|---|---|
| Probable pitchers | MLB Stats API | Daily (10am CT) |
| Pitcher Statcast (wOBA gap etc) | Baseball Savant | Daily |
| Team batting xwOBA | Baseball Savant | Daily |
| Pitcher-batter XLS matchups | **Manual** (Baseball Reference) | When you have PC access |

---

## What stays manual

**The Sports Reference matchup XLS** — Baseball Reference actively blocks
automated downloads. You still need to download this from your PC when you
want the third layer of batter-vs-pitcher historical data. The composite
model runs fine without it; the XLS adds optional depth.

**stats.csv updates** — The GitHub Action fetches this automatically from
Baseball Savant daily. You no longer need to manually refresh it.

---

## Triggering from mobile (via Claude)

When you're away from your PC, you can:

1. Trigger the GitHub Action manually from the GitHub mobile app or
   github.com → Actions tab → Run workflow
2. Wait ~2 minutes for it to complete
3. Tell Claude "run the analysis" and it will fetch from your repo
   and run the full composite model

Or just paste the Baseball Reference SP text directly into Claude as
you've been doing — that still works perfectly and takes 10 seconds.

---

## Data files produced

| File | Description |
|---|---|
| `data/probable_pitchers.csv` | Today's confirmed starters |
| `data/stats.csv` | Pitcher Statcast (wOBA, xwOBA, HH%, etc) |
| `data/statcast_hitting.csv` | Team batting Statcast |
| `data/metadata.json` | Timestamp + game count for freshness check |
| `analysis_output/composite_YYYYMMDD.csv` | Qualifying plays output |
