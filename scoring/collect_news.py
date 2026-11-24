"""
SGD news collector - Phase 1
============================

Two modes:

  python collect_news.py
      Reads the RSS feeds and adds every NEW article to news_pool.csv.
      No filtering: the scoring rubric decides relevance later.
      The keyword check is only recorded (keyword_hit column), so you can
      later test what a keyword filter would have wrongly thrown away.
      Run this once a day (RSS feeds only show the last few days).

  python collect_news.py sample
      Randomly picks 80 articles from news_pool.csv and writes
      hand_labels.xlsx for you to score by hand.
      Run this once the pool has 200+ articles.

  python collect_news.py holdout
      Picks 30 articles you haven't labelled (20 the AI found relevant,
      10 it scored 0, shuffled) and writes hand_labels_holdout.xlsx.

Install once:
  pip install feedparser openpyxl requests
"""

import csv
import os
import random
import re
import sys
import time
from datetime import datetime

import feedparser
import requests
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation


# ----------------------------------------------------------------------
# SETTINGS - edit these freely
# ----------------------------------------------------------------------

# Each feed is [source name, feed URL].
# Open each URL in your browser once to check it still works.
FEEDS = [
    ["Business Times", "https://www.businesstimes.com.sg/rss.xml"],
    ["Straits Times Business", "https://www.straitstimes.com/news/business/rss.xml"],
    ["CNA Business", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936"],
    ["CNA Singapore", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=10416"],
]

# These keywords NO LONGER filter anything. Every article is kept.
# We only record whether an article contains one (keyword_hit = yes/no),
# to later measure what a keyword filter would have missed.
KEYWORDS = [
    # MAS policy and the currency
    "MAS", "Monetary Authority", "S$NEER", "Singapore dollar", "Singdollar",
    "exchange rate", "monetary policy", "interest rate", "SORA",
    # inflation and growth
    "inflation", "CPI", "core inflation", "GDP", "growth", "recession",
    "wages", "unemployment", "productivity",
    # trade and global demand
    "exports", "NODX", "trade", "tariff", "semiconductor", "electronics",
    "China", "manufacturing", "PMI",
    # USD and global risk
    "Fed", "Federal Reserve", "Treasury", "US dollar", "yields",
    # energy and food prices
    "oil", "energy", "electricity", "food prices", "commodity",
    # capital flows and politics
    "Budget", "GST", "property", "cooling measures", "foreign investment",
    "family office", "wealth", "election", "METI", "SingStat",
]

POOL_FILE = "news_pool.csv"
LABEL_FILE = "hand_labels.xlsx"
SAMPLE_SIZE = 80
RANDOM_SEED = 42  # fixed seed, so the same pool always gives the same sample

POOL_COLUMNS = ["id", "date_collected", "published", "source",
                "headline", "summary", "link", "keyword_hit"]


# ----------------------------------------------------------------------
# HELPER FUNCTIONS
# ----------------------------------------------------------------------

def fetch_feed(url):
    """
    Download a feed and parse it.
    We download with 'requests' (not feedparser directly) because:
      1. requests brings its own up-to-date security certificates,
         which fixes the common Mac 'CERTIFICATE_VERIFY_FAILED' problem
      2. we can say we are a normal browser, so sites don't block us
    Returns the parsed feed, or None if the download failed.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=20)
    except Exception as error:
        print("  ERROR downloading: " + str(error))
        return None

    if response.status_code != 200:
        print("  ERROR: website replied with status " + str(response.status_code))
        return None

    return feedparser.parse(response.content)


def get_page_teaser(url):
    """
    Some feeds (CNA) leave the summary empty. In that case we open the
    article page and read its meta description: the short teaser sites
    publish for link previews (WhatsApp, LinkedIn etc).
    Returns "" if nothing is found.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=20)
    except Exception:
        return ""
    if response.status_code != 200:
        return ""

    html = response.text

    # Try the two common tags, in order:
    #   <meta property="og:description" content="...">
    #   <meta name="description" content="...">
    patterns = [
        r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"',
        r'<meta[^>]+content="([^"]*)"[^>]+property="og:description"',
        r'<meta[^>]+name="description"[^>]+content="([^"]*)"',
        r'<meta[^>]+content="([^"]*)"[^>]+name="description"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            teaser = match.group(1)
            teaser = teaser.replace("&amp;", "&").replace("&quot;", '"')
            teaser = teaser.replace("&#39;", "'").replace("&#x27;", "'")
            return teaser.strip()
    return ""


def strip_html(text):
    """Remove HTML tags like <p> from RSS summaries."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def contains_word(text, word):
    """
    True if 'word' appears in 'text' as a whole word.
    Whole-word matching stops 'MAS' matching inside 'Christmas'.
    """
    pattern = r"\b" + re.escape(word.lower()) + r"\b"
    if re.search(pattern, text.lower()):
        return True
    return False


def is_relevant(headline, summary):
    """True if any keyword appears. Recorded only, not used to filter."""
    text = headline + " " + summary
    for word in KEYWORDS:
        if contains_word(text, word):
            return True
    return False


def load_pool():
    """Read news_pool.csv into a list of dictionaries (one per article)."""
    pool = []
    if not os.path.exists(POOL_FILE):
        return pool
    with open(POOL_FILE, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pool.append(row)
    return pool


def save_pool(pool):
    """Write the full list of articles back to news_pool.csv."""
    with open(POOL_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=POOL_COLUMNS)
        writer.writeheader()
        for row in pool:
            writer.writerow(row)


# ----------------------------------------------------------------------
# MODE 1: COLLECT
# ----------------------------------------------------------------------

def collect():
    pool = load_pool()

    # Make a list of links we already have, so we never save an article twice
    seen_links = []
    for row in pool:
        seen_links.append(row["link"])

    next_id = len(pool) + 1
    today = datetime.now().strftime("%Y-%m-%d")

    for feed in FEEDS:
        source = feed[0]
        url = feed[1]

        print("Reading " + source + " ...")
        parsed = fetch_feed(url)

        if parsed is None:
            continue

        if len(parsed.entries) == 0:
            print("  WARNING: no articles found. Check the URL in your browser.")
            continue

        found = 0
        new = 0

        for entry in parsed.entries:
            found = found + 1

            headline = strip_html(entry.get("title", ""))
            summary = strip_html(entry.get("summary", ""))
            link = entry.get("link", "")
            published = entry.get("published", "")

            # Skip articles we already saved on an earlier day
            if link in seen_links:
                continue

            # If the feed gave no summary, read the teaser from the page itself
            if summary == "" and link != "":
                summary = get_page_teaser(link)
                time.sleep(1)  # be polite: one page per second

            # Record the keyword check (no filtering)
            if is_relevant(headline, summary):
                keyword_hit = "yes"
            else:
                keyword_hit = "no"

            row = {
                "id": next_id,
                "date_collected": today,
                "published": published,
                "source": source,
                "headline": headline,
                "summary": summary,
                "link": link,
                "keyword_hit": keyword_hit,
            }
            pool.append(row)
            seen_links.append(link)
            next_id = next_id + 1
            new = new + 1

        print("  " + str(found) + " articles in feed, " + str(new) + " new")

    save_pool(pool)
    print("")
    print("Pool now has " + str(len(pool)) + " articles in " + POOL_FILE)
    if len(pool) < 200:
        print("Keep running daily until you have 200+, then run: "
              "python collect_news.py sample")


# ----------------------------------------------------------------------
# MODE 2: SAMPLE 50 AND BUILD THE LABELLING WORKBOOK
# ----------------------------------------------------------------------

def sample():
    pool = load_pool()
    if len(pool) < SAMPLE_SIZE:
        print("Only " + str(len(pool)) + " articles in the pool. "
              "Collect more before sampling.")
        return

    if len(pool) < 200:
        print("Note: pool has fewer than 200 articles. "
              "A bigger pool gives a more representative sample.")

    # Random sample. This matters for PPI: the hand-labelled set must be
    # a random draw from the pool, not articles you picked yourself.
    random.seed(RANDOM_SEED)
    chosen = random.sample(pool, SAMPLE_SIZE)
    write_label_workbook(chosen, LABEL_FILE)
    print("Wrote " + str(SAMPLE_SIZE) + " randomly sampled articles to " + LABEL_FILE)


def write_label_workbook(chosen, filename):
    """The labelling workbook: Labels sheet (yellow cells to fill) + Codebook sheet."""
    wb = Workbook()

    # Styles
    base_font = Font(name="Arial", size=10)
    bold_font = Font(name="Arial", size=10, bold=True)
    header_fill = PatternFill("solid", start_color="D9D9D9")
    input_fill = PatternFill("solid", start_color="FFFF00")  # yellow = fill me in
    wrap = Alignment(wrap_text=True, vertical="top")

    # ---------------- Sheet 1: Labels ----------------
    ws = wb.active
    ws.title = "Labels"

    headers = ["id", "published", "source", "headline", "summary", "link",
               "channel", "relevance (0-3)", "impact (0-3)", "surprise (0-3)",
               "importance", "mas_direction", "notes"]
    widths = [6, 18, 16, 45, 60, 30, 22, 10, 10, 10, 11, 14, 40]

    for col in range(len(headers)):
        cell = ws.cell(row=1, column=col + 1, value=headers[col])
        cell.font = bold_font
        cell.fill = header_fill
        cell.alignment = wrap
        letter = cell.column_letter
        ws.column_dimensions[letter].width = widths[col]

    # Dropdown lists so you can only enter valid values
    channel_list = DataValidation(
        type="list",
        formula1='"Imported inflation,Domestic inflation,MAS signals,'
                 'Growth and output gap,Government policy,Global rates and USD,None"',
        allow_blank=True)
    score_list = DataValidation(type="list", formula1='"0,1,2,3"', allow_blank=True)
    direction_list = DataValidation(
        type="list", formula1='"hawkish,dovish,neutral"', allow_blank=True)
    ws.add_data_validation(channel_list)
    ws.add_data_validation(score_list)
    ws.add_data_validation(direction_list)

    row_number = 2
    for article in chosen:
        values = [int(article["id"]), article["published"], article["source"],
                  article["headline"], article["summary"], article["link"]]
        for col in range(len(values)):
            cell = ws.cell(row=row_number, column=col + 1, value=values[col])
            cell.font = base_font
            cell.alignment = wrap

        # Columns G, H, I, J, L, M are yours to fill in (yellow)
        fill_columns = ["G", "H", "I", "J", "L", "M"]
        for letter in fill_columns:
            cell = ws[letter + str(row_number)]
            cell.fill = input_fill
            cell.font = base_font
            cell.alignment = wrap

        # Column K: importance = relevance x (impact + surprise)
        # Stays blank until all three scores are entered.
        r = str(row_number)
        formula = ('=IF(COUNT(H' + r + ':J' + r + ')=3,'
                   'H' + r + '*(I' + r + '+J' + r + '),"")')
        cell = ws["K" + r]
        cell.value = formula
        cell.font = base_font

        channel_list.add("G" + r)
        score_list.add("H" + r)
        score_list.add("I" + r)
        score_list.add("J" + r)
        direction_list.add("L" + r)

        row_number = row_number + 1

    ws.freeze_panes = "E2"

    # ---------------- Sheet 2: Codebook ----------------
    cb = wb.create_sheet("Codebook")
    cb.column_dimensions["A"].width = 22
    cb.column_dimensions["B"].width = 38
    cb.column_dimensions["C"].width = 38
    cb.column_dimensions["D"].width = 38

    lines = [
        ["HOW TO USE", "", "", ""],
        ["1.", "Fill in the yellow cells on the Labels sheet only.", "", ""],
        ["2.", "Score BEFORE you ever see an AI score (avoid anchoring).", "", ""],
        ["3.", "Judge each article on what was known on its publish date.", "", ""],
        ["4.", "Importance fills itself in: relevance x (impact + surprise).", "", ""],
        ["5.", "Write a short reason in notes. It helps you sharpen this codebook.", "", ""],
        ["", "", "", ""],
        ["THE QUESTION", "Does this story push MAS towards tightening (hawkish) or "
         "easing (dovish) at its next meeting?", "", ""],
        ["", "MAS is forward-looking: inflation OUTLOOK first, growth second.", "", ""],
        ["", "", "", ""],
        ["SCORE", "Relevance", "Impact", "Surprise"],
        ["0", "No link to MAS's inflation or growth outlook",
         "Would not change MAS's view at all", "Already known / repeated news"],
        ["1", "Indirect (one company's results, a minor sector)",
         "Small effect on the outlook", "Slightly new detail"],
        ["2", "Clear link (NODX release, oil move, wage data)",
         "Shifts the inflation or growth outlook a bit", "Somewhat unexpected vs forecasts"],
        ["3", "Directly about MAS policy or core inflation",
         "Could change MAS's decision (oil shock, MAS statement)",
         "Big surprise (Oct 2026 MAS hold)"],
        ["", "", "", ""],
        ["CHANNEL", "What belongs here", "Hawkish example", "Dovish example"],
        ["Imported inflation", "Oil, gas, food, shipping costs, trading partners' inflation",
         "Oil spike", "Oil supply recovers"],
        ["Domestic inflation", "Core CPI, wages, labour market, rents, services prices",
         "Wages accelerating", "Core CPI cools"],
        ["MAS signals", "MAS statements, speeches, Macroeconomic Review, economist polls",
         "MAS flags upside inflation risks", "MAS signals extended pause"],
        ["Growth and output gap", "GDP, NODX, PMI, AI/chip cycle, China and US demand",
         "Strong GDP beat", "AI investment pullback"],
        ["Government policy", "Subsidies, GST, Budget, foreign worker rules, property measures",
         "GST hike", "New healthcare subsidies"],
        ["Global rates and USD", "Fed, US yields, USD strength, global risk mood",
         "Strong USD lifts import prices", "Global rate cuts"],
        ["None", "Not relevant to MAS's decision (give relevance 0)", "", ""],
        ["", "", "", ""],
        ["MAS DIRECTION", "hawkish = pushes MAS to tighten (SGD stronger); "
         "dovish = pushes MAS to ease; neutral = no clear push", "", ""],
        ["", "", "", ""],
        ["EXAMPLE ROW", "(how a filled-in row looks; not part of the sample)", "", ""],
        ["headline", "MAS keeps monetary policy unchanged, contrary to expectations", "", ""],
        ["channel", "MAS signals", "", ""],
        ["relevance / impact / surprise", "3 / 3 / 3", "", ""],
        ["importance", "18", "", ""],
        ["mas_direction", "dovish (hold when a tightening was expected)", "", ""],
        ["notes", "17 of 20 economists expected a steeper slope; signals an extended pause", "", ""],
    ]

    section_titles = ["HOW TO USE", "THE QUESTION", "SCORE", "CHANNEL", "MAS DIRECTION", "EXAMPLE ROW"]

    for i in range(len(lines)):
        for j in range(4):
            cell = cb.cell(row=i + 1, column=j + 1, value=lines[i][j])
            cell.font = base_font
            cell.alignment = wrap
        if lines[i][0] in section_titles:
            for j in range(4):
                cell = cb.cell(row=i + 1, column=j + 1)
                cell.font = bold_font
                cell.fill = header_fill

    wb.save(filename)


# ----------------------------------------------------------------------
# MODE 3: HOLDOUT SAMPLE (fresh articles, more of them relevant)
# ----------------------------------------------------------------------
# A purely random sample is ~80% irrelevant articles (score 0), which teaches
# us little. So we draw from two groups ("strata") separately, using the AI's
# pool scores only to SPLIT the articles, never shown to you:
#   HOLDOUT_RELEVANT    articles the AI scored importance > 0
#   HOLDOUT_IRRELEVANT  articles the AI scored 0 (to catch relevant ones it missed)
# build_index.py then weights each group by its real share of the pool,
# so the PPI correction stays fair.

HOLDOUT_FILE = "hand_labels_holdout.xlsx"
HOLDOUT_RELEVANT = 20
HOLDOUT_IRRELEVANT = 10
AI_POOL_SCORES = "ai_scores_pool_gemma4_31b-cloud.csv"


def labelled_ids(filename):
    ids = []
    if not os.path.exists(filename):
        return ids
    sheet = load_workbook(filename)["Labels"]
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if row[0] is not None:
            ids.append(str(row[0]))
    return ids


def holdout():
    if os.path.exists(HOLDOUT_FILE):
        print(HOLDOUT_FILE + " already exists. Rename or delete it first, "
              "so you don't overwrite your labels.")
        return

    pool = load_pool()
    pool_by_id = {}
    for article in pool:
        pool_by_id[article["id"]] = article

    # Never re-use articles you have already labelled
    used = labelled_ids(LABEL_FILE)

    relevant = []
    irrelevant = []
    with open(AI_POOL_SCORES, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["error"] != "" or row["id"] in used or row["id"] not in pool_by_id:
                continue
            if int(row["importance"]) > 0:
                relevant.append(pool_by_id[row["id"]])
            else:
                irrelevant.append(pool_by_id[row["id"]])

    if len(relevant) < HOLDOUT_RELEVANT or len(irrelevant) < HOLDOUT_IRRELEVANT:
        print("Not enough unlabelled scored articles yet. Collect and score more first.")
        return

    random.seed(RANDOM_SEED + 1)
    chosen = random.sample(relevant, HOLDOUT_RELEVANT)
    chosen = chosen + random.sample(irrelevant, HOLDOUT_IRRELEVANT)
    random.shuffle(chosen)   # mixed up, so you can't tell which group a row came from

    write_label_workbook(chosen, HOLDOUT_FILE)
    print("Wrote " + str(len(chosen)) + " articles to " + HOLDOUT_FILE)
    print("Label them blind (don't look at the AI scores), then run:")
    print("  python3 score_articles.py test holdout")


# ----------------------------------------------------------------------
# RUN
# ----------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "sample":
        sample()
    elif len(sys.argv) > 1 and sys.argv[1] == "holdout":
        holdout()
    else:
        collect()
