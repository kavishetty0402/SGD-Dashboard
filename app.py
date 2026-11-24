"""
app.py
The SGD weekly dashboard. Shows one calendar week (Monday to Sunday) in
two tabs:
  Markets                  currencies, equities, rates (data/markets.csv)
  MAS Hawkishness Index    the news index (data/weekly_index.csv and
                           data/weekly_articles.csv)

STYLE (house style + "Choosing Colors for Data Visualization", M. Stone)
  - White background. Everything that is context (grid, axes, labels) is grey,
    so the data is what you see first.
  - Few colours, each with ONE job, never reassigned:
      orange  #E97132  the headline series (what the chart is about)
      blue    #0F9ED5  the comparison series
      grey    #7F7F7F  third series / de-emphasised context
      light orange #FCD5B5 and light grey #D9D9D9: bands and secondary fills only
  - Montserrat everywhere.
  - Every chart: grey subtitle, stat row, legend, then the plot,
    then a small grey source line.
  - Lines end in a dot plus a badge with the latest value.
    Bars carry plain bold black labels.

Run on your Mac:  streamlit run app.py
"""
import math
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

# ------------------------------------------------------------- house colours
ORANGE = "#E97132"         # headline series
BLUE = "#0F9ED5"           # comparison series
GREY = "#7F7F7F"           # third series / context
LIGHT_ORANGE = "#FCD5B5"   # bands and fills only
LIGHT_GREY = "#D9D9D9"     # bands and fills only
INK = "#262626"            # text
SOFT = "#595959"           # secondary text
GRID = "#EDEDED"           # gridlines: barely there
FONT = "Montserrat, 'Century Gothic', 'Gill Sans', Arial, sans-serif"

# Channel colours follow "analogy groups" (Stone): related things share a hue.
#   inflation channels = the orange family, growth = blue,
#   policy and global rates = the grey family.
CHANNELS = [
    ["Imported inflation", ORANGE],
    ["Domestic inflation", LIGHT_ORANGE],
    ["Growth and output gap", BLUE],
    ["Global rates and USD", GREY],
    ["MAS signals", INK],
    ["Government policy", LIGHT_GREY],
]

CURRENCIES = ["USD", "CNY", "MYR", "EUR", "JPY", "GBP", "AUD", "KRW",
              "TWD", "THB", "IDR", "INR", "PHP", "HKD"]
EQUITIES = ["STI (Singapore)", "Hang Seng", "Shanghai Composite", "Nikkei 225",
            "KOSPI", "Taiwan", "Malaysia KLCI", "Indonesia JCI", "S&P 500"]
SHORT_NAMES = {"STI (Singapore)": "STI", "Shanghai Composite": "Shanghai",
               "Nikkei 225": "Nikkei", "Malaysia KLCI": "KLCI",
               "Indonesia JCI": "JCI", "S&P 500": "S&P 500"}
RATES = [["US 10-year yield", "10-year", ORANGE],
         ["US 5-year yield", "5-year", BLUE],
         ["US 3-month yield", "3-month", GREY]]

PLOT_CONFIG = {"displayModeBar": False, "scrollZoom": False}

st.set_page_config(page_title="SGD Weekly", layout="wide")

# ------------------------------------------------------------------- CSS
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"], .stApp, .stMarkdown, p, div, span, label, button, input {
    font-family: Montserrat, 'Century Gothic', 'Gill Sans', Arial, sans-serif;
}
.stApp { background: #FFFFFF; }
.block-container { padding-top: 2.2rem; max-width: 1400px; }
h1, h2, h3 { font-family: Montserrat, sans-serif !important; color: #262626; letter-spacing: -0.01em; }

/* header */
.kicker { color: #E97132; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.16em; }
.page-title { font-size: 2.3rem; font-weight: 800; color: #262626; margin: 0.1rem 0 0 0; letter-spacing: -0.02em; }
.headline { font-size: 1.12rem; line-height: 1.65; color: #262626; font-weight: 500;
            border-left: 3px solid #E97132; padding: 0.2rem 0 0.2rem 1.1rem; margin: 1.2rem 0 1.6rem 0; }
.headline b { font-weight: 700; }

/* big numbers at the top of a tab */
.kpi-row { display: flex; flex-wrap: wrap; gap: 0; margin: 0.4rem 0 0.6rem 0;
           border-top: 1px solid #EDEDED; border-bottom: 1px solid #EDEDED; }
.kpi { flex: 1 1 180px; padding: 1rem 1.2rem 1rem 0; }
.kpi + .kpi { padding-left: 1.2rem; border-left: 1px solid #EDEDED; }
.kpi-label { font-size: 0.72rem; font-weight: 600; color: #7F7F7F; letter-spacing: 0.08em; text-transform: uppercase; }
.kpi-value { font-size: 2rem; font-weight: 700; color: #262626; line-height: 1.25; font-variant-numeric: tabular-nums; }
.kpi-note { font-size: 0.82rem; color: #595959; font-variant-numeric: tabular-nums; }
.kpi-note b { color: #262626; }

/* chart anatomy: section title, subtitle, stat row, legend, source */
.chart-title { font-size: 1.05rem; font-weight: 700; color: #262626; margin: 1.4rem 0 0.15rem 0; }
.subtitle { font-size: 0.84rem; color: #7F7F7F; margin-bottom: 0.7rem; }
.stat-row { display: flex; flex-wrap: wrap; gap: 0.35rem 1.6rem; margin-bottom: 0.55rem;
            font-variant-numeric: tabular-nums; }
.stat-label { color: #7F7F7F; font-size: 0.84rem; margin-right: 0.35rem; }
.stat-value { color: #262626; font-size: 0.98rem; font-weight: 700; }
.legend { display: flex; flex-wrap: wrap; gap: 0.3rem 1.3rem; font-size: 0.8rem; margin-bottom: 0.2rem; }
.legend-item { display: inline-flex; align-items: center; gap: 0.45rem; }
.legend-item b { color: #262626; font-weight: 700; }
.legend-item .desc { color: #7F7F7F; }
.sw-line { display: inline-block; width: 18px; height: 3px; border-radius: 2px; }
.sw-dash { display: inline-block; width: 18px; height: 0; border-top: 2px dashed; }
.sw-box { display: inline-block; width: 11px; height: 11px; border-radius: 2px; }
.source { font-size: 0.72rem; color: #7F7F7F; margin: -0.4rem 0 0.4rem 0; }

/* tabs */
.stTabs [data-baseweb="tab-list"] { gap: 1.8rem; border-bottom: 1px solid #EDEDED; }
.stTabs [data-baseweb="tab"] { font-weight: 600; color: #7F7F7F; padding: 0.6rem 0; }
.stTabs [aria-selected="true"] { color: #262626; }
.stTabs [data-baseweb="tab-highlight"] { background-color: #E97132; }

hr { border-color: #EDEDED !important; }
[data-testid="stExpander"] { border-color: #EDEDED; }
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
    weeks = weeks.sort_values("week_end").reset_index(drop=True)
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
    if monday.month == week_end.month:
        return "%d–%d %s" % (monday.day, week_end.day, week_end.strftime("%b"))
    return "%d %s–%d %s" % (monday.day, monday.strftime("%b"), week_end.day, week_end.strftime("%b"))


def is_missing(value):
    return value is None or (isinstance(value, float) and math.isnan(value))


def signed(value, digits=0, suffix=""):
    if is_missing(value):
        return "–"
    text = ("%+." + str(digits) + "f") % value
    if float(text) == 0:
        text = ("%." + str(digits) + "f") % 0
    return text.replace("-", "−") + suffix


# ============================================================ HTML pieces
def kpi_row(items):
    """items: [label, big value, small note (may contain <b>)]"""
    html = "<div class='kpi-row'>"
    for label, value, note in items:
        html = html + ("<div class='kpi'><div class='kpi-label'>%s</div>"
                       "<div class='kpi-value'>%s</div><div class='kpi-note'>%s</div></div>"
                       % (label, value, note))
    st.markdown(html + "</div>", unsafe_allow_html=True)


def chart_header(title, subtitle, stats, legend):
    """Title, grey subtitle, stat row (grey label + bold value), legend.
    stats:  [label, value, colour or None]
    legend: [kind ('line', 'dash' or 'box'), colour, bold label, grey description]"""
    html = "<div class='chart-title'>%s</div><div class='subtitle'>%s</div>" % (title, subtitle)
    if len(stats) > 0:
        html = html + "<div class='stat-row'>"
        for label, value, colour in stats:
            style = ""
            if colour is not None:
                style = " style='color:%s'" % colour
            html = html + ("<span><span class='stat-label'>%s</span>"
                           "<span class='stat-value'%s>%s</span></span>" % (label, style, value))
        html = html + "</div>"
    if len(legend) > 0:
        html = html + "<div class='legend'>"
        for kind, colour, label, desc in legend:
            if kind == "line":
                swatch = "<span class='sw-line' style='background:%s'></span>" % colour
            elif kind == "dash":
                swatch = "<span class='sw-dash' style='border-color:%s'></span>" % colour
            else:
                swatch = "<span class='sw-box' style='background:%s'></span>" % colour
            html = html + ("<span class='legend-item'>%s<b>%s</b><span class='desc'>%s</span></span>"
                           % (swatch, label, desc))
        html = html + "</div>"
    st.markdown(html, unsafe_allow_html=True)


def source_line(text):
    st.markdown("<div class='source'>%s</div>" % text, unsafe_allow_html=True)


# ========================================================== chart pieces
def house_layout(fig, height, hover="x unified", right_margin=20):
    """White, grey context, horizontal gridlines only, no boxes, Montserrat."""
    fig.update_layout(
        height=height,
        margin=dict(l=4, r=right_margin, t=14, b=8),
        plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
        font=dict(family=FONT, color=SOFT, size=12),
        showlegend=False,
        hovermode=hover,
        hoverlabel=dict(bgcolor="#FFFFFF", bordercolor=LIGHT_GREY,
                        font=dict(family=FONT, color=INK, size=12)),
        bargap=0.35,
    )
    fig.update_xaxes(showgrid=False, showline=False, zeroline=False, ticks="",
                     tickfont=dict(color=SOFT, size=11),
                     showspikes=True, spikemode="across", spikesnap="cursor",
                     spikecolor=GREY, spikethickness=1, spikedash="dot")
    fig.update_yaxes(showgrid=True, gridcolor=GRID, gridwidth=1, showline=False,
                     zeroline=False, ticks="", tickfont=dict(color=SOFT, size=11))


def zero_line(fig):
    """Solid grey baseline, a little heavier than the gridlines."""
    fig.add_hline(y=0, line_color=GREY, line_width=1.4, layer="below")


def end_badge(fig, x, y, text, colour):
    """Solid dot on the last point plus a badge with the current value."""
    fig.add_trace(go.Scatter(x=[x], y=[y], mode="markers", hoverinfo="skip",
                             marker=dict(size=9, color=colour, line=dict(width=2, color="#FFFFFF"))))
    fig.add_annotation(x=x, y=y, text="<b>%s</b>" % text, showarrow=False,
                       xanchor="left", xshift=10, bgcolor=colour, bordercolor=colour,
                       borderpad=4, font=dict(family=FONT, color="#FFFFFF", size=12))


def week_band(fig, x0, x1, caption):
    """Light orange band for the selected week, small grey caption on top."""
    fig.add_vrect(x0=x0, x1=x1, fillcolor=LIGHT_ORANGE, opacity=0.45, line_width=0, layer="below")
    fig.add_annotation(x=x0, xref="x", y=1.0, yref="paper", text=caption, showarrow=False,
                       xanchor="left", yanchor="bottom", font=dict(size=10, color=GREY))


def pad_bars(fig, values):
    """Leave room either side so the bold labels never hit the axis labels."""
    low = min(values + [0])
    high = max(values + [0])
    span = high - low
    fig.update_xaxes(range=[low - span * 0.22, high + span * 0.22])


def show(fig):
    st.plotly_chart(fig, width="stretch", config=PLOT_CONFIG)


# ============================================================== header
if not os.path.exists(MARKETS_FILE):
    st.markdown("<div class='page-title'>SGD Weekly</div>", unsafe_allow_html=True)
    st.warning("No market data yet (data/markets.csv is missing). On GitHub, open the Actions tab, "
               "click 'Daily update', then 'Run workflow'.")
    st.stop()
markets = load_markets()
index_weeks, index_articles = load_index()

weeks = completed_weeks(date.today(), 12)
labels = []
for monday, sunday in weeks:
    labels.append("%s – %s" % (monday.strftime("%d %b"), sunday.strftime("%d %b %Y")))

top_left, top_right = st.columns([3, 1], vertical_alignment="bottom")
with top_right:
    choice = st.selectbox("Week", labels, index=0)
monday, sunday = weeks[labels.index(choice)]
previous_sunday = monday - timedelta(days=1)

with top_left:
    st.markdown("<div class='kicker'>SINGAPORE DOLLAR · WEEKLY</div>"
                "<div class='page-title'>SGD Weekly</div>", unsafe_allow_html=True)

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
        if change is not None and not math.isnan(change):
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
            "It did best against the <b>%s</b> (%s) and worst against the <b>%s</b> (%s). "
            "The STI %s <b>%.2f%%</b>." % (
                "rose" if usd_move >= 0 else "fell", abs(usd_move),
                best[0], signed(best[1], 2, "%"), worst[0], signed(worst[1], 2, "%"),
                "rose" if sti_move >= 0 else "fell", abs(sti_move)))
if index_row is not None:
    value = index_row["index"]
    if value > 5:
        tone = "hawkish"
    elif value < -5:
        tone = "dovish"
    else:
        tone = "neutral"
    headline = headline + " The MAS Hawkishness Index read <b>%s</b> (%s)." % (signed(value), tone)
st.markdown("<div class='headline'>%s</div>" % headline, unsafe_allow_html=True)

tab_markets, tab_index = st.tabs(["Markets", "MAS Hawkishness Index"])

MARKET_SOURCE = ("Source: Yahoo Finance, daily closes. Last updated %s."
                 % markets.index[-1].strftime("%d %b %Y"))

# =====================================================================
#                              MARKETS TAB
# =====================================================================
with tab_markets:
    usdsgd_end = value_on_or_before(markets["USD/SGD"], sunday)
    usdsgd_chg = pct_change(markets["USD/SGD"], previous_sunday, sunday)
    dxy_end = value_on_or_before(markets["Dollar index"], sunday)
    dxy_chg = pct_change(markets["Dollar index"], previous_sunday, sunday)
    sti_end = value_on_or_before(markets["STI (Singapore)"], sunday)
    brent_end = value_on_or_before(markets["Brent oil"], sunday)
    brent_chg = pct_change(markets["Brent oil"], previous_sunday, sunday)
    kpi_row([
        ["USD/SGD", "%.4f" % usdsgd_end,
         "<b>%s</b> on the week · %s" % (signed(usdsgd_chg, 2, "%"),
                                        "SGD weaker" if usdsgd_chg > 0 else "SGD stronger")],
        ["Dollar index", "%.2f" % dxy_end, "<b>%s</b> on the week" % signed(dxy_chg, 2, "%")],
        ["STI", "{:,.0f}".format(sti_end), "<b>%s</b> on the week" % signed(sti_move, 2, "%")],
        ["Brent oil", "$%.2f" % brent_end, "<b>%s</b> on the week" % signed(brent_chg, 2, "%")],
    ])

    left, right = st.columns([1, 1], gap="large")

    # ------------------------------------------- SGD against each currency
    with left:
        chart_header(
            "The SGD against each currency",
            "% change over the week. Right of zero = SGD stronger.",
            [["vs USD", signed(usd_move, 2, "%"), ORANGE],
             ["Best", "%s %s" % (best[0], signed(best[1], 2, "%")), None],
             ["Worst", "%s %s" % (worst[0], signed(worst[1], 2, "%")), None]],
            [["box", ORANGE, "US dollar", "the headline cross"],
             ["box", LIGHT_GREY, "Other currencies", ""]])
        names = []
        values = []
        colours = []
        texts = []
        for code, change in moves:
            names.append(code)
            values.append(change)
            if code == "USD":
                colours.append(ORANGE)
            else:
                colours.append(LIGHT_GREY)
            texts.append("<b>%s</b>" % signed(change, 2, "%"))
        fig = go.Figure(go.Bar(
            x=values, y=names, orientation="h", marker=dict(color=colours, line=dict(width=0)),
            text=texts, textposition="outside", cliponaxis=False,
            textfont=dict(family=FONT, color=INK, size=11),
            hovertemplate="<b>SGD vs %{y}</b>: %{x:+.2f}%<extra></extra>"))
        house_layout(fig, 470, hover="closest", right_margin=50)
        pad_bars(fig, values)
        fig.update_xaxes(showspikes=False, ticksuffix="%", tickformat="+.1f", showgrid=True,
                         gridcolor=GRID)
        fig.update_yaxes(showgrid=False, tickfont=dict(color=INK, size=12))
        fig.add_vline(x=0, line_color=GREY, line_width=1.4)
        show(fig)
        source_line(MARKET_SOURCE)

    # ------------------------------------------- USD/SGD over time
    with right:
        history = markets["USD/SGD"].dropna()
        periods = {"1M": 31, "3M": 92, "6M": 183, "1Y": 366, "2Y": 731}
        period = st.session_state.get("usdsgd_period") or "6M"
        window_start = history.index[-1] - pd.Timedelta(days=periods[period])
        window = history[history.index >= window_start]
        chart_header(
            "USD/SGD",
            "Daily close. Lower = stronger SGD. Shaded: the selected week.",
            [["Latest", "%.4f" % window.iloc[-1], ORANGE],
             ["%s high" % period, "%.4f" % window.max(), None],
             ["%s low" % period, "%.4f" % window.min(), None],
             ["%s change" % period, signed((window.iloc[-1] / window.iloc[0] - 1) * 100, 2, "%"), None]],
            [["line", ORANGE, "USD/SGD", "SGD per US dollar"]])
        st.segmented_control("Period", list(periods.keys()), default="6M",
                             label_visibility="collapsed", key="usdsgd_period")
        fig = go.Figure(go.Scatter(
            x=window.index, y=window.values, mode="lines", line=dict(color=ORANGE, width=2),
            hovertemplate="%{y:.4f}<extra>USD/SGD</extra>"))
        if pd.Timestamp(monday) >= window.index[0]:
            week_band(fig, pd.Timestamp(monday), pd.Timestamp(sunday) + pd.Timedelta(hours=23), "Selected week")
        end_badge(fig, window.index[-1], window.iloc[-1], "%.4f" % window.iloc[-1], ORANGE)
        house_layout(fig, 430, right_margin=80)
        padding = (window.max() - window.min()) * 0.08
        fig.update_yaxes(tickformat=".3f", range=[window.min() - padding, window.max() + padding])
        show(fig)
        source_line(MARKET_SOURCE)

    st.divider()
    left, right = st.columns([1, 1], gap="large")
    year_start = date(sunday.year, 1, 1) - timedelta(days=1)
    month_ago = sunday - timedelta(days=30)

    # ------------------------------------------- equities
    with left:
        rows = []
        for name in EQUITIES:
            if name not in markets.columns:
                continue
            rows.append([pct_change(markets[name], previous_sunday, sunday), name])
        rows.sort()
        sti_month = pct_change(markets["STI (Singapore)"], month_ago, sunday)
        sti_ytd = pct_change(markets["STI (Singapore)"], year_start, sunday)
        chart_header(
            "Regional equities this week",
            "Local-currency index moves, % over the week.",
            [["STI week", signed(sti_move, 2, "%"), ORANGE],
             ["STI 1 month", signed(sti_month, 1, "%"), None],
             ["STI year to date", signed(sti_ytd, 1, "%"), None]],
            [["box", ORANGE, "STI", "Singapore"],
             ["box", LIGHT_GREY, "Other markets", ""]])
        names = []
        values = []
        colours = []
        texts = []
        for change, name in rows:
            names.append(SHORT_NAMES.get(name, name))
            values.append(change)
            colours.append(ORANGE if name == "STI (Singapore)" else LIGHT_GREY)
            texts.append("<b>%s</b>" % signed(change, 2, "%"))
        fig = go.Figure(go.Bar(
            x=values, y=names, orientation="h", marker=dict(color=colours),
            text=texts, textposition="outside", cliponaxis=False,
            textfont=dict(family=FONT, color=INK, size=11),
            hovertemplate="<b>%{y}</b>: %{x:+.2f}%<extra></extra>"))
        house_layout(fig, 380, hover="closest", right_margin=50)
        pad_bars(fig, values)
        fig.update_xaxes(showspikes=False, ticksuffix="%", tickformat="+.1f", showgrid=True,
                         gridcolor=GRID)
        fig.update_yaxes(showgrid=False, tickfont=dict(color=INK, size=12))
        fig.add_vline(x=0, line_color=GREY, line_width=1.4)
        show(fig)
        source_line(MARKET_SOURCE)

        with st.expander("Levels, 1-month and year-to-date moves"):
            table_rows = []
            for name in EQUITIES:
                if name not in markets.columns:
                    continue
                table_rows.append({
                    "Index": name,
                    "Level": value_on_or_before(markets[name], sunday),
                    "Week": pct_change(markets[name], previous_sunday, sunday),
                    "1 month": pct_change(markets[name], month_ago, sunday),
                    "Year to date": pct_change(markets[name], year_start, sunday),
                })
            st.dataframe(
                pd.DataFrame(table_rows), hide_index=True, width="stretch",
                column_config={
                    "Level": st.column_config.NumberColumn(format="%,.0f"),
                    "Week": st.column_config.NumberColumn(format="%+.2f%%"),
                    "1 month": st.column_config.NumberColumn(format="%+.2f%%"),
                    "Year to date": st.column_config.NumberColumn(format="%+.1f%%"),
                })

    # ------------------------------------------- rates
    with right:
        stats = []
        for column, short, colour in RATES:
            end_value = value_on_or_before(markets[column], sunday)
            start_value = value_on_or_before(markets[column], previous_sunday)
            stats.append([short, "%.2f%% (%s bp)" % (end_value, signed((end_value - start_value) * 100)),
                          colour if short == "10-year" else None])
        legend = []
        for column, short, colour in RATES:
            legend.append(["line", colour, short, ""])
        chart_header("US Treasury yields",
                     "Daily, %. Week change in basis points (1bp = 0.01%). Shaded: the selected week.",
                     stats, legend)
        fig = go.Figure()
        start = pd.Timestamp(sunday) - pd.Timedelta(days=182)
        for column, short, colour in RATES:
            series = markets.loc[markets.index >= start, column].dropna()
            fig.add_trace(go.Scatter(x=series.index, y=series.values, mode="lines", name=short,
                                     line=dict(color=colour, width=2),
                                     hovertemplate="%{y:.2f}%<extra>" + short + "</extra>"))
        for column, short, colour in RATES:
            series = markets.loc[markets.index >= start, column].dropna()
            end_badge(fig, series.index[-1], series.iloc[-1], "%.2f" % series.iloc[-1], colour)
        week_band(fig, pd.Timestamp(monday), pd.Timestamp(sunday) + pd.Timedelta(hours=23), "Selected week")
        house_layout(fig, 380, right_margin=70)
        fig.update_xaxes(range=[start, markets.index[-1] + pd.Timedelta(days=3)])
        fig.update_yaxes(ticksuffix="%", tickformat=".2f")
        show(fig)
        source_line(MARKET_SOURCE + " SGS yields and SORA (from MAS) come next.")

# =====================================================================
#                        MAS HAWKISHNESS INDEX TAB
# =====================================================================
INDEX_SOURCE = ("Source: Business Times, Straits Times, CNA. Each article scored by Mistral Medium "
                "against a written codebook; each story counted once.")

with tab_index:
    if index_weeks is None:
        st.info("No index data yet: data/weekly_index.csv is missing.")
    else:
        if index_row is None:
            latest = index_weeks.iloc[-1]["week_end"]
            st.warning("No index reading for the week ending %s. The latest is the week ending %s: "
                       "pick it in the Week box above." % (sunday.strftime("%d %b"), latest.strftime("%d %b")))
        else:
            row = index_row
            partial = "" if bool(row["complete"]) else " · partial week"
            mas_net = row["mas_net"]
            if is_missing(mas_net) or row["mas_news"] == 0:
                mas_text = "none"
                mas_note = "no MAS decision, speech or guidance"
            else:
                mas_text = signed(mas_net)
                mas_note = "a surprise decision ≈ 18"
            kpi_row([
                ["Index", signed(row["index"]),
                 "%d relevant articles%s" % (row["relevant_articles"], partial)],
                ["Corrected", signed(row["ppi_index"]),
                 "95%% range <b>%s</b> to <b>%s</b>" % (signed(row["ppi_low"]), signed(row["ppi_high"]))],
                ["Outside pressure", signed(row["pressure_index"]), "all news except MAS's own words"],
                ["MAS's own signal", mas_text, mas_note],
            ])

        x_labels = []
        selected = None
        position = 0
        for week_end in index_weeks["week_end"]:
            x_labels.append(week_label(week_end))
            if week_end == sunday:
                selected = position
            position = position + 1
        last = len(x_labels) - 1

        def shade_selected(fig):
            if selected is not None:
                week_band(fig, selected - 0.5, selected + 0.5, "Selected")

        left, right = st.columns([1, 1], gap="large")

        # ---- chart 1: the index over time
        with left:
            latest_row = index_weeks.iloc[-1]
            chart_header(
                "Is the news pushing MAS to tighten or ease?",
                "+100 = every relevant story hawkish, −100 = every story dovish. "
                "Corrected = adjusted for the AI's measured bias against 110 hand labels.",
                [["Latest" + ("" if bool(latest_row["complete"]) else " (partial week)"),
                  signed(latest_row["index"]), ORANGE],
                 ["Corrected", signed(latest_row["ppi_index"]), BLUE],
                 ["4-week average", signed(latest_row["index_4wk_avg"]), None]],
                [["line", ORANGE, "Index", "AI scores"],
                 ["dash", BLUE, "Corrected", "with hand labels"],
                 ["box", LIGHT_GREY, "95% range", "of the corrected value"]])
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=x_labels, y=index_weeks["ppi_high"], mode="lines",
                                     line=dict(width=0), hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=x_labels, y=index_weeks["ppi_low"], mode="lines",
                                     line=dict(width=0), fill="tonexty", fillcolor="rgba(217,217,217,0.55)",
                                     hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=x_labels, y=index_weeks["ppi_index"], mode="lines",
                                     line=dict(color=BLUE, width=2, dash="dash"),
                                     hovertemplate="%{y:+.0f}<extra>Corrected</extra>"))
            fig.add_trace(go.Scatter(x=x_labels, y=index_weeks["index"], mode="lines+markers",
                                     line=dict(color=ORANGE, width=2.5),
                                     marker=dict(size=7, color=ORANGE),
                                     hovertemplate="<b>%{y:+.0f}</b><extra>Index</extra>"))
            zero_line(fig)
            end_badge(fig, x_labels[last], latest_row["index"], signed(latest_row["index"]), ORANGE)
            fig.add_annotation(x=0, xref="paper", y=96, text="HAWKISH ↑", showarrow=False,
                               xanchor="left", font=dict(size=10, color=GREY))
            fig.add_annotation(x=0, xref="paper", y=-96, text="DOVISH ↓", showarrow=False,
                               xanchor="left", font=dict(size=10, color=GREY))
            shade_selected(fig)
            house_layout(fig, 430, right_margin=60)
            fig.update_yaxes(range=[-100, 100], tickformat="+d", dtick=25)
            show(fig)
            source_line(INDEX_SOURCE)

        # ---- chart 2: outside pressure vs MAS's own signal
        with right:
            mas_weeks = 0
            for value in index_weeks["mas_news"]:
                if value > 0:
                    mas_weeks = mas_weeks + 1
            chart_header(
                "The world vs MAS's own words",
                "Line: the index using all news except MAS's own statements (left scale). "
                "Bars: the push from MAS's own decisions and speeches (right scale).",
                [["Outside pressure, latest", signed(latest_row["pressure_index"]), ORANGE],
                 ["Weeks with MAS news", "%d of %d" % (mas_weeks, len(x_labels)), BLUE]],
                [["line", ORANGE, "Outside pressure", "−100 to +100"],
                 ["box", BLUE, "MAS's own signal", "surprise decision ≈ 18"]])
            fig = make_subplots(specs=[[{"secondary_y": True}]])
            mas_values = []
            for value in index_weeks["mas_net"]:
                if is_missing(value):
                    mas_values.append(0)
                else:
                    mas_values.append(value)
            fig.add_trace(go.Bar(x=x_labels, y=mas_values, marker=dict(color=BLUE), width=0.32,
                                 opacity=0.85,
                                 hovertemplate="%{y:+.0f}<extra>MAS's own signal</extra>"),
                          secondary_y=True)
            fig.add_trace(go.Scatter(x=x_labels, y=index_weeks["pressure_index"], mode="lines+markers",
                                     line=dict(color=ORANGE, width=2.5), marker=dict(size=7, color=ORANGE),
                                     hovertemplate="<b>%{y:+.0f}</b><extra>Outside pressure</extra>"),
                          secondary_y=False)
            end_badge(fig, x_labels[last], latest_row["pressure_index"],
                      signed(latest_row["pressure_index"]), ORANGE)
            zero_line(fig)
            shade_selected(fig)
            house_layout(fig, 430, right_margin=60)
            fig.update_yaxes(range=[-100, 100], tickformat="+d", dtick=25, secondary_y=False)
            fig.update_yaxes(range=[-24, 24], showgrid=False, secondary_y=True,
                             tickvals=[-18, -12, -6, 0, 6, 12, 18],
                             ticktext=["−18", "−12", "−6", "0", "+6", "+12", "+18"],
                             tickfont=dict(color=BLUE, size=11))
            show(fig)
            source_line(INDEX_SOURCE)

        st.divider()

        # ---- chart 3: which kinds of news moved it
        chosen = latest_row if index_row is None else index_row
        contributions = []
        for channel, colour in CHANNELS:
            column = "contrib: " + channel
            if column in index_weeks.columns:
                contributions.append([chosen[column], channel])
        contributions.sort()
        biggest_down = contributions[0]
        biggest_up = contributions[-1]
        legend = []
        for channel, colour in CHANNELS:
            legend.append(["box", colour, channel, ""])
        legend.append(["box", "#FFFFFF; border:2px solid #262626", "Index", "sum of the bars"])
        chart_header(
            "Which kinds of news moved it",
            "Index points by channel. Bars above zero pushed hawkish, below pushed dovish; "
            "they add up to the index. Inflation channels in orange, growth in blue, policy and rates in grey.",
            [["Week ending", chosen["week_end"].strftime("%d %b"), None],
             ["Biggest hawkish push", "%s %s" % (biggest_up[1], signed(biggest_up[0])), None],
             ["Biggest dovish push", "%s %s" % (biggest_down[1], signed(biggest_down[0])), None]],
            legend)
        fig = go.Figure()
        for channel, colour in CHANNELS:
            column = "contrib: " + channel
            if column in index_weeks.columns:
                fig.add_trace(go.Bar(x=x_labels, y=index_weeks[column], name=channel,
                                     marker=dict(color=colour, line=dict(width=0.6, color="#FFFFFF")),
                                     hovertemplate="%{y:+.1f}<extra>" + channel + "</extra>"))
        fig.add_trace(go.Scatter(x=x_labels, y=index_weeks["index"], mode="markers", name="Index",
                                 marker=dict(size=12, color="#FFFFFF", line=dict(width=2.5, color=INK)),
                                 hovertemplate="<b>%{y:+.0f}</b><extra>Index</extra>"))
        fig.update_layout(barmode="relative")
        zero_line(fig)
        shade_selected(fig)
        house_layout(fig, 440)
        fig.update_xaxes(showspikes=False)
        fig.update_yaxes(tickformat="+d")
        show(fig)
        source_line(INDEX_SOURCE)

        st.divider()

        # ---- the week's biggest stories
        st.markdown("<div class='chart-title'>The week's biggest stories</div>", unsafe_allow_html=True)
        if index_row is None:
            st.markdown("<div class='subtitle'>Pick a week with an index reading.</div>",
                        unsafe_allow_html=True)
        else:
            st.markdown("<div class='subtitle'>One article per story, ranked by how hard it pushed. "
                        "Push = channel weight × importance × direction (+ hawkish, − dovish).</div>",
                        unsafe_allow_html=True)
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
            st.dataframe(
                pd.DataFrame(rows), hide_index=True, width="stretch", height=400,
                column_config={
                    "Push": st.column_config.NumberColumn(format="%+.1f", width="small"),
                    "Headline": st.column_config.TextColumn(width="large"),
                    "Why (AI)": st.column_config.TextColumn(width="large"),
                    "Link": st.column_config.LinkColumn(display_text="open", width="small"),
                })
            source_line(INDEX_SOURCE)

        with st.expander("How the index works"):
            st.markdown("""
1. **Collect.** Every article from four RSS feeds: Business Times, Straits Times Business,
   CNA Business and CNA Singapore. About 750 a week.
2. **Score.** An AI model (Mistral Medium) answers three questions per article, following a
   written codebook: which **channel** it reaches Singapore through, how **important** it is
   (relevance × (impact + surprise), 0 to 18), and which **direction** it pushes MAS.
3. **Combine.** Push = channel weight × importance × direction. The index is
   100 × total push ÷ total weight, so it runs from −100 to +100. Each story counts once,
   even if several outlets carry it.
4. **Check.** 110 articles scored by hand. The AI agrees on direction 88% of the time on a
   random sample, with no hawkish/dovish flips; the corrected value and the grey range use
   those hand labels.

| Channel | Covers | Weight |
|---|---|---|
| Imported inflation | Oil, gas, food, commodities, shipping | 1.0 |
| Domestic inflation | Singapore wages, services prices, jobs | 1.0 |
| MAS signals | MAS decisions, speeches, guidance | 1.0 |
| Growth and output gap | Singapore and regional growth, trade, the AI cycle | 0.7 |
| Government policy | Budget, subsidies, administered prices | 0.5 |
| Global rates and USD | The Fed, US data, the dollar | 0.4 |
""")
