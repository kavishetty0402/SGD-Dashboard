"""
SGD news index - Phase 3: the weekly MAS Hawkishness Index
==========================================================

  python3 build_index.py            (groups duplicates with Gemma via Google AI Studio)
  python3 build_index.py noai       (groups only near-identical headlines)

Reads:
  news_pool.csv                      dates, sources, links
  ai_scores_pool_<model>.csv         the AI's scores for every article
  hand_labels.xlsx + ai_scores_test_<model>.csv              } your labels and the AI's
  hand_labels_holdout.xlsx + ai_scores_holdout_<model>.csv    } scores on them (PPI)

Writes:
  weekly_index.csv          one row per week and version (all copies / events once)
  weekly_articles.csv       every relevant article: week, event group, contribution
  event_groups.csv          which articles report the same event (cache, editable)
  mas_index_chart.png       the chart

HOW THE INDEX WORKS (per week, Monday-Sunday, Singapore time)
  For each article:
      w   = channel weight (CHANNEL_WEIGHTS below)
      d   = +1 hawkish, -1 dovish, 0 neutral
      imp = importance = relevance x (impact + surprise), 0 to 18

      push   = w x d x imp     (signed: which way, and how hard)
      weight = w x imp         (unsigned: how much the article matters)

  Index     = 100 x (sum of push) / (sum of weight)      from -100 to +100
  Intensity = sum of weight                               how much MAS news there was

  +100 means every relevant article pointed hawkish. Neutral relevant articles
  add to the weight but not to the push, so they pull the index towards 0.

THREE MORE NUMBERS PER WEEK
  Outside pressure = the same index, leaving out MAS's own words (the MAS
                     signals channel). What the world is pushing MAS towards.
  MAS's own tone   = the index using ONLY MAS's own words (MAS signals articles
                     whose mas_source is "MAS itself"; economists' views
                     about MAS count as outside pressure instead): which way
                     MAS's decisions, speeches and guidance lean. Blank in
                     weeks with no MAS news. With only 1-2 MAS stories a
                     week it is usually +100 or -100, so we also keep
  MAS net signal   = the total push of MAS's own words (a full surprise
                     decision is about 18; a speech hinting at a change
                     about 9). This shows how STRONG MAS's signal was.
  Net pressure     = total push, NOT divided by total weight. Grows with the
                     amount of one-sided news, not just how one-sided it is.

TWO VERSIONS
  all copies:   every article gets one vote (coverage = salience)
  events once:  an event reported in n articles gives each copy 1/n of a vote

PPI CORRECTION (prediction-powered inference)
  On your hand labels we know both your scores and the AI's. The average
  gap (you - AI), for push and for weight separately, is the AI's bias. We add
  that gap back to the week's averages:
      corrected index = 100 x (avg push + push gap) / (avg weight + weight gap)
  The gap is measured separately for two groups: articles the AI found relevant
  and articles it scored 0. Each week mixes the two gaps in that week's
  proportions. (This lets the holdout over-sample relevant articles fairly.)
  The 95% band comes from re-drawing the hand labels at random (a bootstrap)
  many times. The week's articles are not redrawn: we read all of them, so the
  only doubt is how accurately the AI scored them.

Install once: pip install requests openpyxl matplotlib
"""

import csv
import json
import os
import random
import re
import sys
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import ai_client
from ai_client import ask_gemma
from openpyxl import load_workbook

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ----------------------------------------------------------------------
# SETTINGS
# ----------------------------------------------------------------------

MODEL = ai_client.SCORES_NAME      # set by PROVIDER in ai_client.py

POOL_FILE = "news_pool.csv"
LABEL_FILE = "hand_labels.xlsx"
GROUPS_FILE = "event_groups.csv"
OVERRIDES_FILE = "overrides.csv"   # your manual corrections (see override_score.py)
WEEKLY_FILE = "weekly_index.csv"
ARTICLES_FILE = "weekly_articles.csv"
CHART_FILE = "mas_index_chart.png"
NEWS_MAP_FILE = "mas_news_map.png"
NEWS_MAP_WEEKS = 2          # how many recent weeks the news map shows
NEWS_MAP_LABELS = 6         # how many events get a headline label per week

# Judgement-based for now. Later: estimate from MAS decisions or SGD moves.
CHANNEL_WEIGHTS = {
    "Imported inflation": 1.0,
    "Domestic inflation": 1.0,
    "MAS signals": 1.0,
    "Growth and output gap": 0.7,
    "Government policy": 0.5,
    "Global rates and USD": 0.4,
    "None": 0.0,
}
USD_CHANNEL = "Global rates and USD"
MAS_CHANNEL = "MAS signals"

# Which channels to leave out for each version of the index
EXCLUDE_NOTHING = []
EXCLUDE_USD = [USD_CHANNEL]                 # Singapore-specific news
EXCLUDE_MAS = [MAS_CHANNEL]                 # outside pressure on MAS
# Articles in the MAS signals channel where OTHER people (economists, analysts)
# talk about MAS. They count in the main index and in outside pressure, but
# not in MAS's own signal.
MAS_COMMENTARY = "MAS commentary"
# Economists' views about MAS matter, but less than MAS's own words:
# their channel weight is this, instead of the MAS signals weight of 1.0
COMMENTARY_WEIGHT = 0.7
ONLY_MAS = [MAS_COMMENTARY]                 # MAS's own tone: drop everything else
for _channel in CHANNEL_WEIGHTS:
    if _channel != MAS_CHANNEL:
        ONLY_MAS.append(_channel)

# Two headlines sharing at least this share of their words count as copies
# of the same article (used with or without the AI grouping).
SAME_HEADLINE_OVERLAP = 0.6

BOOTSTRAP_DRAWS = 1000
RANDOM_SEED = 42

SINGAPORE_TIME = timezone(timedelta(hours=8))

STOP_WORDS = ["the", "and", "for", "with", "from", "its", "this", "that",
              "after", "over", "into", "says", "said", "will", "has", "have",
              "more", "than", "amid", "are", "was", "were"]


def model_file(kind):
    safe_model = MODEL.replace(":", "_").replace("/", "_")
    return "ai_scores_" + kind + "_" + safe_model + ".csv"


def direction_number(direction):
    if direction == "hawkish":
        return 1
    if direction == "dovish":
        return -1
    return 0


def push_and_weight(channel, direction, importance, exclude):
    """The two numbers each article feeds into the index.
    exclude = list of channels to leave out (they count as 0)."""
    w = CHANNEL_WEIGHTS.get(channel, 0.0)
    if channel in exclude:
        w = 0.0
    push = w * direction_number(direction) * importance
    weight = w * importance
    return push, weight


# ----------------------------------------------------------------------
# STEP 1: LOAD THE SCORED ARTICLES
# ----------------------------------------------------------------------

def week_end_of(day):
    """The Sunday that ends this day's Monday-Sunday week."""
    return day + timedelta(days=6 - day.weekday())


def load_articles():
    pool = {}
    with open(POOL_FILE, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            pool[row["id"]] = row

    articles = []
    no_date = 0
    with open(model_file("pool"), "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["error"] != "":
                continue
            if row["id"] not in pool:
                continue
            info = pool[row["id"]]
            try:
                published = parsedate_to_datetime(info["published"])
                published = published.astimezone(SINGAPORE_TIME)
            except Exception:
                no_date = no_date + 1
                continue
            day = published.date()
            articles.append({
                "id": row["id"],
                "headline": info["headline"],
                "summary": info["summary"],
                "source": info["source"],
                "link": info["link"],
                "day": day,
                "week_end": week_end_of(day),
                "channel": row["channel"],
                "relevance": int(row["relevance"]),
                "impact": int(row["impact"]),
                "surprise": int(row["surprise"]),
                "importance": int(row["importance"]),
                "direction": row["mas_direction"],
                "mas_source": row.get("mas_source", "") or "",
                "ai_summary": row["summary"],
                "group": row["id"],   # filled in by the duplicate step
                "copies": 1,
            })

    apply_overrides(articles)

    scored_ids = []
    for article in articles:
        scored_ids.append(article["id"])
    unscored = 0
    for article_id in pool:
        if article_id not in scored_ids:
            unscored = unscored + 1

    print(str(len(articles)) + " scored articles loaded")
    if unscored > 0:
        print("  note: " + str(unscored) + " pool articles not scored yet "
              "(run: python3 score_articles.py pool)")
    if no_date > 0:
        print("  note: " + str(no_date) + " articles skipped (unreadable date)")
    return articles


def apply_overrides(articles):
    """Your manual corrections win over the AI's scores (overrides.csv:
    id, field, value). Importance is recalculated afterwards."""
    if not os.path.exists(OVERRIDES_FILE):
        return
    changes = []
    with open(OVERRIDES_FILE, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            changes.append(row)
    count = 0
    for article in articles:
        changed = False
        for change in changes:
            if change["id"] != article["id"]:
                continue
            field = change["field"]
            value = change["value"]
            if field in ["relevance", "impact", "surprise"]:
                article[field] = int(value)
            elif field == "direction" or field == "channel" or field == "mas_source":
                article[field] = value
            changed = True
        if changed:
            article["importance"] = article["relevance"] * (article["impact"] + article["surprise"])
            count = count + 1
    if count > 0:
        print("  applied your overrides to " + str(count) + " articles")


def list_weeks(articles):
    weeks = []
    for article in articles:
        if article["week_end"] not in weeks:
            weeks.append(article["week_end"])
    weeks.sort()
    return weeks


# ----------------------------------------------------------------------
# STEP 2: FIND DUPLICATES (same event reported more than once)
# ----------------------------------------------------------------------
# Only relevant articles (importance > 0) are grouped: the others add
# nothing to the index, so how often they were repeated doesn't matter.
# Groups never cross weeks: each week counts each event once.

def headline_words(text):
    words = []
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if len(word) > 2 and word not in STOP_WORDS and word not in words:
            words.append(word)
    return words


def word_overlap(words_a, words_b):
    """Shared words divided by all distinct words (0 = none, 1 = identical)."""
    shared = 0
    for word in words_a:
        if word in words_b:
            shared = shared + 1
    total = len(words_a) + len(words_b) - shared
    if total == 0:
        return 0.0
    return shared / total


def merge_groups(articles, id_a, id_b):
    """Put two articles (and everything already grouped with them) together."""
    group_a = ""
    group_b = ""
    for article in articles:
        if article["id"] == id_a:
            group_a = article["group"]
        if article["id"] == id_b:
            group_b = article["group"]
    if group_a == "" or group_b == "" or group_a == group_b:
        return
    for article in articles:
        if article["group"] == group_b:
            article["group"] = group_a


def group_same_headlines(week_articles):
    for i in range(len(week_articles)):
        words_i = headline_words(week_articles[i]["headline"])
        for j in range(i + 1, len(week_articles)):
            words_j = headline_words(week_articles[j]["headline"])
            if word_overlap(words_i, words_j) >= SAME_HEADLINE_OVERLAP:
                merge_groups(week_articles, week_articles[i]["id"],
                             week_articles[j]["id"])


GROUPING_INSTRUCTIONS = """You are given news articles from one week. Find articles
that report the SAME underlying news event, so each event can be counted once.

Same event means: the same data release (e.g. September exports, US CPI), the
same policy decision or announcement, or the same market move on the same day.
A market report whose main story is a data release (e.g. "stocks fall after hot
US inflation data") belongs with that release.

Different events: articles that only share a theme (two different days of oil
price moves, two different companies' AI news, CPI and PPI releases).
When unsure, keep articles separate.

Reply with ONLY a JSON object listing groups of 2 or more article ids:
{"groups": [[12, 40], [7, 19, 88]]}
If there are no duplicates, reply {"groups": []}"""


def ask_ai_for_groups(week_articles):
    """Returns a list of groups (lists of ids). Raises an error if Gemma fails."""
    lines = []
    for article in week_articles:
        lines.append(article["id"] + " | " + str(article["day"]) + " | "
                     + article["headline"] + " | " + article["summary"][:200])

    text = ask_gemma(GROUPING_INSTRUCTIONS, "\n".join(lines))
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end < start:
        raise Exception("no JSON in reply")
    raw_groups = json.loads(text[start:end + 1]).get("groups", [])

    # Keep only valid ids, each id in at most one group
    valid_ids = []
    for article in week_articles:
        valid_ids.append(article["id"])
    used = []
    groups = []
    for raw in raw_groups:
        group = []
        for item in raw:
            item_id = str(item).strip()
            if item_id in valid_ids and item_id not in used:
                group.append(item_id)
                used.append(item_id)
        if len(group) >= 2:
            groups.append(group)
    return groups


def load_group_cache():
    """{week_end text: {"ids": [...], "groups": [[...], ...]}}"""
    cache = {}
    if not os.path.exists(GROUPS_FILE):
        return cache
    with open(GROUPS_FILE, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            week = row["week_end"]
            if week not in cache:
                cache[week] = {"ids": [], "group_of": {}}
            cache[week]["ids"].append(row["id"])
            cache[week]["group_of"][row["id"]] = row["group"]
    return cache


def save_group_cache(cache_rows):
    with open(GROUPS_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["week_end", "id", "group", "headline"])
        writer.writeheader()
        for row in cache_rows:
            writer.writerow(row)


def find_duplicates(articles, use_ai):
    cache = load_group_cache()
    cache_rows = []

    for week in list_weeks(articles):
        week_articles = []
        for article in articles:
            if article["week_end"] == week and article["importance"] > 0:
                week_articles.append(article)
        week_ids = []
        for article in week_articles:
            week_ids.append(article["id"])

        week_text = str(week)
        cached = cache.get(week_text)
        same_ids = False
        if cached is not None:
            same_ids = sorted(cached["ids"]) == sorted(week_ids)

        if same_ids:
            # Re-use the saved groups (including any hand edits you made)
            for article in week_articles:
                article["group"] = cached["group_of"][article["id"]]
            how = "saved groups"
        else:
            group_same_headlines(week_articles)
            how = "headline matching only"
            if use_ai and len(week_articles) >= 2:
                try:
                    ai_groups = ask_ai_for_groups(week_articles)
                    for group in ai_groups:
                        for k in range(1, len(group)):
                            merge_groups(week_articles, group[0], group[k])
                    how = "AI grouping"
                except Exception as error:
                    print("  AI grouping failed for week ending " + week_text
                          + " (" + str(error)[:80] + "), using headline matching")

        # Count copies in each group
        for article in week_articles:
            copies = 0
            for other in week_articles:
                if other["group"] == article["group"]:
                    copies = copies + 1
            article["copies"] = copies

        events = []
        for article in week_articles:
            if article["group"] not in events:
                events.append(article["group"])
        print("  week ending " + week_text + ": " + str(len(week_articles))
              + " relevant articles = " + str(len(events)) + " events (" + how + ")")

        # Only AI or saved groups go in the cache; headline-only results are
        # recomputed next time so the AI gets another chance.
        if how != "headline matching only" or len(week_articles) < 2:
            for article in week_articles:
                cache_rows.append({"week_end": week_text, "id": article["id"],
                                   "group": article["group"],
                                   "headline": article["headline"]})

    save_group_cache(cache_rows)

    # One event, one speaker: if any copy of an event is MAS's own decision or
    # statement, every MAS signals copy of that event counts as MAS itself
    for article in articles:
        if article["channel"] != MAS_CHANNEL or article["importance"] == 0:
            continue
        for other in articles:
            if (other["week_end"] == article["week_end"] and other["group"] == article["group"]
                    and other["mas_source"] == "MAS itself"):
                article["mas_source"] = "MAS itself"


# ----------------------------------------------------------------------
# STEP 3: THE PPI CORRECTION (from the 80 hand labels)
# ----------------------------------------------------------------------

LABEL_SETS = [
    # (your labels,                the AI's scores on the same articles)
    ("hand_labels.xlsx",           "test"),
    ("hand_labels_holdout.xlsx",   "holdout"),
]

STRATA = ["AI relevant", "AI scored 0"]


def stratum_of(ai_importance):
    """Which group an article falls in, by the AI's own score."""
    if ai_importance > 0:
        return "AI relevant"
    return "AI scored 0"


def load_label_pairs():
    """List of [your scores, AI scores, group] for each hand-labelled article."""
    pairs = []
    for label_file, kind in LABEL_SETS:
        ai_file = model_file(kind)
        if not os.path.exists(label_file) or not os.path.exists(ai_file):
            continue

        ai = {}
        with open(ai_file, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row["error"] == "":
                    ai[str(row["id"])] = row

        sheet = load_workbook(label_file)["Labels"]
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if row[0] is None or str(row[0]) not in ai:
                continue
            if row[7] is None or row[8] is None or row[9] is None or row[11] is None:
                continue   # not labelled yet
            human = {
                "channel": str(row[6]),
                "importance": int(row[7]) * (int(row[8]) + int(row[9])),
                "direction": str(row[11]),
            }
            machine = ai[str(row[0])]
            robot = {
                "channel": machine["channel"],
                "importance": int(machine["importance"]),
                "direction": machine["mas_direction"],
            }
            pairs.append([human, robot, stratum_of(robot["importance"])])
    return pairs


def label_gaps(pairs, exclude):
    """Per hand-labelled article: [your push - AI push, your weight - AI weight, group]."""
    gaps = []
    for pair in pairs:
        h_push, h_weight = push_and_weight(pair[0]["channel"], pair[0]["direction"],
                                           pair[0]["importance"], exclude)
        a_push, a_weight = push_and_weight(pair[1]["channel"], pair[1]["direction"],
                                           pair[1]["importance"], exclude)
        gaps.append([h_push - a_push, h_weight - a_weight, pair[2]])
    return gaps


def gaps_by_stratum(gaps):
    """Average gap inside each group: {group: [push gap, weight gap, how many labels]}."""
    averages = {}
    for stratum in STRATA:
        push_total = 0.0
        weight_total = 0.0
        count = 0
        for gap in gaps:
            if gap[2] == stratum:
                push_total = push_total + gap[0]
                weight_total = weight_total + gap[1]
                count = count + 1
        if count > 0:
            averages[stratum] = [push_total / count, weight_total / count, count]
        else:
            averages[stratum] = [0.0, 0.0, 0]
    return averages


# ----------------------------------------------------------------------
# STEP 4: WEEKLY NUMBERS
# ----------------------------------------------------------------------

def exclusion_key(article):
    """The channel name used when leaving channels out. MAS signals articles
    where others talk about MAS get their own key, so they can be treated
    as outside pressure rather than MAS's own words."""
    if article["channel"] == MAS_CHANNEL and article.get("mas_source", "") == "others":
        return MAS_COMMENTARY
    return article["channel"]


def article_push_weight(article):
    """Push and weight for one article, with commentary about MAS weighted
    less than MAS's own words."""
    push, weight = push_and_weight(article["channel"], article["direction"],
                                   article["importance"], EXCLUDE_NOTHING)
    if exclusion_key(article) == MAS_COMMENTARY:
        scale = COMMENTARY_WEIGHT / CHANNEL_WEIGHTS[MAS_CHANNEL]
        push = push * scale
        weight = weight * scale
    return push, weight


def article_numbers(week_articles, version, exclude):
    """List of [vote, push, weight, group] for each article in the week."""
    rows = []
    for article in week_articles:
        vote = 1.0
        if version == "events once":
            vote = 1.0 / article["copies"]
        if exclusion_key(article) in exclude:
            push = 0.0
            weight = 0.0
        else:
            push, weight = article_push_weight(article)
        rows.append([vote, push, weight, stratum_of(article["importance"])])
    return rows


def index_from(rows):
    """Plain index: 100 x sum(vote x push) / sum(vote x weight)."""
    push_total = 0.0
    weight_total = 0.0
    for row in rows:
        push_total = push_total + row[0] * row[1]
        weight_total = weight_total + row[0] * row[2]
    if weight_total <= 0:
        return None, 0.0
    return 100 * push_total / weight_total, weight_total


def net_from(rows):
    """Net pressure: the total push, NOT divided by the total weight.
    Bigger when there is more one-sided news, not just more one-sided."""
    total = 0.0
    for row in rows:
        total = total + row[0] * row[1]
    return total


def corrected_index_from(rows, averages):
    """PPI: add the hand-label gaps to the week's average push and weight.

    The gaps are measured separately for articles the AI found relevant and
    articles it scored 0, then mixed in the proportions of THIS week's news.
    """
    votes = 0.0
    push_total = 0.0
    weight_total = 0.0
    votes_in = {}
    for stratum in STRATA:
        votes_in[stratum] = 0.0
    for row in rows:
        votes = votes + row[0]
        push_total = push_total + row[0] * row[1]
        weight_total = weight_total + row[0] * row[2]
        votes_in[row[3]] = votes_in[row[3]] + row[0]
    if votes == 0:
        return None, 0.0

    push_fix = 0.0
    weight_fix = 0.0
    for stratum in STRATA:
        share = votes_in[stratum] / votes
        push_fix = push_fix + share * averages[stratum][0]
        weight_fix = weight_fix + share * averages[stratum][1]

    avg_push = push_total / votes + push_fix
    avg_weight = weight_total / votes + weight_fix
    if avg_weight <= 0:
        return None, 0.0
    index = 100 * avg_push / avg_weight
    # keep within the scale's limits
    if index > 100:
        index = 100.0
    if index < -100:
        index = -100.0
    return index, avg_weight * votes


def bootstrap_band(rows, gaps):
    """95% band for the corrected index.

    Only the hand labels are redrawn at random. The week's articles stay fixed:
    we read every article that week, so the only real doubt is how accurately
    the AI scored them, and that is what the hand labels measure.
    Labels are redrawn inside each group, keeping each group's size.
    """
    if len(gaps) == 0 or len(rows) == 0:
        return None, None
    groups = {}
    for stratum in STRATA:
        groups[stratum] = []
    for gap in gaps:
        groups[gap[2]].append(gap)

    results = []
    for draw in range(BOOTSTRAP_DRAWS):
        new_gaps = []
        for stratum in STRATA:
            members = groups[stratum]
            for k in range(len(members)):
                new_gaps.append(members[random.randrange(len(members))])
        value, intensity = corrected_index_from(rows, gaps_by_stratum(new_gaps))
        if value is not None:
            results.append(value)
    if len(results) < 20:
        return None, None
    results.sort()
    low = results[int(0.025 * len(results))]
    high = results[int(0.975 * len(results)) - 1]
    return low, high


def channel_contributions(rows_by_article, week_articles, total_weight):
    """Each channel's share of the index. They add up to the index."""
    contributions = {}
    for channel in CHANNEL_WEIGHTS:
        if channel != "None":
            contributions[channel] = 0.0
    if total_weight <= 0:
        return contributions
    for k in range(len(week_articles)):
        channel = week_articles[k]["channel"]
        if channel in contributions:
            row = rows_by_article[k]
            contributions[channel] = contributions[channel] + 100 * row[0] * row[1] / total_weight
    return contributions


def build_weekly(articles, pairs):
    random.seed(RANDOM_SEED)
    gaps_all = label_gaps(pairs, EXCLUDE_NOTHING)
    gaps_ex_usd = label_gaps(pairs, EXCLUDE_USD)
    gaps_pressure = label_gaps(pairs, EXCLUDE_MAS)
    averages = gaps_by_stratum(gaps_all)
    averages_ex = gaps_by_stratum(gaps_ex_usd)
    averages_pressure = gaps_by_stratum(gaps_pressure)

    today = datetime.now(SINGAPORE_TIME).date()
    first_day = articles[0]["day"]
    for article in articles:
        if article["day"] < first_day:
            first_day = article["day"]
    results = []
    for week in list_weeks(articles):
        week_articles = []
        days = []
        for article in articles:
            if article["week_end"] == week:
                week_articles.append(article)
                if article["day"] not in days:
                    days.append(article["day"])
        relevant = 0
        for article in week_articles:
            if article["importance"] > 0:
                relevant = relevant + 1

        for version in ["all copies", "events once"]:
            rows = article_numbers(week_articles, version, EXCLUDE_NOTHING)
            rows_ex = article_numbers(week_articles, version, EXCLUDE_USD)
            rows_pressure = article_numbers(week_articles, version, EXCLUDE_MAS)
            rows_mas = article_numbers(week_articles, version, ONLY_MAS)
            index, intensity = index_from(rows)
            index_ex, intensity_ex = index_from(rows_ex)
            pressure, pressure_intensity = index_from(rows_pressure)
            mas_tone, mas_intensity = index_from(rows_mas)

            result = {
                "week_end": week,
                "version": version,
                # complete = the week is over and we were collecting all of it
                "complete": week < today and week - timedelta(days=6) >= first_day,
                "days_with_news": len(days),
                "articles": len(week_articles),
                "relevant_articles": relevant,
                "index": index,
                "intensity": intensity,
                "index_ex_usd": index_ex,
                "ppi_index": None, "ppi_low": None, "ppi_high": None,
                "ppi_intensity": None, "ppi_index_ex_usd": None,
                # Net pressure: total push, not divided (size AND direction)
                "net_pressure": net_from(rows),
                # Outside pressure: every channel except MAS's own words
                "pressure_index": pressure,
                "pressure_net": net_from(rows_pressure),
                "ppi_pressure": None, "ppi_pressure_low": None, "ppi_pressure_high": None,
                # MAS's own tone: decisions, speeches, guidance only
                # (no PPI: too few hand labels are about MAS to correct it)
                "mas_tone": mas_tone,
                "mas_net": net_from(rows_mas),
                "mas_news": mas_intensity,
            }
            if len(pairs) > 0:
                value, value_intensity = corrected_index_from(rows, averages)
                low, high = bootstrap_band(rows, gaps_all)
                value_ex, ignore = corrected_index_from(rows_ex, averages_ex)
                result["ppi_index"] = value
                result["ppi_low"] = low
                result["ppi_high"] = high
                result["ppi_intensity"] = value_intensity
                result["ppi_index_ex_usd"] = value_ex
                value_p, ignore = corrected_index_from(rows_pressure, averages_pressure)
                low_p, high_p = bootstrap_band(rows_pressure, gaps_pressure)
                result["ppi_pressure"] = value_p
                result["ppi_pressure_low"] = low_p
                result["ppi_pressure_high"] = high_p

            contributions = channel_contributions(rows, week_articles, intensity)
            for channel in contributions:
                result["contrib: " + channel] = contributions[channel]
            results.append(result)

    # 4-week moving average of the plain index (each version separately)
    for version in ["all copies", "events once"]:
        history = []
        for result in results:
            if result["version"] == version:
                history.append(result["index"])
                recent = history[-4:]
                total = 0.0
                count = 0
                for value in recent:
                    if value is not None:
                        total = total + value
                        count = count + 1
                if count > 0:
                    result["index_4wk_avg"] = total / count
                else:
                    result["index_4wk_avg"] = None

    return results, averages


# ----------------------------------------------------------------------
# STEP 5: SAVE, PRINT AND CHART
# ----------------------------------------------------------------------

def rounded(value, places):
    if value is None:
        return ""
    return round(value, places)


def save_weekly(results):
    columns = ["week_end", "version", "complete", "days_with_news", "articles",
               "relevant_articles", "index", "index_4wk_avg", "intensity",
               "index_ex_usd", "ppi_index", "ppi_low", "ppi_high",
               "ppi_intensity", "ppi_index_ex_usd", "net_pressure",
               "pressure_index", "pressure_net", "ppi_pressure", "ppi_pressure_low",
               "ppi_pressure_high", "mas_tone", "mas_net", "mas_news"]
    for channel in CHANNEL_WEIGHTS:
        if channel != "None":
            columns.append("contrib: " + channel)
    with open(WEEKLY_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for result in results:
            row = {}
            for column in columns:
                value = result.get(column)
                if isinstance(value, float):
                    value = rounded(value, 1)
                if value is None:
                    value = ""
                row[column] = value
            writer.writerow(row)


def save_articles(articles):
    columns = ["week_end", "day", "id", "source", "channel", "direction",
               "importance", "push", "copies", "group", "headline", "ai_summary", "link"]
    rows = []
    for article in articles:
        if article["importance"] == 0:
            continue
        push, weight = article_push_weight(article)
        rows.append({"week_end": article["week_end"], "day": article["day"],
                     "id": article["id"], "source": article["source"],
                     "channel": article["channel"], "direction": article["direction"],
                     "importance": article["importance"], "push": round(push, 1),
                     "copies": article["copies"], "group": article["group"],
                     "headline": article["headline"],
                     "ai_summary": article["ai_summary"], "link": article["link"]})
    rows.sort(key=lambda row: (str(row["week_end"]), row["group"]))
    with open(ARTICLES_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def top_stories(articles, week, how_many):
    """Biggest pushes of the week, one article per event."""
    best_per_group = {}
    for article in articles:
        if article["week_end"] != week or article["importance"] == 0:
            continue
        push, weight = article_push_weight(article)
        if push == 0:
            continue
        group = article["group"]
        if group not in best_per_group or abs(push) > abs(best_per_group[group][0]):
            best_per_group[group] = [push, article]
    stories = []
    for group in best_per_group:
        stories.append(best_per_group[group])
    stories.sort(key=lambda item: abs(item[0]), reverse=True)
    return stories[:how_many]


def print_summary(results, articles, gap_info):
    print("")
    print("=" * 72)
    print("MAS HAWKISHNESS INDEX   (+100 = all hawkish, -100 = all dovish)")
    print("=" * 72)
    total_labels = 0
    for stratum in STRATA:
        total_labels = total_labels + gap_info[stratum][2]
    if total_labels > 0:
        print("PPI gaps from " + str(total_labels) + " hand labels (you - AI), per article:")
        for stratum in STRATA:
            item = gap_info[stratum]
            line = ("  " + stratum.ljust(12) + str(item[2]).rjust(4) + " labels   push "
                    + str(round(item[0], 3)).rjust(7) + "   weight " + str(round(item[1], 3)).rjust(7))
            if item[2] < 10:
                line = line + "   (few labels: gap is rough)"
            print(line)
    else:
        print("PPI correction skipped: no hand labels with matching AI test scores")
    print("")
    print("week ending   version        index  ppi [95% band]       ex-USD  intensity  4wk")
    for result in results:
        note = ""
        if not result["complete"]:
            note = "  (partial: " + str(result["days_with_news"]) + " days)"
        band = ""
        if result["ppi_low"] is not None:
            band = "[" + str(round(result["ppi_low"])) + ", " + str(round(result["ppi_high"])) + "]"
        print(str(result["week_end"]).ljust(14) + result["version"].ljust(13)
              + str(rounded(result["index"], 1)).rjust(7)
              + str(rounded(result["ppi_index"], 1)).rjust(7) + " " + band.ljust(13)
              + str(rounded(result["index_ex_usd"], 1)).rjust(7)
              + str(rounded(result["intensity"], 1)).rjust(11)
              + str(rounded(result["index_4wk_avg"], 1)).rjust(7) + note)

    print("")
    print("OUTSIDE PRESSURE vs MAS'S OWN TONE   (each story counted once)")
    print("week ending   pressure  ppi [95% band]   MAS net signal  net pressure")
    for result in results:
        if result["version"] != "events once":
            continue
        band = ""
        if result["ppi_pressure_low"] is not None:
            band = ("[" + str(round(result["ppi_pressure_low"])) + ", "
                    + str(round(result["ppi_pressure_high"])) + "]")
        tone = "no MAS news"
        if result["mas_tone"] is not None:
            tone = str(round(result["mas_net"], 1))
        print(str(result["week_end"]).ljust(14)
              + str(rounded(result["pressure_index"], 1)).rjust(8)
              + str(rounded(result["ppi_pressure"], 1)).rjust(7) + " " + band.ljust(11)
              + tone.rjust(17)
              + str(rounded(result["net_pressure"], 1)).rjust(14))

    # Report on the most recent week that has ended (the one the note covers)
    today = datetime.now(SINGAPORE_TIME).date()
    latest = results[-1]["week_end"]
    for result in results:
        if result["week_end"] < today:
            latest = result["week_end"]
    print("")
    print("What drove the week ending " + str(latest) + " (each story counted once, points of index):")
    for result in results:
        if result["week_end"] == latest and result["version"] == "events once":
            for channel in CHANNEL_WEIGHTS:
                if channel != "None":
                    value = result["contrib: " + channel]
                    if value != 0:
                        print("  " + channel.ljust(24) + str(round(value, 1)).rjust(7))

    print("")
    print("Top stories, week ending " + str(latest) + " (one per event):")
    for item in top_stories(articles, latest, 5):
        article = item[1]
        print("  " + str(round(item[0], 1)).rjust(6) + "  " + article["headline"][:80])
        print("          " + article["ai_summary"][:100])


CHANNEL_COLOURS = {
    # Muted tones (Stone: grey-ish colours look calmer and leave bright ones
    # for highlights). The two inflation channels share warm hues, because
    # analogous colours tell the eye they belong together.
    "Imported inflation": "#d08a5a",
    "Domestic inflation": "#c9a94f",
    "MAS signals": "#4d4d4d",
    "Growth and output gap": "#5f9e8f",
    "Government policy": "#9187b9",
    "Global rates and USD": "#8fa9c7",
}


def style_axes(ax):
    """Context in grey: light frame, grey tick labels, no top or right border."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#777777")
    ax.spines["bottom"].set_color("#777777")
    ax.tick_params(colors="#777777", labelcolor="#333333", labelsize=9)


def sign_colour(value):
    """Red for hawkish, blue for dovish, grey for zero or missing."""
    if value != value or value == 0:
        return NEUTRAL_COLOUR
    if value > 0:
        return HAWKISH_COLOUR
    return DOVISH_COLOUR


def panel_title(ax, title, subtitle):
    ax.set_title(title, fontsize=12, loc="left", fontweight="bold", pad=20, color="#222222")
    ax.text(0.0, 1.02, subtitle, transform=ax.transAxes, fontsize=8.5,
            color=TEXT_GREY, va="bottom", ha="left")


def draw_chart(results):
    """The main chart. Design follows Stone, "Choosing Colors for Data Visualization":
    white background, everything that is context in grey, red = hawkish and
    blue = dovish used the same way everywhere, muted colours for the channels."""
    weeks = []
    for result in results:
        if result["week_end"] not in weeks:
            weeks.append(result["week_end"])

    def series(version, key):
        values = []
        for week in weeks:
            for result in results:
                if result["week_end"] == week and result["version"] == version:
                    value = result[key]
                    if value is None:
                        value = float("nan")
                    values.append(value)
        return values

    x = []
    labels = []
    partial = []
    complete_weeks = 0
    for k in range(len(weeks)):
        x.append(k)
        monday = weeks[k] - timedelta(days=6)
        label = monday.strftime("%d %b") + " - " + weeks[k].strftime("%d %b")
        for result in results:
            if result["week_end"] == weeks[k] and result["version"] == "all copies":
                if result["complete"]:
                    complete_weeks = complete_weeks + 1
                    partial.append(False)
                else:
                    partial.append(True)
                    label = label + "\npartial: " + str(result["days_with_news"]) + " of 7 days"
        labels.append(label)

    every = series("all copies", "index")
    once = series("events once", "index")
    low = series("events once", "ppi_low")
    high = series("events once", "ppi_high")
    corrected = series("events once", "ppi_index")

    fig, axes = plt.subplots(4, 1, figsize=(10, 17),
                             gridspec_kw={"height_ratios": [3, 2.4, 2.4, 1.6]})

    def finish_x(ax):
        ax.set_xlim(-0.6, len(x) - 0.4)
        ax.set_xticks(x)
        ax.set_xticklabels(labels)

    # ---------------- Panel 1: the index ----------------
    ax = axes[0]
    ax.axhline(0, color="#999999", linewidth=0.9, zorder=1)
    ax.text(-0.55, 96, "HAWKISH (towards tightening)", color=TEXT_GREY, fontsize=8, va="top")
    ax.text(-0.55, -96, "DOVISH (towards easing)", color=TEXT_GREY, fontsize=8, va="bottom")

    for k in range(len(x)):
        if low[k] == low[k] and high[k] == high[k]:
            label = None
            if k == 0:
                label = "Uncertainty range (95%)"
            ax.plot([x[k], x[k]], [low[k], high[k]], color=INDEX_LIGHT, linewidth=16,
                    solid_capstyle="butt", zorder=1, label=label)
            # the corrected value: a short dark tick across the range
            tick_label = None
            if k == 0:
                tick_label = "Corrected with my hand labels"
            ax.plot([x[k] - 0.05, x[k] + 0.05], [corrected[k], corrected[k]], color="#333333",
                    linewidth=2.5, zorder=2, label=tick_label)

    ax.plot(x, every, ":", color=INDEX_COMPARE, linewidth=1.4, zorder=2)
    ax.plot(x, once, "-", color=INDEX_COLOUR, linewidth=2.2, zorder=3)
    for k in range(len(x)):
        colour = sign_colour(once[k])
        face = colour
        if partial[k]:
            face = "white"   # hollow = partial week
        ax.plot(x[k], every[k], "s", markersize=5, markerfacecolor="white",
                markeredgecolor=INDEX_COMPARE, zorder=2)
        ax.plot(x[k], once[k], "o", markersize=11, markerfacecolor=face,
                markeredgecolor=colour, markeredgewidth=2.5, zorder=4)
        if once[k] == once[k]:
            ax.annotate(str(round(once[k])), (x[k], once[k]), textcoords="offset points",
                        xytext=(-16, 0), fontsize=12, va="center", ha="right",
                        fontweight="bold", color=colour)
    # Legend entries drawn separately, so their colours don't suggest a direction
    ax.plot([], [], "o", markersize=9, markerfacecolor=HAWKISH_COLOUR,
            markeredgecolor=HAWKISH_COLOUR, label="Index, hawkish week")
    ax.plot([], [], "o", markersize=9, markerfacecolor=DOVISH_COLOUR,
            markeredgecolor=DOVISH_COLOUR, label="Index, dovish week")
    ax.plot([], [], "s", markersize=5, markerfacecolor="white", markeredgecolor=INDEX_COMPARE,
            label="Every article counts (for comparison)")
    if complete_weeks >= 4:
        ax.plot(x, series("events once", "index_4wk_avg"), "--", color=INDEX_COMPARE,
                linewidth=1.2, label="4-week average")
    ax.set_ylim(-100, 100)
    finish_x(ax)
    panel_title(ax, "MAS Hawkishness Index",
                "Is the news pushing MAS to tighten or ease? +100 = all hawkish, "
                "-100 = all dovish. Hollow = partial week.")
    ax.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=8, frameon=False)
    style_axes(ax)

    # ---------------- Panel 2: outside pressure vs MAS's own signal ----------------
    ax = axes[1]
    pressure = series("events once", "pressure_index")
    p_low = series("events once", "ppi_pressure_low")
    p_high = series("events once", "ppi_pressure_high")
    p_corrected = series("events once", "ppi_pressure")
    tone = series("events once", "mas_tone")
    mas_net = series("events once", "mas_net")
    ax.axhline(0, color="#999999", linewidth=0.9, zorder=1)

    ax_mas = ax.twinx()
    shown_bar = False
    for k in range(len(x)):
        if tone[k] == tone[k]:
            label = None
            if not shown_bar:
                shown_bar = True
            ax_mas.bar(x[k] + 0.14, mas_net[k], 0.2, color=sign_colour(mas_net[k]),
                       alpha=0.55)
            nudge = 3
            va = "bottom"
            if mas_net[k] < 0:
                nudge = -3
                va = "top"
            ax_mas.annotate(str(round(mas_net[k])), (x[k] + 0.14, mas_net[k]),
                            textcoords="offset points", xytext=(0, nudge), ha="center",
                            va=va, fontsize=9, color=sign_colour(mas_net[k]),
                            fontweight="bold")
        else:
            ax_mas.annotate("no MAS news", (x[k] + 0.14, 0), textcoords="offset points",
                            xytext=(0, 6), ha="center", fontsize=8, color=TEXT_GREY)
    biggest = 24.0
    for k in range(len(x)):
        if tone[k] == tone[k] and abs(mas_net[k]) * 1.3 > biggest:
            biggest = abs(mas_net[k]) * 1.3
    ax_mas.set_ylim(-biggest, biggest)
    ax_mas.set_ylabel("MAS signal (surprise decision = 18)", fontsize=8, color=TEXT_GREY)
    ax_mas.spines["top"].set_visible(False)
    ax_mas.spines["left"].set_visible(False)
    ax_mas.spines["bottom"].set_visible(False)
    ax_mas.spines["right"].set_color("#777777")
    ax_mas.tick_params(colors="#777777", labelcolor="#333333", labelsize=8)

    for k in range(len(x)):
        if p_low[k] == p_low[k] and p_high[k] == p_high[k]:
            label = None
            if k == 0:
                label = "Uncertainty range (95%)"
            ax.plot([x[k] - 0.14, x[k] - 0.14], [p_low[k], p_high[k]], color=PRESSURE_LIGHT,
                    linewidth=14, solid_capstyle="butt", zorder=1, label=label)
            tick_label = None
            if k == 0:
                tick_label = "Corrected with my hand labels"
            ax.plot([x[k] - 0.18, x[k] - 0.10], [p_corrected[k], p_corrected[k]],
                    color="#333333", linewidth=2.5, zorder=2, label=tick_label)
    pressure_x = []
    for k in range(len(x)):
        pressure_x.append(x[k] - 0.14)
    ax.plot(pressure_x, pressure, "-", color=PRESSURE_COLOUR, linewidth=2.2, zorder=3)
    for k in range(len(x)):
        colour = sign_colour(pressure[k])
        label = None
        if k == 0:
            label = "Outside pressure (line, left scale)"
        ax.plot(pressure_x[k], pressure[k], "o", markersize=10, markerfacecolor=colour,
                markeredgecolor="white", markeredgewidth=1.5, zorder=4)
        if pressure[k] == pressure[k]:
            ax.annotate(str(round(pressure[k])), (pressure_x[k], pressure[k]),
                        textcoords="offset points", xytext=(-14, 0), fontsize=11,
                        va="center", ha="right", color=colour, fontweight="bold")
    ax.plot([], [], "-o", color=PRESSURE_COLOUR, markersize=8, label="Outside pressure (line, left scale)")
    ax.set_ylim(-100, 100)
    finish_x(ax)
    panel_title(ax, "Outside pressure vs MAS's own signal",
                "Line: all news except MAS's own words (-100 to +100). "
                "Bars: MAS decisions, speeches and guidance.")
    ax.plot([], [], "s", markersize=10, color=DOVISH_COLOUR, alpha=0.55,
            label="MAS's own signal (bars, right scale)")
    ax.plot([], [], " ", label="Red = hawkish, blue = dovish")
    handles, names = ax.get_legend_handles_labels()
    ax.legend(handles, names, loc="center left",
              bbox_to_anchor=(1.1, 0.5), fontsize=8, frameon=False)
    style_axes(ax)

    # ---------------- Panel 3: what drove it ----------------
    ax = axes[2]
    width = 0.5
    up_base = []
    down_base = []
    for k in range(len(x)):
        up_base.append(0.0)
        down_base.append(0.0)
    for channel in CHANNEL_COLOURS:
        values = series("events once", "contrib: " + channel)
        shown = False
        for k in range(len(x)):
            value = values[k]
            if value != value or value == 0:
                continue
            label = None
            if not shown:
                label = channel
                shown = True
            if value > 0:
                ax.bar(x[k], value, width, bottom=up_base[k], color=CHANNEL_COLOURS[channel],
                       edgecolor="white", linewidth=1, label=label)
                up_base[k] = up_base[k] + value
            else:
                ax.bar(x[k], value, width, bottom=down_base[k], color=CHANNEL_COLOURS[channel],
                       edgecolor="white", linewidth=1, label=label)
                down_base[k] = down_base[k] + value
    ax.axhline(0, color="#999999", linewidth=0.9)
    for k in range(len(x)):
        colour = sign_colour(once[k])
        ax.plot(x[k], once[k], "o", markerfacecolor="white", markeredgecolor=colour,
                markersize=11, markeredgewidth=2.5, zorder=5)
    ax.plot([], [], "o", markerfacecolor="white", markeredgecolor=INDEX_COLOUR,
            markersize=9, markeredgewidth=2, label="Index (sum of the bars)")
    finish_x(ax)
    ax.set_ylabel("Index points", color=TEXT_GREY, fontsize=9)
    panel_title(ax, "Which kinds of news moved it",
                "Bars above 0 pushed hawkish, below 0 pushed dovish. They add up to the index.")
    ax.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=8, frameon=False)
    style_axes(ax)

    # ---------------- Panel 4: news intensity ----------------
    ax = axes[3]
    left = []
    right = []
    for k in range(len(x)):
        left.append(x[k] - 0.14)
        right.append(x[k] + 0.14)
    ax.bar(left, series("events once", "intensity"), 0.28, color="#222222",
           label="Each story counted once")
    ax.bar(right, series("all copies", "intensity"), 0.28, color="#9ecae1",
           label="Every article counts")
    finish_x(ax)
    panel_title(ax, "How much MAS-relevant news there was",
                "Bigger = the index rests on more news.")
    ax.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=8, frameon=False)
    style_axes(ax)

    fig.text(0.01, 0.003, "Weeks run Monday-Sunday, Singapore time. Sources: Business Times, "
             "Straits Times, CNA. Articles scored by " + MODEL + ".",
             fontsize=7.5, color="#888888")
    fig.tight_layout(rect=[0, 0.015, 1, 1], h_pad=3)
    fig.savefig(CHART_FILE, dpi=160, bbox_inches="tight")
    plt.close(fig)


def week_events(articles, week):
    """One entry per event (duplicate group) in a week.
    push and importance are averages over the copies, so each event counts once."""
    groups = []
    for article in articles:
        if article["week_end"] == week and article["importance"] > 0:
            if article["group"] not in groups:
                groups.append(article["group"])

    events = []
    for group in groups:
        copies = []
        for article in articles:
            if article["week_end"] == week and article["importance"] > 0 and article["group"] == group:
                copies.append(article)
        push_total = 0.0
        importance_total = 0.0
        first_day = copies[0]["day"]
        strongest = copies[0]
        strongest_push = 0.0
        for article in copies:
            push, weight = article_push_weight(article)
            push_total = push_total + push
            importance_total = importance_total + article["importance"]
            if article["day"] < first_day:
                first_day = article["day"]
            if abs(push) >= abs(strongest_push):
                strongest_push = push
                strongest = article
        events.append({
            "day": first_day,
            "push": push_total / len(copies),
            "importance": importance_total / len(copies),
            "channel": strongest["channel"],
            "headline": strongest["headline"],
            "copies": len(copies),
        })
    return events


def short_headline(text, length):
    if len(text) <= length:
        return text
    cut = text[:length]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut + "..."


HAWKISH_COLOUR = "#c0392b"   # warm red
DOVISH_COLOUR = "#2166ac"    # cool blue
NEUTRAL_COLOUR = "#bdbdbd"   # grey: relevant but neutral
TEXT_GREY = "#555555"        # subtitles and small notes

# The index's own colour family (indigo), used for the index line, its
# uncertainty range and the "every article counts" comparison
INDEX_COLOUR = "#555555"
INDEX_LIGHT = "#e3e3e3"
INDEX_COMPARE = "#bbbbbb"
# Outside pressure's colour family (amber)
PRESSURE_COLOUR = "#555555"
PRESSURE_LIGHT = "#e3e3e3"


def draw_news_map(articles, results):
    """Each event is a dot: across = day, up = hawkish, down = dovish, size = importance.

    Design (following Stone, "Choosing Colors for Data Visualization"):
    - only two hues, red = hawkish and blue = dovish, so colour carries ONE message
    - the biggest stories are drawn in full colour; the rest are pale tints of the
      same hue, so attention goes to what matters ("contrast highlights")
    - the biggest stories get a number, and the headlines are listed under the
      chart, so no text sits on top of the dots
    - everything that is context (axes, zero line, labels) is grey on white
    """
    weeks = []
    for result in results:
        if result["week_end"] not in weeks:
            weeks.append(result["week_end"])
    weeks = weeks[-NEWS_MAP_WEEKS:]

    fig, axes = plt.subplots(len(weeks), 1, figsize=(11, 6.8 * len(weeks)), squeeze=False)

    for row in range(len(weeks)):
        week = weeks[row]
        ax = axes[row][0]
        monday = week - timedelta(days=6)
        events = week_events(articles, week)

        biggest = 10.0
        for event in events:
            if abs(event["push"]) * 1.3 > biggest:
                biggest = abs(event["push"]) * 1.3

        # Which events are "top stories" (numbered and in full colour)
        ranked = []
        for event in events:
            if event["push"] != 0:
                ranked.append(event)
        ranked.sort(key=lambda event: abs(event["push"]), reverse=True)
        ranked = ranked[:NEWS_MAP_LABELS]
        for event in events:
            event["rank"] = 0
        for k in range(len(ranked)):
            ranked[k]["rank"] = k + 1

        # Spread the dots of each day across the day's width, ordered from most
        # dovish to most hawkish, so dots with the same score sit side by side
        days = []
        for event in events:
            if event["day"] not in days:
                days.append(event["day"])
        for day in days:
            same_day = []
            for event in events:
                if event["day"] == day:
                    same_day.append(event)
            same_day.sort(key=lambda event: event["push"])
            n = len(same_day)
            for k in range(n):
                spread = 0.0
                if n > 1:
                    spread = -0.4 + 0.8 * k / (n - 1)
                same_day[k]["x"] = (day - monday).days + spread

        # Context first: light grey day separators and zero line
        for k in range(8):
            ax.axvline(k - 0.5, color="#eeeeee", linewidth=0.8, zorder=0)
        ax.axhline(0, color="#999999", linewidth=0.9, zorder=1)
        ax.text(-0.45, biggest * 0.93, "HAWKISH (towards tightening)", color=TEXT_GREY,
                fontsize=8, va="top")
        ax.text(-0.45, -biggest * 0.93, "DOVISH (towards easing)", color=TEXT_GREY,
                fontsize=8, va="bottom")

        # Dots: pale tints for the background stories, full colour for the top ones
        for event in events:
            colour = NEUTRAL_COLOUR
            if event["push"] > 0:
                colour = HAWKISH_COLOUR
            if event["push"] < 0:
                colour = DOVISH_COLOUR
            size = 25 + event["importance"] * 22
            if event["rank"] > 0:
                ax.scatter(event["x"], event["push"], s=size, color=colour, alpha=0.95,
                           edgecolors="white", linewidths=1.2, zorder=4)
            else:
                ax.scatter(event["x"], event["push"], s=size, color=colour, alpha=0.28,
                           edgecolors="none", zorder=3)

        # Numbers next to the top stories (outside the dot, away from the zero line)
        placed = []
        for event in ranked:
            # Stack numbers further out when another number is close by
            close = 0
            for spot in placed:
                same_side = (spot[1] >= 0) == (event["push"] >= 0)
                if same_side and abs(spot[0] - event["x"]) < 0.25 and abs(spot[1] - event["push"]) < biggest * 0.15:
                    close = close + 1
            placed.append([event["x"], event["push"]])
            offset = 10 + 13 * close
            align = "bottom"
            if event["push"] < 0:
                offset = -offset
                align = "top"
            ax.annotate(str(event["rank"]), (event["x"], event["push"]),
                        textcoords="offset points", xytext=(0, offset), ha="center",
                        va=align, fontsize=10, fontweight="bold", color="#222222", zorder=5)

        # The numbered headlines, listed under the chart
        lines = []
        for event in ranked:
            lean = "hawkish"
            if event["push"] < 0:
                lean = "dovish"
            text = (str(event["rank"]) + ".  " + short_headline(event["headline"], 95)
                    + "   [" + event["day"].strftime("%a") + ", " + lean + ", "
                    + event["channel"])
            if event["copies"] > 1:
                text = text + ", " + str(event["copies"]) + " articles"
            lines.append(text + "]")
        ax.text(0.0, -0.13, "\n".join(lines), transform=ax.transAxes, fontsize=8.5,
                va="top", ha="left", color="#222222", linespacing=1.6)

        day_labels = []
        for k in range(7):
            day_labels.append((monday + timedelta(days=k)).strftime("%a %d %b"))
        ax.set_xticks(range(7))
        ax.set_xticklabels(day_labels, fontsize=8, color=TEXT_GREY)
        ax.tick_params(axis="y", colors=TEXT_GREY, labelsize=8)
        ax.set_xlim(-0.6, 6.6)
        ax.set_ylim(-biggest, biggest)
        ax.set_ylabel("Push on MAS", color=TEXT_GREY, fontsize=9)

        title = "News map, " + monday.strftime("%d %b") + " - " + week.strftime("%d %b")
        for result in results:
            if result["week_end"] == week and result["version"] == "events once":
                if result["index"] is not None:
                    title = title + "   (index " + str(round(result["index"])) + ")"
                if not result["complete"]:
                    title = title + "   partial week"
        ax.set_title(title, fontsize=12, loc="left", fontweight="bold", pad=18)
        ax.text(0.0, 1.015, "Each dot is one story. Bigger = more important. Numbered "
                "= the week's biggest stories, listed below.", transform=ax.transAxes,
                fontsize=8, color=TEXT_GREY, va="bottom", ha="left")

        # Legend: equal-sized markers
        ax.scatter([], [], s=60, color=HAWKISH_COLOUR, label="Hawkish")
        ax.scatter([], [], s=60, color=DOVISH_COLOUR, label="Dovish")
        ax.scatter([], [], s=60, color=NEUTRAL_COLOUR, label="Relevant but neutral")
        ax.scatter([], [], s=60, color=HAWKISH_COLOUR, alpha=0.28, label="Smaller stories (pale)")
        ax.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=8, frameon=False)
        for side in ["top", "right"]:
            ax.spines[side].set_visible(False)
        for side in ["left", "bottom"]:
            ax.spines[side].set_color("#bbbbbb")

    fig.tight_layout(h_pad=10)
    fig.savefig(NEWS_MAP_FILE, dpi=160, bbox_inches="tight")
    plt.close(fig)


# ----------------------------------------------------------------------
# RUN
# ----------------------------------------------------------------------

if __name__ == "__main__":
    use_ai = True
    if len(sys.argv) > 1 and sys.argv[1] == "noai":
        use_ai = False

    articles = load_articles()
    print("Finding duplicates:")
    find_duplicates(articles, use_ai)
    pairs = load_label_pairs()
    results, gap_info = build_weekly(articles, pairs)

    save_weekly(results)
    save_articles(articles)
    draw_chart(results)
    draw_news_map(articles, results)
    print_summary(results, articles, gap_info)
    print("")
    print("Saved " + NEWS_MAP_FILE + " too.")
    print("Saved " + WEEKLY_FILE + ", " + ARTICLES_FILE + ", "
          + GROUPS_FILE + " and " + CHART_FILE)
