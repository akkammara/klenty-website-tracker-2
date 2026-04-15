#!/usr/bin/env python3
"""
Klenty Website Performance Tracker — Streamlit Dashboard
Shows weekly traffic by channel, demo/signup clicks (excl Chennai/TN),
and Pipedrive leads (excl Outbound) from Jan 1 2026 onwards.
Includes regional breakdown: NORTHAM, EUK, AMEA, LATAM.
"""

import json
import os
import requests
import streamlit as st
import plotly.graph_objects as go
from datetime import date, datetime, timedelta
from collections import defaultdict

# ── Page Config ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Klenty Website Performance",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Constants ───────────────────────────────────────────────────────────────

# ── Config: supports both local files and Streamlit Cloud secrets ─────────
GA4_CREDS_FILE = "/Applications/Claude Code Cowork/GA4 Files/klenty-ga4-mcp-aa6a79e8f638.json"
GA4_PROPERTY_ID = "properties/264568011"
DATA_START = date(2026, 1, 1)

# Pipedrive — read from Streamlit secrets if available, else fallback
def _get_secret(key, default):
    try:
        return st.secrets[key]
    except Exception:
        return default

PIPEDRIVE_API_KEY = _get_secret("PIPEDRIVE_API_KEY", "d97cc4c5f3bcbb60cab34412845a95414a9f4350")
PIPEDRIVE_DOMAIN = _get_secret("PIPEDRIVE_DOMAIN", "klentysales")
PIPEDRIVE_FILTER_ID = _get_secret("PIPEDRIVE_FILTER_ID", "21551")
PIPEDRIVE_BASE_URL = f"https://{PIPEDRIVE_DOMAIN}.pipedrive.com/api/v1"
DEAL_SOURCE_KEY = "17a15b65151c6b068b70c1572b9c36f36538eb62"

# Pipeline classification
TRIAL_PIPELINES = {36, 1}
DEMO_PIPELINES = {2, 14}
AE_PIPELINE = 5
TRIAL_STAGES = {
    "In trial", "In Trial", "MQL Qualified", "MQL Activated",
    "MQL Trial Ended", "Qualified", "Activated", "Trial Completed",
}
DEMO_STAGES = {
    "Qualified demo requests", "No Show", "Meeting Scheduled",
    "Scheduling", "Requested",
}

# GA4 channel name mapping
CHANNEL_MAP = {
    "Organic Search": "organic_search",
    "Direct": "direct",
    "Paid Search": "paid_search",
    "Referral": "referral",
    "Organic Social": "organic_social",
}

# Country → Region mapping
NORTHAM_COUNTRIES = {"United States", "Canada"}
EUK_COUNTRIES = {
    "United Kingdom", "Germany", "France", "Netherlands", "Spain", "Italy",
    "Sweden", "Norway", "Denmark", "Finland", "Belgium", "Switzerland",
    "Austria", "Ireland", "Poland", "Portugal", "Czech Republic", "Romania",
    "Hungary", "Greece", "Bulgaria", "Croatia", "Slovakia", "Slovenia",
    "Lithuania", "Latvia", "Estonia", "Luxembourg", "Malta", "Cyprus",
    "Iceland", "Serbia", "Ukraine", "Turkey", "Russia", "Belarus",
    "Moldova", "Bosnia & Herzegovina", "North Macedonia", "Albania",
    "Montenegro", "Kosovo", "Liechtenstein", "Monaco", "Andorra",
    "San Marino", "Vatican City", "Czechia",
}
AMEA_COUNTRIES = {
    "India", "Australia", "Singapore", "New Zealand",
    "United Arab Emirates", "Saudi Arabia", "Qatar", "Bahrain", "Kuwait",
    "Oman", "Israel", "Japan", "South Korea", "Hong Kong", "Taiwan",
    "Thailand", "Malaysia", "Indonesia", "Philippines", "Vietnam",
    "Bangladesh", "Sri Lanka", "Pakistan", "Nepal", "Myanmar (Burma)",
    "Cambodia", "Laos",
    # China excluded — mostly bot traffic
}

# Countries to exclude globally (bot traffic)
EXCLUDED_COUNTRIES = {"China"}

REGION_COLORS = {
    "NORTHAM": "#4472C4",
    "AMEA": "#2E7D32",
    "EUK": "#E6A817",
    "LATAM": "#9C27B0",
}


def country_to_region(country):
    if not country:
        return "LATAM"
    if country in NORTHAM_COUNTRIES:
        return "NORTHAM"
    if country in EUK_COUNTRIES:
        return "EUK"
    if country in AMEA_COUNTRIES:
        return "AMEA"
    return "LATAM"


# ── Styling ─────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .block-container { padding-top: 2rem; padding-left: 3rem; padding-right: 3rem; max-width: 1700px; }
    section[data-testid="stSidebar"] { display: none; }
    .stTabs [data-baseweb="tab-list"] { gap: 0; }
    .stTabs [data-baseweb="tab"] {
        padding: 12px 24px;
        font-size: 13px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.3px;
    }
    /* Blue fetch button */
    button[kind="primary"] {
        background-color: #4472C4 !important;
        border-color: #4472C4 !important;
    }
    button[kind="primary"]:hover {
        background-color: #3a62a8 !important;
        border-color: #3a62a8 !important;
    }
</style>
""", unsafe_allow_html=True)


# ── Week Helpers ────────────────────────────────────────────────────────────

def compute_weeks(start_date, end_date):
    """Generate Mon-Sun week buckets split by month boundaries."""
    raw_weeks = []
    monday = start_date - timedelta(days=start_date.weekday())
    if monday < start_date:
        monday = start_date
    current = monday
    while current <= end_date:
        week_end = current + timedelta(days=(6 - current.weekday()))
        week_end = min(week_end, end_date)
        raw_weeks.append({"start": current, "end": week_end})
        current = week_end + timedelta(days=1)

    split_weeks = []
    for w in raw_weeks:
        s, e = w["start"], w["end"]
        if s.month == e.month:
            split_weeks.append({"start": s, "end": e, "month": s.strftime("%B")})
        else:
            if s.month == 12:
                month_end = date(s.year + 1, 1, 1) - timedelta(days=1)
            else:
                month_end = date(s.year, s.month + 1, 1) - timedelta(days=1)
            split_weeks.append({"start": s, "end": month_end, "month": s.strftime("%B")})
            split_weeks.append({"start": month_end + timedelta(days=1), "end": e, "month": e.strftime("%B")})

    pass1 = []
    for idx, w in enumerate(split_weeks):
        is_last = (idx == len(split_weeks) - 1)
        days = (w["end"] - w["start"]).days + 1
        if days == 1 and not is_last and pass1 and pass1[-1]["month"] == w["month"]:
            pass1[-1]["end"] = w["end"]
        else:
            pass1.append(w)

    weeks = []
    i = 0
    while i < len(pass1):
        w = pass1[i]
        days = (w["end"] - w["start"]).days + 1
        is_last = (i == len(pass1) - 1)
        if days == 1 and not is_last and i + 1 < len(pass1) and pass1[i + 1]["month"] == w["month"]:
            pass1[i + 1]["start"] = w["start"]
            i += 1
        else:
            weeks.append(w)
            i += 1

    for w in weeks:
        s, e = w["start"], w["end"]
        if s.month == e.month:
            w["label"] = f"{s.strftime('%b')} {s.day}-{e.day}"
        else:
            w["label"] = f"{s.strftime('%b')} {s.day}-{e.strftime('%b')} {e.day}"
    return weeks


def compute_standard_weeks(start_date, end_date):
    """Generate standard Mon-Sun weeks (no month splits) for charting."""
    weeks = []
    current = start_date - timedelta(days=start_date.weekday())
    if current < start_date:
        current = start_date
    while current <= end_date:
        week_end = current + timedelta(days=(6 - current.weekday()))
        week_end = min(week_end, end_date)
        s, e = current, week_end
        if s.month == e.month:
            label = f"{s.strftime('%b')} {s.day}-{e.day}"
        else:
            label = f"{s.strftime('%b')} {s.day}-{e.strftime('%b')} {e.day}"
        weeks.append({"label": label, "start": s, "end": e})
        current = week_end + timedelta(days=1)
    return weeks


def date_to_week_idx(d, weeks):
    for i, w in enumerate(weeks):
        if w["start"] <= d <= w["end"]:
            return i
    return None


def parse_ga4_date(date_str):
    return datetime.strptime(date_str, "%Y%m%d").date()


# ── GA4 Fetching ────────────────────────────────────────────────────────────

def fetch_ga4_data(start_str, end_str):
    """Fetch all GA4 data. Returns dict with result sets."""
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Dimension, Metric,
    )

    # Support both local creds file and Streamlit Cloud secrets
    if "ga4_credentials" in st.secrets:
        from google.oauth2 import service_account
        creds_info = dict(st.secrets["ga4_credentials"])
        credentials = service_account.Credentials.from_service_account_info(creds_info)
        client = BetaAnalyticsDataClient(credentials=credentials)
    else:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GA4_CREDS_FILE
        client = BetaAnalyticsDataClient()
    dr = [DateRange(start_date=start_str, end_date=end_str)]

    results = {}

    # Query 1: Total users by date
    r1 = client.run_report(RunReportRequest(
        property=GA4_PROPERTY_ID, date_ranges=dr,
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="totalUsers")],
        limit=50000,
    ))
    results["total_users"] = [
        {"date": row.dimension_values[0].value, "users": int(row.metric_values[0].value)}
        for row in r1.rows
    ]

    # Query 2: Users by date + channel group
    r2 = client.run_report(RunReportRequest(
        property=GA4_PROPERTY_ID, date_ranges=dr,
        dimensions=[Dimension(name="date"), Dimension(name="sessionDefaultChannelGroup")],
        metrics=[Metric(name="totalUsers")],
        limit=50000,
    ))
    results["channels"] = [
        {"date": row.dimension_values[0].value, "channel": row.dimension_values[1].value,
         "users": int(row.metric_values[0].value)}
        for row in r2.rows
    ]

    # Query 3: Users by date + source + medium (for LinkedIn)
    r3 = client.run_report(RunReportRequest(
        property=GA4_PROPERTY_ID, date_ranges=dr,
        dimensions=[Dimension(name="date"), Dimension(name="sessionSource"), Dimension(name="sessionMedium")],
        metrics=[Metric(name="totalUsers")],
        limit=50000,
    ))
    results["sources"] = [
        {"date": row.dimension_values[0].value, "source": row.dimension_values[1].value,
         "medium": row.dimension_values[2].value, "users": int(row.metric_values[0].value)}
        for row in r3.rows
    ]

    # Query 4: Demo/signup clicks by date + city + region
    r4 = client.run_report(RunReportRequest(
        property=GA4_PROPERTY_ID, date_ranges=dr,
        dimensions=[Dimension(name="date"), Dimension(name="city"), Dimension(name="region")],
        metrics=[Metric(name="keyEvents:demo_button_clicks"), Metric(name="keyEvents:signup_button_clicks")],
        limit=50000,
    ))
    results["clicks"] = [
        {"date": row.dimension_values[0].value, "city": row.dimension_values[1].value,
         "region": row.dimension_values[2].value,
         "demo_clicks": int(row.metric_values[0].value), "signup_clicks": int(row.metric_values[1].value)}
        for row in r4.rows
    ]

    # Query 5: Users by date + country (for regional breakdown)
    r5 = client.run_report(RunReportRequest(
        property=GA4_PROPERTY_ID, date_ranges=dr,
        dimensions=[Dimension(name="date"), Dimension(name="country")],
        metrics=[Metric(name="totalUsers")],
        limit=50000,
    ))
    results["country_users"] = [
        {"date": row.dimension_values[0].value, "country": row.dimension_values[1].value,
         "users": int(row.metric_values[0].value)}
        for row in r5.rows
    ]

    # Query 6: Demo/signup clicks by date + country + city + region (for regional clicks)
    r6 = client.run_report(RunReportRequest(
        property=GA4_PROPERTY_ID, date_ranges=dr,
        dimensions=[Dimension(name="date"), Dimension(name="country"), Dimension(name="city"), Dimension(name="region")],
        metrics=[Metric(name="keyEvents:demo_button_clicks"), Metric(name="keyEvents:signup_button_clicks")],
        limit=100000,
    ))
    results["country_clicks"] = [
        {"date": row.dimension_values[0].value, "country": row.dimension_values[1].value,
         "city": row.dimension_values[2].value, "region": row.dimension_values[3].value,
         "demo_clicks": int(row.metric_values[0].value), "signup_clicks": int(row.metric_values[1].value)}
        for row in r6.rows
    ]

    # Query 7: Users by date + country + channel (for regional channel breakdown)
    r7 = client.run_report(RunReportRequest(
        property=GA4_PROPERTY_ID, date_ranges=dr,
        dimensions=[Dimension(name="date"), Dimension(name="country"), Dimension(name="sessionDefaultChannelGroup")],
        metrics=[Metric(name="totalUsers")],
        limit=100000,
    ))
    results["country_channels"] = [
        {"date": row.dimension_values[0].value, "country": row.dimension_values[1].value,
         "channel": row.dimension_values[2].value, "users": int(row.metric_values[0].value)}
        for row in r7.rows
    ]

    # Query 8: Users by date + country + source + medium (for regional LinkedIn)
    r8 = client.run_report(RunReportRequest(
        property=GA4_PROPERTY_ID, date_ranges=dr,
        dimensions=[Dimension(name="date"), Dimension(name="country"),
                    Dimension(name="sessionSource"), Dimension(name="sessionMedium")],
        metrics=[Metric(name="totalUsers")],
        limit=100000,
    ))
    results["country_sources"] = [
        {"date": row.dimension_values[0].value, "country": row.dimension_values[1].value,
         "source": row.dimension_values[2].value, "medium": row.dimension_values[3].value,
         "users": int(row.metric_values[0].value)}
        for row in r8.rows
    ]

    return results


# ── Pipedrive Fetching ──────────────────────────────────────────────────────

def pd_api_get(endpoint, params=None):
    url = f"{PIPEDRIVE_BASE_URL}/{endpoint}"
    p = {"api_token": PIPEDRIVE_API_KEY}
    if params:
        p.update(params)
    resp = requests.get(url, params=p)
    resp.raise_for_status()
    return resp.json()


def fetch_pipedrive_data():
    """Fetch and process Pipedrive deals. Returns list of classified deals with region."""
    all_fields = []
    start = 0
    while True:
        data = pd_api_get("dealFields", {"start": start, "limit": 500})
        if data.get("data"):
            all_fields.extend(data["data"])
        p = data.get("additional_data", {}).get("pagination", {})
        if p.get("more_items_in_collection"):
            start = p["next_start"]
        else:
            break

    field_map = {}
    option_map = {}
    for f in all_fields:
        field_map[f["key"]] = f["name"]
        if f.get("options"):
            option_map[f["key"]] = {str(o["id"]): o["label"] for o in f["options"]}

    # Find key fields
    def find_key(search):
        for k, n in field_map.items():
            if search.lower() == n.lower():
                return k
        for k, n in field_map.items():
            if search.lower() in n.lower():
                return k
        return None

    mql_key = find_key("MQL")
    region_key = find_key("Region")

    all_deals = []
    start = 0
    while True:
        data = pd_api_get("deals", {"filter_id": PIPEDRIVE_FILTER_ID, "start": start, "limit": 500})
        if data.get("data"):
            all_deals.extend(data["data"])
        else:
            break
        p = data.get("additional_data", {}).get("pagination", {})
        if p.get("more_items_in_collection"):
            start = p["next_start"]
        else:
            break

    stage_history = {}
    history_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "deal_stage_history.json")
    if os.path.exists(history_file):
        with open(history_file) as f:
            stage_history = json.load(f)

    def resolve(fk, rv):
        if rv is None or rv == "":
            return None
        if fk in option_map:
            raw_str = str(rv)
            if "," in raw_str:
                ids = [v.strip() for v in raw_str.split(",")]
                return ", ".join([option_map[fk].get(i, i) for i in ids])
            return option_map[fk].get(raw_str, raw_str)
        return rv

    def classify(pid, did):
        if pid in TRIAL_PIPELINES:
            return "Free Trial"
        if pid in DEMO_PIPELINES:
            return "Demo"
        if pid in (AE_PIPELINE, 14):
            dh = stage_history.get(str(did), {})
            ps = set(dh.get("stages", []))
            ft = bool(ps & TRIAL_STAGES)
            fd = bool(ps & DEMO_STAGES)
            if ft and not fd:
                return "Free Trial"
            return "Demo"
        return "Demo"

    processed = []
    for deal in all_deals:
        add_time = deal.get("add_time", "")
        add_date = None
        if add_time:
            try:
                add_date = datetime.strptime(add_time[:10], "%Y-%m-%d").date()
            except ValueError:
                pass
        if not add_date or add_date < DATA_START:
            continue

        mv = resolve(mql_key, deal.get(mql_key)) if mql_key else None
        ds = resolve(DEAL_SOURCE_KEY, deal.get(DEAL_SOURCE_KEY))
        region_val = resolve(region_key, deal.get(region_key)) if region_key else None
        is_icp = str(mv).strip().lower() == "yes" if mv else False
        is_outbound = "outbound" in str(ds).strip().lower() if ds else False
        lead_type = classify(deal.get("pipeline_id"), deal.get("id"))

        if is_outbound:
            continue

        processed.append({
            "add_date": add_date,
            "lead_type": lead_type,
            "is_icp": is_icp,
            "region": str(region_val or "").strip().upper() if region_val else "LATAM",
        })

    return processed


# ── High-Intent Pages Config ────────────────────────────────────────────────

HIGH_INTENT_PAGES = [
    # (path, category, type)
    ("/blog/what-is-a-parallel-dialer/", "Dialer", "Blog Post"),
    ("/blog/sales-engagement-platform-tools/", "SEP", "Blog Post"),
    ("/blog/parallel-dialer-software/", "Dialer", "Blog Post"),
    ("/feature/cadence-playbooks", "SEP", "Feature Page"),
    ("/feature/agentic-cadences", "SEP", "Feature Page"),
    ("/blog/how-many-cold-calls-to-make-per-day/", "Dialer", "Blog Post"),
    ("/feature/multi-channel-outreach", "SEP", "Feature Page"),
    ("/feature/account-research/", "SEP", "Feature Page"),
    ("/compare/outreach", "SEP", "Compare Page"),
    ("/dial-iq/feature/power-dialer", "Dialer", "Feature Page"),
    ("/compare/salesloft", "SEP", "Compare Page"),
    ("/blog/what-is-a-power-dialer/", "Dialer", "Blog Post"),
    ("/blog/power-dialer-software-tools/", "Dialer", "Blog Post"),
    ("/email-sequence-software-tools/", "SEP", "Landing Page"),
    ("/feature/account-based-selling", "SEP", "Feature Page"),
    ("/feature/agentic-cadences/", "SEP", "Feature Page"),
    ("/feature/account-research", "SEP", "Feature Page"),
    ("/dial-iq/feature/voicemail-drop", "Dialer", "Feature Page"),
    ("/feature/click-to-call-dialer", "Dialer", "Feature Page"),
    ("/blog/cold-calling-software/", "Dialer", "Blog Post"),
    ("/feature/india-dialer", "Dialer", "Feature Page"),
    ("/feature/india-dialer/", "Dialer", "Feature Page"),
    ("/feature/sales-dialer", "Dialer", "Feature Page"),
    ("/feature/click-to-call-dialer/", "Dialer", "Feature Page"),
    ("/dial-iq/feature/voicemail-detection", "Dialer", "Feature Page"),
    ("/", "SEP", "Homepage"),
    ("/sales-engagement-platform/pricing", "SEP", "Pricing"),
    ("/sales-engagement-platform/pricing/", "SEP", "Pricing"),
    ("/dial-iq/pricing/", "Dialer", "Pricing"),
    ("/dial-iq/pricing", "Dialer", "Pricing"),
    ("/dial-iq/", "Dialer", "Feature Page"),
    ("/dial-iq", "Dialer", "Feature Page"),
    ("/dial-iq/feature/parallel-dialer", "Dialer", "Feature Page"),
    ("/dial-iq/feature/parallel-dialer/", "Dialer", "Feature Page"),
    # New pages
    ("/blog/nooks-alternatives/", "Dialer", "Blog Post"),
    ("/blog/orum-alternatives/", "Dialer", "Blog Post"),
    ("/dial-iq/feature/connect-ai/", "Dialer", "Feature Page"),
    ("/blog/connect-ai/", "Dialer", "Blog Post"),
    ("/dial-iq/feature/ai-call-coaching/", "Dialer", "Feature Page"),
    ("/dial-iq/feature/ai-call-coaching-suite/", "Dialer", "Feature Page"),
    ("/blog/introducing-ai-call-coaching-suite/", "Dialer", "Blog Post"),
]

# Deduplicate — keep first occurrence of each path (strip trailing slash for matching)
_seen_pages = set()
HIGH_INTENT_PAGES_DEDUP = []
for path, cat, ptype in HIGH_INTENT_PAGES:
    norm = path.rstrip("/") if path != "/" else path
    if norm not in _seen_pages:
        _seen_pages.add(norm)
        HIGH_INTENT_PAGES_DEDUP.append((path, cat, ptype))

HIGH_INTENT_PAGE_SET = {p.rstrip("/") if p != "/" else p for p, _, _ in HIGH_INTENT_PAGES}


def _get_ga4_client():
    """Get GA4 client (reusable helper)."""
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    try:
        if "ga4_credentials" in st.secrets:
            from google.oauth2 import service_account
            creds_info = dict(st.secrets["ga4_credentials"])
            credentials = service_account.Credentials.from_service_account_info(creds_info)
            return BetaAnalyticsDataClient(credentials=credentials)
    except Exception:
        pass
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GA4_CREDS_FILE
    return BetaAnalyticsDataClient()


def _merge_page_rows(rows):
    """Merge rows with same normalized path (e.g. /pricing and /pricing/)."""
    merged = {}
    for row in rows:
        norm = row["page_norm"]
        if norm in merged:
            m = merged[norm]
            total_sessions = m["sessions"] + row["sessions"]
            if total_sessions > 0:
                m["bounce_rate"] = (m["bounce_rate"] * m["sessions"] + row["bounce_rate"] * row["sessions"]) / total_sessions
                m["avg_engagement"] = (m["avg_engagement"] * m["sessions"] + row["avg_engagement"] * row["sessions"]) / total_sessions
            m["users"] += row["users"]
            m["sessions"] += row["sessions"]
            m["demo_clicks"] += row["demo_clicks"]
            m["demo_completions"] += row["demo_completions"]
            m["signup_clicks"] += row["signup_clicks"]
            m["signup_completions"] += row["signup_completions"]
        else:
            merged[norm] = dict(row)
    return list(merged.values())


def fetch_ga4_page_data(start_str, end_str):
    """Fetch page-level GA4 data for high-intent pages (overall)."""
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Dimension, Metric,
    )
    client = _get_ga4_client()
    dr = [DateRange(start_date=start_str, end_date=end_str)]

    r = client.run_report(RunReportRequest(
        property=GA4_PROPERTY_ID, date_ranges=dr,
        dimensions=[Dimension(name="pagePath")],
        metrics=[
            Metric(name="totalUsers"),
            Metric(name="sessions"),
            Metric(name="bounceRate"),
            Metric(name="averageSessionDuration"),
            Metric(name="keyEvents:demo_button_clicks"),
            Metric(name="keyEvents:demo_form"),
            Metric(name="keyEvents:signup_button_clicks"),
            Metric(name="keyEvents:signup_success_new"),
        ],
        limit=50000,
    ))

    rows = []
    for row in r.rows:
        path = row.dimension_values[0].value
        norm = path.rstrip("/") if path != "/" else path
        if norm in HIGH_INTENT_PAGE_SET:
            rows.append({
                "page": path, "page_norm": norm,
                "users": int(row.metric_values[0].value),
                "sessions": int(row.metric_values[1].value),
                "bounce_rate": float(row.metric_values[2].value),
                "avg_engagement": float(row.metric_values[3].value),
                "demo_clicks": int(row.metric_values[4].value),
                "demo_completions": int(row.metric_values[5].value),
                "signup_clicks": int(row.metric_values[6].value),
                "signup_completions": int(row.metric_values[7].value),
            })

    return _merge_page_rows(rows)


def fetch_ga4_page_data_by_country(start_str, end_str):
    """Fetch page-level GA4 data with country dimension for regional breakdown."""
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Dimension, Metric,
    )
    client = _get_ga4_client()
    dr = [DateRange(start_date=start_str, end_date=end_str)]

    r = client.run_report(RunReportRequest(
        property=GA4_PROPERTY_ID, date_ranges=dr,
        dimensions=[Dimension(name="pagePath"), Dimension(name="country")],
        metrics=[
            Metric(name="totalUsers"),
            Metric(name="sessions"),
            Metric(name="bounceRate"),
            Metric(name="averageSessionDuration"),
            Metric(name="keyEvents:demo_button_clicks"),
            Metric(name="keyEvents:demo_form"),
            Metric(name="keyEvents:signup_button_clicks"),
            Metric(name="keyEvents:signup_success_new"),
        ],
        limit=100000,
    ))

    rows = []
    for row in r.rows:
        path = row.dimension_values[0].value
        country = row.dimension_values[1].value
        norm = path.rstrip("/") if path != "/" else path
        if norm in HIGH_INTENT_PAGE_SET:
            rows.append({
                "page": path, "page_norm": norm, "country": country,
                "users": int(row.metric_values[0].value),
                "sessions": int(row.metric_values[1].value),
                "bounce_rate": float(row.metric_values[2].value),
                "avg_engagement": float(row.metric_values[3].value),
                "demo_clicks": int(row.metric_values[4].value),
                "demo_completions": int(row.metric_values[5].value),
                "signup_clicks": int(row.metric_values[6].value),
                "signup_completions": int(row.metric_values[7].value),
            })
    return rows


def filter_page_data_by_region(rows, region_name):
    """Filter page-level country rows to a specific region, then merge by page."""
    if region_name == "NORTHAM":
        match = lambda c: c in NORTHAM_COUNTRIES
    elif region_name == "EUK":
        match = lambda c: c in EUK_COUNTRIES
    elif region_name == "AMEA":
        match = lambda c: c in AMEA_COUNTRIES and c not in EXCLUDED_COUNTRIES
    else:  # LATAM
        match = lambda c: c not in NORTHAM_COUNTRIES and c not in EUK_COUNTRIES and c not in AMEA_COUNTRIES and c not in EXCLUDED_COUNTRIES

    filtered = [r for r in rows if match(r.get("country", ""))]
    return _merge_page_rows(filtered)


# ── LinkedIn Helper ─────────────────────────────────────────────────────────

def is_linkedin_source(source, medium):
    src = source.lower()
    med = medium.lower()
    return "linkedin" in src or "linkedin" in med or "lnkd" in src or "lnkd" in med


# ── Data Aggregation ────────────────────────────────────────────────────────

def _empty_week_row():
    return {
        "total_users": 0,
        "organic_search": 0, "direct": 0, "paid_search": 0,
        "referral": 0, "organic_social": 0, "linkedin": 0,
        "demo_clicks": 0, "demo_leads": 0,
        "signup_clicks": 0, "trial_leads": 0,
        "icp_demo": 0, "icp_trial": 0,
        "total_leads": 0, "conv_pct": 0.0,
    }


def aggregate_weekly(weeks, ga4_data, pipedrive_deals):
    """Aggregate all data sources into weekly rows (overall, excluding bot countries)."""
    n = len(weeks)
    weekly = [_empty_week_row() for _ in range(n)]

    # Use country-level data to exclude bot countries
    for row in ga4_data.get("country_users", []):
        if row["country"] in EXCLUDED_COUNTRIES:
            continue
        d = parse_ga4_date(row["date"])
        idx = date_to_week_idx(d, weeks)
        if idx is not None:
            weekly[idx]["total_users"] += row["users"]

    for row in ga4_data.get("country_channels", []):
        if row["country"] in EXCLUDED_COUNTRIES:
            continue
        d = parse_ga4_date(row["date"])
        idx = date_to_week_idx(d, weeks)
        if idx is not None:
            col = CHANNEL_MAP.get(row["channel"])
            if col and col in weekly[idx]:
                weekly[idx][col] += row["users"]

    for row in ga4_data.get("sources", []):
        if is_linkedin_source(row["source"], row["medium"]):
            d = parse_ga4_date(row["date"])
            idx = date_to_week_idx(d, weeks)
            if idx is not None:
                weekly[idx]["linkedin"] += row["users"]

    for row in ga4_data.get("country_clicks", []):
        if row["country"] in EXCLUDED_COUNTRIES:
            continue
        if row["city"].lower() == "chennai" or "tamil nadu" in row["region"].lower():
            continue
        d = parse_ga4_date(row["date"])
        idx = date_to_week_idx(d, weeks)
        if idx is not None:
            weekly[idx]["demo_clicks"] += row["demo_clicks"]
            weekly[idx]["signup_clicks"] += row["signup_clicks"]

    for deal in pipedrive_deals:
        idx = date_to_week_idx(deal["add_date"], weeks)
        if idx is not None:
            if deal["lead_type"] == "Demo":
                weekly[idx]["demo_leads"] += 1
                if deal["is_icp"]:
                    weekly[idx]["icp_demo"] += 1
            else:
                weekly[idx]["trial_leads"] += 1
                if deal["is_icp"]:
                    weekly[idx]["icp_trial"] += 1

    for w in weekly:
        w["total_leads"] = w["demo_leads"] + w["trial_leads"]
        if w["total_users"] > 0:
            w["conv_pct"] = round((w["total_leads"] / w["total_users"]) * 100, 2)

    return weekly


def aggregate_weekly_region(weeks, ga4_data, pipedrive_deals, region_name):
    """Aggregate weekly data for a specific region."""
    n = len(weeks)
    weekly = [_empty_week_row() for _ in range(n)]

    region_countries = set()
    if region_name == "NORTHAM":
        region_countries = NORTHAM_COUNTRIES
    elif region_name == "EUK":
        region_countries = EUK_COUNTRIES
    elif region_name == "AMEA":
        region_countries = AMEA_COUNTRIES
    # LATAM = everything else

    def is_in_region(country):
        if country in EXCLUDED_COUNTRIES:
            return False
        if region_name == "LATAM":
            return country not in NORTHAM_COUNTRIES and country not in EUK_COUNTRIES and country not in AMEA_COUNTRIES and country not in EXCLUDED_COUNTRIES
        return country in region_countries

    # Total users by country
    for row in ga4_data.get("country_users", []):
        if is_in_region(row["country"]):
            d = parse_ga4_date(row["date"])
            idx = date_to_week_idx(d, weeks)
            if idx is not None:
                weekly[idx]["total_users"] += row["users"]

    # Channel breakdown by country
    for row in ga4_data.get("country_channels", []):
        if is_in_region(row["country"]):
            d = parse_ga4_date(row["date"])
            idx = date_to_week_idx(d, weeks)
            if idx is not None:
                col = CHANNEL_MAP.get(row["channel"])
                if col and col in weekly[idx]:
                    weekly[idx][col] += row["users"]

    # LinkedIn by country (from source/medium)
    for row in ga4_data.get("country_sources", []):
        if is_in_region(row["country"]) and is_linkedin_source(row["source"], row["medium"]):
            d = parse_ga4_date(row["date"])
            idx = date_to_week_idx(d, weeks)
            if idx is not None:
                weekly[idx]["linkedin"] += row["users"]

    # Clicks by country (exclude Chennai/TN)
    for row in ga4_data.get("country_clicks", []):
        if not is_in_region(row["country"]):
            continue
        if row["city"].lower() == "chennai" or "tamil nadu" in row["region"].lower():
            continue
        d = parse_ga4_date(row["date"])
        idx = date_to_week_idx(d, weeks)
        if idx is not None:
            weekly[idx]["demo_clicks"] += row["demo_clicks"]
            weekly[idx]["signup_clicks"] += row["signup_clicks"]

    # Pipedrive deals by region
    for deal in pipedrive_deals:
        if deal["region"] != region_name:
            continue
        idx = date_to_week_idx(deal["add_date"], weeks)
        if idx is not None:
            if deal["lead_type"] == "Demo":
                weekly[idx]["demo_leads"] += 1
                if deal["is_icp"]:
                    weekly[idx]["icp_demo"] += 1
            else:
                weekly[idx]["trial_leads"] += 1
                if deal["is_icp"]:
                    weekly[idx]["icp_trial"] += 1

    for w in weekly:
        w["total_leads"] = w["demo_leads"] + w["trial_leads"]
        if w["total_users"] > 0:
            w["conv_pct"] = round((w["total_leads"] / w["total_users"]) * 100, 2)

    return weekly


def aggregate_chart_data(chart_weeks, ga4_data, pipedrive_deals, region=None):
    """Aggregate data into standard Mon-Sun weeks for charting. Optionally filter by region."""
    n = len(chart_weeks)
    data = [{"total_users": 0, "demo_leads": 0, "trial_leads": 0, "total_leads": 0} for _ in range(n)]

    if region is None:
        # Overall (exclude bot countries)
        for row in ga4_data.get("country_users", []):
            if row["country"] in EXCLUDED_COUNTRIES:
                continue
            d = parse_ga4_date(row["date"])
            for i, w in enumerate(chart_weeks):
                if w["start"] <= d <= w["end"]:
                    data[i]["total_users"] += row["users"]
                    break
    else:
        # Regional
        if region == "LATAM":
            def match(c): return c not in NORTHAM_COUNTRIES and c not in EUK_COUNTRIES and c not in AMEA_COUNTRIES and c not in EXCLUDED_COUNTRIES
        elif region == "NORTHAM":
            def match(c): return c in NORTHAM_COUNTRIES
        elif region == "EUK":
            def match(c): return c in EUK_COUNTRIES
        else:
            def match(c): return c in AMEA_COUNTRIES

        for row in ga4_data.get("country_users", []):
            if match(row["country"]):
                d = parse_ga4_date(row["date"])
                for i, w in enumerate(chart_weeks):
                    if w["start"] <= d <= w["end"]:
                        data[i]["total_users"] += row["users"]
                        break

    # Pipedrive
    for deal in pipedrive_deals:
        if region is not None and deal["region"] != region:
            continue
        for i, w in enumerate(chart_weeks):
            if w["start"] <= deal["add_date"] <= w["end"]:
                if deal["lead_type"] == "Demo":
                    data[i]["demo_leads"] += 1
                else:
                    data[i]["trial_leads"] += 1
                break

    for d in data:
        d["total_leads"] = d["demo_leads"] + d["trial_leads"]

    return data


# ── WoW Calculation ─────────────────────────────────────────────────────────

def compute_wow(weekly, cols):
    wow = []
    for i in range(len(weekly)):
        row = {}
        for col in cols:
            if i == 0:
                row[col] = ""
            else:
                prev = weekly[i - 1][col]
                curr = weekly[i][col]
                if prev == 0 and curr == 0:
                    row[col] = ""
                elif prev == 0:
                    row[col] = ("up", "NEW")
                else:
                    pct = ((curr - prev) / prev) * 100
                    if pct > 0:
                        row[col] = ("up", f"+{pct:.0f}%")
                    elif pct < 0:
                        row[col] = ("down", f"{pct:.0f}%")
                    else:
                        row[col] = ""
        wow.append(row)
    return wow


# ── Chart ───────────────────────────────────────────────────────────────────

def render_line_chart(chart_weeks, chart_data, title="Weekly Trend — Traffic & Leads"):
    labels = [w["label"] for w in chart_weeks]

    users = [d["total_users"] for d in chart_data]
    demos = [d["demo_leads"] for d in chart_data]
    trials = [d["trial_leads"] for d in chart_data]
    totals = [d["total_leads"] for d in chart_data]

    max_leads = max(max(totals, default=0), 1)
    right_axis_max = max(max_leads * 3, 10)

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=labels, y=users, name="Total Users",
        line=dict(color="#4472C4", width=3),
        mode="lines+markers", marker=dict(size=5),
        fill="tozeroy", fillcolor="rgba(68, 114, 196, 0.06)",
        yaxis="y",
    ))
    fig.add_trace(go.Scatter(
        x=labels, y=totals, name="Total Leads",
        line=dict(color="#DC2626", width=2.5, dash="dot"),
        mode="lines+markers+text",
        text=[str(v) for v in totals], textposition="top center",
        textfont=dict(size=9, color="#DC2626"),
        marker=dict(size=7, symbol="circle"),
        yaxis="y2",
    ))
    fig.add_trace(go.Scatter(
        x=labels, y=demos, name="Demo Leads",
        line=dict(color="#2E7D32", width=2),
        mode="lines+markers+text",
        text=[str(v) for v in demos], textposition="top center",
        textfont=dict(size=8, color="#2E7D32"),
        marker=dict(size=5, symbol="diamond"),
        yaxis="y2",
    ))
    fig.add_trace(go.Scatter(
        x=labels, y=trials, name="Trial Leads",
        line=dict(color="#E6A817", width=2),
        mode="lines+markers+text",
        text=[str(v) for v in trials], textposition="bottom center",
        textfont=dict(size=8, color="#E6A817"),
        marker=dict(size=5, symbol="square"),
        yaxis="y2",
    ))

    fig.update_layout(
        title=dict(text=title, font=dict(size=16)),
        template="plotly_white", height=450,
        margin=dict(t=60, b=50, l=60, r=60),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        yaxis=dict(title="Total Users", side="left", showgrid=True, gridcolor="#f0f0f0", rangemode="tozero"),
        yaxis2=dict(title="Leads", side="right", overlaying="y", showgrid=False, rangemode="tozero", range=[0, right_axis_max]),
        xaxis=dict(tickangle=-45),
    )
    return fig


# ── HTML Table ──────────────────────────────────────────────────────────────

TABLE_COLS = [
    ("total_users", "Total Users"),
    ("organic_search", "Organic Search"),
    ("direct", "Direct"),
    ("linkedin", "LinkedIn"),
    ("paid_search", "Paid Search"),
    ("referral", "Referral"),
    ("organic_social", "Organic Social"),
    ("demo_clicks", "Demo Clicks"),
    ("demo_leads", "Demo Leads"),
    ("signup_clicks", "Signup Clicks"),
    ("trial_leads", "Trial Leads"),
    ("icp_demo", "ICP Demo"),
    ("icp_trial", "ICP Trial"),
    ("total_leads", "Total Leads"),
    ("conv_pct", "Conv %"),
]

REGION_TABLE_COLS = [
    ("total_users", "Total Users"),
    ("organic_search", "Organic Search"),
    ("direct", "Direct"),
    ("linkedin", "LinkedIn"),
    ("paid_search", "Paid Search"),
    ("referral", "Referral"),
    ("organic_social", "Organic Social"),
    ("demo_clicks", "Demo Clicks"),
    ("demo_leads", "Demo Leads"),
    ("signup_clicks", "Signup Clicks"),
    ("trial_leads", "Trial Leads"),
    ("icp_demo", "ICP Demo"),
    ("icp_trial", "ICP Trial"),
    ("total_leads", "Total Leads"),
    ("conv_pct", "Conv %"),
]


def wow_html(wow_val):
    if not wow_val:
        return ""
    direction, text = wow_val
    if direction == "up":
        return f'<div style="color:#2E7D32;font-size:10px;line-height:1;">▲ {text}</div>'
    else:
        return f'<div style="color:#DC2626;font-size:10px;line-height:1;">▼ {text}</div>'


def render_html_table(weeks, weekly, wow, cols=None):
    if cols is None:
        cols = TABLE_COLS
    th = 'style="background:#4472C4;color:white;padding:8px 10px;text-align:center;font-size:12px;font-weight:600;border:1px solid #3a62a8;white-space:nowrap;"'
    td_base = 'style="padding:6px 10px;border:1px solid #D9E2F3;text-align:center;font-size:12px;vertical-align:top;"'
    td_week = 'style="padding:6px 10px;border:1px solid #D9E2F3;text-align:left;font-size:12px;font-weight:600;white-space:nowrap;background:#f8f9fb;"'

    html = '<div style="overflow-x:auto;max-height:80vh;position:relative;"><table style="border-collapse:collapse;width:100%;font-family:sans-serif;">'
    html += f'<thead style="position:sticky;top:0;z-index:10;"><tr>'
    html += f'<th {th}>Week</th>'
    for _, label in cols:
        html += f'<th {th}>{label}</th>'
    html += "</tr></thead><tbody>"

    current_month = None
    month_totals = defaultdict(int)

    for i, (week, data, wow_row) in enumerate(zip(weeks, weekly, wow)):
        week_month = week.get("month", "")
        if week_month != current_month and current_month is not None:
            html += _month_total_row(current_month, month_totals, cols)
            month_totals = defaultdict(int)
        if week_month != current_month:
            current_month = week_month

        for col, _ in cols:
            if col != "conv_pct":
                month_totals[col] += data.get(col, 0)

        bg = "#ffffff" if i % 2 == 0 else "#f8f9fb"
        html += f'<tr style="background:{bg};"><td {td_week}>{week["label"]}</td>'
        for col, _ in cols:
            val = data.get(col, 0)
            wow_cell = wow_html(wow_row.get(col, ""))
            if col == "conv_pct":
                html += f'<td {td_base}><div style="font-weight:600;">{val:.2f}%</div>{wow_cell}</td>'
            else:
                html += f'<td {td_base}><div style="font-weight:600;">{val:,}</div>{wow_cell}</td>'
        html += "</tr>"

    if current_month:
        html += _month_total_row(current_month, month_totals, cols)

    html += "</tbody></table></div>"
    return html


def _month_total_row(month_name, totals, cols=None):
    if cols is None:
        cols = TABLE_COLS
    td_total = 'style="padding:8px 10px;border:1px solid #3a62a8;text-align:center;font-size:12px;font-weight:700;background:#D6DCE4;border-top:2px solid #4472C4;"'
    td_label = 'style="padding:8px 10px;border:1px solid #3a62a8;text-align:left;font-size:12px;font-weight:700;background:#D6DCE4;border-top:2px solid #4472C4;white-space:nowrap;"'

    total_users = totals.get("total_users", 0)
    total_leads = totals.get("total_leads", 0)
    month_conv = round((total_leads / total_users) * 100, 2) if total_users > 0 else 0.0

    html = f"<tr><td {td_label}>{month_name} Total</td>"
    for col, _ in cols:
        if col == "conv_pct":
            html += f'<td {td_total}>{month_conv:.2f}%</td>'
        else:
            html += f'<td {td_total}>{totals.get(col, 0):,}</td>'
    html += "</tr>"
    return html


# ── Render a tab (chart + table) ───────────────────────────────────────────

def render_tab(weeks, weekly, chart_weeks, chart_data, title, cols=None, caption=""):
    """Render a complete tab with line chart + weekly table."""
    fig = render_line_chart(chart_weeks, chart_data, title=title)
    st.plotly_chart(fig, use_container_width=True)
    st.markdown("---")
    st.markdown(f"### Weekly Breakdown — {title.split('—')[-1].strip() if '—' in title else title}")
    if caption:
        st.caption(caption)
    wow_cols = [col for col, _ in (cols or TABLE_COLS)]
    wow = compute_wow(weekly, wow_cols)
    table_html = render_html_table(weeks, weekly, wow, cols=cols)
    st.markdown(table_html, unsafe_allow_html=True)


# ── Page Performance Tab ────────────────────────────────────────────────────

def _build_page_table_html(curr_data, prev_data, page_info):
    """Build HTML table for page performance data."""
    curr_map = {r["page_norm"]: r for r in curr_data}
    prev_map = {r["page_norm"]: r for r in prev_data}

    EMPTY_ROW = {"users": 0, "sessions": 0, "bounce_rate": 0, "avg_engagement": 0,
                 "demo_clicks": 0, "demo_completions": 0, "signup_clicks": 0, "signup_completions": 0}

    PAGE_METRICS = [
        ("users", "Users", "int"),
        ("sessions", "Sessions", "int"),
        ("bounce_rate", "Bounce Rate", "pct"),
        ("avg_engagement", "Avg Engage", "time"),
        ("demo_clicks", "Demo Clicks", "int"),
        ("demo_completions", "Demo Comp.", "int"),
        ("demo_rate", "Demo Rate", "pct"),
        ("signup_clicks", "Signup Clicks", "int"),
        ("signup_completions", "Signup Comp.", "int"),
        ("signup_rate", "Signup Rate", "pct"),
    ]

    th = 'style="background:#4472C4;color:white;padding:8px 10px;text-align:center;font-size:11px;font-weight:600;border:1px solid #3a62a8;white-space:nowrap;position:sticky;top:0;z-index:10;"'
    td = 'style="padding:6px 8px;border:1px solid #D9E2F3;text-align:center;font-size:11px;vertical-align:top;"'
    td_page = 'style="padding:6px 8px;border:1px solid #D9E2F3;text-align:left;font-size:11px;font-weight:500;white-space:nowrap;max-width:300px;overflow:hidden;text-overflow:ellipsis;"'
    td_cat = 'style="padding:6px 8px;border:1px solid #D9E2F3;text-align:center;font-size:10px;"'

    html = '<div style="overflow-x:auto;max-height:75vh;"><table style="border-collapse:collapse;width:100%;font-family:sans-serif;">'
    html += '<thead><tr>'
    html += f'<th {th}>Page Path</th><th {th}>Category</th><th {th}>Type</th>'
    for _, label, _ in PAGE_METRICS:
        html += f'<th {th}>{label}</th>'
    html += '</tr></thead><tbody>'

    all_norms = list(page_info.keys())
    all_norms.sort(key=lambda n: curr_map.get(n, {}).get("users", 0), reverse=True)

    for norm in all_norms:
        info = page_info[norm]
        curr = {**EMPTY_ROW, **curr_map.get(norm, {})}
        prev = {**EMPTY_ROW, **prev_map.get(norm, {})}

        # Demo rate = completions / sessions, Signup rate = completions / sessions
        curr["demo_rate"] = (curr["demo_completions"] / curr["sessions"] * 100) if curr["sessions"] > 0 else 0
        curr["signup_rate"] = (curr["signup_completions"] / curr["sessions"] * 100) if curr["sessions"] > 0 else 0
        prev["demo_rate"] = (prev["demo_completions"] / prev["sessions"] * 100) if prev["sessions"] > 0 else 0
        prev["signup_rate"] = (prev["signup_completions"] / prev["sessions"] * 100) if prev["sessions"] > 0 else 0

        cat_bg = "#E8F0FE" if info["category"] == "SEP" else "#FFF3E0"

        html += '<tr>'
        html += f'<td {td_page} title="{info["display_path"]}">{info["display_path"]}</td>'
        html += f'<td {td_cat} style="padding:6px 8px;border:1px solid #D9E2F3;text-align:center;font-size:10px;background:{cat_bg};">{info["category"]}</td>'
        html += f'<td {td_cat}>{info["type"]}</td>'

        for key, _, fmt in PAGE_METRICS:
            cv = curr.get(key, 0)
            pv = prev.get(key, 0)

            if fmt == "int":
                val_str = f"{int(cv):,}"
            elif fmt == "pct":
                val_str = f"{cv:.1f}%"
            elif fmt == "time":
                mins = int(cv) // 60
                secs = int(cv) % 60
                val_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

            arrow = ""
            if isinstance(pv, (int, float)) and isinstance(cv, (int, float)):
                if pv > 0:
                    pct_change = ((cv - pv) / pv) * 100
                    if key == "bounce_rate":
                        if pct_change < -1:
                            arrow = f'<div style="color:#2E7D32;font-size:9px;">▲ {abs(pct_change):.0f}%</div>'
                        elif pct_change > 1:
                            arrow = f'<div style="color:#DC2626;font-size:9px;">▼ {abs(pct_change):.0f}%</div>'
                    else:
                        if pct_change > 1:
                            arrow = f'<div style="color:#2E7D32;font-size:9px;">▲ {pct_change:.0f}%</div>'
                        elif pct_change < -1:
                            arrow = f'<div style="color:#DC2626;font-size:9px;">▼ {abs(pct_change):.0f}%</div>'
                elif cv > 0:
                    arrow = f'<div style="color:#2E7D32;font-size:9px;">NEW</div>'

            html += f'<td {td}><div style="font-weight:600;">{val_str}</div>{arrow}</td>'
        html += '</tr>'

    html += '</tbody></table></div>'
    return html


def render_page_performance_tab(ga4_data):
    """Render the high-intent page tracking tab with date range picker and regional sub-tabs."""
    st.markdown("### 📄 High-Intent Page Performance")
    st.caption("Track demo/signup clicks & completions on key pages | Demo Rate & Signup Rate based on completions")

    col1, col2, col3 = st.columns([2, 2, 2])
    with col1:
        current_start = st.date_input("Current period start", value=date(2026, 4, 1), min_value=DATA_START, key="page_curr_start")
    with col2:
        current_end = st.date_input("Current period end", value=date.today() - timedelta(days=1), min_value=DATA_START, key="page_curr_end")
    with col3:
        period_days = (current_end - current_start).days
        compare_start = current_start - timedelta(days=period_days + 1)
        compare_end = current_start - timedelta(days=1)
        st.markdown(f"**Comparing vs:** {compare_start.strftime('%b %d')} - {compare_end.strftime('%b %d')}")

    if st.button("📊 Load Page Data", type="primary", key="load_pages"):
        with st.spinner("Fetching current period (overall + by country)..."):
            curr_overall = fetch_ga4_page_data(current_start.strftime("%Y-%m-%d"), current_end.strftime("%Y-%m-%d"))
            curr_by_country = fetch_ga4_page_data_by_country(current_start.strftime("%Y-%m-%d"), current_end.strftime("%Y-%m-%d"))
            st.session_state["page_curr"] = curr_overall
            st.session_state["page_curr_country"] = curr_by_country
        with st.spinner("Fetching comparison period (overall + by country)..."):
            prev_overall = fetch_ga4_page_data(compare_start.strftime("%Y-%m-%d"), compare_end.strftime("%Y-%m-%d"))
            prev_by_country = fetch_ga4_page_data_by_country(compare_start.strftime("%Y-%m-%d"), compare_end.strftime("%Y-%m-%d"))
            st.session_state["page_prev"] = prev_overall
            st.session_state["page_prev_country"] = prev_by_country
        st.session_state["page_periods"] = {
            "curr_label": f"{current_start.strftime('%b %d')} - {current_end.strftime('%b %d')}",
            "prev_label": f"{compare_start.strftime('%b %d')} - {compare_end.strftime('%b %d')}",
        }
        st.rerun()

    if "page_curr" not in st.session_state:
        st.info("Select a date range and click **Load Page Data**.")
        return

    periods = st.session_state.get("page_periods", {})
    st.caption(f"**Current:** {periods.get('curr_label', '')} | **vs:** {periods.get('prev_label', '')}")

    # Build page info lookup
    page_info = {}
    for path, cat, ptype in HIGH_INTENT_PAGES_DEDUP:
        norm = path.rstrip("/") if path != "/" else path
        page_info[norm] = {"category": cat, "type": ptype, "display_path": path}

    # Sub-tabs for regions
    ptab_all, ptab_northam, ptab_euk, ptab_amea, ptab_latam = st.tabs([
        "🌐 All", "🇺🇸 NORTHAM", "🇪🇺 EUK", "🌏 AMEA", "🌎 LATAM",
    ])

    with ptab_all:
        html = _build_page_table_html(st.session_state["page_curr"], st.session_state["page_prev"], page_info)
        st.markdown(html, unsafe_allow_html=True)

    for ptab, region_name in [
        (ptab_northam, "NORTHAM"),
        (ptab_euk, "EUK"),
        (ptab_amea, "AMEA"),
        (ptab_latam, "LATAM"),
    ]:
        with ptab:
            curr_region = filter_page_data_by_region(st.session_state["page_curr_country"], region_name)
            prev_region = filter_page_data_by_region(st.session_state["page_prev_country"], region_name)
            html = _build_page_table_html(curr_region, prev_region, page_info)
            st.markdown(html, unsafe_allow_html=True)


# ── Main App ────────────────────────────────────────────────────────────────

def main():
    col_title, col_btn = st.columns([4, 1])
    with col_title:
        st.markdown("## 📊 Klenty Website Performance Tracker")
        st.caption("Weekly traffic, clicks & leads from Jan 1, 2026 | Clicks excl. Chennai/TN | Leads excl. Outbound")
    with col_btn:
        st.write("")
        fetch_clicked = st.button("🔄 Fetch Data", type="primary", use_container_width=True)

    if fetch_clicked:
        end_date = date.today() - timedelta(days=1)
        start_str = DATA_START.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        with st.spinner("Fetching GA4 data (8 queries)..."):
            try:
                ga4_data = fetch_ga4_data(start_str, end_str)
                st.session_state["ga4_data"] = ga4_data
            except Exception as e:
                st.error(f"GA4 fetch failed: {e}")

        with st.spinner("Fetching Pipedrive leads..."):
            try:
                pipedrive_deals = fetch_pipedrive_data()
                st.session_state["pipedrive_deals"] = pipedrive_deals
            except Exception as e:
                st.error(f"Pipedrive fetch failed: {e}")

        st.session_state["data_loaded"] = True
        st.session_state["last_fetch"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.rerun()

    if not st.session_state.get("data_loaded"):
        st.info("👆 Click **Fetch Data** to load traffic and leads data from GA4 & Pipedrive.")
        st.stop()

    ga4_data = st.session_state.get("ga4_data", {})
    pipedrive_deals = st.session_state.get("pipedrive_deals", [])
    last_fetch = st.session_state.get("last_fetch", "")
    if last_fetch:
        st.caption(f"Last fetched: {last_fetch}")

    end_date = date.today() - timedelta(days=1)
    weeks = compute_weeks(DATA_START, end_date)
    chart_weeks = compute_standard_weeks(DATA_START, end_date)

    # ── Tabs ────────────────────────────────────────────────────────────────
    tab_overall, tab_northam, tab_euk, tab_amea, tab_latam, tab_pages = st.tabs([
        "📊 Overall", "🇺🇸 NORTHAM", "🇪🇺 EUK", "🌏 AMEA", "🌎 LATAM", "📄 Page Performance",
    ])

    with tab_overall:
        weekly = aggregate_weekly(weeks, ga4_data, pipedrive_deals)
        chart_data = aggregate_chart_data(chart_weeks, ga4_data, pipedrive_deals)
        render_tab(
            weeks, weekly, chart_weeks, chart_data,
            title="Overall — Traffic & Leads",
            cols=TABLE_COLS,
            caption="LinkedIn* = linkedin.com + lnkd.in + button clicks + UTM campaigns (overlaps with Organic Social)",
        )

    for tab, region_name, region_label in [
        (tab_northam, "NORTHAM", "NORTHAM (US & Canada)"),
        (tab_euk, "EUK", "EUK (Europe & UK)"),
        (tab_amea, "AMEA", "AMEA (Asia, Middle East, Australia)"),
        (tab_latam, "LATAM", "LATAM (Latin America & Others)"),
    ]:
        with tab:
            r_weekly = aggregate_weekly_region(weeks, ga4_data, pipedrive_deals, region_name)
            r_chart_data = aggregate_chart_data(chart_weeks, ga4_data, pipedrive_deals, region=region_name)
            render_tab(
                weeks, r_weekly, chart_weeks, r_chart_data,
                title=f"{region_label} — Traffic & Leads",
                cols=REGION_TABLE_COLS,
            )


    with tab_pages:
        render_page_performance_tab(ga4_data)


if __name__ == "__main__":
    main()
