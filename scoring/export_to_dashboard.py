"""
export_to_dashboard.py
Copies the index results into the dashboard folder, so the Streamlit app
can show them. Run it after build_index.py.

Copies:
  weekly_index.csv     -> ../sgd-dashboard/data/weekly_index.csv
  weekly_articles.csv  -> ../sgd-dashboard/data/weekly_articles.csv

Run (from the sgd-project folder):
  python3 export_to_dashboard.py
If your dashboard folder is somewhere else, give its path:
  python3 export_to_dashboard.py ~/Documents/sgd-dashboard
"""
import os
import shutil
import sys

FILES = ["weekly_index.csv", "weekly_articles.csv"]


def main():
    dashboard = "../sgd-dashboard"
    if len(sys.argv) > 1:
        dashboard = os.path.expanduser(sys.argv[1])

    target = os.path.join(dashboard, "data")
    if not os.path.isdir(dashboard):
        print("Can't find the dashboard folder:", dashboard)
        print("Give its path, e.g.  python3 export_to_dashboard.py ~/Downloads/sgd-dashboard")
        return
    os.makedirs(target, exist_ok=True)

    for name in FILES:
        if not os.path.exists(name):
            print("Missing", name, "- run python3 build_index.py first")
            return
        shutil.copy(name, os.path.join(target, name))
        print("Copied", name, "->", target)

    print()
    print("Now put the new files on GitHub (see the dashboard README, Part 4).")


main()
