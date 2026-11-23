# SGD Weekly dashboard

Shows last calendar week (Monday to Sunday) in two tabs:

- **Markets:** how the SGD did against 14 currencies, regional equities, US rates,
  the dollar and oil. GitHub downloads this data by itself every weekday.
- **MAS Hawkishness Index:** the weekly index, corrected value and range, outside
  pressure vs MAS's own signal, which kinds of news moved it, and the week's
  biggest stories. You copy this in from `sgd-project` each Monday (Part 4),
  because the scoring runs on your Mac.

The headline sentence at the top is written automatically, ready for the weekly call.

## Files

| File | What it does |
|---|---|
| `fetch_data.py` | Downloads 3 years of daily closes from Yahoo Finance into `data/markets.csv` |
| `app.py` | The Streamlit dashboard |
| `.github/workflows/daily.yml` | Runs `fetch_data.py` every weekday at 06:30 Singapore time and saves the data |
| `.streamlit/config.toml` | Colours and fonts |
| `data/weekly_index.csv`, `data/weekly_articles.csv` | The index results, copied from `sgd-project` by `export_to_dashboard.py` |
| `requirements.txt` | The Python packages needed |

## Part 1: run it on your Mac (one command at a time)

1. `cd ~/Downloads/sgd-dashboard`
2. `pip3 install -r requirements.txt`
3. `python3 fetch_data.py` (downloads real data, about 30 seconds)
4. `streamlit run app.py` (opens in your browser; press Ctrl+C in Terminal to stop)

## Part 2: put it on GitHub

1. Make an account at github.com if you don't have one.
2. Click **New repository**. Name it `sgd-dashboard`. Choose **Private** until Macro Hive approves making it public.
3. On the new repo's page, click **uploading an existing file** and drag in everything from the folder, including the hidden `.github` and `.streamlit` folders. (In Finder, press Cmd+Shift+. to show hidden folders.)
4. Go to the **Actions** tab, click **Daily data update**, then **Run workflow**. After about a minute, `data/markets.csv` should update. From now on it runs by itself every weekday.

## Part 3: put it online with Streamlit Community Cloud (free)

1. Go to share.streamlit.io and sign in with your GitHub account.
2. Click **Create app**, pick the `sgd-dashboard` repo, branch `main`, file `app.py`.
3. Click **Deploy**. You get a link to share with Liang.
4. Each time GitHub saves new data, the app picks it up on its next load.

## Part 4: update the index every Monday (one command at a time)

1. `cd ~/Downloads/sgd-project`
2. `python3 build_index.py`
3. `python3 export_to_dashboard.py ~/Downloads/sgd-dashboard`
4. On GitHub, open the repo, click the `data` folder, then **Add file → Upload files**.
   Drag in `weekly_index.csv` and `weekly_articles.csv` from `~/Downloads/sgd-dashboard/data`,
   then click **Commit changes**.
5. Open the Streamlit link. The index tab shows the new week within a few minutes.

## What comes next

1. SGS yields and SORA from MAS (not on Yahoo).
2. Replicated daily S$NEER and its position in MAS's band.
3. News collection and the hybrid scorer in the GitHub workflow, so the index updates by itself too.
