"""Polished conversational tutor UI with accounts and inline video lessons."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

# Streamlit executes this file as a script and therefore adds `frontend/`, not
# the repository root, to sys.path. Make package imports work for both
# `streamlit run frontend/app.py` and direct module/test execution.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import httpx
import streamlit as st
import streamlit.components.v1 as components
from streamlit.components.v1 import declare_component

from frontend.components.subtitle_track import subtitle_track_markup

API_URL = os.getenv("SCHOLARMOTION_API_URL", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.getenv("SCHOLARMOTION_API_KEY")
LANGUAGES = [
    "English",
    "Hindi",
    "Bengali",
    "Tamil",
    "Telugu",
    "Marathi",
    "Spanish",
    "French",
    "Arabic",
    "Japanese",
]
AUTH_COOKIE_NAME = "scholarmotion_session"
auth_cookie_component = declare_component(
    "scholarmotion_auth_cookie",
    path=str(Path(__file__).parent / "components" / "auth_cookie"),
)
_brand_asset = Path(__file__).parent / "assets" / "zha-mark.svg"
_brand_asset_light = Path(__file__).parent / "assets" / "zha-mark-light.svg"
DARK_BRAND_MARK = "data:image/svg+xml;base64," + base64.b64encode(_brand_asset.read_bytes()).decode(
    "ascii"
)
LIGHT_BRAND_MARK = "data:image/svg+xml;base64," + base64.b64encode(
    _brand_asset_light.read_bytes()
).decode("ascii")
BRAND_MARK = DARK_BRAND_MARK


def brand_mark_html(class_name: str = "brand-mark") -> str:
    return f"<img class='{class_name}' src='{BRAND_MARK}' alt='ழ' />"


def _schedule_auth_cookie(action: str, token: str | None = None) -> None:
    st.session_state.auth_cookie_command = {
        "action": action,
        "token": token,
        "request_id": uuid.uuid4().hex,
    }


def _sync_auth_cookie() -> None:
    command = st.session_state.get("auth_cookie_command")
    result = auth_cookie_component(
        action=command["action"] if command else "noop",
        token=command.get("token") if command else None,
        request_id=command.get("request_id") if command else None,
        key="auth-cookie-sync",
        default=None,
        height=1,
    )
    if command and isinstance(result, dict) and result.get("request_id") == command["request_id"]:
        st.session_state.pop("auth_cookie_command", None)

    if "auth_token" not in st.session_state:
        # The clear command takes precedence over a cookie that was present at
        # the start of this browser request (for example, an expired API token).
        if command and command["action"] == "clear":
            return
        st.session_state.auth_token = st.context.cookies.get(AUTH_COOKIE_NAME)


def api(method: str, path: str, **kwargs: Any) -> Any:
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    token = st.session_state.get("auth_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(kwargs.pop("headers", {}))
    timeout = kwargs.pop("timeout", 60)
    response = httpx.request(
        method, f"{API_URL}/v1{path}", timeout=timeout, headers=headers, **kwargs
    )
    response.raise_for_status()
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def credit_left() -> str | None:
    """Model credit remaining, refreshed at most once a minute per session."""
    cached = st.session_state.get("credit_cache")
    if cached and time.monotonic() - cached[0] < 60:
        return cached[1]
    try:
        credit = api("GET", "/credits", timeout=20) or {}
    except httpx.HTTPError:
        credit = {}
    remaining = credit.get("remaining_usd") if credit.get("available") else None
    text = None if remaining is None else f"${remaining:,.2f}"
    st.session_state.credit_cache = (time.monotonic(), text)
    return text


def set_active_chat(project_id: str | None) -> None:
    """Keep the selected conversation in session state and in the shareable URL."""
    st.session_state.active_project_id = project_id
    if project_id:
        st.query_params["chat"] = project_id
    elif "chat" in st.query_params:
        del st.query_params["chat"]


def api_error(error: httpx.HTTPError) -> str:
    response = getattr(error, "response", None)
    if response is not None:
        try:
            return str(response.json().get("detail", response.text))
        except ValueError:
            return response.text
    return str(error)


_PROGRESS_LABELS = {
    "project.started": "Starting your lesson",
    "preparation.started": "Understanding your request and preferences",
    "preparation.complete": "Choosing languages and finding learning sources",
    "retrieval.complete": "Source material is ready",
    "paper.reading": "Reading the whole paper",
    "paper.outlined": "Lecture plan for the paper is ready",
    "curriculum.complete": "Lesson outline is ready",
    "script.complete": "Narration is ready",
    "storyboard.complete": "Scenes are planned",
    "scenes.created": "Building the planned scenes",
    "scene.started": "Preparing scene",
    "scene.code_started": "Drawing the scene",
    "scene.audio_started": "Recording narration",
    "scene.render_started": "Rendering scene",
    "scene.verifying": "Checking scene layout and accuracy",
    "scene.repairing": "Fixing a scene issue and checking again",
    "scene.complete": "Scene passed verification",
    "scene.degraded": "Scene rendered with quality issues",
    "scene.blocked": "A scene could not be completed",
    "video.assembling": "Putting scenes, narration, and subtitles together",
    "video.complete": "Your video is ready",
    "localization.started": "Preparing the translated lesson",
    "localization.complete": "The translated lesson is ready",
}

_SCENE_STAGE = {
    "scene.started": 0.05,
    "scene.code_started": 0.15,
    "scene.audio_started": 0.15,
    "scene.render_started": 0.45,
    "scene.timing_repaired": 0.5,
    "scene.verifying": 0.7,
    "scene.rejected": 0.35,
    "scene.repairing": 0.35,
    "scene.complete": 1.0,
    "scene.degraded": 1.0,
    "scene.blocked": 1.0,
}

_REPAIR_ACTIONS = {
    "patch_code": "patching the animation code",
    "regenerate_code": "regenerating the animation code",
    "revise_spec": "revising the scene plan",
    "simplify": "simplifying the scene",
    "block": "no repairs left",
}


def describe_progress_event(event: dict[str, Any]) -> str | None:
    """Turn one pipeline event into a sentence a student can follow."""
    name = event.get("event", "")
    detail = event.get("detail") or {}
    scene = detail.get("scene_key") or detail.get("scene_id")
    reasons = detail.get("reasons") or []
    reason_text = "; ".join(str(reason) for reason in reasons[:3])
    if name == "preparation.complete":
        sources = detail.get("sources") or []
        kind = detail.get("source_type")
        where = (
            "only your sources"
            if kind == "given"
            else "your sources plus web sources"
            if kind == "given+web"
            else "web sources"
            if kind == "web"
            else "saved/library sources"
            if sources
            else "no sources"
        )
        return (
            f"Understood the request ({detail.get('rationale') or 'lesson'}). "
            f"Text {detail.get('text_language', 'English')}, voice "
            f"{detail.get('voice_language', 'English')}. Grounding: {where}"
            + (f" — {', '.join(sources[:4])}" if sources else "")
        )
    if name == "paper.reading":
        pages = f" ({detail['pages']} pages)" if detail.get("pages") else ""
        return f"Reading the paper '{detail.get('title', 'attached paper')}'{pages}: text, equations, tables and figures"
    if name == "paper.outlined":
        chapters = detail.get("chapters") or []
        return (
            f"Planned a {detail.get('minutes', '?')}-minute lecture on '{detail.get('title', 'the paper')}': "
            f"{len(chapters)} chapters, {detail.get('equations', 0)} equations, "
            f"{detail.get('plots', 0)} result plots, "
            f"{len(detail.get('figures') or [])} of the paper's own figures — "
            + "; ".join(chapters[:12])
        )
    if name == "retrieval.web_started":
        return detail.get("reason") or "Searching the web for reliable sources"
    if name == "script.complete":
        return (
            f"Script written for {detail.get('segments', '?')} scenes and reviewed for "
            f"{', '.join(detail.get('checks') or ['coherence'])}"
            + (f" after {detail['revisions']} revision(s)" if detail.get("revisions") else "")
        )
    if name == "script.review":
        problems = detail.get("problems") or []
        if detail.get("approved") or not problems:
            return f"Script review round {detail.get('round')}: approved"
        return (
            f"Script review round {detail.get('round')}: "
            + ("blocking problems" if detail.get("blocking") else "revising")
            + " — "
            + "; ".join(problems[:3])
        )
    if name == "project.failed":
        return "Build stopped: " + str(detail.get("error") or "unknown error")[:500]
    if name == "scenes.created":
        return "Planned scenes: " + "; ".join(detail.get("scenes") or [])
    if name == "usage.summary":
        top = detail.get("by_purpose") or []
        breakdown = ", ".join(
            f"{row.get('purpose')} ${row.get('cost_usd', 0):.2f}" for row in top[:4]
        )
        cached = detail.get("cached_tokens") or 0
        return (
            f"Model cost for this {detail.get('operation', 'run')}: "
            f"${detail.get('cost_usd', 0):.2f} over {detail.get('calls', 0)} calls"
            + (f" ({cached:,} input tokens served from cache)" if cached else "")
            + (f" — {breakdown}" if breakdown else "")
        )
    if name == "narration.started":
        takes = detail.get("takes") or 1
        if takes > 1:
            return (
                f"Recording the lecture in {takes} voice takes, one per group of chapters "
                f"({detail.get('scenes')} scenes)"
            )
        return f"Recording the whole lesson in one voice take ({detail.get('scenes')} scenes)"
    if name == "narration.complete":
        takes = detail.get("takes") or 1
        return (
            f"Narration recorded in {'one take' if takes == 1 else f'{takes} takes'}: "
            f"{detail.get('seconds')} s, voice {detail.get('voice')}"
        )
    if name == "narration.per_scene":
        return detail.get("reason") or "Recording narration scene by scene"
    if name == "scene.rejected":
        return (
            f"{scene} rejected (attempt {detail.get('attempt')} of {detail.get('max_attempts')}): "
            f"{reason_text or 'quality check failed'} → "
            f"{_REPAIR_ACTIONS.get(detail.get('next_action', ''), 'repairing')}"
        )
    if name == "scene.complete":
        return f"{scene} passed on attempt {detail.get('attempt', 1)}" + (
            f" ({', '.join(detail.get('checks') or [])})" if detail.get("checks") else ""
        )
    if name == "scene.degraded":
        return f"{scene} kept with minor layout issues: {reason_text}"
    if name == "scene.blocked":
        return f"{scene} could not be completed: {reason_text}"
    if name == "scene.code_failed":
        return f"{scene}: animation model unavailable — {reason_text}"
    if name == "scene.timing_repaired":
        return f"{scene}: retimed the animation to match the narration"
    if name == "scene.review_repair":
        return f"{scene}: rebuilding after the whole-lesson review — {reason_text}"
    if name == "video.ending":
        return "Added a settled ending (held final frame and fade-out)"
    if name == "lesson.reviewed":
        problems = detail.get("problems") or []
        if problems:
            return "Whole-lesson review notes: " + "; ".join(problems[:3])
        return "Whole-lesson review: scenes flow coherently and the ending resolves"
    if name in {"scene.started", "scene.render_started", "scene.verifying", "scene.repairing"}:
        return None  # Shown live in the status line; too noisy for the log.
    label = _PROGRESS_LABELS.get(name)
    return f"{label}: {scene}" if label and scene else label


_PROGRESS_MILESTONES = {
    "project.started": 0.03,
    "preparation.started": 0.04,
    "preparation.complete": 0.10,
    "retrieval.complete": 0.12,
    "curriculum.complete": 0.22,
    "script.complete": 0.32,
    "storyboard.complete": 0.40,
    "scenes.created": 0.43,
    "video.assembling": 0.94,
    "video.complete": 1.0,
    "localization.started": 0.05,
    "localization.complete": 1.0,
}


@st.fragment(run_every="5s")
def render_project_progress(project_id: str) -> None:
    try:
        progress = api("GET", f"/projects/{project_id}/progress")
    except httpx.HTTPError as error:
        st.warning(f"Could not refresh video progress: {api_error(error)}")
        return
    if progress["status"] == "CANCELLED":
        st.info(
            "Video generation was cancelled. You can continue chatting or request another video."
        )
        return
    if progress["status"] == "FAILED":
        st.error(progress.get("failure_message") or "The video build failed before saving a video.")
        failed_log = [
            line
            for line in (describe_progress_event(event) for event in progress.get("events", []))
            if line
        ]
        if failed_log:
            with st.expander("What happened", expanded=False):
                st.markdown("\n".join(f"- {html.escape(line)}" for line in failed_log[-20:]))
        if st.button("Try generating again", key=f"retry-generation-{project_id}"):
            try:
                api("POST", f"/projects/{project_id}/generate")
                st.toast("Video generation restarted.", icon="🎬")
                st.rerun(scope="app")
            except httpx.HTTPError as error:
                st.error(f"Could not restart generation: {api_error(error)}")
        return
    if progress["status"] not in {"GENERATING", "EDITING", "LOCALIZING", "CANCELLING"}:
        st.rerun()
    events = progress.get("events", [])
    if not events:
        dark_mode = st.session_state.get("dark_mode", False)
        status_color = "#a7e5ca" if dark_mode else "#176b4b"
        status_bg = "#17271f" if dark_mode else "#edf8f2"
        st.markdown(
            "<div role='status' style='padding:10px 14px;border-left:3px solid #10A37F;"
            f"border-radius:8px;background:{status_bg};color:{status_color};'>"
            "Your lesson is queued. Waiting for a worker to start.</div>",
            unsafe_allow_html=True,
        )
        if progress["status"] == "CANCELLING":
            st.info("Cancellation requested. Stopping the current generation…")
        else:
            if st.button(
                "Cancel video generation",
                key=f"cancel-generation-{project_id}",
                icon=":material/stop_circle:",
            ):
                try:
                    api("POST", f"/projects/{project_id}/cancel")
                    st.toast("Cancellation requested.", icon="⏹️")
                    st.rerun(scope="app")
                except httpx.HTTPError as error:
                    st.error(f"Could not cancel generation: {api_error(error)}")
        return
    # Ignore events from an earlier build when this project is being rebuilt.
    starts = {"project.started", "preparation.started", "localization.started", "edit.started"}
    start_index = max(
        (index for index, event in enumerate(events) if event.get("event") in starts),
        default=0,
    )
    current_events = events[start_index:]
    latest = current_events[-1]
    name = latest.get("event", "project.started")
    scene = latest.get("detail", {}).get("scene_key")
    label = _PROGRESS_LABELS.get(name, name.replace(".", " ").replace("_", " ").capitalize())
    if scene:
        label = f"{label}: {scene}"
    if name == "preparation.complete":
        detail = latest.get("detail", {})
        languages = f"Text: {detail.get('text_language', 'English')} · voice: {detail.get('voice_language', 'English')}"
        sources = detail.get("sources") or []
        source_text = (
            f" · Sources: {', '.join(sources[:3])}" if sources else " · No saved source matched"
        )
        label = f"{label} · {languages}{source_text}"

    if name == "scene.rejected":
        label = describe_progress_event(latest) or label
    value = _PROGRESS_MILESTONES.get(name)
    if value is None and name.startswith(("scene.", "narration.")):
        planned = next(
            (
                event.get("detail", {}).get("scene_count")
                for event in reversed(current_events)
                if event.get("event") == "scenes.created"
            ),
            None,
        )
        scene_count = max(int(planned or progress.get("scene_count") or 0), 1)
        # Each scene advances through code, render and verification; a
        # rejection moves it back, so the bar reflects real remaining work.
        stages: dict[str, float] = {}
        for event in current_events:
            key = event.get("detail", {}).get("scene_key")
            if key and event.get("event") in _SCENE_STAGE:
                stages[key] = _SCENE_STAGE[event["event"]]
        value = 0.45 + 0.47 * min(sum(stages.values()) / scene_count, 1.0)
    if value is None:
        value = 0.05 if progress["status"] == "EDITING" else 0.08

    dark_mode = st.session_state.get("dark_mode", False)
    status_color = "#a7e5ca" if dark_mode else "#176b4b"
    status_bg = "#17271f" if dark_mode else "#edf8f2"
    st.markdown(
        "<div role='status' style='padding:10px 14px;border-left:3px solid #10A37F;"
        f"border-radius:8px;background:{status_bg};color:{status_color};'>{html.escape(label)}</div>",
        unsafe_allow_html=True,
    )
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    st.progress(
        value,
        text=f"Video generation · {round(value * 100)}% done · {100 - round(value * 100)}% remaining",
    )
    log = [line for line in (describe_progress_event(event) for event in current_events) if line]
    if log:
        with st.expander(f"What's happening ({len(log)} steps)", expanded=True):
            st.markdown("\n".join(f"- {html.escape(line)}" for line in log[-14:]))
    if progress["status"] == "CANCELLING":
        st.info("Cancellation requested. Stopping the current generation…")
    elif st.button(
        "Cancel video generation",
        key=f"cancel-generation-{project_id}",
        icon=":material/stop_circle:",
    ):
        try:
            api("POST", f"/projects/{project_id}/cancel")
            st.toast("Cancellation requested.", icon="⏹️")
            st.rerun(scope="app")
        except httpx.HTTPError as error:
            st.error(f"Could not cancel generation: {api_error(error)}")


_THINKING_LEVELS = {"Quick": "low", "Balanced": "medium", "Deep": "high"}


def thinking_selector() -> None:
    """Let the learner choose how much the model reasons before answering."""
    st.radio(
        "Thinking",
        list(_THINKING_LEVELS),
        index=1,
        horizontal=True,
        key="thinking_level",
        help="Quick answers faster; Deep reasons longer before explaining (when the model supports it).",
    )


def thinking_effort() -> str:
    return _THINKING_LEVELS.get(st.session_state.get("thinking_level", "Balanced"), "medium")


def transcribe_audio(audio: Any, language: str | None = None) -> str:
    """Send a browser recording through the configured speech-to-text provider."""
    encoded = base64.b64encode(audio.getvalue()).decode("ascii")
    result = api(
        "POST",
        "/capabilities/stt/transcribe",
        json={"audio_base64": encoded, "language": language},
    )
    return str(result.get("text") or "").strip()


def _submit_composer_text(widget_key: str) -> None:
    text = str(st.session_state.get(widget_key) or "").strip()
    if text:
        st.session_state.pending_prompt = text
        st.session_state[widget_key] = ""


def _stage_paper(file_key: str, link_key: str) -> None:
    """Hold a chosen PDF or paper link until the next message is sent."""
    upload = st.session_state.get(file_key)
    link = str(st.session_state.get(link_key) or "").strip()
    if upload is not None:
        st.session_state.pending_paper = {"name": upload.name, "data": upload.getvalue()}
    elif link:
        st.session_state.pending_paper = {"name": link, "link": link}
    st.session_state[link_key] = ""


def attach_pending_paper(project_id: str) -> str | None:
    """Upload a staged paper to this chat; returns its title."""
    paper = st.session_state.pop("pending_paper", None)
    if not paper:
        return None
    if paper.get("link"):
        result = api(
            "POST",
            f"/projects/{project_id}/sources/link",
            json={"url": paper["link"]},
            timeout=300,
        )
    else:
        result = api(
            "POST",
            f"/projects/{project_id}/sources",
            files={"file": (paper["name"], paper["data"], "application/pdf")},
            timeout=300,
        )
    return str((result or {}).get("title") or paper["name"])


def _toggle_voice_input(enabled_key: str) -> None:
    st.session_state[enabled_key] = not st.session_state.get(enabled_key, False)


def message_composer(key: str, placeholder: str, language: str | None = None) -> None:
    """Render one unified composer with mic, text entry, and send controls."""
    enabled_key = f"voice_input_enabled_{key}"
    text_key = f"composer-text-{key}"
    transcript_key = f"pending-voice-transcript-{key}"
    if transcript := st.session_state.pop(transcript_key, None):
        existing_text = str(st.session_state.get(text_key) or "").strip()
        st.session_state[text_key] = f"{existing_text} {transcript}".strip()
    staged = st.session_state.get("pending_paper")
    if staged:
        name_col, remove_col = st.columns([0.9, 0.1], vertical_alignment="center")
        name_col.caption(f"📄 Attached: {staged['name']} (sent with your next message)")
        if remove_col.button("", key=f"remove-paper-{key}", icon=":material/close:"):
            st.session_state.pop("pending_paper", None)
            st.rerun()
    with st.container(key="composer-shell"):
        attach_col, text_col, mic_col, send_col = st.columns(
            [0.07, 0.78, 0.07, 0.08], gap="small", vertical_alignment="center"
        )
        with attach_col, st.popover("", icon=":material/attach_file:", help="Attach a paper"):
            st.file_uploader("Upload a paper (PDF)", type=["pdf"], key=f"paper-file-{key}")
            st.text_input(
                "…or paste a paper link (arXiv or a direct PDF URL)",
                key=f"paper-link-{key}",
            )
            st.button(
                "Attach",
                key=f"attach-paper-{key}",
                on_click=_stage_paper,
                args=(f"paper-file-{key}", f"paper-link-{key}"),
            )
            st.caption("Then ask, for example: “explain this paper” for a full video lecture.")
        with mic_col:
            st.button(
                "",
                key=f"voice-toggle-{key}",
                icon=":material/mic:"
                if not st.session_state.get(enabled_key)
                else ":material/stop:",
                help="Record a voice message"
                if not st.session_state.get(enabled_key)
                else "Stop recording",
                on_click=_toggle_voice_input,
                args=(enabled_key,),
            )
        with text_col:
            st.text_input(
                "Message",
                key=text_key,
                placeholder=placeholder,
                label_visibility="collapsed",
                on_change=_submit_composer_text,
                args=(text_key,),
            )
        with send_col:
            st.button(
                "",
                key=f"send-composer-{key}",
                icon=":material/arrow_upward:",
                type="primary",
                help="Send message",
                on_click=_submit_composer_text,
                args=(text_key,),
            )
        if st.session_state.get(enabled_key):
            audio = st.audio_input(
                "Record a voice message",
                key=f"voice-recording-{key}",
                help="Your recording is transcribed by the configured speech model.",
            )
            if audio:
                raw = audio.getvalue()
                fingerprint = hashlib.sha256(raw).hexdigest()
                if fingerprint != st.session_state.get("last_voice_fingerprint"):
                    try:
                        with st.spinner("Transcribing…"):
                            transcript = transcribe_audio(audio, language)
                        st.session_state.last_voice_fingerprint = fingerprint
                        st.session_state[enabled_key] = False
                        if transcript:
                            st.session_state[transcript_key] = transcript
                            st.toast("Voice message ready", icon="🎙️")
                            st.rerun()
                        st.warning("I couldn’t hear any words in that recording.")
                    except httpx.HTTPError as error:
                        st.error(f"Speech-to-text is unavailable: {api_error(error)}")


def timestamp(seconds: float | None) -> str:
    minutes, remainder = divmod(float(seconds or 0), 60)
    return f"{int(minutes)}:{remainder:04.1f}"


def title_for(project: dict[str, Any]) -> str:
    return project["title"].strip()[:52] or "New conversation"


def displayed_conversation(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, tuple[str, int, list[int], int]]]:
    """The active conversation, or an older branch where the learner picked one.

    An edited message keeps its earlier versions as branches. Picking one with
    ‹ › shows that version and its old continuation, read-only, in place of
    everything after it. Returns the messages to show and, per message that
    has versions, ``(state_key, selected, options, active)`` for the arrows.
    """
    shown: list[dict[str, Any]] = []
    controls: dict[str, tuple[str, int, list[int], int]] = {}
    for message in messages:
        branches = message.get("branches") or []
        if message["role"] == "user" and branches:
            active = message.get("branch_index") or len(branches) + 1
            options = sorted({branch["index"] for branch in branches} | {active})
            key = f"branch-view-{message['id']}"
            selected = st.session_state.get(key, active)
            if selected != active:
                older = next(b["messages"] for b in branches if b["index"] == selected)
                older = [{**item, "readonly": True} for item in older]
                if older:
                    controls[older[0]["id"]] = (key, selected, options, active)
                return [*shown, *older], controls
            controls[message["id"]] = (key, selected, options, active)
        shown.append(message)
    return shown, controls


def videos_by_message(
    video_messages: list[dict[str, Any]], kept_videos: list[dict[str, Any]]
) -> dict[str, tuple[int, bool]]:
    """Pair each video-producing chat turn with the version it produced.

    A turn owns the newest version made after it and before the next such turn,
    so every edit keeps its own playable result in the conversation. Returns
    ``{message_id: (version, is_latest_version)}``.
    """
    if not kept_videos:
        return {}
    latest = max(item["version"] for item in kept_videos)
    stamps = [_parse_time(message.get("created_at")) for message in video_messages]
    paired: dict[str, tuple[int, bool]] = {}
    for index, message in enumerate(video_messages):
        start = stamps[index]
        end = stamps[index + 1] if index + 1 < len(stamps) else None
        made = [
            item["version"]
            for item in kept_videos
            if (start is None or _parse_time(item["created_at"]) >= start)
            and (end is None or _parse_time(item["created_at"]) < end)
        ]
        if made:
            paired[message["id"]] = (max(made), max(made) == latest)
    return paired


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def media_url(project_id: str, resource: str) -> str:
    params = [f"access_token={quote(st.session_state.auth_token, safe='')}"]
    if API_KEY:
        params.append(f"api_key={quote(API_KEY, safe='')}")
    return f"{API_URL}/v1/projects/{project_id}/{resource}?{'&'.join(params)}"


def chapter_player(
    project_id: str,
    segments: list[dict[str, Any]],
    *,
    video_version: int | None = None,
    subtitle_language: str = "English",
    dark_mode: bool = True,
) -> None:
    """Display an authenticated video artifact inline in the conversation."""
    duration = max((float(item.get("end") or 0) for item in segments), default=0)
    video_element_id = f"lesson-video-{project_id.replace('-', '')}"
    markers, labels = [], []
    for index, segment in enumerate(segments, 1):
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or 0)
        left = 100 * start / duration if duration else 0
        width = max(2, 100 * (end - start) / duration) if duration else 100
        midpoint = left + width / 2
        label = f"Scene {index}: {timestamp(start)}–{timestamp(end)}"
        markers.append(
            f"<span class='scene-segment' style='left:{left:.3f}%;width:{width:.3f}%'></span>"
            f"<button class='marker' style='left:clamp(18px,{midpoint:.3f}%,calc(100% - 18px))' "
            f"onclick='document.getElementById(\"{video_element_id}\").currentTime={start};' "
            f"title='{html.escape(label)}' aria-label='{html.escape(label)}'>{index}</button>"
        )
        labels.append(f"<span>{html.escape(label)}</span>")
    # Pinning every resource to one version keeps an earlier video in the chat
    # playable after later edits, and stops the browser reusing a cached MP4.
    pin = "" if video_version is None else f"&version={int(video_version)}"
    video_url = html.escape(media_url(project_id, "video") + pin)
    chapters_url = html.escape(media_url(project_id, "chapters.vtt") + pin)
    subtitles_track = subtitle_track_markup(
        media_url(project_id, "subtitles.vtt") + pin, subtitle_language
    )
    label_color = "#aab7af" if dark_mode else "#67756d"
    marker_color = "#26352d" if dark_mode else "#edf3ef"
    marker_border = "#46574c" if dark_mode else "#d9e4dd"
    marker_text = "#edf4ef" if dark_mode else "#314139"
    accent = "#65c5a0" if dark_mode else "#197a65"
    components.html(
        f"""
        <style>
          body {{ margin:0; background:transparent; color:{label_color}; font-family:"OpenAI Sans", Inter, system-ui, sans-serif; }}
          video {{ display:block; width:100%; aspect-ratio:16/9; object-fit:contain; border-radius:16px; background:#050706; box-shadow:0 14px 32px #0004; }}
          .timeline {{ position:relative; height:36px; margin:10px 0 4px; overflow:hidden; }}
          .scene-segment {{ position:absolute; top:14px; height:4px; border-radius:999px; background:{marker_border}; }}
          .marker {{ position:absolute; top:2px; transform:translateX(-50%); box-sizing:border-box; display:flex;
            align-items:center; justify-content:center; width:36px; min-width:36px; height:28px; overflow:visible;
            border:1px solid {marker_border}; border-radius:9px; color:{marker_text}; background:{marker_color};
            cursor:pointer; font-size:13px; font-weight:650; padding:0; white-space:nowrap; z-index:1; }}
          .marker:hover {{ background:{accent}; border-color:{accent}; color:#fff; }}
          .labels {{ display:flex; flex-wrap:wrap; gap:8px; font-size:13px; color:{label_color}; }}
          .labels span {{ padding:4px 8px; border-radius:999px; background:{marker_color}; }}
        </style>
        <video id="{video_element_id}" controls preload="metadata" crossorigin="anonymous">
          <source src="{video_url}" type="video/mp4">
          <track kind="chapters" src="{chapters_url}" default>
          {subtitles_track}
        </video>
        <div class="timeline">{"".join(markers)}</div>
        <div class="labels">{"".join(labels)}</div>
        """,
        height=560,
    )


def copy_message_button(value: str, help_text: str, *, dark_mode: bool) -> None:
    """Copy from a small browser-side control, so clipboard access has a user gesture."""
    encoded = (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    label = html.escape(help_text, quote=True)
    color = "#9eaca2" if dark_mode else "#758279"
    accent = "#a8e3c5" if dark_mode else "#197a65"
    components.html(
        f"""
        <style>
          html,body {{ width:100%; height:34px; margin:0; background:transparent; overflow:hidden; }}
          button {{ width:100%; height:30px; display:grid; place-items:center; padding:0;
            color:{color}; background:transparent; border:1px solid transparent; border-radius:8px;
            cursor:pointer; }}
          button:hover, button:focus-visible {{ color:{accent}; background:{"#1d352a" if dark_mode else "#eaf5f0"};
            border-color:{"#345440" if dark_mode else "#d6eae0"}; outline:none; }}
          button svg {{ width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:1.8;
            stroke-linecap:round; stroke-linejoin:round; }}
          button.copied {{ color:{accent}; }}
        </style>
        <button type="button" id="copy-action" title="{label}" aria-label="{label}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="13" rx="2"/>
            <path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h3"/></svg>
        </button>
        <script>
          const textToCopy = {encoded};
          const action = document.getElementById('copy-action');
          const copyIcon = action.innerHTML;
          const checkIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>';
          action.addEventListener('click', async () => {{
            let copied = false;
            try {{
              await navigator.clipboard.writeText(textToCopy);
              copied = true;
            }} catch (error) {{
              const field = document.createElement('textarea');
              field.value = textToCopy;
              field.setAttribute('readonly', '');
              field.style.position = 'fixed';
              field.style.opacity = '0';
              document.body.appendChild(field);
              field.focus();
              field.select();
              copied = document.execCommand('copy');
              field.remove();
            }}
            if (copied) {{
              action.innerHTML = checkIcon;
              action.classList.add('copied');
              action.title = 'Copied';
              action.setAttribute('aria-label', 'Copied');
              setTimeout(() => {{
                action.innerHTML = copyIcon;
                action.classList.remove('copied');
                action.title = '{label}';
                action.setAttribute('aria-label', '{label}');
              }}, 1200);
            }}
          }});
        </script>
        """,
        height=34,
    )


def render_chat_markdown(content: str) -> None:
    """Render mixed Markdown and common LaTeX delimiter styles."""
    sections = content.split("```")
    for index in range(0, len(sections), 2):
        sections[index] = (
            sections[index]
            .replace(r"\[", "\n$$\n")
            .replace(r"\]", "\n$$\n")
            .replace(r"\(", "$")
            .replace(r"\)", "$")
        )
    st.markdown("```".join(sections))


def sign_out(keep_chat: bool = False) -> None:
    try:
        api("POST", "/auth/logout")
    except httpx.HTTPError:
        pass
    st.session_state.pop("auth_token", None)
    if not keep_chat:
        set_active_chat(None)
    _schedule_auth_cookie("clear")
    st.rerun()


@st.dialog("Settings", width="medium")
def settings_dialog(user: dict[str, Any], project: dict[str, Any] | None) -> None:
    account_tab, video_tab, memory_tab = st.tabs(["Account", "Video", "Memory"])

    with account_tab:
        left, right = st.columns([1.4, 1], gap="large")
        with left, st.form("account-settings"):
            st.markdown("#### Profile")
            st.text_input("Username", value=user["username"], disabled=True)
            display_name = st.text_input("Display name", value=user["display_name"])
            email = st.text_input("Email", value=user["email"] or "")
            avatar_color = st.color_picker("Avatar color", value=user["avatar_color"])
            save_account = st.form_submit_button("Save changes", width="stretch")
        with right:
            st.markdown("#### Account")
            st.caption("Your chats and preferences are private to this PostgreSQL account.")
            st.button("Sign out", on_click=sign_out, width="stretch")
        if save_account:
            try:
                api(
                    "PATCH",
                    "/auth/me",
                    json={
                        "display_name": display_name,
                        "email": email or None,
                        "avatar_color": avatar_color,
                    },
                )
                st.rerun()
            except httpx.HTTPError as error:
                st.error(api_error(error))

    with video_tab:
        st.markdown("#### Video and language")
        context = "this conversation" if project else "new conversations"
        st.caption(f"These settings apply to {context}.")
        with st.form("video-settings"):
            duration = st.number_input(
                "Target duration (minutes)",
                min_value=0.5,
                max_value=30.0,
                value=float(
                    project["target_duration_minutes"]
                    if project
                    else user["default_duration_minutes"]
                ),
                step=0.5,
            )
            text_value = project["text_language"] if project else user["default_text_language"]
            voice_value = project["voice_language"] if project else user["default_voice_language"]
            text_language = st.selectbox(
                "Text in video",
                LANGUAGES,
                index=LANGUAGES.index(text_value) if text_value in LANGUAGES else 0,
            )
            voice_language = st.selectbox(
                "Narration voice",
                LANGUAGES,
                index=LANGUAGES.index(voice_value) if voice_value in LANGUAGES else 0,
            )
            save_video = st.form_submit_button("Save video settings", width="stretch")
        if save_video:
            try:
                if project:
                    api(
                        "PATCH",
                        f"/projects/{project['id']}/settings",
                        json={
                            "target_duration_minutes": duration,
                            "text_language": text_language,
                            "voice_language": voice_language,
                        },
                    )
                else:
                    api(
                        "PATCH",
                        "/auth/me",
                        json={
                            "default_duration_minutes": duration,
                            "default_text_language": text_language,
                            "default_voice_language": voice_language,
                        },
                    )
                st.rerun()
            except httpx.HTTPError as error:
                st.error(api_error(error))

    with memory_tab:
        st.markdown("#### Conversation memory")
        if project is None:
            st.caption("Open a saved conversation to inspect its memory and artifacts.")
        else:
            try:
                memory = api("GET", f"/projects/{project['id']}/memory")
                conversation = memory["conversation"]
                artifacts = memory["artifacts"]
                agent_memory = memory["agent_memory"]
                first, second, third = st.columns(3)
                first.metric("Messages", conversation["message_count"])
                second.metric("Scenes", artifacts["scenes"])
                third.metric("Revisions", artifacts["revisions"])
                st.caption(
                    f"Available agent memory: {agent_memory['active_skills']} skills and "
                    f"{agent_memory['active_corrections']} learned correction patterns."
                )
            except httpx.HTTPError as error:
                st.error(api_error(error))


@st.dialog("New project")
def new_project_dialog() -> None:
    st.caption("Create a workspace that can contain multiple chats, sources, and lesson videos.")
    with st.form("new-project-form"):
        name = st.text_input("Project name", placeholder="e.g. Linear algebra course")
        create = st.form_submit_button("Create project", type="primary", width="stretch")
    if create:
        clean_name = name.strip()
        if not clean_name:
            st.error("Enter a project name.")
            return
        try:
            folder = api("POST", "/project-folders", json={"name": clean_name})
            st.session_state.active_folder_id = folder["id"]
            set_active_chat(None)
            st.rerun()
        except httpx.HTTPError as error:
            st.error(api_error(error))


@st.dialog("Rename chat", width="small")
def rename_chat_dialog(project_id: str, current_title: str) -> None:
    with st.form(f"rename-chat-dialog-{project_id}"):
        title = st.text_input("Chat name", value=current_title, max_chars=300)
        save = st.form_submit_button("Save name", type="primary", width="stretch")
    if save:
        if not title.strip():
            st.error("Enter a chat name.")
            return
        try:
            api("PATCH", f"/projects/{project_id}", json={"title": title.strip()})
            st.rerun()
        except httpx.HTTPError as error:
            st.error(api_error(error))


@st.dialog("Move chat to a project", width="small")
def move_chat_dialog(project: dict[str, Any], folders: list[dict[str, Any]]) -> None:
    if not folders:
        st.caption("Create a project first, then you can move this chat into it.")
        return
    choices = {item["name"]: item["id"] for item in folders}
    current_name = next(
        (name for name, folder_id in choices.items() if folder_id == project.get("folder_id")),
        None,
    )
    target_name = st.selectbox(
        "Project",
        list(choices),
        index=list(choices).index(current_name) if current_name else None,
    )
    if st.button("Move chat", type="primary", width="stretch"):
        try:
            api("POST", f"/projects/{project['id']}/move", json={"folder_id": choices[target_name]})
            st.session_state.active_folder_id = choices[target_name]
            st.rerun()
        except httpx.HTTPError as error:
            st.error(api_error(error))


@st.dialog("Delete chat", width="small")
def delete_chat_dialog(project: dict[str, Any]) -> None:
    st.caption(f"Permanently delete “{project['title']}” and its conversation history?")
    confirm = st.checkbox("I understand this can’t be undone.")
    if st.button("Delete chat", type="primary", disabled=not confirm, width="stretch"):
        try:
            api("DELETE", f"/projects/{project['id']}")
            if st.session_state.get("active_project_id") == project["id"]:
                set_active_chat(None)
            st.rerun()
        except httpx.HTTPError as error:
            st.error(api_error(error))


@st.dialog("Share chat", width="small")
def share_chat_dialog(share_url: str, dark_mode: bool) -> None:
    st.caption("Copy this link to reopen the conversation in your Ekalavya account.")
    link_col, copy_col = st.columns([1, 0.14], gap="small", vertical_alignment="bottom")
    with link_col:
        st.text_input("Chat link", value=share_url)
    with copy_col:
        copy_message_button(share_url, "Copy chat link", dark_mode=dark_mode)


def conversation_menu(project: dict[str, Any], folders: list[dict[str, Any]]) -> None:
    """Compact ChatGPT-like chat actions, anchored to its sidebar row."""
    menu_key = f"chat-actions-{project['id'].replace('-', '')}"
    app_origin = os.getenv("SCHOLARMOTION_PUBLIC_URL", "http://127.0.0.1:8501").rstrip("/")
    share_url = f"{app_origin}/?chat={quote(project['id'])}"
    with st.container(key=menu_key), st.popover("", icon=":material/more_horiz:"):
        if st.button(
            "Rename", key=f"rename-chat-{project['id']}", icon=":material/edit:", width="stretch"
        ):
            rename_chat_dialog(project["id"], project["title"])
        if st.button(
            "Pin" if not project.get("pinned") else "Unpin",
            key=f"pin-chat-{project['id']}",
            icon=":material/push_pin:",
            width="stretch",
        ):
            try:
                api(
                    "PATCH",
                    f"/projects/{project['id']}",
                    json={"pinned": not project.get("pinned", False)},
                )
                st.rerun()
            except httpx.HTTPError as error:
                st.error(api_error(error))
        if st.button(
            "Share", key=f"share-chat-{project['id']}", icon=":material/share:", width="stretch"
        ):
            share_chat_dialog(share_url, st.session_state.dark_mode)
        if st.button(
            "Unarchive" if project.get("archived") else "Archive",
            key=f"archive-chat-{project['id']}",
            icon=":material/unarchive:" if project.get("archived") else ":material/archive:",
            width="stretch",
        ):
            try:
                api(
                    "PATCH",
                    f"/projects/{project['id']}",
                    json={"archived": not project.get("archived", False)},
                )
                if st.session_state.get("active_project_id") == project["id"]:
                    set_active_chat(None)
                st.rerun()
            except httpx.HTTPError as error:
                st.error(api_error(error))
        if st.button(
            "Delete", key=f"delete-chat-{project['id']}", icon=":material/delete:", width="stretch"
        ):
            delete_chat_dialog(project)
        if st.button(
            "Move to project  ›",
            key=f"move-chat-{project['id']}",
            icon=":material/folder:",
            width="stretch",
        ):
            move_chat_dialog(project, folders)


def sidebar_chat_row(
    project: dict[str, Any], folders: list[dict[str, Any]], selected_id: str | None, prefix: str
) -> None:
    row_id = project["id"].replace("-", "")
    with st.container(key=f"chat-row-{row_id}"):
        title_col, menu_col = st.columns([1, 0.16], gap="small", vertical_alignment="center")
        with title_col:
            if st.button(
                title_for(project),
                key=f"{prefix}-{project['id']}",
                width="stretch",
                type="primary" if project["id"] == selected_id else "secondary",
            ):
                set_active_chat(project["id"])
                st.session_state.active_folder_id = project.get("folder_id")
                st.rerun()
        with menu_col:
            conversation_menu(project, folders)


def auth_screen() -> None:
    st.markdown("<style>[data-testid='stSidebar']{display:none}</style>", unsafe_allow_html=True)
    if st.button(
        "Light mode" if st.session_state.dark_mode else "Dark mode",
        key="auth-theme-toggle",
        icon=":material/light_mode:" if st.session_state.dark_mode else ":material/dark_mode:",
    ):
        st.session_state.dark_mode = not st.session_state.dark_mode
        st.rerun()
    with st.container(key="auth_card"):
        st.markdown(
            f"<div class='auth-brand'>{brand_mark_html('welcome-mark')}<h1>Ekalavya</h1>"
            "<p>Your conversational visual tutor</p></div>",
            unsafe_allow_html=True,
        )
        login_tab, register_tab = st.tabs(["Sign in", "Create account"])
        with login_tab, st.form("login-form"):
            username = st.text_input("Username", autocomplete="username")
            password = st.text_input("Password", type="password", autocomplete="current-password")
            submit_login = st.form_submit_button("Continue", type="primary", width="stretch")
        if submit_login:
            try:
                response = api(
                    "POST", "/auth/login", json={"username": username, "password": password}
                )
                st.session_state.auth_token = response["token"]
                _schedule_auth_cookie("save", response["token"])
                st.rerun()
            except httpx.HTTPError as error:
                st.error(api_error(error))

        with register_tab, st.form("register-form"):
            display_name = st.text_input("Name")
            new_username = st.text_input("Username", autocomplete="username")
            new_password = st.text_input(
                "Password",
                type="password",
                autocomplete="new-password",
                help="Use at least 8 characters.",
            )
            submit_registration = st.form_submit_button(
                "Create account", type="primary", width="stretch"
            )
        if submit_registration:
            try:
                response = api(
                    "POST",
                    "/auth/register",
                    json={
                        "username": new_username,
                        "password": new_password,
                        "display_name": display_name or new_username,
                    },
                )
                st.session_state.auth_token = response["token"]
                _schedule_auth_cookie("save", response["token"])
                st.rerun()
            except httpx.HTTPError as error:
                st.error(api_error(error))
    st.stop()


st.set_page_config(page_title="Ekalavya", page_icon="✦", layout="wide")
st.session_state.setdefault("dark_mode", True)
BRAND_MARK = DARK_BRAND_MARK if st.session_state.dark_mode else LIGHT_BRAND_MARK
st.session_state.setdefault("temporary_chat", False)
st.session_state.setdefault("temporary_messages", [])
st.session_state.setdefault("voice_input_enabled_temporary", False)
st.session_state.setdefault("voice_input_enabled_welcome", False)
st.session_state.setdefault("voice_input_enabled_conversation", False)
st.session_state.setdefault("last_voice_fingerprint", None)
_sync_auth_cookie()
st.markdown(
    """
    <style>
      :root {
        color-scheme: light;
        --canvas: #f4f6f3;
        --surface: #ffffff;
        --ink: #19241f;
        --muted: #6b7771;
        --line: #e4e9e5;
        --accent: #197a65;
        --accent-dark: #12644f;
        --accent-soft: #eaf5f0;
        --shadow: 0 14px 38px rgba(28, 48, 38, .08);
      }
      #MainMenu, footer, [data-testid="stStatusWidget"],
      [data-testid="stHeader"] [data-testid="stToolbarActions"],
      [data-testid="stHeader"] [data-testid="stAppDeployButton"] { display:none; }
    [data-testid="stHeader"] { height:0; background:transparent; pointer-events:none; }
    [data-testid="stHeader"] [data-testid="stExpandSidebarButton"] { pointer-events:auto; }
    [data-testid="stHeader"] [data-testid="stToolbar"] { pointer-events:none !important; }
      [data-testid="stHeader"] [data-testid="stExpandSidebarButton"] {
        position:fixed; top:12px; left:12px; z-index:30; width:36px; height:36px;
        display:flex; align-items:center; justify-content:center; border-radius:11px;
        background:#fff; border:1px solid var(--line); box-shadow:0 3px 10px #18241f12;
      }
            [data-testid="stHeader"] [data-testid="stExpandSidebarButton"] button {
        width:36px; height:36px; color:var(--ink);
      }
            html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"] {
        background:var(--canvas); color:var(--ink); font-size:16px;
                font-family:"OpenAI Sans", Inter, system-ui, sans-serif;
      }
      [data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] p,
      [data-testid="stMarkdownContainer"] p, [data-testid="stCaptionContainer"] {
        color:var(--ink);
      }
      [data-testid="stMain"] { background:transparent; }
    .block-container { width:100% !important; max-width:1000px; min-width:0; box-sizing:border-box; margin-inline:auto;
        padding:20px 32px 112px; }
      [data-testid="stSidebar"] { width:276px !important; background:var(--surface); border-right:1px solid var(--line); }
      [data-testid="stSidebar"] > div:first-child { width:276px !important; background:var(--surface); }
    [data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding:0 14px 76px !important; overflow-y:auto; }
    [data-testid="stSidebar"] [data-testid="stSidebarHeader"] { min-height:28px; height:28px; }
      [data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] button {
                width:32px; height:32px; color:var(--muted); border-radius:9px; transform:translateY(26px);
                position:relative; z-index:40; visibility:visible !important; opacity:1 !important; pointer-events:auto !important;
      }
      [data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"],
      [data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] > div {
        position:relative; z-index:40; visibility:visible !important; pointer-events:auto !important;
      }
      [data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] button,
      [data-testid="stHeader"] [data-testid="stExpandSidebarButton"],
      [data-testid="stHeader"] [data-testid="stExpandSidebarButton"] button {
        color:var(--ink) !important; opacity:1 !important; filter:none !important;
      }
      [data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] button *,
      [data-testid="stHeader"] [data-testid="stExpandSidebarButton"] *,
      [data-testid="stHeader"] [data-testid="stExpandSidebarButton"] button * {
        color:var(--ink) !important; opacity:1 !important; fill:currentColor !important;
      }
      [data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] button:hover { background:#f0f4f1; }
      [data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap:3px; }
      [data-testid="stSidebar"] .stButton > button {
        width:100%; justify-content:flex-start; color:#45534b; background:transparent;
                border:1px solid transparent; border-radius:10px; min-height:36px; height:36px; padding:6px 10px;
        transition:background .14s ease,border-color .14s ease,color .14s ease;
      }
      [data-testid="stSidebar"] .stButton > button:hover { background:#f4f7f4; border-color:#edf1ed; color:var(--ink); }
      [data-testid="stSidebar"] .stButton > button:active { background:#edf4ef; transform:none; }
      button:focus, button:focus-visible, [role="button"]:focus-visible {
        outline:none !important; box-shadow:0 0 0 3px #197a6525 !important;
      }
      [data-testid="stSidebar"] .stButton > button p { width:100%; text-align:left; overflow:hidden;
        text-overflow:ellipsis; white-space:nowrap; font-size:15px; }
      [data-testid="stSidebar"] .stButton > button [data-testid="stMarkdownContainer"] { width:100%; text-align:left; }
      [data-testid="stSidebar"] .stButton > button > div,
      [data-testid="stSidebar"] .stButton > button > div > span {
        width:100%; justify-content:flex-start !important; text-align:left !important;
      }
      [data-testid="stSidebar"] button[kind="primary"] {
        background:var(--accent-soft) !important; border-color:#d6eae0 !important; color:var(--accent-dark) !important;
      }
            .sidebar-brand { display:flex; align-items:center; gap:10px; margin:0 0 12px;
                color:var(--ink); font-size:17px; font-weight:720; letter-spacing:-.025em; line-height:30px;
                transform:translateY(-20px); }
            .sidebar-credit { display:flex; align-items:center; justify-content:space-between; gap:8px;
                margin:0 0 18px; padding:10px 14px; border:1px solid var(--line); border-radius:12px;
                background:var(--accent-soft); font-variant-numeric:tabular-nums; }
            .sidebar-credit-label { color:var(--muted); font-size:13px; font-weight:560; }
            .sidebar-credit-value { color:var(--accent); font-size:15px; font-weight:720; letter-spacing:-.01em; }
      .brand-mark, .profile-avatar, .welcome-mark { display:inline-flex; align-items:center; justify-content:center; }
            .brand-mark { width:28px; height:28px; border-radius:8px; object-fit:cover; box-shadow:0 5px 12px #197a6526; }
    .st-key-new-chat button { margin:10px 0 14px; }
      .st-key-new-chat button p { font-size:15px !important; font-weight:600; }
            .st-key-sidebar_footer { position:fixed; left:14px; bottom:0; z-index:5; width:248px; margin:0; padding:10px 0 10px;
        background:linear-gradient(180deg,#ffffffd9,#fff 22%); border-top:1px solid var(--line); }
            .st-key-sidebar_footer [data-testid="stHorizontalBlock"] { gap:6px !important; }
      .st-key-sidebar_footer [data-testid="column"] { display:flex; align-items:center; }
            .st-key-open-settings button, .st-key-theme-toggle button { min-width:36px !important; width:36px !important;
                height:36px !important; min-height:36px !important; justify-content:center;
                padding:0; border:1px solid var(--line); color:var(--muted); background:#fff; }
            .st-key-theme-toggle button { transform:translateX(4px); }
            .st-key-temporary-toggle button { min-width:36px !important; width:36px !important;
                height:36px !important; min-height:36px !important; padding:0; }
            .st-key-sidebar_brand_shell [data-testid="column"] { display:flex; align-items:center; }
      .st-key-open-settings button p { display:none; }
      .st-key-open-settings button:hover { color:var(--accent); background:var(--accent-soft) !important; }
      .sidebar-empty { color:#89938d; font-size:14px; line-height:1.5; padding:3px 10px 8px; }
      [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
        color:#8b9690; padding:0 10px; font-size:12px; line-height:15px;
        font-weight:700; letter-spacing:.1em; text-transform:uppercase;
      }
      [data-testid="stSidebar"] [data-testid="stElementContainer"]:has([data-testid="stCaptionContainer"]) {
        margin-top:10px; margin-bottom:4px;
      }
      .st-key-new-project button { color:#65736b !important; }
      [class*="st-key-folder-new-chat-"] button,
      [class*="st-key-folder-chat-"] button { padding-left:26px !important; }
      .project-indent { padding-left:26px; }
    .st-key-app_topbar_shell { position:fixed; top:16px; right:24px; z-index:50; width:88px; margin:0; padding:0; border-bottom:0; }
            @media (min-width:851px) {
                .st-key-app_topbar_shell { top:16px; z-index:100; }
                .st-key-app_topbar_shell [data-testid="stHorizontalBlock"] { gap:4px !important; }
                .st-key-app_topbar_shell [data-testid="stHorizontalBlock"] { justify-content:flex-end; padding-right:4px; }
            }
      .st-key-app_topbar_shell [data-testid="stHorizontalBlock"] { align-items:center; }
      .st-key-app_topbar_shell .stButton > button {
        width:36px !important; min-width:36px !important; height:36px !important; min-height:36px !important;
        padding:0 !important; color:#56645b !important; background:#fff !important;
        border:1px solid var(--line) !important; border-radius:11px !important; box-shadow:0 3px 10px rgba(20,35,26,.06);
      }
      .st-key-app_topbar_shell .stButton > button:hover { color:var(--accent-dark) !important; background:var(--accent-soft) !important; }
      .st-key-app_topbar_shell .st-key-temporary-toggle button[kind="primary"] {
        color:#fff !important; background:var(--accent) !important; border-color:var(--accent) !important;
      }
      .st-key-app_topbar_shell .st-key-temporary-toggle button[kind="primary"] * { color:#fff !important; }
                .app-topbar { display:flex; align-items:center; min-height:34px; margin:0; padding:0; gap:10px; min-width:0; }
                        .topbar-mark { width:28px; height:28px; border-radius:8px; object-fit:cover; flex:none; }
            .topbar-brand-name { color:var(--ink); font-size:16px; font-weight:720; white-space:nowrap; }
            .topbar-divider { width:1px; height:20px; background:var(--line); flex:none; }
      .topbar-copy { display:flex; flex-direction:column; gap:3px; min-width:0; }
            .app-topbar .topbar-copy { display:flex; flex-direction:column; gap:3px; min-width:0; }
      .topbar-copy small { color:#87938c; font-size:12px; line-height:1.2; font-weight:700; letter-spacing:.12em; text-transform:uppercase; }
      .topbar-copy strong { color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
        font-size:16px; font-weight:650; letter-spacing:-.01em; }
            .topbar-lesson-title { color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
                min-width:0; font-size:16px; font-weight:650; }
      [class*="st-key-chat-row-"] { min-width:0; }
      [class*="st-key-chat-row-"] [data-testid="stHorizontalBlock"] { gap:2px !important; align-items:center; }
      [class*="st-key-chat-row-"] [data-testid="stColumn"] { min-width:0 !important; padding:0 !important; }
      [class*="st-key-chat-row-"] [data-testid="stColumn"]:last-child { flex:0 0 34px !important; width:34px !important; }
      [class*="st-key-chat-row-"] [data-testid="stColumn"]:first-child { flex:1 1 auto !important; }
      [class*="st-key-chat-actions-"] { position:relative; z-index:30; width:30px; min-width:30px; margin:0; overflow:visible; }
      [class*="st-key-chat-actions-"] [data-testid="stPopover"] { width:30px !important; min-width:30px !important; }
      [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"] {
        width:30px !important; min-width:30px !important; height:30px !important; min-height:30px !important;
        padding:0 !important; border:0 !important; border-radius:8px !important;
        color:var(--muted) !important; background:transparent !important; box-shadow:none !important;
      }
      [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:hover,
      [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"][aria-expanded="true"] {
        color:var(--ink) !important; background:transparent !important;
      }
      [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"] [aria-hidden="true"] { display:none !important; }
      [data-testid="stPopoverBody"] {
        z-index:1000000 !important; visibility:visible !important; opacity:1 !important; pointer-events:auto !important;
        width:220px !important; min-width:min(220px,calc(100vw - 24px)) !important;
        max-width:calc(100vw - 24px) !important;
        max-height:min(74vh,390px) !important; overflow-y:auto !important; overflow-x:hidden !important;
        padding:7px !important; border:1px solid #dfe7e1 !important;
        border-radius:15px !important; background:#fff !important;
        box-shadow:0 16px 44px rgba(15,23,18,.2) !important;
      }
      [data-testid="stPopoverBody"] [data-testid="stVerticalBlock"] { gap:2px !important; }
      [data-testid="stPopoverBody"] button {
        width:100% !important; min-height:34px !important; height:34px !important;
        justify-content:flex-start !important; gap:10px !important; padding:5px 10px !important;
        border:0 !important; border-radius:8px !important; background:transparent !important;
        color:#343a40 !important; box-shadow:none !important; text-align:left !important;
      }
      [data-testid="stPopoverBody"] button > div { width:100% !important; justify-content:flex-start !important; }
      [data-testid="stPopoverBody"] button > div > span { flex:1; text-align:left !important; }
      [data-testid="stPopoverBody"] button [data-has-shortcut] {
        width:100% !important; justify-content:flex-start !important; gap:10px !important;
      }
      [data-testid="stPopoverBody"] button [data-testid="stIconMaterial"] { flex:0 0 16px; width:16px; }
      [data-testid="stPopoverBody"] button [data-testid="stMarkdownContainer"] { flex:1; text-align:left !important; }
      [data-testid="stPopoverBody"] button p { margin:0 !important; text-align:left !important; }
      [data-testid="stPopoverBody"] button:hover {
        color:#20252a !important; background:#f1f1f1 !important;
      }
      [data-testid="stPopoverBody"] button * { color:inherit !important; }
      [data-testid="stPopoverBody"] [class*="st-key-delete-chat-"] button { color:#dc4c4c !important; }
      [data-testid="stPopoverBody"] [class*="st-key-delete-chat-"] button:hover { background:#fff0f0 !important; }
            .st-key-main-theme-toggle button { min-width:36px; width:36px; height:36px; padding:0;
                color:#718078; background:#fff; border:1px solid var(--line); border-radius:10px; }
            .st-key-main-theme-toggle button:hover { background:#f2f7f3; color:var(--accent); border-color:#d7e6dc; }
      [class*="st-key-chat-actions-"] button:focus,
      [class*="st-key-chat-actions-"] button:focus-visible { border-color:#b8d8c7 !important; }
      [data-testid="stChatMessage"] { max-width:800px; margin:0 auto; background:transparent; padding:17px 0; gap:0; }
      [data-testid="stChatMessageAvatarUser"], [data-testid="stChatMessageAvatarAssistant"] { display:none; }
      [data-testid="stChatMessageContent"] { color:#344139; max-width:100%; font-size:17px; line-height:1.75; }
      [data-testid="stChatMessageContent"] p { margin:0 0 13px; }
      [data-testid="stChatMessageContent"] h1, [data-testid="stChatMessageContent"] h2,
      [data-testid="stChatMessageContent"] h3 { color:var(--ink); letter-spacing:-.025em; margin:12px 0 10px; }
      [data-testid="stChatMessageContent"] h3 { font-size:20px; }
      [data-testid="stChatMessageContent"] pre { background:#f2f5f2; border:1px solid var(--line); border-radius:12px; }
      [data-testid="stChatMessageContent"] .katex { color:#24342c; font-size:1.05em; }
      [data-testid="stChatMessageContent"] .katex-display { margin:1.1em 0; overflow-x:auto; overflow-y:hidden; }
      [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        display:flex; justify-content:flex-end; padding:10px 0 17px;
      }
      [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
        display:flex; align-items:center; box-sizing:border-box; flex:0 1 auto; min-height:42px;
        max-width:min(78%,660px); background:#e8f3ed; border:1px solid #dcebe2; border-radius:24px 24px 7px 24px;
        padding:10px 16px; color:#23382d; line-height:1.6; margin-left:auto !important; margin-right:0 !important;
      }
      [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
      [data-testid="stChatMessageContent"] [data-testid="stMarkdownContainer"] { margin-bottom:0 !important; }
      [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] p { margin:0; }
      [data-testid="stBottom"], [data-testid="stBottom"] > div {
        background:linear-gradient(180deg,#f4f6f300 0%,#f4f6f3e8 22%,var(--canvas) 100%) !important;
      }
      [data-testid="stBottomBlockContainer"] {
        box-sizing:border-box; width:100% !important; max-width:1000px !important; min-width:0; margin:0 auto !important;
        padding:20px 32px 25px !important;
      }
            [data-testid="stChatInput"] { height:54px !important; min-height:54px !important; background:#fff !important;
                border:1px solid #dce4de !important; border-radius:28px; box-shadow:0 5px 20px #18241f0c; }
      [data-testid="stChatInput"] > div,
      [data-testid="stChatInput"] [data-baseweb="textarea"],
      [data-testid="stChatInput"] [data-baseweb="textarea"] > div {
                height:40px !important; min-height:40px !important; display:flex !important; align-items:center !important;
                background:#fff !important; border-color:transparent !important;
        box-shadow:none !important;
      }
            [data-testid="stChatInput"] textarea { min-height:34px !important; height:34px !important;
                box-sizing:border-box !important; color:#19241f !important; background:transparent !important;
                padding:0 10px !important; font-size:16px; line-height:34px !important;
                transform:translateY(-6px); }
      [data-testid="stChatInput"] textarea::placeholder { color:#68776d !important; opacity:1; }
            [data-testid="stChatInput"] textarea { line-height:34px !important; }
            [data-testid="stChatInputSubmitButton"] { width:34px !important; height:34px !important; min-width:34px !important;
                min-height:34px !important; margin:0 8px 0 0 !important; align-self:center !important; display:grid !important;
                place-items:center !important; transform:translateY(-6px); }
      [data-testid="stChatInput"]:focus-within { border-color:#8fc1a8; box-shadow:0 0 0 3px #197a6514,0 5px 20px #18241f0c; }
      [data-testid="stChatInputSubmitButton"] { background:var(--accent); color:#fff; border-radius:11px; }
      [data-testid="stChatInputSubmitButton"]:hover { background:var(--accent-dark); }
      [data-testid="stDialog"] > div { background:#fff; border:1px solid var(--line);
        border-radius:20px; box-shadow:0 24px 80px #15271e26; }
      [data-testid="stDialog"] [data-testid="stForm"], .st-key-auth_card [data-testid="stForm"] { border:0; padding:0; }
      [data-testid="stDialog"] input, .st-key-auth_card input {
        min-height:44px; color:var(--ink); background:#fbfcfb !important;
        border:1px solid #dce4de !important; border-radius:11px !important; box-shadow:none !important;
      }
      [data-testid="stDialog"] input:disabled { color:#758279 !important; opacity:1 !important; -webkit-text-fill-color:#758279 !important; }
      [data-baseweb="input"] > div, [data-baseweb="textarea"] > div {
        background:#fbfcfb !important; border:1px solid #dce4de !important; border-radius:11px !important;
        box-shadow:none !important;
      }
      [data-baseweb="input"] input, [data-baseweb="textarea"] textarea { color:var(--ink) !important; }
      [data-baseweb="input"]:focus-within > div, [data-baseweb="textarea"]:focus-within > div {
        border-color:#88bda1 !important; box-shadow:0 0 0 3px #197a6514 !important;
      }
      [data-testid="stTextInputRootElement"] {
        min-height:44px !important; color:var(--ink) !important; background:#fbfcfb !important;
        border:1px solid #dce4de !important; border-radius:11px !important; box-shadow:none !important;
      }
      [data-testid="stTextInputRootElement"]:focus-within {
        border-color:#88bda1 !important; box-shadow:0 0 0 3px #197a6514 !important;
      }
      [data-testid="stTextInputRootElement"] input { color:var(--ink) !important; background:transparent !important; }
      [data-testid="stTextInputRootElement"] button { color:#64756b !important; background:transparent !important; }
      [data-testid="stDialog"] input:focus, .st-key-auth_card input:focus { border-color:#88bda1 !important; }
      [data-testid="stDialog"] [data-baseweb="select"] > div {
        min-height:44px; background:#fbfcfb; border-color:#dce4de; border-radius:11px;
      }
      [data-testid="stDialog"] [data-baseweb="tab-list"], .st-key-auth_card [data-baseweb="tab-list"] {
        gap:12px; border-bottom:1px solid var(--line); }
      [data-testid="stDialog"] [role="tab"], .st-key-auth_card [role="tab"] { padding:9px 12px; color:#69756e; }
      [data-testid="stDialog"] [role="tab"][aria-selected="true"],
      .st-key-auth_card [role="tab"][aria-selected="true"] { color:var(--accent-dark) !important; }
      [data-testid="stDialog"] button[kind="primary"], [data-testid="stDialog"] button[kind="primaryFormSubmit"],
      .st-key-auth_card button[kind="primary"], .st-key-auth_card button[kind="primaryFormSubmit"] {
        min-height:44px; background:var(--accent) !important; color:#fff !important;
        border-color:var(--accent) !important; border-radius:11px !important; font-weight:650;
      }
      [data-testid="stDialog"] button[kind="primary"]:hover,
      .st-key-auth_card button[kind="primaryFormSubmit"]:hover { background:var(--accent-dark) !important; }
      [data-testid="stDialog"] button[kind="primary"] *,
      [data-testid="stDialog"] button[kind="primaryFormSubmit"] *,
      .st-key-auth_card button[kind="primary"] *,
      .st-key-auth_card button[kind="primaryFormSubmit"] * { color:#fff !important; }
    .profile-avatar { color:#fff; background:linear-gradient(145deg,#248b70,#14654f); }
      .profile-avatar { flex:none; width:34px; height:34px; border-radius:50%; font-size:14px; font-weight:700; }
    .profile-row { display:flex; align-items:center; gap:10px; height:36px; min-height:36px; padding:0; transform:translateY(-8px); color:#35443b;
        font-size:14px; font-weight:600; overflow:hidden; }
      .profile-row span:last-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .st-key-welcome_shell { padding-top:clamp(28px,8vh,86px); }
      .welcome { display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; }
    .welcome h1 { margin:0 0 10px; color:var(--ink); font-size:clamp(28px,3vw,36px); font-weight:680; letter-spacing:-.045em; }
      .welcome p { color:#718078; max-width:520px; margin:0 0 22px; font-size:16px; line-height:1.65; }
            .welcome-mark { flex:none; width:54px; height:54px; border-radius:15px; object-fit:cover;
                box-shadow:0 9px 24px #197a6528; margin-bottom:16px; }
      .st-key-welcome_shell [data-testid="stChatInput"] { width:100%; }
      .st-key-temporary-banner { margin:-12px 0 14px; }
            .temporary-banner { display:flex; align-items:center; gap:7px; color:#19634f; background:var(--accent-soft);
                border:1px solid #d6eae0; border-radius:10px; padding:7px 10px; font-size:14px; }
      [class*="st-key-voice-controls-"] { display:flex; align-items:center; gap:8px; margin:0 auto 5px; max-width:800px; }
      [class*="st-key-voice-controls-"] [data-testid="stHorizontalBlock"] { align-items:center; }
      [class*="st-key-voice-controls-"] .stButton > button { width:34px; min-width:34px; height:34px; min-height:34px;
        padding:0; border-radius:50%; color:var(--muted); background:transparent; border:1px solid transparent; }
      [class*="st-key-voice-controls-"] .stButton > button:hover { color:var(--accent); background:var(--accent-soft); border-color:var(--line); }
      [class*="st-key-voice-controls-"] [data-testid="stAudioInput"] { flex:1; min-width:0; }
      [class*="st-key-voice-controls-"] [data-testid="stAudioInput"] label { font-size:14px; color:var(--muted); }
      [class*="st-key-voice-controls-"] button { color:#53645a !important; background:#fff !important; border-color:#dce4de !important; }
      [class*="st-key-voice-controls-"] button:hover { color:#12644f !important; background:#eaf5f0 !important; }
    .prompt-suggestions { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px;
        margin:28px auto 0; max-width:820px; color:#516057; font-size:14px; }
            .suggestion-card { min-height:76px; padding:12px 13px 11px; background:#fff; border:1px solid var(--line);
                border-radius:12px; box-shadow:0 3px 12px #18241f05; }
      .suggestion-card span { display:block; margin-bottom:8px; color:var(--accent); font-size:18px; }
      .suggestion-card strong { display:block; color:#35443b; font-size:14px; font-weight:620; line-height:1.4; }
      .conversation-state { display:inline-flex; margin:0 0 14px; color:#19634f; background:#eaf5f0;
        border:1px solid #d6eae0; border-radius:999px; padding:7px 12px; font-size:14px; font-weight:550; }
      .auth-brand { text-align:center; margin-bottom:22px; }
      .auth-brand .welcome-mark { margin:auto; }
    .auth-brand h1 { margin:13px 0 4px; color:var(--ink); font-size:29px; letter-spacing:-.04em; }
      .auth-brand p { margin:0; color:#7b8880; font-size:15px; }
    .st-key-auth_card { max-width:440px; margin:5vh auto 0; padding:26px 32px 28px;
        background:#fff; border:1px solid #e6ebe7; border-radius:22px; box-shadow:var(--shadow); }
      .st-key-auth-theme-toggle { position:fixed; top:16px; right:20px; z-index:50; width:auto; }
      .st-key-auth-theme-toggle button, .st-key-theme-toggle button {
        min-height:36px; width:auto; padding:0 12px; background:#fff; color:#53645a;
        border:1px solid var(--line); border-radius:10px; font-size:14px; font-weight:600;
      }
      .st-key-theme-toggle button { width:36px; min-width:36px; padding:0; }
      .st-key-auth-theme-toggle button:hover, .st-key-theme-toggle button:hover {
        background:var(--accent-soft) !important; color:var(--accent-dark); border-color:#cfe1d6;
      }
      [class*="st-key-message-actions-"] { max-width:800px; margin:-10px auto 14px; }
      [class*="st-key-message-actions-"] [data-testid="stHorizontalBlock"] {
        display:flex !important; flex-flow:row nowrap !important; gap:4px !important; align-items:center !important;
      }
      [class*="st-key-message-actions-"] [data-testid="stColumn"]:not(:has(.stButton, iframe, .branch-count)) {
        display:none !important;
      }
      [class*="st-key-message-actions-"] [data-testid="stColumn"] {
        flex:0 0 30px !important; width:30px !important; min-width:30px !important; padding:0 !important;
      }
      [class*="st-key-message-actions-"] [data-testid="stColumn"]:has(.branch-count) {
        flex:0 0 auto !important; width:auto !important; min-width:28px !important;
      }
      [class*="st-key-message-actions-"] iframe { display:block; height:34px !important; border:0 !important; }
      [class*="st-key-message-actions-user-"] [data-testid="stHorizontalBlock"] { justify-content:flex-end !important; }
      [class*="st-key-message-actions-assistant-"] [data-testid="stHorizontalBlock"] { justify-content:flex-start !important; }
      .branch-count { display:block; text-align:center; font-size:13px; color:var(--muted); font-variant-numeric:tabular-nums; }
      [data-testid="stChatMessage"]:has([data-testid="stForm"]) [data-testid="stChatMessageContent"] {
        flex:1 1 auto !important; width:100% !important; max-width:min(100%,660px) !important; display:block !important;
      }
      [data-testid="stChatMessage"] [data-testid="stForm"] { border:0 !important; padding:0 !important; }
      [data-testid="stChatMessage"] [data-testid="stForm"] [data-testid="stHorizontalBlock"] {
        display:flex !important; flex-flow:row nowrap !important; justify-content:flex-end !important; gap:8px !important;
      }
      [data-testid="stChatMessage"] [data-testid="stForm"] [data-testid="stColumn"] {
        flex:0 0 auto !important; width:auto !important; min-width:0 !important;
      }
      [data-testid="stChatMessage"] [data-testid="stForm"] button { width:auto !important; padding:4px 14px !important; white-space:nowrap !important; }
      [class*="st-key-message-actions-"] .stButton > button,
      [class*="st-key-message-actions-"] .stButton button,
      [class*="st-key-message-actions-"] [data-testid="stTooltipIcon"] button {
        box-sizing:border-box; min-width:30px !important; width:30px !important;
        min-height:30px !important; height:30px !important; padding:2px 6px !important;
        color:#758279 !important; background:transparent !important; background-image:none !important;
        border:1px solid transparent !important; border-radius:8px !important;
        box-shadow:none !important; font-size:13px;
      }
      [class*="st-key-message-actions-"] .stButton > button:hover,
      [class*="st-key-message-actions-"] .stButton button:hover,
      [class*="st-key-message-actions-"] [data-testid="stTooltipIcon"] button:hover {
        color:var(--accent-dark) !important; background:var(--accent-soft) !important;
        border-color:var(--line) !important;
      }
      [class*="st-key-message-actions-"] .stButton > button *,
      [class*="st-key-message-actions-"] .stButton button *,
      [class*="st-key-message-actions-"] [data-testid="stTooltipIcon"] button * { color:inherit !important; }
      .stVerticalBlock[class*="st-key-suggestion-"] { min-height:82px; padding:9px 11px 7px;
        background:#fff; border:1px solid var(--line); border-radius:16px; box-shadow:0 4px 16px #18241f08; }
      .stElementContainer[class*="st-key-suggestion-"] {
        padding:0 !important; margin:0 !important; background:transparent !important;
        border:0 !important; box-shadow:none !important;
      }
            .stElementContainer[class*="st-key-suggestion-"] .stButton > button {
                min-height:34px; justify-content:flex-start !important; padding:5px 7px; color:#29382f;
        background:transparent !important; border:0 !important; border-radius:9px; text-align:left;
        box-shadow:none !important;
      }
      .stElementContainer[class*="st-key-suggestion-"] .stButton > button:hover {
        color:var(--accent-dark); background:var(--accent-soft) !important;
      }
      .stElementContainer[class*="st-key-suggestion-"] .stButton > button > div {
        width:100%; justify-content:flex-start !important; text-align:left !important;
      }
      .stElementContainer[class*="st-key-suggestion-"] [data-testid="stMarkdownContainer"] {
        width:100%; text-align:left !important;
      }
      .stElementContainer[class*="st-key-suggestion-"] .stButton > button p {
        color:inherit !important; font-size:15px; font-weight:650; line-height:1.4; white-space:normal;
      }
            .stVerticalBlock[class*="st-key-suggestion-"] [data-testid="stCaptionContainer"] {
                margin:2px 0 0 !important; padding:0 7px; color:#647269; font-size:14px; line-height:1.45; letter-spacing:0; text-transform:none;
      }
      .st-key-quoted-reply-card { padding:10px 13px; margin:10px 0 14px; background:#eaf5f0;
        border:1px solid #d6eae0; border-left:3px solid var(--accent); border-radius:10px; }
      .st-key-quoted-reply-card [data-testid="stMarkdownContainer"] p { margin:0; color:#43534a; font-size:14px; }
      .st-key-quoted-reply-card .stButton { width:max-content; }
      .st-key-quoted-reply-card .stButton button {
        width:auto !important; min-width:0 !important; min-height:30px !important; height:30px !important;
        padding:3px 9px !important; color:#586a60 !important; background:transparent !important;
        border:1px solid #d6e6dc !important; border-radius:8px !important; box-shadow:none !important;
        font-size:13px !important;
      }
      .st-key-quoted-reply-card .stButton button:hover { color:var(--accent-dark) !important; background:#f3faf6 !important; }
      .st-key-quoted-reply-card .stButton button * { color:inherit !important; }
      .st-key-auth_card [data-testid="stWidgetLabel"], .st-key-auth_card [data-testid="stWidgetLabel"] p {
        color:#344239 !important; }
      .st-key-auth_card [data-testid="stTextInput"] input { border:0 !important; background:transparent !important; }
      .st-key-auth_card [data-testid="stTextInput"] button { color:#64756b !important; background:transparent !important; }
      .st-key-auth_card button[kind="primary"] p,
      .st-key-auth_card button[kind="primaryFormSubmit"] p { color:#fff !important; }
      body:has(.st-key-auth_card) .block-container { max-width:540px; min-height:90vh; padding-top:20px; }
      body:has(.st-key-auth_card) [data-testid="stAppViewContainer"] {
        background:radial-gradient(ellipse at 50% 8%,#e8f3ed 0,transparent 36%),var(--canvas);
      }
      .composer-disclaimer { position:fixed; left:calc(50% + 138px); transform:translateX(-50%);
        bottom:6px; z-index:999; color:#89948d; font-size:12px; white-space:nowrap; }
      @media (max-width:850px) {
        .block-container { padding-left:20px; padding-right:20px; }
        [data-testid="stBottomBlockContainer"] { padding-left:20px !important; padding-right:20px !important; }
        .prompt-suggestions { grid-template-columns:1fr; max-width:520px; }
        .suggestion-card { min-height:auto; }
        .composer-disclaimer { left:50%; }
        [class*="st-key-message-actions-"] [data-testid="stHorizontalBlock"] {
          display:flex !important; flex-flow:row nowrap !important; gap:6px !important;
        }
        [class*="st-key-message-actions-"] [data-testid="stColumn"]:not(:has(.stButton, iframe)) {
          display:none !important;
        }
        [class*="st-key-message-actions-"] [data-testid="stColumn"]:has(.stButton, iframe) {
          flex:0 0 30px !important; width:30px !important; min-width:30px !important; padding:0 !important;
        }
        [class*="st-key-message-actions-"] iframe { display:block; height:34px !important; border:0 !important; }
        [class*="st-key-message-actions-user-"] [data-testid="stHorizontalBlock"] { justify-content:flex-end !important; }
        [class*="st-key-message-actions-assistant-"] [data-testid="stHorizontalBlock"] { justify-content:flex-start !important; }
      }
      @media (max-width:560px) {
        .block-container { padding:18px 15px 116px; }
        [data-testid="stBottomBlockContainer"] { padding:14px 15px 22px !important; }
        [data-testid="stChatMessage"] { padding:13px 0; }
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] { max-width:90%; }
        .st-key-auth_card { margin:2vh auto 0; padding:25px 22px 26px; }
        .st-key-welcome_shell { padding-top:24px; }
        .st-key-app_topbar_shell { top:12px; right:14px; width:82px; }
                .st-key-app_topbar_shell .st-key-main-theme-toggle { position:absolute; top:0; right:0; z-index:2; }
      }
    </style>
    """,
    unsafe_allow_html=True,
)

if st.session_state.get("dark_mode", True):
    st.markdown(
        """
        <style>
          :root {
            color-scheme:dark; --canvas:#101714; --surface:#171f1b; --ink:#eaf1ec;
            --muted:#a5b1a9; --line:#2b3931; --accent:#65c5a0; --accent-dark:#48ad86;
            --accent-soft:#1d352a; --shadow:0 18px 44px rgba(0,0,0,.28);
          }
          html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"],
          [data-testid="stMain"], section.main { background:var(--canvas) !important; color:var(--ink) !important; }
          [data-testid="stSidebar"], [data-testid="stSidebar"] > div:first-child,
          [data-testid="stSidebar"] [data-testid="stSidebarContent"] { background:var(--surface) !important; }
          [data-testid="stSidebar"] { border-color:var(--line) !important; }
          [data-testid="stHeader"] [data-testid="stExpandSidebarButton"] { background:var(--surface); border-color:var(--line); }
          [data-testid="stHeader"] [data-testid="stExpandSidebarButton"] button { color:var(--ink); }
          [data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] p,
          [data-testid="stMarkdownContainer"] p, [data-testid="stCaptionContainer"],
          [data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color:var(--ink) !important; }
          [data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color:#9eaaa2 !important; }
          [data-testid="stSidebar"] .stButton > button { color:#d5e0d8; }
          [data-testid="stSidebar"] .stButton > button:hover { background:#222e27; border-color:#2e3d34; color:#f2f7f3; }
          [data-testid="stSidebar"] button[kind="primary"] { background:#20392e !important; border-color:#355744 !important; color:#a6e6c7 !important; }
          .sidebar-brand, .topbar-copy strong, .welcome h1, .auth-brand h1 { color:var(--ink) !important; }
          .topbar-copy small, .welcome p, .auth-brand p, .sidebar-empty { color:#9ba9a0 !important; }
          .app-topbar { border-color:var(--line); }
          .st-key-sidebar_footer { background:linear-gradient(180deg,#171f1bd9,#171f1b 22%); border-color:var(--line); }
          .profile-row { color:#d9e4dc; }
          .st-key-open-settings button, .st-key-theme-toggle button,
          .st-key-main-theme-toggle button, .st-key-conversation_menu button,
          [class*="st-key-chat-actions-"] > div > button { color:#c2d0c7; background:transparent; border-color:transparent; }
          .st-key-app_topbar_shell .stButton > button { color:#c2d0c7 !important; background:#202a24 !important; border-color:#35433a !important; }
          .st-key-app_topbar_shell .stButton > button:hover { color:#a8e3c5 !important; background:#26382e !important; }
          .st-key-app_topbar_shell .st-key-temporary-toggle button[kind="primary"] { color:#102018 !important; background:#65c5a0 !important; border-color:#65c5a0 !important; }
          .st-key-app_topbar_shell .st-key-temporary-toggle button[kind="primary"] * { color:#102018 !important; }
          .st-key-auth-theme-toggle button { color:#c2d0c7; background:#202a24; border-color:#35433a; }
          .st-key-open-settings button:hover, .st-key-theme-toggle button:hover,
          .st-key-main-theme-toggle button:hover,
          .st-key-auth-theme-toggle button:hover, .st-key-conversation_menu button:hover,
          [class*="st-key-chat-actions-"] > div > button:hover {
            color:#8cdbb7; background:#26382e !important; border-color:#3a5b47;
          }
          [data-testid="stChatMessageContent"] { color:#dbe5de; }
          [data-testid="stChatMessageContent"] h1, [data-testid="stChatMessageContent"] h2,
          [data-testid="stChatMessageContent"] h3 { color:#f0f5f1; }
          [data-testid="stChatMessageContent"] pre { background:#17201b; border-color:#2c3931; }
          [data-testid="stChatMessageContent"] .katex { color:#e9f1eb; }
          [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
          [data-testid="stChatMessageContent"] { color:#e4f1e9; background:#203a2e; border-color:#2f4e3e; }
          [data-testid="stBottom"], [data-testid="stBottom"] > div {
            background:linear-gradient(180deg,#10171400 0%,#101714e8 22%,var(--canvas) 100%) !important;
          }
          [data-testid="stChatInput"] { background:#18211c !important; border-color:#35443a !important; }
          [data-testid="stChatInput"] > div,
          [data-testid="stChatInput"] [data-baseweb="textarea"],
          [data-testid="stChatInput"] [data-baseweb="textarea"] > div {
            background:#18211c !important; border-color:transparent !important;
          }
          [data-testid="stChatInput"] textarea { color:#eef4ef !important; background:transparent !important; }
          [data-testid="stChatInput"] textarea::placeholder { color:#afbbb2 !important; }
          [data-testid="stChatInput"]:focus-within { border-color:#65b994; box-shadow:0 0 0 3px #65c5a01a; }
          [data-testid="stChatInputSubmitButton"] { background:var(--accent); color:#102018; }
          [class*="st-key-voice-controls-"] .stButton > button { color:#aebbb2; background:transparent; border-color:transparent; }
          [class*="st-key-voice-controls-"] .stButton > button:hover { color:#a8e3c5; background:#1d352a; border-color:#35443a; }
          [class*="st-key-voice-controls-"] button { color:#aebbb2 !important; background:#202a24 !important; border-color:#35443a !important; }
          [class*="st-key-voice-controls-"] button:hover { color:#a8e3c5 !important; background:#1d352a !important; }
          [class*="st-key-voice-controls-"] [data-testid="stAudioInput"] label { color:#aebbb2; }
          [role="tooltip"], [data-testid="stTooltipContent"], [data-baseweb="tooltip"] {
            color:#e8f1eb !important; background:#19221d !important; border:1px solid #334139 !important;
          }
          [data-testid="stDialog"] > div { background:#19221d; border-color:#334139; }
          [data-testid="stDialog"] [data-testid="stWidgetLabel"],
          [data-testid="stDialog"] [data-testid="stWidgetLabel"] p,
          [data-testid="stDialog"] [data-testid="stMarkdownContainer"] p { color:#e5eee8 !important; }
          [data-testid="stDialog"] input, .st-key-auth_card input {
            color:#edf4ef !important; background:#111915 !important; border-color:#39483f !important;
          }
          .st-key-auth_card [data-testid="stWidgetLabel"],
          .st-key-auth_card [data-testid="stWidgetLabel"] p { color:#e5eee8 !important; }
          .st-key-auth_card [data-testid="stTextInput"] input { color:#edf4ef !important; border:0 !important; }
          .st-key-auth_card [data-testid="stTextInput"] button { color:#bac8bf !important; background:transparent !important; }
          [data-testid="stDialog"] [data-baseweb="input"] > div,
          [data-testid="stDialog"] [data-baseweb="textarea"] > div,
          .st-key-auth_card [data-baseweb="input"] > div,
          .st-key-auth_card [data-baseweb="textarea"] > div {
            background:#111915 !important; border-color:#39483f !important;
          }
          [data-testid="stDialog"] [data-baseweb="input"] input,
          .st-key-auth_card [data-baseweb="input"] input { color:#edf4ef !important; }
          [data-testid="stDialog"] [data-baseweb="select"] > div { background:#111915; border-color:#39483f; }
          [data-testid="stTextInputRootElement"] { color:#edf4ef !important; background:#111915 !important; border-color:#39483f !important; }
          [data-testid="stTextInputRootElement"] input { color:#edf4ef !important; background:transparent !important; }
          [data-testid="stTextInputRootElement"] button { color:#bac8bf !important; background:transparent !important; }
          [data-testid="stDialog"] [data-baseweb="tab-list"], .st-key-auth_card [data-baseweb="tab-list"] { border-color:#334139; }
          [data-testid="stDialog"] [role="tab"], .st-key-auth_card [role="tab"] { color:#b1beb5; }
          [data-testid="stDialog"] button[kind="primary"] p,
          [data-testid="stDialog"] button[kind="primaryFormSubmit"] p,
          .st-key-auth_card button[kind="primary"] p,
          .st-key-auth_card button[kind="primaryFormSubmit"] p { color:#fff !important; }
          [data-testid="stDialog"] button[kind="primary"] *,
          [data-testid="stDialog"] button[kind="primaryFormSubmit"] *,
          .st-key-auth_card button[kind="primary"] *,
          .st-key-auth_card button[kind="primaryFormSubmit"] * { color:#fff !important; }
          .st-key-auth_card { background:#19221d; border-color:#334139; }
          body:has(.st-key-auth_card) [data-testid="stAppViewContainer"] {
            background:radial-gradient(ellipse at 50% 8%,#1a3629 0,transparent 36%),var(--canvas) !important;
          }
          .suggestion-card { background:#18211c; border-color:#2d3a32; box-shadow:none; }
          .suggestion-card strong { color:#d9e4dc; }
          .stVerticalBlock[class*="st-key-suggestion-"] { background:#18211c; border-color:#2d3a32; box-shadow:none; }
          .stElementContainer[class*="st-key-suggestion-"] {
            background:transparent !important; border:0 !important; box-shadow:none !important;
          }
          .stElementContainer[class*="st-key-suggestion-"] .stButton > button {
            color:#e2ece5; background:transparent !important; border:0 !important; box-shadow:none !important;
          }
          .stElementContainer[class*="st-key-suggestion-"] .stButton > button:hover { background:#203a2e !important; }
          .stVerticalBlock[class*="st-key-suggestion-"] [data-testid="stCaptionContainer"] { color:#c0cbc3 !important; }
          .st-key-quoted-reply-card { background:#1d352a; border-color:#345440; }
          .st-key-quoted-reply-card [data-testid="stMarkdownContainer"] p { color:#d9e7de !important; }
          .st-key-quoted-reply-card .stButton button { color:#c1d0c7 !important; border-color:#40594a !important; }
          .st-key-quoted-reply-card .stButton button:hover { color:#d9efe2 !important; background:#284335 !important; }
          [class*="st-key-message-actions-"] .stButton > button,
          [class*="st-key-message-actions-"] .stButton button,
          [class*="st-key-message-actions-"] [data-testid="stTooltipIcon"] button { color:#9eaca2 !important; }
          [class*="st-key-message-actions-"] .stButton > button:hover,
          [class*="st-key-message-actions-"] .stButton button:hover,
          [class*="st-key-message-actions-"] [data-testid="stTooltipIcon"] button:hover { color:#a8e3c5 !important; }
          .conversation-state { color:#a8e3c5; background:#1d352a; border-color:#345440; }
          .composer-disclaimer { color:#86968b; }
          [role="listbox"], [role="option"] { background:#19221d !important; color:#e5eee8 !important; }
          [data-testid="stPopover"] > div, [data-testid="stPopoverBody"] { background:#19221d; border-color:#334139; }
          [data-testid="stPopoverBody"] { background:#202020 !important; border-color:#383838 !important; }
          [data-testid="stPopoverBody"] button { color:#ececec !important; }
          [data-testid="stPopoverBody"] button:hover { color:#fff !important; background:#303030 !important; }
          [data-testid="stPopoverBody"] [class*="st-key-delete-chat-"] button { color:#ff7878 !important; }
          [data-testid="stPopoverBody"] [class*="st-key-delete-chat-"] button:hover { color:#ffadad !important; background:#382424 !important; }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] { background:#19221d !important; border-color:#334139 !important; }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] button { color:#d7e4dc !important; }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] button:hover { color:#a8e3c5 !important; background:#203a2e !important; }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] [class*="st-key-delete-chat-"] button { color:#ff8b8f !important; }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] [class*="st-key-delete-chat-"] button:hover { color:#ffb0b4 !important; background:#42272a !important; }
          @media (max-width:850px) {
            [data-testid="stBottom"], [data-testid="stBottom"] > div {
              background:linear-gradient(180deg,#10171400 0%,#101714e8 22%,var(--canvas) 100%) !important;
            }
          }
        </style>
        """,
        unsafe_allow_html=True,
    )

st.markdown(
    f"""
        <style>
    .st-key-theme-light {{ display:none; }}
            body:has(.st-key-theme-light) [data-testid="stDialog"] > div,
            body:has(.st-key-theme-light) [role="dialog"] {{ color:#19241f !important; background:#fff !important; border-color:#e4e9e5 !important; }}
            body:has(.st-key-theme-light) [data-testid="stDialog"] button:not([kind="primary"]):not([kind="primaryFormSubmit"]),
            body:has(.st-key-theme-light) [role="dialog"] button:not([kind="primary"]):not([kind="primaryFormSubmit"]) {{
                color:#53645a !important; background:#fff !important; border-color:#e4e9e5 !important;
            }}
            body:has(.st-key-theme-light) [data-testid="stDialog"] [role="tab"],
            body:has(.st-key-theme-light) [role="dialog"] [role="tab"] {{ color:#69756e !important; }}
            body:has(.st-key-theme-light) [data-testid="stDialog"] button[kind="secondaryFormSubmit"],
            body:has(.st-key-theme-light) [role="dialog"] button[kind="secondaryFormSubmit"] {{
                color:#fff !important; background:var(--accent) !important; border-color:var(--accent) !important;
            }}
            body:has(.st-key-theme-light) [data-testid="stDialog"] *,
            body:has(.st-key-theme-light) [role="dialog"] *,
            body:has(.st-key-theme-light) [data-testid="stPopover"] *,
            body:has(.st-key-theme-light) [data-testid="stPopoverBody"] * {{
                color:#24332b !important;
            }}
            body:has(.st-key-theme-light) [data-testid="stDialog"],
            body:has(.st-key-theme-light) [data-testid="stDialog"] > div,
            body:has(.st-key-theme-light) [role="dialog"],
            body:has(.st-key-theme-light) [data-testid="stPopover"],
            body:has(.st-key-theme-light) [data-testid="stPopoverBody"],
            body:has(.st-key-theme-light) [role="listbox"] {{
                color:#24332b !important; background:#fff !important; border-color:#e1e8e3 !important;
            }}
            body:has(.st-key-theme-light) [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] button {{
                color:#39483f !important; background:transparent !important;
            }}
            body:has(.st-key-theme-light) [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] button:hover {{
                color:#12644f !important; background:#eaf5f0 !important;
            }}
            body:has(.st-key-theme-light) [data-testid="stPopoverBody"]:has([class*="st-key-rename-chat-"]) {{
                background:#fff !important; border-color:#e1e5e2 !important;
            }}
            body:has(.st-key-theme-light) [data-testid="stPopoverBody"]:has([class*="st-key-rename-chat-"]) button {{
                color:#343a40 !important; background:transparent !important;
            }}
            body:has(.st-key-theme-light) [data-testid="stPopoverBody"]:has([class*="st-key-rename-chat-"]) button:hover {{
                color:#20252a !important; background:#f1f1f1 !important;
            }}
            body:has(.st-key-theme-light) [data-testid="stPopoverBody"]:has([class*="st-key-rename-chat-"]) [class*="st-key-delete-chat-"] button {{ color:#dc4c4c !important; }}
            body:has(.st-key-theme-light) [data-testid="stPopover"] button:hover,
            body:has(.st-key-theme-light) [data-testid="stPopoverBody"] button:hover,
            body:has(.st-key-theme-light) [role="dialog"] button:hover {{
                color:#12644f !important; background:#eaf5f0 !important;
            }}
            body:has(.st-key-theme-light) [data-testid="stDialog"] button[kind="primary"],
            body:has(.st-key-theme-light) [data-testid="stDialog"] button[kind="primaryFormSubmit"],
            body:has(.st-key-theme-light) [role="dialog"] button[kind="primary"],
            body:has(.st-key-theme-light) [role="dialog"] button[kind="primaryFormSubmit"] {{
                color:#fff !important; background:var(--accent) !important; border-color:var(--accent) !important;
            }}
            body:has(.st-key-theme-light) [data-testid="stDialog"] button[kind="primary"] *,
            body:has(.st-key-theme-light) [data-testid="stDialog"] button[kind="primaryFormSubmit"] *,
            body:has(.st-key-theme-light) [role="dialog"] button[kind="primary"] *,
            body:has(.st-key-theme-light) [role="dialog"] button[kind="primaryFormSubmit"] * {{ color:#fff !important; }}
            body:has(.st-key-theme-light) [data-testid="stDialog"] input,
            body:has(.st-key-theme-light) [data-testid="stDialog"] textarea,
            body:has(.st-key-theme-light) [role="dialog"] input,
            body:has(.st-key-theme-light) [role="dialog"] textarea {{
                color:#24332b !important; background:#fbfcfb !important; border-color:#dce4de !important;
            }}
            body:has(.st-key-theme-light) [class*="st-key-voice-controls-"] .stButton > button {{
                color:#53645a !important; background:#fff !important; border:1px solid #dce4de !important;
            }}
            body:has(.st-key-theme-light) [class*="st-key-voice-controls-"] .stButton > button:hover {{
                color:#12644f !important; background:#eaf5f0 !important; border-color:#cfe1d6 !important;
            }}
            body:has(.st-key-theme-light) [role="tooltip"],
            body:has(.st-key-theme-light) [data-testid="stTooltipContent"],
            body:has(.st-key-theme-light) [data-baseweb="tooltip"] {{
                color:#24332b !important; background:#fff !important; border:1px solid #dce4de !important;
                box-shadow:0 8px 24px rgba(28,48,38,.12) !important;
            }}
            .st-key-sidebar-brand-toggle button, .st-key-main-brand-toggle button {{
                width:30px; min-width:30px; height:30px; padding:0; border:0; border-radius:8px;
                background-color:transparent !important; background-image:url('{BRAND_MARK}') !important;
                background-size:cover !important; background-position:center !important;
            }}
            .st-key-sidebar-brand-toggle button p, .st-key-main-brand-toggle button p {{ display:none; }}
        </style>
        """,
    unsafe_allow_html=True,
)
if not st.session_state.dark_mode:
    st.markdown("<div class='st-key-theme-light'></div>", unsafe_allow_html=True)

# Keep theme-specific surfaces consistent, including widgets rendered through
# Streamlit's portals (popover, dialog, select menu, and tooltips).
if st.session_state.dark_mode:
    st.markdown(
        """
        <style>
          :root {
            color-scheme: dark; --canvas:#212121; --surface:#171717; --ink:#ececec;
            --muted:#a0a0a0; --line:#343434; --accent:#65c5a0; --accent-dark:#48ad86;
            --accent-soft:#2b2b2b;
          }
          .st-key-composer-shell {
            position:fixed !important; z-index:110 !important; left:calc(276px + max(0px,(100vw - 1276px)/2)) !important;
            bottom:0 !important; width:min(1000px,calc(100vw - 276px)) !important; box-sizing:border-box !important;
            margin:0 !important; padding:12px 32px 24px !important;
            background:linear-gradient(180deg,#21212100 0%,#212121e8 25%,#212121 100%) !important;
          }
          .st-key-composer-shell [data-testid="stHorizontalBlock"] {
            display:flex !important; flex-wrap:nowrap !important; align-items:center !important; gap:1px !important;
            min-height:50px !important; padding:4px !important; box-sizing:border-box !important;
            border:1px solid #414141 !important; border-radius:30px !important; background:#303030 !important;
            box-shadow:0 5px 20px rgba(0,0,0,.16) !important;
          }
          .st-key-composer-shell [data-testid="stColumn"] { display:flex !important; align-items:center !important; min-width:0 !important; padding:0 !important; }
          .st-key-composer-shell [data-testid="stColumn"]:nth-child(2) { flex:1 1 auto !important; }
          .st-key-composer-shell [data-testid="stColumn"]:nth-child(1), .st-key-composer-shell [data-testid="stColumn"]:nth-child(3) { flex:0 0 32px !important; width:32px !important; }
          .st-key-composer-shell [data-testid="stColumn"]:last-child { flex:0 0 36px !important; width:36px !important; }
          .st-key-composer-shell [data-testid="stTextInputRootElement"],
          .st-key-composer-shell [data-baseweb="input"] > div {
            width:100% !important; min-height:36px !important; height:36px !important; padding:0 !important;
            color:#f0f0f0 !important; background:transparent !important; border:0 !important;
            border-radius:0 !important; box-shadow:none !important;
          }
          .st-key-composer-shell input { color:#f0f0f0 !important; background:transparent !important; border:0 !important; box-shadow:none !important; padding:0 4px !important; font-size:16px !important; }
          .st-key-composer-shell input::placeholder { color:#aaa !important; opacity:1 !important; }
          .st-key-composer-shell button {
            display:grid !important; place-items:center !important; width:34px !important; min-width:34px !important;
            height:34px !important; min-height:34px !important; padding:0 !important; border-radius:50% !important;
            box-shadow:none !important;
          }
          .st-key-composer-shell [data-testid="stColumn"]:nth-child(1) button, .st-key-composer-shell [data-testid="stColumn"]:nth-child(3) button {
            color:#c4c4c4 !important; background:transparent !important; border:0 !important;
            width:30px !important; min-width:30px !important; height:30px !important; min-height:30px !important;
          }
          .st-key-composer-shell [data-testid="stColumn"]:nth-child(1) button:hover, .st-key-composer-shell [data-testid="stColumn"]:nth-child(3) button:hover { color:#fff !important; background:transparent !important; }
          .st-key-composer-shell [data-testid="stColumn"]:last-child button {
            color:#102018 !important; background:#65c5a0 !important; border:0 !important;
          }
          .st-key-composer-shell [data-testid="stColumn"]:last-child button:hover { background:#78d5b0 !important; }
          .st-key-composer-shell [data-testid="stAudioInput"] { margin:8px 4px 0 !important; padding:10px 12px !important; border:1px solid #3d3d3d !important; border-radius:14px !important; background:#292929 !important; }
          .st-key-composer-shell [data-testid="stPopover"], .st-key-composer-shell [data-testid="stPopover"] > div { width:auto !important; background:transparent !important; border:0 !important; box-shadow:none !important; }
          .st-key-composer-shell [data-testid="stPopoverButton"] div[aria-hidden="true"] { display:none !important; }
          .st-key-composer-shell [data-testid="stAudioInput"] label { color:#ddd !important; }
          @media(max-width:850px) { .st-key-composer-shell { padding:12px 20px 22px !important; } }
          @media(max-width:560px) { .st-key-composer-shell { left:0 !important; width:100vw !important; padding:10px 15px 18px !important; } }
          @media(min-width:561px) {
            body:has([data-testid="stExpandSidebarButton"]) .st-key-composer-shell {
              left:max(0px,calc((100vw - 1000px)/2)) !important; width:min(1000px,100vw) !important;
            }
          }
          html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"],
          [data-testid="stMain"], section.main { background:var(--canvas) !important; color:var(--ink) !important; }
          [data-testid="stSidebar"], [data-testid="stSidebar"] > div:first-child,
          [data-testid="stSidebar"] [data-testid="stSidebarContent"] { background:var(--surface) !important; }
          [data-testid="stSidebar"] { border-color:#2a2a2a !important; }
          [data-testid="stSidebar"] .stButton > button { color:#dedede !important; }
          [data-testid="stSidebar"] .stButton > button:hover { background:#242424 !important; border-color:#303030 !important; color:#fff !important; }
          [data-testid="stSidebar"] button[kind="primary"] { background:#292929 !important; border-color:#383838 !important; color:#f3f3f3 !important; }
          [data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color:#858585 !important; }
          [data-testid="stHeader"] [data-testid="stExpandSidebarButton"] { background:#202020 !important; border-color:#393939 !important; }
          [data-testid="stHeader"] [data-testid="stExpandSidebarButton"] button { color:#e7e7e7 !important; }
          .sidebar-brand, .topbar-copy strong, .welcome h1, .auth-brand h1 { color:#f3f3f3 !important; }
          .topbar-copy small, .welcome p, .auth-brand p, .sidebar-empty { color:#a0a0a0 !important; }
          .st-key-sidebar_footer { background:linear-gradient(180deg,#171717d9,#171717 22%) !important; border-color:#303030 !important; }
          .profile-row { color:#e0e0e0 !important; }
          .st-key-app_topbar_shell .stButton > button,
          .st-key-open-settings button, .st-key-theme-toggle button,
          .st-key-main-theme-toggle button { color:#dedede !important; background:#262626 !important; border-color:#3a3a3a !important; }
          .st-key-app_topbar_shell .stButton > button:hover,
          .st-key-open-settings button:hover, .st-key-theme-toggle button:hover,
          .st-key-main-theme-toggle button:hover { color:#fff !important; background:#333 !important; border-color:#454545 !important; }
          .st-key-app_topbar_shell .st-key-temporary-toggle button[kind="primary"] { color:#102018 !important; background:#65c5a0 !important; border-color:#65c5a0 !important; }
          .st-key-app_topbar_shell .st-key-temporary-toggle button[kind="primary"] * { color:#102018 !important; }
          [data-testid="stChatMessageContent"] { color:#e5e5e5 !important; }
          [data-testid="stChatMessageContent"] h1, [data-testid="stChatMessageContent"] h2,
          [data-testid="stChatMessageContent"] h3 { color:#f4f4f4 !important; }
          [data-testid="stChatMessageContent"] pre { background:#1b1b1b !important; border-color:#363636 !important; }
          [data-testid="stChatMessageContent"] .katex { color:#eee !important; }
          [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
            color:#f0f0f0 !important; background:#303030 !important; border-color:#3b3b3b !important;
          }
          [data-testid="stBottom"], [data-testid="stBottom"] > div {
            background:linear-gradient(180deg,#21212100 0%,#212121e8 22%,#212121 100%) !important;
          }
          [data-testid="stChatInput"] {
            color:#f0f0f0 !important; background:#303030 !important; border:1px solid #303030 !important;
            border-radius:28px !important; overflow:hidden !important;
          }
          [data-testid="stChatInput"] > div,
          [data-testid="stChatInput"] [data-baseweb="textarea"],
          [data-testid="stChatInput"] [data-baseweb="textarea"] > div {
            color:#f0f0f0 !important; background:transparent !important; border:0 !important;
            border-radius:inherit !important; box-shadow:none !important;
          }
          [data-testid="stChatInput"] textarea { color:#f0f0f0 !important; background:transparent !important; border:0 !important; }
          [data-testid="stChatInput"] textarea::placeholder { color:#aaa !important; }
          [data-testid="stChatInput"]:focus-within { border-color:#65b994 !important; box-shadow:0 0 0 3px #65c5a01a !important; }
          [class*="st-key-voice-controls-"] button { color:#ddd !important; background:#292929 !important; border-color:#3a3a3a !important; }
          [class*="st-key-voice-controls-"] button:hover { color:#fff !important; background:#363636 !important; }
          [class*="st-key-voice-controls-"] [data-testid="stAudioInput"] label { color:#ddd !important; }
          [role="tooltip"], [data-testid="stTooltipContent"], [data-baseweb="tooltip"] {
            color:#eee !important; background:#292929 !important; border:1px solid #414141 !important;
          }
          [data-testid="stDialog"] > div, [role="dialog"] { color:#eee !important; background:#212121 !important; border-color:#393939 !important; }
          [data-testid="stDialog"] [data-testid="stWidgetLabel"], [data-testid="stDialog"] [data-testid="stWidgetLabel"] p,
          [data-testid="stDialog"] [data-testid="stMarkdownContainer"] p { color:#e5e5e5 !important; }
          [data-testid="stDialog"] input, [data-testid="stDialog"] textarea,
          [data-testid="stDialog"] [data-baseweb="select"] > div {
            color:#eee !important; background:#2b2b2b !important; border-color:#454545 !important;
          }
          [data-testid="stDialog"] button:not([kind="primary"]):not([kind="primaryFormSubmit"]) { color:#ddd !important; background:#292929 !important; border-color:#404040 !important; }
          [data-testid="stDialog"] button[kind="primary"], [data-testid="stDialog"] button[kind="primaryFormSubmit"] { color:#102018 !important; background:#65c5a0 !important; border-color:#65c5a0 !important; }
          [data-testid="stPopover"], [data-testid="stPopoverBody"] { color:#eee !important; background:#2b2b2b !important; border-color:#3d3d3d !important; }
          [class*="st-key-chat-actions-"] [data-testid="stPopover"],
          [class*="st-key-chat-actions-"] [data-testid="stPopover"] > div {
            background:transparent !important; background-color:transparent !important; border:0 !important; box-shadow:none !important;
          }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"],
          [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:hover,
          [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:focus,
          [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:focus-visible,
          [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"][aria-expanded="true"] {
            color:#bdbdbd !important; background:transparent !important; background-color:transparent !important;
            border:0 !important; box-shadow:none !important; outline:none !important;
          }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] { padding:6px !important; }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] button {
            min-height:36px !important; height:36px !important; padding:0 10px !important; border:0 !important;
            border-radius:7px !important; color:#e5e5e5 !important; background:transparent !important; box-shadow:none !important;
          }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] button:hover { color:#fff !important; background:#3a3a3a !important; }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] [class*="st-key-delete-chat-"] button { color:#ff7777 !important; }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] [class*="st-key-delete-chat-"] button:hover { color:#ffaaaa !important; background:#422929 !important; }
          [role="listbox"], [role="option"] { color:#eee !important; background:#292929 !important; }
          .suggestion-card, .stVerticalBlock[class*="st-key-suggestion-"] { color:#eee !important; background:#292929 !important; border-color:#393939 !important; box-shadow:none !important; }
          .suggestion-card strong { color:#f3f3f3 !important; }
          .stVerticalBlock[class*="st-key-suggestion-"] [data-testid="stCaptionContainer"] { color:#aaa !important; }
          @media (max-width:850px) {
            [data-testid="stBottom"], [data-testid="stBottom"] > div { background:linear-gradient(180deg,#21212100 0%,#212121e8 22%,#212121 100%) !important; }
          }
        </style>
        """,
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        """
        <style>
          :root { color-scheme:light; --canvas:#fff; --surface:#f9f9f9; --ink:#171717; --muted:#6b6b6b; --line:#e5e5e5; --accent:#197a65; --accent-dark:#12644f; --accent-soft:#eaf5f0; }
          .st-key-composer-shell {
            position:fixed !important; z-index:110 !important; left:calc(276px + max(0px,(100vw - 1276px)/2)) !important;
            bottom:0 !important; width:min(1000px,calc(100vw - 276px)) !important; box-sizing:border-box !important;
            margin:0 !important; padding:12px 32px 24px !important;
            background:linear-gradient(180deg,#fff0 0%,#ffffffe8 25%,#fff 100%) !important;
          }
          .st-key-composer-shell [data-testid="stHorizontalBlock"] {
            display:flex !important; flex-wrap:nowrap !important; align-items:center !important; gap:1px !important;
            min-height:50px !important; padding:4px !important; box-sizing:border-box !important;
            border:1px solid #e1e5e2 !important; border-radius:30px !important; background:#fff !important;
            box-shadow:0 5px 20px rgba(20,35,26,.08) !important;
          }
          .st-key-composer-shell [data-testid="stColumn"] { display:flex !important; align-items:center !important; min-width:0 !important; padding:0 !important; }
          .st-key-composer-shell [data-testid="stColumn"]:nth-child(2) { flex:1 1 auto !important; }
          .st-key-composer-shell [data-testid="stColumn"]:nth-child(1), .st-key-composer-shell [data-testid="stColumn"]:nth-child(3) { flex:0 0 32px !important; width:32px !important; }
          .st-key-composer-shell [data-testid="stColumn"]:last-child { flex:0 0 36px !important; width:36px !important; }
          .st-key-composer-shell [data-testid="stTextInputRootElement"], .st-key-composer-shell [data-baseweb="input"] > div {
            width:100% !important; min-height:36px !important; height:36px !important; padding:0 !important;
            color:#171717 !important; background:transparent !important; border:0 !important; border-radius:0 !important; box-shadow:none !important;
          }
          .st-key-composer-shell input { color:#171717 !important; background:transparent !important; border:0 !important; box-shadow:none !important; padding:0 4px !important; font-size:16px !important; }
          .st-key-composer-shell input::placeholder { color:#737373 !important; opacity:1 !important; }
          .st-key-composer-shell button { display:grid !important; place-items:center !important; width:34px !important; min-width:34px !important; height:34px !important; min-height:34px !important; padding:0 !important; border-radius:50% !important; box-shadow:none !important; }
          .st-key-composer-shell [data-testid="stColumn"]:nth-child(1) button, .st-key-composer-shell [data-testid="stColumn"]:nth-child(3) button { width:30px !important; min-width:30px !important; height:30px !important; min-height:30px !important; color:#555 !important; background:transparent !important; border:0 !important; }
          .st-key-composer-shell [data-testid="stColumn"]:nth-child(1) button:hover, .st-key-composer-shell [data-testid="stColumn"]:nth-child(3) button:hover { color:#171717 !important; background:transparent !important; }
          .st-key-composer-shell [data-testid="stColumn"]:last-child button { color:#fff !important; background:#197a65 !important; border:0 !important; }
          .st-key-composer-shell [data-testid="stColumn"]:last-child button:hover { background:#12644f !important; }
          .st-key-composer-shell [data-testid="stAudioInput"] { margin:8px 4px 0 !important; padding:10px 12px !important; border:1px solid #e1e5e2 !important; border-radius:14px !important; background:#f8f9f8 !important; }
          .st-key-composer-shell [data-testid="stPopover"], .st-key-composer-shell [data-testid="stPopover"] > div { width:auto !important; background:transparent !important; border:0 !important; box-shadow:none !important; }
          .st-key-composer-shell [data-testid="stPopoverButton"] div[aria-hidden="true"] { display:none !important; }
          .st-key-composer-shell [data-testid="stAudioInput"] label { color:#4a4a4a !important; }
          @media(max-width:850px) { .st-key-composer-shell { padding:12px 20px 22px !important; } }
          @media(max-width:560px) { .st-key-composer-shell { left:0 !important; width:100vw !important; padding:10px 15px 18px !important; } }
          @media(min-width:561px) {
            body:has([data-testid="stExpandSidebarButton"]) .st-key-composer-shell {
              left:max(0px,calc((100vw - 1000px)/2)) !important; width:min(1000px,100vw) !important;
            }
          }
          html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"], [data-testid="stMain"], section.main { background:#fff !important; color:#171717 !important; }
          [data-testid="stSidebar"], [data-testid="stSidebar"] > div:first-child, [data-testid="stSidebar"] [data-testid="stSidebarContent"] { background:#f9f9f9 !important; }
          [data-testid="stChatInput"] { background:#fff !important; border:1px solid #e5e5e5 !important; border-radius:28px !important; overflow:hidden !important; }
          [data-testid="stChatInput"] > div, [data-testid="stChatInput"] [data-baseweb="textarea"],
          [data-testid="stChatInput"] [data-baseweb="textarea"] > div {
            background:transparent !important; border:0 !important; border-radius:inherit !important; box-shadow:none !important;
          }
          [data-testid="stChatInput"] textarea { color:#171717 !important; background:transparent !important; border:0 !important; }
          .st-key-temporary-toggle button[kind="secondary"], .st-key-theme-toggle button,
          .st-key-open-settings button, .st-key-main-theme-toggle button {
            color:#4e5a53 !important; background:#fff !important; border:1px solid #e5e5e5 !important;
          }
          .st-key-temporary-toggle button[kind="secondary"] *, .st-key-theme-toggle button *,
          .st-key-open-settings button *, .st-key-main-theme-toggle button * { color:#4e5a53 !important; }
          .st-key-temporary-toggle button[kind="secondary"]:hover, .st-key-theme-toggle button:hover,
          .st-key-open-settings button:hover, .st-key-main-theme-toggle button:hover { color:#12644f !important; background:#eaf5f0 !important; border-color:#d6eae0 !important; }
          .st-key-app_topbar_shell .st-key-temporary-toggle button[kind="primary"] { color:#fff !important; background:#197a65 !important; border-color:#197a65 !important; }
          .st-key-app_topbar_shell .st-key-temporary-toggle button[kind="primary"] * { color:#fff !important; }
          [data-testid="stPopover"], [data-testid="stPopoverBody"], [role="dialog"], [data-testid="stDialog"] > div { color:#24332b !important; background:#fff !important; border-color:#e1e8e3 !important; }
          [class*="st-key-chat-actions-"] [data-testid="stPopover"],
          [class*="st-key-chat-actions-"] [data-testid="stPopover"] > div {
            background:transparent !important; background-color:transparent !important; border:0 !important; box-shadow:none !important;
          }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"],
          [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:hover,
          [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:focus,
          [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:focus-visible,
          [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"][aria-expanded="true"] {
            color:#616161 !important; background:transparent !important; background-color:transparent !important;
            border:0 !important; box-shadow:none !important; outline:none !important;
          }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] button { color:#343a40 !important; background:transparent !important; }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] button:hover { color:#20252a !important; background:#f1f1f1 !important; }
          [class*="st-key-chat-actions-"] [data-testid="stPopoverBody"] [class*="st-key-delete-chat-"] button { color:#dc4c4c !important; }
          body:has(.st-key-theme-light) [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopover"],
          body:has(.st-key-theme-light) [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopover"] > div {
            background:transparent !important; background-color:transparent !important;
            border:0 !important; box-shadow:none !important;
          }
          body:has(.st-key-theme-light) [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"],
          body:has(.st-key-theme-light) [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:hover,
          body:has(.st-key-theme-light) [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:focus,
          body:has(.st-key-theme-light) [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:focus-visible,
          body:has(.st-key-theme-light) [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"][aria-expanded="true"] {
            color:#616161 !important; background:transparent !important; background-color:transparent !important;
            border:0 !important; box-shadow:none !important; outline:none !important;
          }
          /* Streamlit themes can reintroduce button fills via its button-kind and
             focus rules. Keep the chat action trigger visually transparent in
             every interaction state, including its internal focus ring layers. */
          [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"],
          [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:hover,
          [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:active,
          [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:focus,
          [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:focus-visible,
          [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"][aria-expanded="true"] {
            color:inherit !important; background:transparent !important; background-color:transparent !important;
            background-image:none !important; border:0 !important; outline:0 !important;
            box-shadow:none !important; filter:none !important;
          }
          [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]::before,
          [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]::after {
            background:transparent !important; box-shadow:none !important; outline:0 !important;
          }
          [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"]:hover {
            background:transparent !important; background-color:transparent !important;
          }
          [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"] > div,
          [data-testid="stSidebar"] [class*="st-key-chat-actions-"] [data-testid="stPopoverButton"] > div:hover {
            background:transparent !important; background-color:transparent !important;
            box-shadow:none !important; outline:0 !important;
          }
          [role="tooltip"], [data-testid="stTooltipContent"], [data-baseweb="tooltip"] { color:#24332b !important; background:#fff !important; border:1px solid #dce4de !important; box-shadow:0 8px 24px rgba(28,48,38,.12) !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

try:
    httpx.get(f"{API_URL}/health", timeout=15).raise_for_status()
except httpx.HTTPError as error:
    st.error(f"Cannot connect to Ekalavya at {API_URL}: {api_error(error)}")
    st.stop()

if not st.session_state.get("auth_token"):
    auth_screen()

try:
    user = api("GET", "/auth/me")
except httpx.HTTPStatusError as error:
    if error.response.status_code == 401:
        st.session_state.pop("auth_token", None)
        _schedule_auth_cookie("clear")
        st.rerun()
    st.error(api_error(error))
    st.stop()

shared_chat_id = st.query_params.get("chat")
if shared_chat_id and st.session_state.get("active_project_id") != shared_chat_id:
    set_active_chat(shared_chat_id)
st.session_state.setdefault("active_project_id", None)
st.session_state.setdefault("active_folder_id", None)
try:
    projects = api("GET", "/projects")
    archived_projects = api("GET", "/projects", params={"archived": "true"})
    folders = api("GET", "/project-folders")
except httpx.HTTPError as error:
    st.error(f"Could not load projects and conversation history: {api_error(error)}")
    st.stop()

selected_id = st.session_state.active_project_id
project = next((item for item in projects if item["id"] == selected_id), None)
project_load_error = None
if selected_id and project is None:
    try:
        project = api("GET", f"/projects/{selected_id}")
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            project_load_error = (
                "This conversation isn’t available to the signed-in account. "
                "Sign in to the account that owns it, or choose one of your saved chats."
            )
        else:
            project_load_error = f"Could not open this conversation: {api_error(error)}"
    except httpx.HTTPError as error:
        project_load_error = f"Could not open this conversation: {api_error(error)}"
active_folder_id = st.session_state.active_folder_id
if project and project.get("folder_id"):
    active_folder_id = project["folder_id"]
    st.session_state.active_folder_id = active_folder_id
folder = next((item for item in folders if item["id"] == active_folder_id), None)
if active_folder_id and folder is None:
    st.session_state.active_folder_id = None
    active_folder_id = None

with st.sidebar:
    st.markdown(
        f"<div class='sidebar-brand'>{brand_mark_html()}<span>Ekalavya</span></div>",
        unsafe_allow_html=True,
    )
    if credit_text := credit_left():
        st.markdown(
            "<div class='sidebar-credit'><span class='sidebar-credit-label'>Credit left</span>"
            f"<span class='sidebar-credit-value'>{credit_text}</span></div>",
            unsafe_allow_html=True,
        )
    if st.button("New chat", key="new-chat", icon=":material/edit_square:", width="stretch"):
        set_active_chat(None)
        st.session_state.active_folder_id = None
        st.rerun()

    st.caption("Projects")
    if st.button("New project", key="new-project", icon=":material/add:", width="stretch"):
        new_project_dialog()
    for item in folders:
        if st.button(
            item["name"],
            key=f"folder-{item['id']}",
            icon=":material/folder:",
            width="stretch",
            type="primary" if item["id"] == active_folder_id else "secondary",
        ):
            st.session_state.active_folder_id = item["id"]
            set_active_chat(None)
            st.rerun()
        if item["id"] == active_folder_id:
            if st.button(
                "New chat",
                key=f"folder-new-chat-{item['id']}",
                icon=":material/add:",
                width="stretch",
            ):
                set_active_chat(None)
                st.rerun()
            folder_chats = [chat for chat in projects if chat.get("folder_id") == item["id"]]
            if not folder_chats:
                st.markdown(
                    "<div class='sidebar-empty project-indent'>No chats in this project yet.</div>",
                    unsafe_allow_html=True,
                )
            for chat in folder_chats:
                sidebar_chat_row(chat, folders, selected_id, "folder-chat")

    st.caption("Chats")
    chats = [item for item in projects if not item.get("folder_id")]
    if not chats:
        st.markdown(
            "<div class='sidebar-empty'>Your saved conversations will appear here.</div>",
            unsafe_allow_html=True,
        )
    for item in chats:
        sidebar_chat_row(item, folders, selected_id, "chat")
    if archived_projects:
        st.caption("Archived")
        for item in archived_projects:
            sidebar_chat_row(item, folders, selected_id, "archived-chat")
    with st.container(key="sidebar_footer"):
        initial = (user["display_name"] or user["username"])[0].upper()
        profile_col, settings_col = st.columns([1, 0.23], gap="small", vertical_alignment="center")
        with profile_col:
            st.markdown(
                f"<div class='profile-row'><span class='profile-avatar' "
                f"style='background:{html.escape(user['avatar_color'])}'>{html.escape(initial)}</span>"
                f"<span>{html.escape(user['display_name'])}</span></div>",
                unsafe_allow_html=True,
            )
        with settings_col:
            if st.button(
                "",
                key="open-settings",
                icon=":material/settings:",
                help="Account settings",
            ):
                settings_dialog(user, project)

topbar_title = title_for(project) if project else (folder["name"] if folder else "New conversation")
with st.container(key="app_topbar_shell"):
    temp_col, theme_col = st.columns([1, 1], gap="small", vertical_alignment="center")
    with temp_col:
        if st.button(
            "",
            key="temporary-toggle",
            icon=":material/bolt:",
            help="Temporary chat: not saved and removed on refresh",
            type="primary" if st.session_state.temporary_chat else "secondary",
        ):
            st.session_state.temporary_chat = not st.session_state.temporary_chat
            if not st.session_state.temporary_chat:
                st.session_state.temporary_messages = []
            st.rerun()
    with theme_col:
        if st.button(
            "",
            key="theme-toggle",
            icon=":material/light_mode:" if st.session_state.dark_mode else ":material/dark_mode:",
            help="Switch to light mode" if st.session_state.dark_mode else "Switch to dark mode",
        ):
            st.session_state.dark_mode = not st.session_state.dark_mode
            st.rerun()

prompt = None
if st.session_state.temporary_chat:
    with st.container(key="temporary-banner"):
        st.markdown(
            "<div class='temporary-banner'><span>Temporary chat</span>"
            "<span>Nothing here is saved.</span></div>",
            unsafe_allow_html=True,
        )
    for temporary_message in st.session_state.temporary_messages:
        with st.chat_message(temporary_message["role"]):
            render_chat_markdown(temporary_message["message"])
    message_composer("temporary", "Message Ekalavya temporarily", user["default_text_language"])
elif project is None:
    first_name = html.escape((user["display_name"] or user["username"]).split()[0])
    greeting = (
        f"What should we work on in {html.escape(folder['name'])}?"
        if folder
        else f"Good to see you, {first_name}."
    )
    with st.container(key="welcome_shell"):
        if project_load_error:
            st.warning(project_load_error)
            switch_col, clear_col = st.columns(2, gap="small")
            with switch_col:
                if st.button("Sign out to switch accounts", key="switch-chat-account"):
                    sign_out(keep_chat=True)
            with clear_col:
                if st.button("Clear chat link", key="clear-unavailable-chat"):
                    set_active_chat(None)
                    st.rerun()
        st.markdown(
            f"<div class='welcome'>{brand_mark_html('welcome-mark')}"
            f"<h1>{greeting}</h1>"
            "<p>Explore an idea, build a visual lesson, or ask a question."
            " We’ll work through it together.</p></div>",
            unsafe_allow_html=True,
        )
        suggestion_columns = st.columns(3, gap="small")
        suggestions = [
            (
                "suggestion-pythagoras",
                "📐  Pythagoras, made visual",
                "A gentle proof with a narrated triangle lesson.",
                "Explain Pythagoras’ theorem and generate me a video.",
            ),
            (
                "suggestion-concept",
                "🧠  Untangle a concept",
                "Get a clear explanation, one step at a time.",
                "Explain a difficult concept step by step with a clear example.",
            ),
            (
                "suggestion-source",
                "✨  Explore a source",
                "Turn a paper, link, or idea into a visual lesson.",
                "Generate a visual video lesson from this source: ",
            ),
        ]
        for column, (key, title, description, suggested_prompt) in zip(
            suggestion_columns, suggestions, strict=True
        ):
            with column, st.container(key=key):
                if st.button(title, key=f"{key}-button", width="stretch"):
                    st.session_state.pending_prompt = suggested_prompt
                st.caption(description)
    thinking_selector()
    message_composer("welcome", "Message Ekalavya", user["default_text_language"])
else:
    try:
        messages = api("GET", f"/projects/{project['id']}/chat")
    except httpx.HTTPError as error:
        st.warning(f"Could not load this conversation: {api_error(error)}")
        messages = []
    latest_user_message_id = next(
        (message["id"] for message in reversed(messages) if message["role"] == "user"),
        None,
    )
    video_action_ids = {
        "generate_video",
        "edit_range",
        "localize",
        "localize_scene",
        "verification_complete",
    }
    every_message = [
        *messages,
        *(
            old
            for message in messages
            for branch in message.get("branches") or []
            for old in branch["messages"]
        ),
    ]
    # Pair videos across every branch so an old branch's video never shows in the active one.
    video_messages = sorted(
        (
            message
            for message in every_message
            if message["role"] == "assistant" and message.get("action") in video_action_ids
        ),
        key=lambda message: str(message.get("created_at") or ""),
    )
    try:
        kept_videos = (
            api("GET", f"/projects/{project['id']}/videos") if project.get("video_path") else []
        )
    except httpx.HTTPError:
        kept_videos = []
    video_for_message = videos_by_message(video_messages, kept_videos)
    last_user_message = None
    messages, branch_controls = displayed_conversation(messages)
    for message in messages:
        is_user = message["role"] == "user"
        message_id = message["id"]
        readonly = bool(message.get("readonly"))
        editing = is_user and st.session_state.get("editing_message_id") == message_id
        if readonly and message_id in branch_controls:
            st.caption("Earlier version of this conversation · read-only")
        with st.chat_message("user" if is_user else "assistant"):
            if editing:
                with st.form(f"edit_message_{message_id.replace('-', '_')}"):
                    edited_text = st.text_area(
                        "Edit your message",
                        value=message["message"],
                        height=100,
                        key=f"edit_text_{message_id.replace('-', '_')}",
                    )
                    save_col, cancel_col = st.columns(2, gap="small")
                    with save_col:
                        save_edit = st.form_submit_button(
                            "Save & resend", type="primary", icon=":material/send:"
                        )
                    with cancel_col:
                        cancel_edit = st.form_submit_button("Cancel")
                if cancel_edit:
                    st.session_state.pop("editing_message_id", None)
                    st.rerun()
                if save_edit:
                    if not edited_text.strip():
                        st.error("Your message can’t be empty.")
                    else:
                        try:
                            with st.spinner("Updating the conversation…"):
                                api(
                                    "PATCH",
                                    f"/projects/{project['id']}/chat/{message_id}",
                                    json={"message": edited_text.strip()},
                                )
                            st.session_state.pop("editing_message_id", None)
                            st.session_state.pop("quoted_message", None)
                            st.toast("Updated. The reply has been refreshed.", icon="✅")
                            st.rerun()
                        except httpx.HTTPError as error:
                            st.error(api_error(error))
            else:
                render_chat_markdown(message["message"])
                if message.get("action") in {"edit_range", "localize_scene"}:
                    metadata = message.get("metadata") or {}
                    st.caption(
                        f"Editing {timestamp(metadata.get('start'))} to {timestamp(metadata.get('end'))}."
                    )
                if message.get("action") == "generate_video":
                    source_note = (message.get("metadata") or {}).get("source_note")
                    if source_note:
                        st.caption(source_note)
                if message_id in video_for_message:
                    version, is_latest = video_for_message[message_id]
                    try:
                        timeline = api(
                            "GET", f"/projects/{project['id']}/timeline?version={version}"
                        )
                        with st.container(key=f"video-response-{message_id}"):
                            st.markdown(
                                "**Your lesson video**"
                                if is_latest
                                else f"**Earlier version (v{version})**"
                            )
                            chapter_player(
                                project["id"],
                                timeline.get("scenes", []),
                                video_version=version,
                                subtitle_language=project.get("voice_language", "English"),
                                dark_mode=st.session_state.dark_mode,
                            )
                            if timeline.get("scenes"):
                                st.caption(
                                    "Click a scene marker to seek, then ask for a change using a time range."
                                )
                    except httpx.HTTPError as error:
                        st.warning(f"Could not load the video: {api_error(error)}")

        if not editing:
            if is_user:
                with st.container(key=f"message-actions-user-{message_id.replace('-', '')}"):
                    control = branch_controls.get(message_id)
                    columns = st.columns(5 if control else 2, gap="small")
                    if control:
                        state_key, selected, options, _active = control
                        position = options.index(selected)
                        with columns[0]:
                            if st.button(
                                "",
                                key=f"branch-prev-{message_id}",
                                icon=":material/chevron_left:",
                                help="Earlier version",
                                disabled=position == 0,
                            ):
                                st.session_state[state_key] = options[position - 1]
                                st.rerun()
                        with columns[1]:
                            st.markdown(
                                f"<span class='branch-count'>{position + 1}/{len(options)}</span>",
                                unsafe_allow_html=True,
                            )
                        with columns[2]:
                            if st.button(
                                "",
                                key=f"branch-next-{message_id}",
                                icon=":material/chevron_right:",
                                help="Later version",
                                disabled=position == len(options) - 1,
                            ):
                                st.session_state[state_key] = options[position + 1]
                                st.rerun()
                    copy_col, edit_col = columns[-2], columns[-1]
                    with copy_col:
                        copy_message_button(
                            message["message"],
                            "Copy your message",
                            dark_mode=st.session_state.dark_mode,
                        )
                    with edit_col:
                        if not readonly and st.button(
                            "",
                            key=f"edit-message-{message_id}",
                            icon=":material/edit:",
                            help="Edit and retry from this message",
                            disabled=project["status"]
                            in {"GENERATING", "EDITING", "LOCALIZING", "CANCELLING"},
                        ):
                            st.session_state.editing_message_id = message_id
                            st.rerun()
                last_user_message = message
            elif not readonly:
                action_key = f"message-actions-assistant-{message_id.replace('-', '')}"
                with st.container(key=action_key):
                    buttons = st.columns([0.07, 0.07, 0.07, 1], gap="small")
                    with buttons[0]:
                        copy_message_button(
                            message["message"],
                            "Copy reply",
                            dark_mode=st.session_state.dark_mode,
                        )
                    with buttons[1]:
                        if st.button(
                            "",
                            key=f"quote-message-{message_id}",
                            icon=":material/format_quote:",
                            help="Quote this reply",
                        ):
                            st.session_state.quoted_message = {
                                "id": message_id,
                                "text": message["message"],
                            }
                            st.rerun()
                    if last_user_message is not None:
                        with buttons[2]:
                            if st.button(
                                "",
                                key=f"regenerate-message-{message_id}",
                                icon=":material/refresh:",
                                help="Regenerate this reply",
                                disabled=project["status"]
                                in {"GENERATING", "EDITING", "LOCALIZING", "CANCELLING"},
                            ):
                                try:
                                    api(
                                        "PATCH",
                                        f"/projects/{project['id']}/chat/{last_user_message['id']}",
                                        json={"message": last_user_message["message"]},
                                    )
                                    st.rerun()
                                except httpx.HTTPError as error:
                                    st.error(api_error(error))
        if (
            is_user
            and message_id == latest_user_message_id
            and project["status"]
            in {"GENERATING", "EDITING", "LOCALIZING", "CANCELLING", "CANCELLED", "FAILED"}
        ):
            render_project_progress(project["id"])
    pending_chat_prompt = st.session_state.get("pending_prompt")
    if latest_user_message_id is None and project["status"] in {
        "GENERATING",
        "EDITING",
        "LOCALIZING",
        "CANCELLING",
        "CANCELLED",
        "FAILED",
    }:
        render_project_progress(project["id"])
    if pending_chat_prompt:
        with st.chat_message("user"):
            render_chat_markdown(pending_chat_prompt)
        with st.chat_message("assistant"):
            st.markdown("Thinking…")
    quoted_message = st.session_state.get("quoted_message")
    if quoted_message:
        with st.container(key="quoted-reply-card"):
            st.markdown("**Replying to**")
            render_chat_markdown(quoted_message["text"][:220])
            if st.button("Clear quote", key="clear-quote", icon=":material/close:"):
                st.session_state.pop("quoted_message", None)
                st.rerun()
    st.markdown(
        "<div class='composer-disclaimer'>Ekalavya can make mistakes. Verify important information.</div>",
        unsafe_allow_html=True,
    )
    thinking_selector()
    message_composer(
        "conversation", "Ask a follow-up or request an edit…", project["text_language"]
    )
if prompt is None:
    prompt = st.session_state.pop("pending_prompt", None)
if prompt:
    try:
        if st.session_state.temporary_chat:
            history = [
                f"{item['role']}: {item['message']}"
                for item in st.session_state.temporary_messages[-12:]
            ]
            with st.spinner("Thinking…"):
                response = api(
                    "POST",
                    "/temporary-chat",
                    json={
                        "message": prompt.strip(),
                        "language": user["default_text_language"],
                        "text_language": user["default_text_language"],
                        "history": history,
                        "reasoning_effort": thinking_effort(),
                    },
                )
            st.session_state.temporary_messages.extend(
                [
                    {"role": "user", "message": prompt.strip()},
                    {"role": "assistant", "message": response["message"]},
                ]
            )
            st.rerun()
        quoted_message = st.session_state.pop("quoted_message", None)
        if quoted_message:
            prompt = f"> {quoted_message['text']}\n\n{prompt}"
        if project is None:
            project = api(
                "POST",
                "/projects",
                json={
                    "title": prompt.strip()[:80],
                    "request": prompt.strip(),
                    "mode": "chat",
                    "folder_id": active_folder_id,
                    "target_duration_minutes": user["default_duration_minutes"],
                    "text_language": user["default_text_language"],
                    "voice_language": user["default_voice_language"],
                },
            )
            set_active_chat(project["id"])
            st.session_state.pending_prompt = prompt.strip()
            st.rerun()
        if st.session_state.get("pending_paper"):
            with st.spinner("Reading the attached paper…"):
                attached = attach_pending_paper(project["id"])
            if attached:
                st.toast(f"Attached {attached}")
        with st.spinner("Thinking…"):
            api(
                "POST",
                f"/projects/{project['id']}/chat",
                json={
                    "message": prompt.strip(),
                    "language": project["text_language"],
                    "reasoning_effort": thinking_effort(),
                },
            )
        st.rerun()
    except httpx.HTTPError as error:
        st.error(api_error(error))
