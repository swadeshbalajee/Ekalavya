"""Usage & activity dashboard: spend, tokens, time and retries per chat, and live runs.

Opened from Settings → Account (``?view=usage``). Data comes from the
``/v1/usage`` API. Charts follow one rule set: a single series per chart in
the validated categorical blue, recessive axes, tooltips on every mark; run
states use the reserved status colours, always paired with an icon and label.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from datetime import datetime
from typing import Any

import altair as alt
import httpx
import pandas as pd
import streamlit as st

PERIODS = {"7d": 7, "30d": 30, "90d": 90, "All": 3650}
STATE_ICON = {
    "done": "✓",
    "running": "●",
    "queued": "○",
    "failed": "✕",
    "degraded": "!",
    "skipped": "–",
}
STATE_LABEL = {
    "done": "Done",
    "running": "Running",
    "queued": "Up next",
    "failed": "Failed",
    "degraded": "Shipped with notes",
    "skipped": "Skipped",
}


def _tokens(dark: bool) -> dict[str, str]:
    """Colour roles for this page, stepped separately for each theme."""
    if dark:
        return {
            "surface": "#2a2a2a",
            "surface_2": "#323232",
            "border": "#3b3b3b",
            "ink": "#ececec",
            "ink_2": "#b4bdb7",
            "muted": "#8b958f",
            "grid": "#3a3a3a",
            "series": "#3987e5",
            "accent": "#65c5a0",
        }
    return {
        "surface": "#ffffff",
        "surface_2": "#f6f8f6",
        "border": "#e2e8e3",
        "ink": "#18241d",
        "ink_2": "#4d5d54",
        "muted": "#76837b",
        "grid": "#edf1ee",
        "series": "#2a78d6",
        "accent": "#197a65",
    }


# Status colours are fixed across themes and never used for data series.
STATUS = {
    "done": "#0ca30c",
    "running": None,  # the series blue, so "in progress" reads as active, not as a verdict
    "queued": None,
    "failed": "#d03b3b",
    "degraded": "#fab219",
    "skipped": None,
}


def money(value: float | None) -> str:
    if not value:
        return "$0.00"
    return "<$0.01" if value < 0.01 else f"${value:,.2f}"


def count(value: float | None) -> str:
    value = value or 0
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 10_000:
        return f"{value / 1000:.0f}k"
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return f"{value:,.0f}"


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = round(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def _when(value: str | None) -> str:
    if not value:
        return "—"
    moment = datetime.fromisoformat(value)
    return moment.astimezone().strftime("%d %b, %H:%M")


def _style(colors: dict[str, str]) -> None:
    c = colors
    st.markdown(
        f"""
        <style>
          [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {{ display:none; }}
          .block-container {{ max-width:1180px; padding-top:1.2rem !important; }}
          [data-testid="stHeader"] {{ display:none; }}
          .usage-title {{ display:flex; align-items:center; gap:12px; }}
          .usage-title h1 {{ font-size:1.55rem; margin:0; color:{c["ink"]}; letter-spacing:-.01em; }}
          .usage-title p {{ margin:2px 0 0; color:{c["muted"]}; font-size:.92rem; }}
          .usage-section {{ margin:26px 0 10px; display:flex; align-items:baseline; gap:10px; }}
          .usage-section h3 {{ margin:0; font-size:1.05rem; color:{c["ink"]}; }}
          .usage-section span {{ color:{c["muted"]}; font-size:.85rem; }}
          .kpi {{ background:{c["surface"]}; border:1px solid {c["border"]}; border-radius:14px;
                  padding:14px 16px; min-height:96px; }}
          .kpi .label {{ color:{c["muted"]}; font-size:.78rem; text-transform:uppercase;
                         letter-spacing:.05em; }}
          .kpi .value {{ color:{c["ink"]}; font-size:1.6rem; font-weight:650; margin-top:4px;
                         font-variant-numeric:tabular-nums; }}
          .kpi .sub {{ color:{c["ink_2"]}; font-size:.8rem; margin-top:2px; }}
          .run {{ background:{c["surface"]}; border:1px solid {c["border"]}; border-radius:16px;
                  padding:16px 18px; margin-bottom:14px; }}
          .run-head {{ display:flex; justify-content:space-between; align-items:center; gap:12px; }}
          .run-head b {{ color:{c["ink"]}; font-size:1rem; }}
          .run-head .meta {{ color:{c["muted"]}; font-size:.82rem; }}
          .run-next {{ color:{c["ink_2"]}; font-size:.86rem; margin:6px 0 14px; }}
          .flow {{ display:flex; align-items:center; flex-wrap:wrap; gap:4px; }}
          .node {{ display:inline-flex; align-items:center; gap:6px; padding:6px 11px;
                   border-radius:999px; font-size:.8rem; border:1px solid {c["border"]};
                   color:{c["ink_2"]}; background:{c["surface_2"]}; white-space:nowrap; }}
          .node .icon {{ font-weight:700; }}
          .node.done {{ border-color:{STATUS["done"]}55; color:{c["ink"]}; }}
          .node.done .icon {{ color:{STATUS["done"]}; }}
          .node.running {{ border-color:{c["series"]}; color:{c["ink"]};
                           box-shadow:0 0 0 3px {c["series"]}22; }}
          .node.running .icon {{ color:{c["series"]}; animation:pulse 1.4s ease-in-out infinite; }}
          .node.failed {{ border-color:{STATUS["failed"]}; }}
          .node.failed .icon {{ color:{STATUS["failed"]}; }}
          .node.degraded .icon {{ color:{STATUS["degraded"]}; }}
          .node.queued {{ border-style:dashed; color:{c["muted"]}; background:transparent; }}
          .node.skipped {{ opacity:.55; border-style:dotted; }}
          .edge {{ width:18px; height:2px; background:{c["border"]}; }}
          .edge.done {{ background:{STATUS["done"]}88; }}
          @keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:.35; }} }}
          .lanes {{ margin-top:14px; display:grid; grid-template-columns:72px 1fr 92px;
                    gap:6px 10px; align-items:center; }}
          .lane-key {{ color:{c["ink"]}; font-weight:600; font-size:.84rem; }}
          .lane-meta {{ color:{c["muted"]}; font-size:.78rem; text-align:right; }}
          .legend {{ display:flex; gap:14px; flex-wrap:wrap; color:{c["muted"]};
                     font-size:.78rem; margin:4px 0 0; }}
          .utable {{ overflow:auto; border:1px solid {c["border"]}; border-radius:14px;
                     background:{c["surface"]}; }}
          .utable table {{ width:100%; border-collapse:collapse; font-size:.86rem; }}
          .utable th {{ position:sticky; top:0; background:{c["surface_2"]}; color:{c["muted"]};
                        font-weight:600; font-size:.74rem; text-transform:uppercase;
                        letter-spacing:.04em; padding:10px 12px; text-align:left; }}
          .utable td {{ padding:9px 12px; color:{c["ink"]}; border-top:1px solid {c["border"]};
                        font-variant-numeric:tabular-nums; max-width:420px; overflow:hidden;
                        text-overflow:ellipsis; white-space:nowrap; }}
          .utable .r {{ text-align:right; }}
          .utable tr:hover td {{ background:{c["surface_2"]}; }}
          .st-key-usage-back button, .st-key-usage-open-chat button {{
            background:{c["surface"]} !important; border:1px solid {c["border"]} !important; }}
          .st-key-usage-back button *, .st-key-usage-open-chat button * {{
            color:{c["ink"]} !important; }}
          .st-key-usage-back button:hover, .st-key-usage-open-chat button:hover {{
            border-color:{c["accent"]} !important; }}
          [class*="st-key-usage-period"] button, [class*="st-key-usage-scope"] button {{
            background:{c["surface"]} !important; border-color:{c["border"]} !important; }}
          [class*="st-key-usage-period"] button *, [class*="st-key-usage-scope"] button * {{
            color:{c["ink_2"]} !important; }}
          [class*="st-key-usage-period"] button[aria-checked="true"],
          [class*="st-key-usage-scope"] button[aria-checked="true"] {{
            background:{c["accent"]}1f !important; border-color:{c["accent"]} !important; }}
          [class*="st-key-usage-period"] button[aria-checked="true"] *,
          [class*="st-key-usage-scope"] button[aria-checked="true"] * {{
            color:{c["ink"]} !important; }}
          .st-key-usage-detail .react-aria-ComboBox > div {{
            background-color:{c["surface"]} !important; border:1px solid {c["border"]} !important; }}
          .st-key-usage-detail .react-aria-ComboBox input,
          .st-key-usage-detail .react-aria-ComboBox button {{ color:{c["ink"]} !important; }}
          [role="listbox"] {{ background-color:{c["surface"]} !important; }}
          [role="listbox"] [role="option"] {{ color:{c["ink"]} !important; }}
          [role="listbox"] [role="option"]:hover, [role="listbox"] [data-focused] {{
            background-color:{c["surface_2"]} !important; }}
          /* Style-only blocks and the hidden cookie helper add no content: keep them out of
             the page flow so the header sits at the top. */
          [data-testid="stMainBlockContainer"] > div > [data-testid="stElementContainer"]:has(style) {{
            display:none; }}
          [data-testid="stMainBlockContainer"] > div > [data-testid="stElementContainer"]:has(iframe.stCustomComponentV1) {{
            position:absolute; height:0; overflow:hidden; }}
          .st-key-usage-detail [data-baseweb="select"] * {{ color:{c["ink"]} !important; }}
          .st-key-usage-detail [data-baseweb="tab"] p {{ color:{c["ink_2"]} !important; }}
          .st-key-usage-detail [aria-selected="true"] p {{ color:{c["ink"]} !important; }}
          .st-key-usage-detail label p {{ color:{c["muted"]} !important; }}
          .empty {{ background:{c["surface"]}; border:1px dashed {c["border"]};
                    border-radius:14px; padding:22px; color:{c["muted"]}; text-align:center; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _table(
    rows: list[dict[str, Any]], columns: list[tuple[str, str, str]], *, height: int = 0
) -> None:
    """A themed, scrollable HTML table. ``columns``: (key, header, "l" or "r")."""
    head = "".join(f"<th class='{align}'>{html.escape(label)}</th>" for _, label, align in columns)
    body = "".join(
        "<tr>"
        + "".join(
            f"<td class='{align}'>{html.escape(str(row.get(key, '')))}</td>"
            for key, _, align in columns
        )
        + "</tr>"
        for row in rows
    )
    limit = f"max-height:{height}px;" if height else ""
    st.markdown(
        f"<div class='utable' style='{limit}'><table><thead><tr>{head}</tr></thead>"
        f"<tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def _kpi(column, label: str, value: str, sub: str = "") -> None:
    column.markdown(
        f"<div class='kpi'><div class='label'>{html.escape(label)}</div>"
        f"<div class='value'>{html.escape(value)}</div>"
        f"<div class='sub'>{html.escape(sub)}</div></div>",
        unsafe_allow_html=True,
    )


def _section(title: str, note: str = "") -> None:
    st.markdown(
        f"<div class='usage-section'><h3>{html.escape(title)}</h3>"
        f"<span>{html.escape(note)}</span></div>",
        unsafe_allow_html=True,
    )


def _node(label: str, state: str) -> str:
    return (
        f"<span class='node {state}' title='{STATE_LABEL.get(state, state)}'>"
        f"<span class='icon'>{STATE_ICON.get(state, '')}</span>{html.escape(label)}</span>"
    )


def _flow(items: list[tuple[str, str]]) -> str:
    parts = []
    for index, (label, state) in enumerate(items):
        if index:
            previous = items[index - 1][1]
            parts.append(f"<span class='edge {'done' if previous == 'done' else ''}'></span>")
        parts.append(_node(label, state))
    return f"<div class='flow'>{''.join(parts)}</div>"


def _run_card(run: dict[str, Any]) -> str:
    pipeline = run["pipeline"]
    owner = f" · {html.escape(run['owner'])}" if run.get("owner") else ""
    status = {"GENERATING": "Building video", "EDITING": "Editing", "LOCALIZING": "Translating"}
    lanes = ""
    if pipeline["scenes"]:
        rows = []
        for lane in pipeline["scenes"]:
            steps = [(step["label"], step["state"]) for step in lane["steps"]]
            if lane["state"] == "degraded":
                steps[-1] = ("Done", "degraded")
            attempt = f"attempt {lane['attempt']}" if lane["attempt"] > 1 else ""
            rows.append(
                f"<div class='lane-key'>{html.escape(lane['scene'])}</div>"
                f"<div>{_flow(steps)}</div><div class='lane-meta'>{attempt}</div>"
            )
        lanes = f"<div class='lanes'>{''.join(rows)}</div>"
    stages = [(stage["label"], stage["state"]) for stage in pipeline["stages"]]
    return (
        "<div class='run'><div class='run-head'>"
        f"<b>{html.escape(run['title'])}</b>"
        f"<span class='meta'>{status.get(run['status'], run['status'])}{owner} · "
        f"{duration(run['elapsed_seconds'])} elapsed</span></div>"
        f"<div class='run-next'>{html.escape(pipeline.get('next') or 'Starting…')}</div>"
        f"{_flow(stages)}{lanes}</div>"
    )


def _legend() -> str:
    return (
        "<div class='legend'>"
        + "".join(
            f"<span>{_node(STATE_LABEL[state], state)}</span>"
            for state in ("done", "running", "queued", "degraded", "failed")
        )
        + "</div>"
    )


def _axis(colors: dict[str, str]) -> dict[str, Any]:
    return {
        "labelColor": colors["muted"],
        "titleColor": colors["muted"],
        "gridColor": colors["grid"],
        "domainColor": colors["border"],
        "tickColor": colors["border"],
        "labelFontSize": 11,
        "titleFontSize": 11,
        "titleFontWeight": "normal",
    }


def _chart(chart: alt.Chart, colors: dict[str, str]) -> alt.Chart:
    return (
        chart.configure(background="transparent")
        .configure_view(stroke=None)
        .configure_axis(**_axis(colors))
    )


def _spend_by_day(daily: list[dict[str, Any]], days: int, colors: dict[str, str]) -> None:
    """One bar per day across the period, zero days included, so gaps read as gaps."""
    frame = pd.DataFrame(daily)
    frame["date"] = pd.to_datetime(frame["date"])
    last = pd.Timestamp.now().normalize()
    first = max(frame["date"].min(), last - pd.Timedelta(days=days - 1))
    frame = (
        frame.set_index("date")
        .reindex(pd.date_range(first, last, freq="D"), fill_value=0)
        .rename_axis("date")
        .reset_index()
    )
    frame["day"] = frame["date"].dt.strftime("%d %b")
    chart = (
        alt.Chart(frame)
        .mark_bar(color=colors["series"], cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X(
                "day:O",
                sort=None,
                title=None,
                axis=alt.Axis(labelAngle=0, labelOverlap="greedy", grid=False),
            ),
            y=alt.Y("cost_usd:Q", title="Spend ($)", axis=alt.Axis(format="$.2f", tickCount=4)),
            tooltip=[
                alt.Tooltip("day:N", title="Day"),
                alt.Tooltip("cost_usd:Q", title="Spend", format="$.3f"),
                alt.Tooltip("calls:Q", title="Calls"),
            ],
        )
        .properties(height=230)
    )
    st.altair_chart(_chart(chart, colors), use_container_width=True)


def _spend_by_module(modules: list[dict[str, Any]], colors: dict[str, str]) -> None:
    frame = pd.DataFrame(modules)
    frame["label"] = frame["cost_usd"].map(money)
    base = alt.Chart(frame).encode(
        y=alt.Y("module:N", sort="-x", title=None, axis=alt.Axis(grid=False, ticks=False)),
        x=alt.X("cost_usd:Q", title=None, axis=None),
        tooltip=[
            alt.Tooltip("module:N", title="Module"),
            alt.Tooltip("cost_usd:Q", title="Spend", format="$.3f"),
            alt.Tooltip("calls:Q", title="Calls"),
            alt.Tooltip("tokens:Q", title="Tokens", format=","),
            alt.Tooltip("avg_latency_ms:Q", title="Avg time (ms)", format=",.0f"),
        ],
    )
    bars = base.mark_bar(
        color=colors["series"], cornerRadiusTopRight=4, cornerRadiusBottomRight=4, height=14
    )
    labels = base.mark_text(align="left", dx=6, color=colors["ink_2"], fontSize=11).encode(
        text="label:N"
    )
    chart = (bars + labels).properties(height=max(120, 30 * len(frame)))
    st.altair_chart(_chart(chart, colors), use_container_width=True)


def _timeline(run: dict[str, Any], colors: dict[str, str]) -> None:
    frame = pd.DataFrame(run["timeline"])
    if frame.empty:
        st.caption("No stage timings recorded for this run.")
        return
    frame["length"] = (frame["end_seconds"] - frame["start_seconds"]).map(duration)
    chart = (
        alt.Chart(frame)
        .mark_bar(color=colors["series"], cornerRadius=4, height=16)
        .encode(
            y=alt.Y("stage:N", sort=None, title=None, axis=alt.Axis(grid=False, ticks=False)),
            x=alt.X("start_seconds:Q", title="Seconds from start"),
            x2="end_seconds:Q",
            tooltip=[
                alt.Tooltip("stage:N", title="Stage"),
                alt.Tooltip("length:N", title="Took"),
                alt.Tooltip("start_seconds:Q", title="Started at (s)"),
            ],
        )
        .properties(height=max(140, 34 * len(frame)))
    )
    st.altair_chart(_chart(chart, colors), use_container_width=True)


def _chat_detail(api: Callable, project_id: str, colors: dict[str, str]) -> None:
    try:
        detail = api("GET", f"/usage/chats/{project_id}")
    except httpx.HTTPError as error:
        st.error(f"Could not load this chat's usage: {error}")
        return
    totals = detail["totals"]
    head, action = st.columns([4, 1], vertical_alignment="center")
    head.markdown(
        f"<div class='usage-section' style='margin-top:6px'><h3>"
        f"{html.escape(detail['project']['title'])}</h3><span>{money(totals['cost_usd'])} · "
        f"{count(totals['tokens'])} tokens · {totals['calls']} calls · "
        f"{totals['retries']} retries</span></div>",
        unsafe_allow_html=True,
    )
    if action.button("Open chat", key="usage-open-chat", icon=":material/chat:", width="stretch"):
        st.query_params.clear()
        st.query_params["chat"] = project_id
        st.session_state.active_project_id = project_id
        st.rerun()
    module_tab, scene_tab, run_tab, call_tab = st.tabs(
        ["By module", "By scene", "Run timeline", "Every call"]
    )
    with module_tab:
        if detail["modules"]:
            _spend_by_module(detail["modules"], colors)
        else:
            st.caption("No model calls recorded for this chat yet.")
    with scene_tab:
        if detail["scenes"]:
            _table(
                [
                    {
                        "scene": scene["scene"],
                        "spend": money(scene["cost_usd"]),
                        "tokens": f"{scene['tokens']:,}",
                        "calls": scene["calls"],
                        "retries": scene["retries"],
                        "latency": duration(scene["avg_latency_ms"] / 1000)
                        if scene["avg_latency_ms"]
                        else "—",
                        "module": scene["modules"][0]["module"] if scene["modules"] else "",
                    }
                    for scene in detail["scenes"]
                ],
                [
                    ("scene", "Scene", "l"),
                    ("spend", "Spend", "r"),
                    ("tokens", "Tokens", "r"),
                    ("calls", "Calls", "r"),
                    ("retries", "Retries", "r"),
                    ("latency", "Avg call", "r"),
                    ("module", "Costliest module", "l"),
                ],
            )
        else:
            st.caption("Per-scene usage is recorded for videos built from now on.")
    with run_tab:
        runs = detail["runs"]
        if not runs:
            st.caption("No runs yet.")
        else:
            labels = [
                f"Run {run['index']} · {_when(run['started_at'])} · {run['outcome']} · "
                f"{duration(run['seconds'])}"
                for run in runs
            ]
            substantial = [index for index, run in enumerate(runs) if run["seconds"] >= 30]
            choice = st.selectbox(
                "Run",
                range(len(runs)),
                index=substantial[-1] if substantial else len(runs) - 1,
                format_func=labels.__getitem__,
            )
            _timeline(runs[choice], colors)
    with call_tab:
        calls = detail["calls"]
        if calls:
            outcome = {"ok": "OK", "fallback": "Fallback model", "error": "Failed, retried"}
            _table(
                [
                    {
                        "time": _when(call["time"]),
                        "operation": call["operation"],
                        "module": call["module"],
                        "scene": call["scene"] or "—",
                        "model": call["model"],
                        "status": outcome.get(call["status"], call["status"]),
                        "tokens": f"{call['tokens']:,}" if call["tokens"] else "—",
                        "cost": "—" if call["cost_usd"] is None else money(call["cost_usd"]),
                        "took": duration(call["latency_ms"] / 1000) if call["latency_ms"] else "—",
                    }
                    for call in reversed(calls)
                ],
                [
                    ("time", "Time", "l"),
                    ("operation", "Run", "l"),
                    ("module", "Module", "l"),
                    ("scene", "Scene", "l"),
                    ("model", "Model", "l"),
                    ("status", "Outcome", "l"),
                    ("tokens", "Tokens", "r"),
                    ("cost", "Cost", "r"),
                    ("took", "Took", "r"),
                ],
                height=420,
            )
        else:
            st.caption("No calls recorded.")


def render_dashboard(api: Callable, user: dict[str, Any], *, dark: bool) -> None:
    """The full-page usage dashboard. Stops the script when done."""
    colors = _tokens(dark)
    _style(colors)
    params = st.query_params
    period_label = params.get("period", "30d")
    period_label = period_label if period_label in PERIODS else "30d"
    scope = "all" if user.get("is_admin") and params.get("scope") == "all" else "me"

    title, controls, back = st.columns([2.2, 2.4, 1], vertical_alignment="center")
    title.markdown(
        "<div class='usage-title'><div><h1>Usage &amp; activity</h1>"
        f"<p>{'Everyone' if scope == 'all' else html.escape(user['display_name'])} · "
        "spend, tokens, time and retries</p></div></div>",
        unsafe_allow_html=True,
    )
    with controls:
        period_col, scope_col = st.columns([1.7, 1])
        picked = period_col.segmented_control(
            "Period",
            list(PERIODS),
            default=period_label,
            label_visibility="collapsed",
            key="usage-period",
        )
        if picked and picked != period_label:
            st.query_params["period"] = picked
            st.rerun()
        if user.get("is_admin"):
            who = scope_col.segmented_control(
                "Scope",
                ["Me", "All users"],
                default="All users" if scope == "all" else "Me",
                label_visibility="collapsed",
                key="usage-scope",
            )
            if who and (who == "All users") != (scope == "all"):
                st.query_params["scope"] = "all" if who == "All users" else "me"
                st.rerun()
    if back.button("Back to chat", key="usage-back", icon=":material/arrow_back:", width="stretch"):
        chat = st.query_params.get("chat")
        st.query_params.clear()
        if chat:
            st.query_params["chat"] = chat
        st.rerun()

    try:
        data = api("GET", "/usage/summary", params={"days": PERIODS[period_label], "scope": scope})
    except httpx.HTTPError as error:
        st.error(f"Could not load usage: {error}")
        st.stop()
    totals = data["totals"]
    tiles = st.columns(6)
    _kpi(tiles[0], "Spend", money(totals["cost_usd"]), f"{totals['calls']:,} model calls")
    _kpi(
        tiles[1],
        "Tokens",
        count(totals["tokens"]),
        f"{count(totals['input_tokens'])} in · {count(totals['output_tokens'])} out",
    )
    _kpi(tiles[2], "Videos built", str(totals["videos"]), f"across {totals['chats']} chats")
    _kpi(
        tiles[3],
        "Avg build",
        duration(totals["build_seconds_avg"]),
        f"{duration(totals['build_seconds_total'])} in total",
    )
    rate = totals["success_rate"]
    _kpi(tiles[4], "Success", "—" if rate is None else f"{rate:.0%}", "videos finished")
    _kpi(
        tiles[5],
        "Retries",
        str(totals["retries"]),
        f"{totals['failed_calls']} failed calls · {totals['fallback_calls']} fallbacks",
    )

    _section("Live runs", "refreshes every few seconds")
    _live(api, scope)

    left, right = st.columns([1.15, 1], gap="large")
    with left:
        _section("Spend per day")
        if data["daily"]:
            _spend_by_day(data["daily"], PERIODS[period_label], colors)
        else:
            st.markdown("<div class='empty'>No spend in this period.</div>", unsafe_allow_html=True)
    with right:
        _section("Spend by module", "where the money goes")
        if data["modules"]:
            _spend_by_module(data["modules"], colors)
        else:
            st.markdown("<div class='empty'>No model calls yet.</div>", unsafe_allow_html=True)

    if scope == "all" and data["users"]:
        _section("Users", "highest spend first")
        _table(
            [
                {
                    "user": item["user"],
                    "username": item["username"] or "—",
                    "spend": money(item["cost_usd"]),
                    "chats": item["chats"],
                    "calls": f"{item['calls']:,}",
                    "tokens": count(item["tokens"]),
                }
                for item in data["users"]
            ],
            [
                ("user", "User", "l"),
                ("username", "Username", "l"),
                ("spend", "Spend", "r"),
                ("chats", "Chats", "r"),
                ("calls", "Calls", "r"),
                ("tokens", "Tokens", "r"),
            ],
        )

    _section("Chats", "most recent first")
    chats = data["chats"]
    if not chats:
        st.markdown(
            "<div class='empty'>No chats with usage in this period yet.</div>",
            unsafe_allow_html=True,
        )
        st.stop()
    _table(
        [
            {
                "chat": chat["title"],
                "owner": chat["owner"] or "—",
                "spend": money(chat["cost_usd"]),
                "tokens": f"{chat['tokens']:,}",
                "videos": chat["videos"],
                "time": duration(chat["time_seconds"]),
                "retries": chat["retries"],
                "last": _when(chat["last_activity"]),
            }
            for chat in chats
        ],
        [
            ("chat", "Chat", "l"),
            *([("owner", "Owner", "l")] if scope == "all" else []),
            ("spend", "Spend", "r"),
            ("tokens", "Tokens", "r"),
            ("videos", "Videos", "r"),
            ("time", "Build time", "r"),
            ("retries", "Retries", "r"),
            ("last", "Last activity", "l"),
        ],
        height=380,
    )
    with st.container(key="usage-detail"):
        _section("Chat details", "modules, scenes, run timeline and every call")
        titles = [f"{chat['title'][:70]} · {money(chat['cost_usd'])}" for chat in chats]
        picked_chat = st.selectbox(
            "Chat", range(len(chats)), format_func=titles.__getitem__, key="usage-chat"
        )
        _chat_detail(api, chats[picked_chat]["project_id"], colors)
    st.stop()


def _live(api: Callable, scope: str) -> None:
    @st.fragment(run_every="4s")
    def live_runs() -> None:
        try:
            runs = api("GET", "/usage/live", params={"scope": scope})["runs"]
        except httpx.HTTPError as error:
            st.caption(f"Live runs unavailable: {error}")
            return
        if not runs:
            st.markdown(
                "<div class='empty'>No video is being built right now. Runs appear here, "
                "stage by stage, while they happen.</div>",
                unsafe_allow_html=True,
            )
            return
        st.markdown("".join(_run_card(run) for run in runs) + _legend(), unsafe_allow_html=True)

    live_runs()
