"""
SGD news index - Phase 2: AI scoring (Mistral or Google, set in ai_client.py)
=============================================================================

CHANGED 24 Nov 2026: the AI service is chosen by PROVIDER in ai_client.py.
  mistral (default) -> Mistral's free tier; scores in ai_scores_..._mistral-large.csv
  google            -> Gemma 4 31B via Google AI Studio; ai_scores_..._gemma4_31b-cloud.csv
(23 Nov 2026: moved from Ollama to Google. Ollama scores are the gemma4 files.)
In pool mode, a Python filter (train_filter.py) first skips articles that are
very unlikely to matter for MAS; they are saved with zero scores and a note.

Two modes:

  python score_articles.py test
      Scores the 80 hand-labelled articles in hand_labels.xlsx,
      saves the AI scores to ai_scores_test_<model>.csv, then prints
      how closely the AI agrees with your labels.

  python score_articles.py test holdout
      Same, for your holdout labels in hand_labels_holdout.xlsx
      (saves ai_scores_holdout_<model>.csv).

  python score_articles.py pool
      Scores every article in news_pool.csv that hasn't been scored yet,
      saving to ai_scores_pool_<model>.csv. Safe to stop and restart:
      it skips articles already scored.

To try a different model, change MODEL below (e.g. "qwen3.5:4b").

After each MAS statement (Jan, Apr, Jul, Oct), add a new entry to
MAS_STANCES below so the AI knows MAS's latest view.

Codebook version: v2.1 (23 Oct 2026) - v2 plus rule 16 (property prices and
rents: relevance at most 1, as core inflation excludes accommodation).
24 Oct 2026: added the mas_source field (who is speaking in MAS signals
articles); it changes no score.

Before running: mistral_api_key.txt (see mistral_ai.py) or google_api_key.txt
(see google_ai.py) must exist, and
relevance_filter.pkl (run python3 train_filter.py once).
Install once: pip3 install requests openpyxl scikit-learn
"""

import csv
import json
import os
import sys
import time
from email.utils import parsedate_to_datetime

import pickle

from openpyxl import load_workbook

import ai_client
from ai_client import ask_gemma, DailyLimitReached


# ----------------------------------------------------------------------
# SETTINGS
# ----------------------------------------------------------------------

# Which model's score files to use. Set by PROVIDER in ai_client.py:
#   mistral -> ai_scores_..._mistral-large.csv   google -> ai_scores_..._gemma4_31b-cloud.csv
MODEL = ai_client.SCORES_NAME

FILTER_FILE = "relevance_filter.pkl"   # made by train_filter.py

# Thinking models reason step by step before answering. That can improve
# answers but is much slower on an 8 GB Mac. Start with False.
THINK = False

LABEL_FILE = "hand_labels.xlsx"
HOLDOUT_FILE = "hand_labels_holdout.xlsx"
POOL_FILE = "news_pool.csv"

CHANNELS = ["Imported inflation", "Domestic inflation", "MAS signals",
            "Growth and output gap", "Government policy",
            "Global rates and USD", "None"]
DIRECTIONS = ["hawkish", "dovish", "neutral"]

OUTPUT_COLUMNS = ["id", "headline", "channel", "relevance", "impact",
                  "surprise", "importance", "mas_direction", "mas_source",
                  "summary", "seconds", "error"]


# ----------------------------------------------------------------------
# THE CODEBOOK (the AI's instructions - same rules you labelled with)
# ----------------------------------------------------------------------

CODEBOOK = """You are an analyst scoring news articles for a MAS Hawkishness Index.

THE QUESTION: Does this article push the Monetary Authority of Singapore (MAS)
towards tightening (hawkish) or easing (dovish) at its next policy meeting?

HOW MAS WORKS:
- MAS manages the Singapore dollar's trade-weighted exchange rate (S$NEER)
  inside a policy band. It does NOT target interest rates.
- Hawkish = MAS tightens = steeper band slope = SGD appreciates faster,
  which makes imports cheaper and lowers inflation.
- Dovish = MAS eases = flatter slope = SGD appreciates slower.
- MAS targets MEDIUM-TERM INFLATION. It is forward-looking: the inflation
  OUTLOOK matters most, growth (the output gap) second.

CHANNELS (pick the main one):
- Imported inflation: oil, gas, food, shipping costs, trading partners' inflation
- Domestic inflation: core CPI, wages, labour market, services prices
  (property prices and rents: see rule 16)
- MAS signals: MAS statements, speeches, Macroeconomic Review, economist polls on MAS
- Growth and output gap: GDP, NODX/exports, PMI, AI/chip cycle, China and US demand
- Government policy: subsidies, GST, Budget, foreign worker rules, property measures
- Global rates and USD: Fed, US yields, US inflation data, USD strength, global risk mood
- None: not relevant to MAS's decision

SCORES (whole numbers 0-3 only):
Relevance: 0 = no link to MAS's inflation or growth outlook;
  1 = indirect (one company, minor sector, market wrap);
  2 = clear link (export data, oil move, wage data);
  3 = directly about MAS policy or Singapore core inflation ONLY.
Impact: 0 = would not change MAS's view; 1 = small effect on the outlook;
  2 = shifts the inflation or growth outlook a bit;
  3 = could change MAS's decision (oil shock, MAS statement).
Surprise: 0 = already known or expected; 1 = slightly unexpected;
  2 = clear beat or miss vs forecasts; 3 = narrative-changing shock.
Direction: hawkish, dovish or neutral.

RULES:
1. Every story reaches MAS through PRICES or GROWTH. Rising prices or
   stronger growth = hawkish. Falling prices or weaker growth = dovish.
2. Score each article on its own, as if it were the only article you read.
   Judge surprise against what markets expected before the news.
3. If impact is 0, direction must be neutral.
4. If channel is None, relevance must be 0 (and vice versa).
5. Daily market wraps (gold, stocks, FX moves) usually get impact 0-1.
   If a market wrap reports real news, score that news, but cap relevance
   at 1 unless the news is directly about MAS.
6. Foreign currency, interest rate and stock market stories score 0-1 unless
   they change Singapore's inflation or growth outlook. MAS's band absorbs
   currency moves automatically.
7. Stock moves only count when they signal a shift in the growth outlook
   (e.g. the AI cycle that drives Singapore's exports). Use the growth channel.
8. Single-company news scores 0 unless the company is big enough to move
   Singapore's economy. Singapore's GDP is about S$700-750 billion and its
   labour force about 4 million: convert any numbers to a share of these.
9. Administrative MAS news (appointments etc.) scores 0. Only MAS policy or
   outlook news earns relevance 3.
10. "Singapore imports more or less" is not a direction. Finish the chain to
    prices or growth. If effects pull in opposite directions, use neutral.
11. MAS does not try to help or hurt exporters. Strong exports are hawkish
    because they heat up the economy, not because of export prices.
12. US inflation data goes under Global rates and USD (it works through the
    Fed and the dollar), not Imported inflation. Oil price stories go under
    Imported inflation.
13. Major US inflation prints (CPI, PPI) get relevance 2 under Global rates
    and USD. Market wraps reacting to them still follow rule 5.
14. Threats, warnings and vows of disruption (e.g. "vows attacks on oil
    facilities") get impact 1. Actual supply losses get impact 2 or more.
15. Articles reporting a MAS policy decision: the direction is the decision
    versus what economists expected beforehand. A hold when tightening was
    expected is dovish. Tightening when a hold was expected is hawkish. Warnings
    or caveats in the statement (e.g. "but warns of elevated inflation") do not
    flip the direction, because MAS chose its decision knowing those risks. Surprise is 3 if most economists expected a different decision.
    Only if the decision matched expectations, use the tone of MAS's outlook
    versus its previous statement. Later commentary about the decision (e.g.
    analysts' views on the next meeting, market reactions) is scored on its
    own content, not this rule.
16. Property prices and rents (homes, HDB, offices, industrial space) get
    relevance at most 1. MAS's core inflation measure excludes accommodation
    costs, so property prices barely move the inflation MAS targets. Office
    and industrial rents can matter a little through business costs. Do not
    let property news outweigh actual inflation data.
17. Opinion pieces, essays and long features with no new data score
    None 0/0/0. Foreign company deals, partnerships, funding rounds and
    IPOs (e.g. a chip partnership, an AI firm's IPO valuation) also score
    None 0/0/0. Only hard results that show the whole sector's demand
    (e.g. TSMC or Nvidia earnings, chip sales data) go under Growth.

EXAMPLES:
- "MAS keeps monetary policy unchanged, contrary to expectations"
  -> MAS signals, 3/3/3, dovish (hold when tightening was expected)
- "MAS holds policy but warns inflation will stay elevated" (most economists
  expected tightening)
  -> MAS signals, 3/3/3, dovish (rule 15: the hold versus expectations sets the
     direction; the warning does not flip it)
- "Oil falls as Middle East supply recovers faster than expected"
  -> Imported inflation, 2/2/2, dovish
- "Luxury watch brand opens flagship store on Orchard Road"
  -> None, 0/0/0, neutral
- "Asian currencies edge higher as dollar dips"
  -> Global rates and USD, 1/0/0, neutral
- "Chip stocks tumble as investors question AI spending"
  -> Growth and output gap, 2/1/1, dovish (the AI cycle drives Singapore's
     exports, so doubts about AI spending weaken the growth outlook)

MAS SOURCE (only when the channel is MAS signals): say who is speaking.
- "MAS itself": a MAS decision or statement, or comments by a MAS official
  (e.g. the MAS chief economist, managing director, a MAS publication).
- "others": other people's views about MAS (economists, analysts, banks,
  market pricing, previews and polls).
If the article reports a MAS decision or statement, it is "MAS itself", even
when economists' reactions are also quoted.
For every other channel, use "n/a". This does not change any score.

Reply with ONLY a JSON object, no other text:
{"channel": "...", "relevance": 0, "impact": 0, "surprise": 0,
 "mas_direction": "...", "mas_source": "...",
 "summary": "one sentence on why this matters (or not) for MAS"}
"""


# ----------------------------------------------------------------------
# MAS STANCE - update after each MAS statement (Jan, Apr, Jul, Oct)
# ----------------------------------------------------------------------
# Each entry is [date the statement was published (YYYY-MM-DD), summary].
# Every article is scored using the latest summary published ON OR BEFORE
# the article's date, so the AI never "knows the future" (no look-ahead).
# To add a new one: copy an entry, change the date and the text.

MAS_STANCES = [
    # ---- History, for the backtest (sources: MAS Monetary Policy Statements
    # and MAS "Past monetary policy decisions"; expectations from news reports)
    ["2021-10-14", """MAS Monetary Policy Statement, 14 October 2021:
- Decision: TIGHTEN. Slope raised slightly from 0% a year (first tightening
  since the pandemic). Width and centre unchanged.
- MAS reviews policy twice a year (April and October). Next: April 2022."""],
    ["2022-01-25", """MAS off-cycle policy statement, 25 January 2022:
- Decision: TIGHTEN, off-cycle and unscheduled. Slope raised slightly;
  width and centre unchanged. A pre-emptive move against rising inflation,
  with GDP growth expected at 3-5% in 2022.
- The move surprised markets (only the second off-cycle change in MAS
  history).
- Next scheduled decision: April 2022."""],
    ["2022-04-14", """MAS Monetary Policy Statement, 14 April 2022:
- Decision: TIGHTEN. Band re-centred up to the prevailing level of the
  S$NEER AND slope increased slightly ("double tightening").
- Tightening was widely expected; the third tightening since October 2021.
- Next scheduled decision: October 2022."""],
    ["2022-07-14", """MAS off-cycle policy statement, 14 July 2022:
- Decision: TIGHTEN, off-cycle and unscheduled. Band re-centred up to the
  prevailing level of the S$NEER; slope and width unchanged.
- Second off-cycle move of 2022: both external and domestic factors added
  to inflation pressure since April.
- Next scheduled decision: October 2022."""],
    ["2022-10-14", """MAS Monetary Policy Statement, 14 October 2022:
- Decision: TIGHTEN. Band re-centred up to the prevailing level of the
  S$NEER; slope and width unchanged. Fifth tightening since October 2021.
- Tightening was expected; some analysts had expected a double move
  (re-centring plus a steeper slope).
- MAS expects price pressures to persist until the first half of 2023.
- Next scheduled decision: April 2023."""],
    ["2023-04-14", """MAS Monetary Policy Statement, 14 April 2023:
- Decision: HOLD. Slope, width and centre unchanged; first pause after five
  tightenings since October 2021.
- The hold SURPRISED markets: most economists expected a sixth tightening
  (12 of 22 in a Bloomberg survey).
- MAS judged the current appreciating path sufficiently tight; core
  inflation expected to ease materially by end-2023. Growth outlook weak
  (Q1 GDP contracted); risks to growth tilted to the downside.
- Next scheduled decision: October 2023."""],
    ["2023-10-13", """MAS Monetary Policy Statement, 13 October 2023:
- Decision: HOLD (second pause). Slope, width and centre unchanged.
- Widely expected by economists.
- Core inflation moderating (3.4% in August from a 5.5% peak in January).
- From 2024 MAS will issue statements quarterly (January, April, July,
  October). Next decision: January 2024."""],
    ["2024-01-29", """MAS Monetary Policy Statement, 29 January 2024:
- Decision: HOLD. Slope, width and centre unchanged. Expected by economists.
- Core inflation forecast for 2024: 2.5-3.5% (1.5-2.5% excluding the
  one-off January GST increase). Core inflation expected to rise this
  quarter because of the GST hike, then ease.
- Next decision: April 2024."""],
    ["2024-04-12", """MAS Monetary Policy Statement, 12 April 2024:
- Decision: HOLD. Slope, width and centre unchanged. Expected by economists.
- Next decision: July 2024."""],
    ["2024-07-26", """MAS Monetary Policy Statement, 26 July 2024:
- Decision: HOLD. Slope, width and centre unchanged. Widely expected.
- Next decision: October 2024."""],
    ["2024-10-14", """MAS Monetary Policy Statement, 14 October 2024:
- Decision: HOLD. Slope, width and centre unchanged, even as growth picked
  up and inflation declined. Policy unchanged since the October 2022
  tightening.
- Next decision: January 2025."""],

    # ---- Live
    ["2026-10-14", """MAS Monetary Policy Statement, 14 October 2026:
- Decision: HOLD. Slope (estimated ~1.25% a year), width and centre unchanged.
  First pause after tightening in April and July 2026.
- The hold surprised markets: most economists (17 of 20 in a Bloomberg poll)
  expected a steeper slope.
- Core inflation forecast for 2026 narrowed to 1.5-2% (from 1.5-2.5%);
  headline about 2%. For 2027, both core and headline 1.5-2.5%.
- Core inflation rose to 2.1% in July-August on higher import costs. MAS
  expects it to stay slightly elevated near term, then ease more
  discernibly from mid-2027.
- Risks to inflation tilted to the upside: a sharp, sustained energy or food
  price surge; AI-boom spillovers raising unit labour costs.
  Downside risks: sharper AI investment pullback, higher interest rates,
  faster recovery of Middle East energy supply.
- Growth firm near term (Q3 GDP +5.4% y/y), moderating as the AI cycle eases;
  positive output gap expected to narrow through 2027.
- The S$NEER has strengthened modestly in the upper half of the band.
- Next decision: January 2027."""],
]


def article_date(published):
    """Turn an RSS date like 'Thu, 15 Oct 2026 15:12:25 +0000' into 'YYYY-MM-DD'."""
    try:
        return parsedate_to_datetime(str(published)).strftime("%Y-%m-%d")
    except Exception:
        return ""


def stance_for(date_text):
    """Latest MAS stance published on or before the article's date."""
    if date_text == "":
        # Unknown date: use the most recent stance
        return MAS_STANCES[-1][1]
    chosen = ""
    for entry in MAS_STANCES:
        if entry[0] <= date_text:
            chosen = entry[1]
    return chosen


# ----------------------------------------------------------------------
# TALKING TO THE AI (through ai_client.py: Mistral or Google)
# ----------------------------------------------------------------------

def ask_ai(headline, summary, stance):
    """Send one article to Gemma. Returns a dictionary of scores."""
    article = "Headline: " + headline + "\nSummary: " + summary

    instructions = CODEBOOK
    if stance != "":
        instructions = (CODEBOOK + "\nCURRENT MAS STANCE (judge the article's "
                        "direction and surprise relative to this):\n" + stance)
    instructions = instructions + "\n\nReply with ONLY the JSON object, nothing else."

    text = ask_gemma(instructions, article)
    return extract_json(text)


def extract_json(text):
    """
    Pull the JSON object out of the model's reply.
    Handles replies wrapped in ```json ... ``` or with extra words around them.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        snippet = text.strip().replace("\n", " ")[:120]
        if snippet == "":
            snippet = "(empty reply)"
        raise Exception("no JSON found. Model replied: " + snippet)
    return json.loads(text[start:end + 1])


def clean_scores(raw):
    """
    Check the AI's answer is valid. Returns (scores, problem).
    Scores are kept exactly as the AI gave them; we only check the format.
    """
    scores = {}

    channel = str(raw.get("channel", "")).strip()
    if channel not in CHANNELS:
        return None, "unknown channel: " + channel
    scores["channel"] = channel

    for name in ["relevance", "impact", "surprise"]:
        try:
            value = int(raw.get(name))
        except Exception:
            return None, "bad " + name + ": " + str(raw.get(name))
        if value < 0 or value > 3:
            return None, name + " out of range: " + str(value)
        scores[name] = value

    direction = str(raw.get("mas_direction", "")).strip().lower()
    if direction not in DIRECTIONS:
        return None, "unknown direction: " + direction
    scores["mas_direction"] = direction

    # Who is speaking, for MAS signals articles only
    source_text = str(raw.get("mas_source", "")).strip().lower()
    source = "n/a"
    if channel == "MAS signals":
        source = "unknown"
        if "itself" in source_text or source_text == "mas":
            source = "MAS itself"
        if "other" in source_text:
            source = "others"
    scores["mas_source"] = source

    scores["summary"] = str(raw.get("summary", "")).strip()
    scores["importance"] = scores["relevance"] * (scores["impact"] + scores["surprise"])
    return scores, ""


def score_one(headline, summary, stance):
    """Score one article, retrying once if the answer is invalid."""
    start = time.time()
    problem = ""
    for attempt in range(2):
        try:
            raw = ask_ai(headline, summary, stance)
            scores, problem = clean_scores(raw)
            if scores is not None:
                # tag scores from the smaller fallback model, to redo with 31B later
                if ai_client.backend.last_model_used != ai_client.backend.MODEL:
                    scores["summary"] = "[26B] " + scores["summary"]
                scores["seconds"] = round(time.time() - start, 1)
                scores["error"] = ""
                return scores
        except DailyLimitReached:
            raise
        except Exception as error:
            problem = str(error)

    # Both attempts failed: return an empty row with the error noted
    empty = {"channel": "", "relevance": "", "impact": "", "surprise": "",
             "importance": "", "mas_direction": "", "mas_source": "", "summary": "",
             "seconds": round(time.time() - start, 1), "error": problem}
    return empty


# ----------------------------------------------------------------------
# SAVING (one row at a time, so you can stop and restart)
# ----------------------------------------------------------------------

def output_name(kind):
    safe_model = MODEL.replace(":", "_").replace("/", "_")
    return "ai_scores_" + kind + "_" + safe_model + ".csv"


def already_scored(filename):
    """List of ids already in the output file."""
    done = []
    if not os.path.exists(filename):
        return done
    with open(filename, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["error"] == "":
                done.append(str(row["id"]))
    return done


def read_rows(filename):
    rows = []
    with open(filename, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def write_rows(filename, rows):
    with open(filename, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            clean = {}
            for column in OUTPUT_COLUMNS:
                clean[column] = row.get(column, "")
            writer.writerow(clean)


def update_columns(filename):
    """Older score files have no mas_source column: add it (empty) once."""
    if not os.path.exists(filename):
        return
    with open(filename, "r", encoding="utf-8", newline="") as f:
        header = f.readline()
    if "mas_source" not in header:
        write_rows(filename, read_rows(filename))
        print("(added the mas_source column to " + filename + ")")


def append_row(filename, row):
    new_file = not os.path.exists(filename)
    with open(filename, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def load_filter():
    """The Python relevance filter from train_filter.py, or None if missing."""
    if not os.path.exists(FILTER_FILE):
        print("No " + FILTER_FILE + " yet: every article goes to Gemma.")
        print("(Run python3 train_filter.py once to save most of the daily allowance.)")
        return None
    with open(FILTER_FILE, "rb") as f:
        return pickle.load(f)


def filter_says_skip(the_filter, headline, summary):
    """Returns (skip, chance). Skip only if the chance of relevance is low
    AND the article mentions none of the must-send words."""
    for word in the_filter["must_send"]:
        if word in headline or word in summary:
            return False, 1.0
    text = (headline + ". " + summary).lower()
    X = the_filter["vectorizer"].transform([text])
    chance = the_filter["model"].predict_proba(X)[0][1]
    return chance < the_filter["threshold"], chance


def skipped_scores(chance):
    return {"channel": "None", "relevance": 0, "impact": 0, "surprise": 0,
            "importance": 0, "mas_direction": "neutral", "mas_source": "n/a",
            "summary": "Skipped by the Python filter (chance of relevance %.0f%%)" % (chance * 100),
            "seconds": 0, "error": ""}


def score_list(articles, filename, use_filter=False):
    """Score a list of articles (each a dict with id, headline, summary).
    use_filter=True: articles the Python filter rates unlikely to matter
    are saved with zero scores, without asking Gemma."""
    update_columns(filename)
    done = already_scored(filename)
    todo = []
    for article in articles:
        if str(article["id"]) not in done:
            todo.append(article)

    the_filter = None
    if use_filter:
        the_filter = load_filter()

    print("Model: " + ai_client.backend.MODEL + " (" + ai_client.PROVIDER + ")")
    print(str(len(articles)) + " articles, " + str(len(done))
          + " already scored, " + str(len(todo)) + " to go")
    print("")

    count = 0
    asked = 0
    skipped = 0
    for article in todo:
        count = count + 1
        row = {"id": article["id"], "headline": article["headline"]}

        skip = False
        if the_filter is not None:
            skip, chance = filter_says_skip(the_filter, article["headline"], article["summary"])
        if skip:
            scores = skipped_scores(chance)
            skipped = skipped + 1
            for key in scores:
                row[key] = scores[key]
            append_row(filename, row)
            print(str(count) + "/" + str(len(todo)) + "  skipped (%.0f%%)  " % (chance * 100)
                  + article["headline"][:60])
            continue

        stance = stance_for(article_date(article.get("published", "")))
        try:
            scores = score_one(article["headline"], article["summary"], stance)
        except DailyLimitReached:
            print("")
            print("STOPPED: Google's free daily limit is used up.")
            print("Everything so far is saved. Run the same command tomorrow to carry on.")
            print("Sent to the AI: " + str(asked) + ", skipped by the filter: " + str(skipped))
            return
        asked = asked + 1

        for key in scores:
            row[key] = scores[key]
        append_row(filename, row)

        if scores["error"] == "":
            print(str(count) + "/" + str(len(todo)) + "  "
                  + scores["channel"] + " " + str(scores["relevance"]) + "/"
                  + str(scores["impact"]) + "/" + str(scores["surprise"]) + " "
                  + scores["mas_direction"] + "  (" + str(scores["seconds"]) + "s)  "
                  + article["headline"][:60])
        else:
            print(str(count) + "/" + str(len(todo)) + "  ERROR: "
                  + scores["error"][:100])

    print("")
    print("Sent to the AI: " + str(asked) + ", skipped by the filter: " + str(skipped))
    print("Saved to " + filename)


# ----------------------------------------------------------------------
# MODE 1: TEST ON YOUR HAND LABELS
# ----------------------------------------------------------------------

def load_hand_labels(filename):
    """Read the Labels sheet into a list of dictionaries (skips unfinished rows)."""
    sheet = load_workbook(filename)["Labels"]
    labels = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if row[0] is None:
            continue
        if row[7] is None or row[8] is None or row[9] is None or row[11] is None:
            continue
        labels.append({
            "id": str(row[0]),
            "headline": str(row[3] or ""),
            "summary": str(row[4] or ""),
            "published": str(row[1] or ""),
            "channel": row[6],
            "relevance": row[7],
            "impact": row[8],
            "surprise": row[9],
            "mas_direction": row[11],
        })
    return labels


def direction_number(direction):
    if direction == "hawkish":
        return 1
    if direction == "dovish":
        return -1
    return 0


def compare(labels, filename):
    """Print how closely the AI matches your hand labels."""
    ai = {}
    with open(filename, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["error"] == "":
                ai[str(row["id"])] = row

    pairs = []
    for label in labels:
        if label["id"] in ai:
            pairs.append([label, ai[label["id"]]])

    n = len(pairs)
    if n == 0:
        print("No scored articles to compare yet.")
        return

    print("")
    print("=" * 60)
    print("AI vs YOUR LABELS  (" + str(n) + " articles, model " + MODEL + ")")
    print("=" * 60)

    # Scores: exact match, within one point, average gap (you minus AI)
    for name in ["relevance", "impact", "surprise"]:
        exact = 0
        within_one = 0
        total_gap = 0
        for pair in pairs:
            human = int(pair[0][name])
            machine = int(pair[1][name])
            if human == machine:
                exact = exact + 1
            if abs(human - machine) <= 1:
                within_one = within_one + 1
            total_gap = total_gap + (human - machine)
        print(name.ljust(10) + "  exact " + str(round(100 * exact / n)) + "%"
              + "   within 1: " + str(round(100 * within_one / n)) + "%"
              + "   avg gap (you - AI): " + str(round(total_gap / n, 2)))

    # Channel and direction agreement
    same_channel = 0
    same_direction = 0
    for pair in pairs:
        if pair[0]["channel"] == pair[1]["channel"]:
            same_channel = same_channel + 1
        if pair[0]["mas_direction"] == pair[1]["mas_direction"]:
            same_direction = same_direction + 1
    print("channel     same " + str(round(100 * same_channel / n)) + "%")
    print("direction   same " + str(round(100 * same_direction / n)) + "%")

    # Direction table: rows = you, columns = AI
    print("")
    print("Direction table (rows = you, columns = AI):")
    print("".ljust(10) + "hawkish".rjust(9) + "dovish".rjust(9) + "neutral".rjust(9))
    for human_dir in DIRECTIONS:
        line = human_dir.ljust(10)
        for ai_dir in DIRECTIONS:
            count = 0
            for pair in pairs:
                if pair[0]["mas_direction"] == human_dir and pair[1]["mas_direction"] == ai_dir:
                    count = count + 1
            line = line + str(count).rjust(9)
        print(line)

    # PPI preview: the signed score that feeds the index
    # signed = direction (+1/-1/0) x importance
    human_total = 0
    ai_total = 0
    for pair in pairs:
        human_imp = int(pair[0]["relevance"]) * (int(pair[0]["impact"]) + int(pair[0]["surprise"]))
        ai_imp = int(pair[1]["importance"])
        human_total = human_total + direction_number(pair[0]["mas_direction"]) * human_imp
        ai_total = ai_total + direction_number(pair[1]["mas_direction"]) * ai_imp
    print("")
    print("Average signed score (direction x importance):")
    print("  you: " + str(round(human_total / n, 3))
          + "   AI: " + str(round(ai_total / n, 3))
          + "   gap (you - AI): " + str(round((human_total - ai_total) / n, 3)))
    print("  This gap is the PPI correction the index will apply.")

    # The biggest disagreements, to learn from
    print("")
    print("Biggest disagreements (by importance gap):")
    gaps = []
    for pair in pairs:
        human_imp = int(pair[0]["relevance"]) * (int(pair[0]["impact"]) + int(pair[0]["surprise"]))
        ai_imp = int(pair[1]["importance"])
        gaps.append([abs(human_imp - ai_imp), pair, human_imp, ai_imp])
    # simple sort by the first item, biggest first
    gaps.sort(key=lambda item: item[0], reverse=True)
    shown = 0
    for item in gaps:
        if shown == 8 or item[0] == 0:
            break
        pair = item[1]
        print("- " + pair[0]["headline"][:70])
        print("    you: " + str(pair[0]["channel"]) + " "
              + str(pair[0]["relevance"]) + "/" + str(pair[0]["impact"]) + "/"
              + str(pair[0]["surprise"]) + " " + str(pair[0]["mas_direction"]))
        print("    AI:  " + pair[1]["channel"] + " "
              + pair[1]["relevance"] + "/" + pair[1]["impact"] + "/"
              + pair[1]["surprise"] + " " + pair[1]["mas_direction"]
              + "  -- " + pair[1]["summary"][:90])
        shown = shown + 1


def test(kind):
    if kind == "holdout":
        labels = load_hand_labels(HOLDOUT_FILE)
        filename = output_name("holdout")
    else:
        labels = load_hand_labels(LABEL_FILE)
        filename = output_name("test")
    score_list(labels, filename)
    compare(labels, filename)


# ----------------------------------------------------------------------
# MODE 2: SCORE THE WHOLE POOL
# ----------------------------------------------------------------------

def pool(since=""):
    """Scores the pool NEWEST FIRST, so the latest week is ready soonest.
    since="2026-11-16": only articles published on or after that date."""
    articles = []
    with open(POOL_FILE, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            day = article_date(row["published"])
            if since != "" and day < since:
                continue
            articles.append({"id": row["id"], "headline": row["headline"],
                             "summary": row["summary"],
                             "published": row["published"], "day": day})

    # newest first: sort by date, then reverse
    def by_day(article):
        return article["day"]
    articles.sort(key=by_day)
    articles.reverse()

    if since != "":
        print("Only articles from " + since + " onwards")
    score_list(articles, output_name("pool"), use_filter=True)


# ----------------------------------------------------------------------
# MODE 2b: SCORE THE HISTORICAL (BACKFILLED) ARTICLES, ONE YEAR AT A TIME
# ----------------------------------------------------------------------
#   python3 score_articles.py history 2022
# Reads historical_pool.csv (made by backfill_news.py), scores only articles
# published in that year, saves to ai_scores_history_<model>.csv. Resumable.

HISTORY_FILE = "historical_pool.csv"


def history(year):
    # Safety check: every MAS statement of that year must be in MAS_STANCES,
    # or articles would be judged against an out-of-date stance
    has_year = False
    for entry in MAS_STANCES:
        if entry[0][:4] == year:
            has_year = True
    if not has_year:
        print("No MAS_STANCES entries for " + year + " yet. Add that year's MAS")
        print("statements first, then run this again.")
        return
    first_stance = MAS_STANCES[0][0]
    articles = []
    skipped = 0
    with open(HISTORY_FILE, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            day = article_date(row["published"])
            if day[:4] != year:
                continue
            if day < first_stance:
                skipped = skipped + 1
                continue
            articles.append({"id": row["id"], "headline": row["headline"],
                             "summary": row["summary"], "published": row["published"]})
    if skipped > 0:
        print("Skipped " + str(skipped) + " articles dated before the first MAS stance ("
              + first_stance + ")")
    score_list(articles, output_name("history"))


# ----------------------------------------------------------------------
# MODE 3: RE-SCORE ONLY SOME ARTICLES (cheap, after a small codebook change)
# ----------------------------------------------------------------------
#   python3 score_articles.py rescore channel "MAS signals"
#   python3 score_articles.py rescore ids 31,48,376
# Only the chosen articles are sent to the AI again; every other row in the
# pool scores file stays exactly as it is.

def rescore(how, value):
    filename = output_name("pool")
    update_columns(filename)
    rows = read_rows(filename)

    chosen = []
    if how == "channel":
        for row in rows:
            if row["channel"] == value:
                chosen.append(row["id"])
    elif how == "fallback":
        # articles scored by the smaller 26B model: redo them with 31B only
        ai_client.backend.FALLBACK_MODEL = ""
        for row in rows:
            if row["summary"].startswith("[26B]"):
                chosen.append(row["id"])
    elif how == "ids":
        for part in value.split(","):
            part = part.strip()
            if part != "":
                chosen.append(part)
    else:
        print("Use: rescore channel <channel name>   or   rescore ids <id,id,...>   or   rescore fallback")
        return

    pool_by_id = {}
    with open(POOL_FILE, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            pool_by_id[row["id"]] = row

    print("Re-scoring " + str(len(chosen)) + " articles (the other "
          + str(len(rows) - len(chosen)) + " stay as they are)")
    print("")
    count = 0
    for row in rows:
        if row["id"] not in chosen or row["id"] not in pool_by_id:
            continue
        count = count + 1
        article = pool_by_id[row["id"]]
        stance = stance_for(article_date(article["published"]))
        try:
            scores = score_one(article["headline"], article["summary"], stance)
        except DailyLimitReached:
            print("STOPPED: Google's free daily limit is used up. Saving what is done so far.")
            break
        if scores["error"] != "":
            print(str(count) + ". ERROR, kept the old scores: " + scores["error"][:80])
            continue
        before = (row["channel"] + " " + str(row["relevance"]) + "/" + str(row["impact"])
                  + "/" + str(row["surprise"]) + " " + row["mas_direction"])
        for key in scores:
            row[key] = scores[key]
        after = (scores["channel"] + " " + str(scores["relevance"]) + "/"
                 + str(scores["impact"]) + "/" + str(scores["surprise"]) + " "
                 + scores["mas_direction"])
        source = ""
        if scores["mas_source"] != "n/a":
            source = "  [" + scores["mas_source"] + "]"
        print(str(count) + ". id " + row["id"] + ": " + before + "  ->  " + after + source
              + "   " + article["headline"][:55])

    write_rows(filename, rows)
    print("")
    print("Saved " + filename)


# ----------------------------------------------------------------------
# MODE 4: CHECK GOOGLE'S GEMMA AGREES WITH THE OLLAMA GEMMA
# ----------------------------------------------------------------------
#   python3 score_articles.py consistency
# Re-scores 20 of your hand-labelled articles through Google and compares
# with the scores Ollama's Gemma gave them before. Saves nothing.

def consistency(how_many):
    old_rows = read_rows(output_name("test"))
    labels = load_hand_labels(LABEL_FILE)
    by_id = {}
    for label in labels:
        by_id[str(label["id"])] = label

    same_channel = 0
    same_direction = 0
    importance_gap = 0
    count = 0
    print("Re-scoring " + str(how_many) + " articles through Google...")
    print("")
    for old in old_rows:
        if count >= how_many:
            break
        if old["error"] != "" or old["id"] not in by_id:
            continue
        label = by_id[old["id"]]
        stance = stance_for(article_date(str(label.get("published", ""))))
        try:
            new = score_one(label["headline"], label["summary"], stance)
        except DailyLimitReached:
            print("Google's daily limit is used up. Try again tomorrow.")
            return
        if new["error"] != "":
            print("ERROR: " + new["error"][:80])
            continue
        count = count + 1
        if new["channel"] == old["channel"]:
            same_channel = same_channel + 1
        if new["mas_direction"] == old["mas_direction"]:
            same_direction = same_direction + 1
        importance_gap = importance_gap + abs(int(new["importance"]) - int(old["importance"]))
        print("%2d. before: %-22s %-8s %2s   now: %-22s %-8s %2s   %s" % (
            count, old["channel"], old["mas_direction"], old["importance"],
            new["channel"], new["mas_direction"], new["importance"], label["headline"][:40]))

    if count == 0:
        return
    print("")
    print("Same channel:   %d of %d" % (same_channel, count))
    print("Same direction: %d of %d" % (same_direction, count))
    print("Average importance gap: %.1f (on a 0-18 scale)" % (importance_gap * 1.0 / count))
    print("Expect most to match. A few small differences are normal.")


# ----------------------------------------------------------------------
# RUN
# ----------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "pool":
        pool(sys.argv[2])
    elif len(sys.argv) > 1 and sys.argv[1] == "pool":
        pool()
    elif len(sys.argv) > 2 and sys.argv[1] == "history":
        history(sys.argv[2])
    elif len(sys.argv) > 2 and sys.argv[1] == "rescore" and sys.argv[2] == "fallback":
        rescore("fallback", "")
    elif len(sys.argv) > 3 and sys.argv[1] == "rescore":
        rescore(sys.argv[2], sys.argv[3])
    elif len(sys.argv) > 2 and sys.argv[1] == "test" and sys.argv[2] == "holdout":
        test("holdout")
    elif len(sys.argv) > 1 and sys.argv[1] == "test":
        test("test")
    elif len(sys.argv) > 1 and sys.argv[1] == "consistency":
        consistency(20)
    else:
        print("Use:  python3 score_articles.py test   (score your 80 labels)")
        print("  or: python3 score_articles.py test holdout   (score your holdout labels)")
        print("  or: python3 score_articles.py pool   (score the whole pool, newest first)")
        print("  or: python3 score_articles.py pool 2026-11-16   (only from that date on)")
        print("  or: python3 score_articles.py rescore fallback   (redo [26B] articles with 31B)")
        print("  or: python3 score_articles.py history 2022   (backfilled articles of one year)")
        print('  or: python3 score_articles.py rescore channel "MAS signals"   (only those)')
        print("  or: python3 score_articles.py rescore ids 31,48   (only those ids)")
        print("  or: python3 score_articles.py consistency   (Google vs old Ollama scores)")
