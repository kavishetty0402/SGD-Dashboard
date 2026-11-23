"""
app.py
The SGD weekly dashboard. Shows last calendar week (Monday to Sunday) in
two tabs:
  Markets                  currencies, equities, rates (data/markets.csv)
  MAS Hawkishness Index    the news index (data/weekly_index.csv and
                           data/weekly_articles.csv, copied over by
                           export_to_dashboard.py)

Run on your Mac:  streamlit run app.py
"""
import os
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# data files sit in the data/ folder next to this script
HERE = os.path.dirname(os.path.abspath(__file__))
MARKETS_FILE = os.path.join(HERE, "data", "markets.csv")
INDEX_FILE = os.path.join(HERE, "data", "weekly_index.csv")
ARTICLES_FILE = os.path.join(HERE, "data", "weekly_articles.csv")

INK = "#1b2430"
MUTED = "#5d6773"
RUST = "#b4432c"     # hawkish / SGD weaker / down
BLUE = "#2f5f98"     # dovish / SGD stronger / up
RULE = "#d9d3c7"
GREY_BAND = "#e3dfd6"

CURRENCIES = ["USD", "CNY", "MYR", "EUR", "JPY", "GBP", "AUD", "KRW",
              "TWD", "THB", "IDR", "INR", "PHP", "HKD"]
EQUITIES = ["STI (Singapore)", "Hang Seng", "Shanghai Composite", "Nikkei 225",
            "KOSPI", "Taiwan", "Malaysia KLCI", "Indonesia JCI", "S&P 500"]
RATES = ["US 3-month yield", "US 5-year yield", "US 10-year yield"]
CHANNELS = [
    ["Imported inflation", "#cf8a5a"],
    ["Domestic inflation", "#c9a64a"],
    ["MAS signals", "#4a4a4a"],
    ["Growth and output gap", "#5f9e8c"],
    ["Government policy", "#8f87b8"],
    ["Global rates and USD", "#96adc8"],
]

st.set_page_config(page_title="SGD Weekly", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:wght@600&family=IBM+Plex+Sans:wght@400;600&display=swap');
html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
h1, h2, h3 { font-family: 'Source Serif 4', Georgia, serif !important; font-weight: 600 !important; }
.kicker { color: #b4432c; font-size: 0.8rem; font-weight: 600; letter-spacing: 0.12em; }
.headline { font-family: 'Source Serif 4', Georgia, serif; font-size: 1.35rem; line-height: 1.45;
            border-left: 3px solid #b4432c; padding-left: 1rem; margin: 0.5rem 0 1.5rem 0; }
[data-testid="stMetricValue"] { font-family: 'Source Serif 4', Georgia, serif; }
</style>
""", unsafe_allow_html=True)


# ================================================================ data
@st.cache_data(ttl=3600)
def load_markets():
    table = pd.read_csv(MARKETS_FILE, index_col="date", parse_dates=True)
    table = table.sort_index().ffill()   # fill holidays with the last value
    # SGD against each currency: units of that currency per 1 SGD
    # = (currency per USD) / (SGD per USD).  Going UP = SGD stronger.
    for code in CURRENCIES:
        if code == "USD":
            table["SGD/USD"] = 1.0 / table["USD/SGD"]
        elif "USD/" + code in table.columns:
            table["SGD/" + code] = table["USD/" + code] / table["USD/SGD"]
    return table


@st.cache_data(ttl=3600)
def load_index():
    """Returns (weeks, articles), or (None, None) if the files aren't there yet.
    Uses the 'events once' version: each story counted once."""
    if not os.path.exists(INDEX_FILE) or not os.path.exists(ARTICLES_FILE):
        return None, None
    weeks = pd.read_csv(INDEX_FILE)
    weeks = weeks[weeks["version"] == "events once"].copy()
    weeks["week_end"] = pd.to_datetime(weeks["week_end"]).dt.date
    weeks = weeks.sort_values("week_end")
    articles = pd.read_csv(ARTICLES_FILE)
    articles["week_end"] = pd.to_datetime(articles["week_end"]).dt.date
    return weeks, articles


def value_on_or_before(series, day):
    """Last available value on or before 'day'."""
    before = series[series.index <= pd.Timestamp(day)].dropna()
    if len(before) == 0:
        return None
    return before.iloc[-1]


def pct_change(series, start_day, end_day):
    a = value_on_or_before(series, start_day)
    b = value_on_or_before(series, end_day)
    if a is None or b is None:
        return None
    return (b / a - 1) * 100


def completed_weeks(today, how_many):
    """Monday-Sunday weeks that have fully ended, newest first."""
    last_sunday = today - timedelta(days=today.weekday() + 1)
    weeks = []
    for k in range(how_many):
        sunday = last_sunday - timedelta(days=7 * k)
        monday = sunday - timedelta(days=6)
        weeks.append([monday, sunday])
    return weeks


def week_label(week_end):
    monday = week_end - timedelta(days=6)
    return "%s – %s" % (monday.strftime("%d %b"), week_end.strftime("%d %b"))


def chart_layout(fig, height):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=10, b=10),
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                      font=dict(color=INK),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    fig.update_xaxes(gridcolor=RULE)
    fig.update_yaxes(gridcolor=RULE)


# ============================================================== header
markets = load_markets()
index_weeks, index_articles = load_index()

weeks = completed_weeks(date.today(), 12)
labels = []
for monday, sunday in weeks:
    labels.append("%s – %s" % (monday.strftime("%d %b"), sunday.strftime("%d %b %Y")))

top_left, top_right = st.columns([3, 1])
with top_right:
    choice = st.selectbox("Week", labels, index=0)
monday, sunday = weeks[labels.index(choice)]
previous_sunday = monday - timedelta(days=1)

with top_left:
    st.markdown("<div class='kicker'>SINGAPORE MARKETS · WEEKLY</div>", unsafe_allow_html=True)
    st.title("SGD Weekly")

# this week's row of the index, if there is one
index_row = None
if index_weeks is not None:
    match = index_weeks[index_weeks["week_end"] == sunday]
    if len(match) > 0:
        index_row = match.iloc[0]

# ============================================== headline for the call
moves = []
for code in CURRENCIES:
    column = "SGD/" + code
    if column in markets.columns:
        change = pct_change(markets[column], previous_sunday, sunday)
        if change is not None:
            moves.append([code, change])
moves.sort(key=lambda row: row[1])

usd_move = 0.0
for code, change in moves:
    if code == "USD":
        usd_move = change
best = moves[-1]
worst = moves[0]
sti_move = pct_change(markets["STI (Singapore)"], previous_sunday, sunday)

headline = ("The SGD %s <b>%.2f%%</b> against the US dollar. "
            "It did best against the <b>%s</b> (%+.2f%%) and worst against the <b>%s</b> (%+.2f%%). "
            "The STI %s <b>%.2f%%</b>." % (
                "rose" if usd_move >= 0 else "fell", abs(usd_move), best[0], best[1],
                worst[0], worst[1], "rose" if sti_move >= 0 else "fell", abs(sti_move)))
if index_row is not None:
    value = index_row["index"]
    if value > 5:
        tone = "hawkish"
    elif value < -5:
        tone = "dovish"
    else:
        tone = "neutral"
    headline = headline + " The MAS Hawkishness Index read <b>%+.0f</b> (%s)." % (value, tone)
st.markdown("<div class='headline'>%s</div>" % headline, unsafe_allow_html=True)

tab_markets, tab_index = st.tabs(["Markets", "MAS Hawkishness Index"])

# =====================================================================
#                              MARKETS TAB
# =====================================================================
with tab_markets:
    m1, m2, m3, m4 = st.columns(4)
    usdsgd_end = value_on_or_before(markets["USD/SGD"], sunday)
    usdsgd_chg = pct_change(markets["USD/SGD"], previous_sunday, sunday)
    m1.metric("USD/SGD", "%.4f" % usdsgd_end, "%+.2f%%" % usdsgd_chg, delta_color="inverse")
    dxy_chg = pct_change(markets["Dollar index"], previous_sunday, sunday)
    m2.metric("Dollar index", "%.2f" % value_on_or_before(markets["Dollar index"], sunday), "%+.2f%%" % dxy_chg)
    sti_end = value_on_or_before(markets["STI (Singapore)"], sunday)
    m3.metric("STI", "{:,.0f}".format(sti_end), "%+.2f%%" % sti_move)
    brent_chg = pct_change(markets["Brent oil"], previous_sunday, sunday)
    m4.metric("Brent oil ($)", "%.2f" % value_on_or_before(markets["Brent oil"], sunday), "%+.2f%%" % brent_chg)

    st.divider()

    left, right = st.columns([1, 1])
    with left:
        st.subheader("How the SGD did against each currency")
        st.caption("% change over the week. Right of zero = SGD stronger.")
        names = []
        values = []
        colours = []
        texts = []
        for code, change in moves:
            names.append(code)
            values.append(change)
            colours.append(BLUE if change >= 0 else RUST)
            texts.append("%+.2f%%" % change)
        fig = go.Figure(go.Bar(x=values, y=names, orientation="h", marker_color=colours,
                               text=texts, textposition="outside", cliponaxis=False))
        chart_layout(fig, 480)
        fig.update_xaxes(zeroline=True, zerolinecolor=INK, ticksuffix="%")
        fig.update_layout(margin=dict(l=10, r=40, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.subheader("USD/SGD, last 6 months")
        st.caption("Shaded: the selected week. Lower = stronger SGD.")
        start = pd.Timestamp(sunday) - pd.Timedelta(days=182)
        recent = markets.loc[(markets.index >= start) & (markets.index <= pd.Timestamp(sunday)), "USD/SGD"]
        fig = go.Figure(go.Scatter(x=recent.index, y=recent.values, mode="lines",
                                   line=dict(color=INK, width=2)))
        fig.add_vrect(x0=pd.Timestamp(monday), x1=pd.Timestamp(sunday), fillcolor=RUST, opacity=0.12, line_width=0)
        chart_layout(fig, 480)
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    left, right = st.columns([1, 1])
    year_start = date(sunday.year, 1, 1) - timedelta(days=1)
    month_ago = sunday - timedelta(days=30)
    with left:
        st.subheader("Equities")
        st.caption("Local-currency index moves, %.")
        rows = []
        for name in EQUITIES:
            if name not in markets.columns:
                continue
            rows.append({
                "Index": name,
                "Level": value_on_or_before(markets[name], sunday),
                "Week": pct_change(markets[name], previous_sunday, sunday),
                "1 month": pct_change(markets[name], month_ago, sunday),
                "Year to date": pct_change(markets[name], year_start, sunday),
            })
        equity_table = pd.DataFrame(rows)
        st.dataframe(
            equity_table.style.format({"Level": "{:,.0f}", "Week": "{:+.2f}%",
                                       "1 month": "{:+.2f}%", "Year to date": "{:+.1f}%"})
            .map(lambda v: "color: %s" % (BLUE if v >= 0 else RUST),
                 subset=["Week", "1 month", "Year to date"]),
            hide_index=True, use_container_width=True)

    with right:
        st.subheader("Rates")
        st.caption("US Treasury yields. Change in basis points (1bp = 0.01%).")
        rows = []
        for name in RATES:
            if name not in markets.columns:
                continue
            end_value = value_on_or_before(markets[name], sunday)
            start_value = value_on_or_before(markets[name], previous_sunday)
            rows.append({"Yield": name, "Level (%)": end_value,
                         "Week (bp)": (end_value - start_value) * 100})
        rates_table = pd.DataFrame(rows)
        st.dataframe(rates_table.style.format({"Level (%)": "{:.2f}", "Week (bp)": "{:+.0f}"}),
                     hide_index=True, use_container_width=True)
        st.info("Singapore government bond (SGS) yields and SORA are not on Yahoo Finance. "
                "They come next, from the MAS website.")

# =====================================================================
#                        MAS HAWKISHNESS INDEX TAB
# =====================================================================
with tab_index:
    if index_weeks is None:
        st.info("No index data yet. On your Mac, run build_index.py, then export_to_dashboard.py, "
                "and upload the two files in data/ to GitHub.")
    else:
        st.caption("Is Singapore's business news pushing MAS to tighten (+100, hawkish) or ease "
                   "(−100, dovish)? Every article from the Business Times, Straits Times and CNA "
                   "is scored by an AI model; each story counts once.")

        if index_row is None:
            latest = index_weeks.iloc[-1]["week_end"]
            st.warning("No index reading for the week ending %s. The latest is the week ending %s: "
                       "pick it in the Week box above." % (sunday.strftime("%d %b"), latest.strftime("%d %b")))
        else:
            row = index_row
            m1, m2, m3, m4 = st.columns(4)
            partial = "" if bool(row["complete"]) else " (partial week)"
            m1.metric("Index" + partial, "%+.0f" % row["index"])
            m1.caption("%d relevant articles" % row["relevant_articles"])
            m2.metric("Corrected with hand labels", "%+.0f" % row["ppi_index"])
            m2.caption("95%% range %+.0f to %+.0f" % (row["ppi_low"], row["ppi_high"]))
            m3.metric("Outside pressure", "%+.0f" % row["pressure_index"])
            m3.caption("All news except MAS's own words")
            m4.metric("MAS's own signal", "%+.0f" % row["mas_net"])
            m4.caption("MAS decisions, speeches, guidance (surprise decision ≈ 18)")

        st.divider()

        # ---- chart 1: the index over time
        x_labels = []
        selected = None
        position = 0
        for week_end in index_weeks["week_end"]:
            x_labels.append(week_label(week_end))
            if week_end == sunday:
                selected = position
            position = position + 1

        def shade_selected(fig):
            """Light rust band behind the selected week."""
            if selected is not None:
                fig.add_vrect(x0=selected - 0.5, x1=selected + 0.5, fillcolor=RUST,
                              opacity=0.07, line_width=0, layer="below")
        left, right = st.columns([1, 1])
        with left:
            st.subheader("The index, week by week")
            st.caption("Grey: 95% uncertainty range after correcting with my hand labels. "
                       "Dash: the corrected value.")
            fig = go.Figure()
            low = list(index_weeks["ppi_low"])
            high = list(index_weeks["ppi_high"])
            fig.add_trace(go.Bar(x=x_labels, y=[high[i] - low[i] for i in range(len(low))], base=low,
                                 marker_color=GREY_BAND, width=0.35, name="95% range", hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=x_labels, y=index_weeks["ppi_index"], mode="markers",
                                     marker=dict(symbol="line-ew", size=22, line=dict(width=3, color=INK)),
                                     name="Corrected"))
            colours = []
            for value in index_weeks["index"]:
                colours.append(RUST if value >= 0 else BLUE)
            fig.add_trace(go.Scatter(x=x_labels, y=index_weeks["index"], mode="lines+markers+text",
                                     line=dict(color=MUTED, width=2), marker=dict(size=13, color=colours),
                                     text=["%+.0f" % v for v in index_weeks["index"]],
                                     textposition="middle left", name="Index"))
            fig.add_hline(y=0, line_color=INK, line_width=1)
            chart_layout(fig, 420)
            fig.update_yaxes(range=[-100, 100])
            shade_selected(fig)
            st.plotly_chart(fig, use_container_width=True)

        # ---- chart 2: outside pressure vs MAS's own signal
        with right:
            st.subheader("Outside pressure vs MAS's own signal")
            st.caption("Line: all news except MAS's own words (left scale). "
                       "Bars: MAS's decisions, speeches and guidance (right scale).")
            fig = make_subplots(specs=[[{"secondary_y": True}]])
            fig.add_trace(go.Scatter(x=x_labels, y=index_weeks["pressure_index"], mode="lines+markers",
                                     line=dict(color=INK, width=2), name="Outside pressure"),
                          secondary_y=False)
            bar_colours = []
            for value in index_weeks["mas_net"]:
                bar_colours.append(RUST if value > 0 else BLUE)
            fig.add_trace(go.Bar(x=x_labels, y=index_weeks["mas_net"], marker_color=bar_colours,
                                 opacity=0.6, width=0.3, name="MAS's own signal"),
                          secondary_y=True)
            fig.add_hline(y=0, line_color=INK, line_width=1)
            chart_layout(fig, 420)
            fig.update_yaxes(range=[-100, 100], secondary_y=False)
            fig.update_yaxes(range=[-24, 24], secondary_y=True, showgrid=False)
            shade_selected(fig)
            st.plotly_chart(fig, use_container_width=True)

        st.divider()

        # ---- chart 3: which kinds of news moved it
        left, right = st.columns([1, 1])
        with left:
            st.subheader("Which kinds of news moved it")
            st.caption("Above zero pushed hawkish, below pushed dovish. They add up to the index.")
            fig = go.Figure()
            for channel, colour in CHANNELS:
                column = "contrib: " + channel
                if column in index_weeks.columns:
                    fig.add_trace(go.Bar(x=x_labels, y=index_weeks[column], name=channel,
                                         marker_color=colour))
            fig.add_trace(go.Scatter(x=x_labels, y=index_weeks["index"], mode="markers",
                                     marker=dict(size=12, color="white", line=dict(width=3, color=INK)),
                                     name="Index"))
            fig.update_layout(barmode="relative")
            chart_layout(fig, 460)
            shade_selected(fig)
            st.plotly_chart(fig, use_container_width=True)

        with right:
            st.subheader("Channel weights")
            st.caption("How much each kind of news counts in the index.")
            st.dataframe(pd.DataFrame([
                ["Imported inflation", "Oil, gas, food, commodities, shipping", 1.0],
                ["Domestic inflation", "Singapore wages, services prices, jobs", 1.0],
                ["MAS signals", "MAS decisions, speeches, guidance", 1.0],
                ["Growth and output gap", "Singapore and regional growth, trade, the AI cycle", 0.7],
                ["Government policy", "Budget, subsidies, administered prices", 0.5],
                ["Global rates and USD", "The Fed, US data, the dollar", 0.4],
            ], columns=["Channel", "Covers", "Weight"]), hide_index=True, use_container_width=True)

        st.divider()

        # ---- the week's biggest stories, full width
        st.subheader("The week's biggest stories")
        if index_row is None:
            st.caption("Pick a week with an index reading.")
        else:
            st.caption("One article per story, ranked by how hard it pushed. "
                       "Push = channel weight × importance × direction.")
            week_articles = index_articles[index_articles["week_end"] == sunday].copy()
            week_articles["size"] = week_articles["push"].abs()
            week_articles = week_articles.sort_values("size", ascending=False)
            week_articles = week_articles.drop_duplicates(subset="group")
            week_articles = week_articles[week_articles["size"] > 0].head(10)
            rows = []
            for _, article in week_articles.iterrows():
                rows.append({"Push": article["push"],
                             "Headline": article["headline"],
                             "Channel": article["channel"],
                             "Why (AI)": article["ai_summary"],
                             "Link": article["link"]})
            stories = pd.DataFrame(rows)
            st.dataframe(
                stories, hide_index=True, use_container_width=True, height=400,
                column_config={
                    "Push": st.column_config.NumberColumn(format="%+.1f", width="small"),
                    "Headline": st.column_config.TextColumn(width="large"),
                    "Why (AI)": st.column_config.TextColumn(width="large"),
                    "Link": st.column_config.LinkColumn(display_text="open", width="small"),
                })

        with st.expander("How the index works"):
            st.markdown("""
1. **Collect.** Every article from four RSS feeds: Business Times, Straits Times Business,
   CNA Business and CNA Singapore. About 750 a week.
2. **Score.** An AI model answers three questions per article, following a written codebook:
   which **channel** it reaches Singapore through, how **important** it is
   (relevance × (impact + surprise), 0 to 18), and which **direction** it pushes MAS.
3. **Combine.** Push = channel weight × importance × direction. The index is
   100 × total push ÷ total weight, so it runs from −100 to +100. Each story counts once,
   even if several outlets carry it.
4. **Check.** 110 articles scored by hand. The AI agrees on direction 89% of the time on a
   random sample; the corrected value and the grey range use those hand labels.
""")

st.divider()
st.caption("Markets: Yahoo Finance, daily closes, last updated %s. Weeks run Monday to Sunday. "
           "Coming next: SGS yields and SORA; replicated daily S$NEER and its position in MAS's band."
           % markets.index[-1].strftime("%d %b %Y"))
