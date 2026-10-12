"""
Page 6 — Ask AI
Claude-powered chatbot grounded on WASDE supply/demand data.
Uses fixed query tools — no open-ended SQL.
"""

import json
import os

import anthropic
import streamlit as st

import pathlib
import sys


def _fresh_modules():
    # Streamlit Cloud re-reads scripts on a push but keeps already-imported modules
    # (db, style, ...), so pushed fixes to them don't run until a Reboot.
    here = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "style.py").exists())
    seen = tuple(sorted((p.name, p.stat().st_mtime_ns) for p in here.glob("*.py")))
    if getattr(sys, "_wasde_code", None) == seen:
        return
    for name, mod in list(sys.modules.items()):
        path = getattr(mod, "__file__", None)
        if name != "__main__" and path and pathlib.Path(path).resolve().parent == here:
            sys.modules.pop(name, None)
    st.cache_data.clear()
    sys._wasde_code = seen


_fresh_modules()

import db
import style
from units import convert, fmt, unit_label

st.set_page_config(
    page_title="JSA WASDE — Ask AI",
    page_icon="💬",
    layout="wide",
    initial_sidebar_state="auto",
)
style.inject()
style.nav_bar()

COMMODITIES = ["Corn", "Soybeans", "Wheat", "Cotton", "Rice, Milled", "Soybean Oil"]

st.markdown("<div class='ctrl-bar'>", unsafe_allow_html=True)
_c1, _c2 = st.columns([2, 1])
with _c1:
    commodity = st.selectbox("Commodity", COMMODITIES)
with _c2:
    region = st.radio("Region", ["US", "World"], horizontal=True)
st.markdown("</div>", unsafe_allow_html=True)
imperial = False
country = "0000US" if region == "US" else "0000World"
ul = unit_label(commodity, imperial)

st.markdown(
    "<h1 style='margin:0;font-size:1.75rem;font-weight:800'>Ask AI — WASDE Assistant</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    f"<div style='color:{style.TEXT_MUTED};font-size:0.83rem;margin-bottom:4px'>"
    "Ask questions about USDA supply & demand data. "
    "Answers are grounded on real PSD data — not hallucinated.</div>",
    unsafe_allow_html=True,
)
st.markdown("<hr style='margin:8px 0 16px'>", unsafe_allow_html=True)

# ── Anthropic client ──────────────────────────────────────────────────────────
def _api_key():
    try:
        return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        return os.environ.get("ANTHROPIC_API_KEY", "")

_key = _api_key()
if not _key:
    st.info("Ask AI is not available in this deployment (ANTHROPIC_API_KEY not configured).")
    st.stop()

client = anthropic.Anthropic(api_key=_key)

# ── Tool definitions ──────────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "get_balance_sheet",
        "description": (
            "Return the latest USDA supply & demand balance sheet for a commodity, "
            "marketing year, and region (US or a specific country code)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "commodity":    {"type": "string", "enum": COMMODITIES},
                "market_year":  {"type": "integer", "description": "e.g. 2024 for 2024/25"},
                "country_code": {"type": "string",  "default": "0000US",
                                 "description": "0000US for US, or a FAS country code"},
            },
            "required": ["commodity", "market_year"],
        },
    },
    {
        "name": "get_revision_series",
        "description": (
            "Return the month-by-month WASDE revision history for one attribute "
            "of a commodity in a given marketing year."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "commodity":      {"type": "string", "enum": COMMODITIES},
                "market_year":    {"type": "integer"},
                "attribute_name": {"type": "string",
                                   "description": "e.g. 'Ending Stocks', 'Production'"},
                "country_code":   {"type": "string", "default": "0000US"},
            },
            "required": ["commodity", "market_year", "attribute_name"],
        },
    },
    {
        "name": "get_tendency",
        "description": (
            "Return the average historical USDA revision for a given release month. "
            "Useful for questions like 'what does USDA usually do in October?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "commodity":      {"type": "string", "enum": COMMODITIES},
                "attribute_name": {"type": "string"},
                "release_month":  {"type": "integer", "description": "1-12"},
                "country_code":   {"type": "string", "default": "0000US"},
            },
            "required": ["commodity", "attribute_name", "release_month"],
        },
    },
    {
        "name": "get_top_producers",
        "description": "Return the top countries by production or exports for a commodity and year.",
        "input_schema": {
            "type": "object",
            "properties": {
                "commodity":      {"type": "string", "enum": COMMODITIES},
                "market_year":    {"type": "integer"},
                "attribute_name": {"type": "string",
                                   "description": "'Production' or 'Exports'"},
                "n":              {"type": "integer", "default": 10},
            },
            "required": ["commodity", "market_year", "attribute_name"],
        },
    },
]


def _run_tool(name: str, inputs: dict) -> str:
    """Execute a tool call and return a JSON string result."""
    try:
        if name == "get_balance_sheet":
            if db.snowflake_available():
                df = db.latest_balance_sheet(
                    inputs["commodity"],
                    inputs["market_year"],
                    inputs.get("country_code", "0000US"),
                )
                return df.to_json(orient="records") if not df.empty else "[]"
            return '{"error": "Snowflake not configured"}'

        elif name == "get_revision_series":
            if db.snowflake_available():
                df = db.revision_series(
                    inputs["commodity"],
                    inputs["market_year"],
                    inputs["attribute_name"],
                    inputs.get("country_code", "0000US"),
                )
                return df.to_json(orient="records") if not df.empty else "[]"
            return '{"error": "Snowflake not configured"}'

        elif name == "get_tendency":
            if db.snowflake_available():
                df = db.tendencies(
                    inputs["commodity"],
                    inputs["attribute_name"],
                    inputs.get("country_code", "0000US"),
                )
                if not df.empty:
                    row = df[df["release_month"] == inputs["release_month"]]
                    return row.to_json(orient="records") if not row.empty else "[]"
            return '{"error": "Snowflake not configured"}'

        elif name == "get_top_producers":
            if db.snowflake_available():
                df = db.top_countries(
                    inputs["commodity"],
                    inputs["market_year"],
                    inputs["attribute_name"],
                    inputs.get("n", 10),
                )
                return df.to_json(orient="records") if not df.empty else "[]"
            return '{"error": "Snowflake not configured"}'

    except Exception as e:
        return json.dumps({"error": str(e)})

    return "[]"


SYSTEM_PROMPT = """You are a WASDE data assistant for John Stewart & Associates (JSA), a grain trading and risk management firm.

You answer questions about USDA World Agricultural Supply and Demand Estimates (WASDE) using real PSD data fetched via tools. You are grounded — you call tools and present their data; you do not make up numbers.

Key conventions:
- Marketing year: corn = Sep 1–Aug 31; soybeans = Sep 1–Aug 31; wheat = Jun 1–May 31
- "2024" means the 2024/25 marketing year
- US ending stocks are the most watched indicator for price direction; stocks-to-use % is the key ratio
- You do NOT make price predictions or investment recommendations
- If asked about something outside your data (e.g. basis, futures prices), say so and redirect to the balance sheet context you can provide
- Keep answers concise and data-driven. Show key figures in a table when helpful.
- Units are 1,000 metric tons unless the user asks for bushels."""


# ── Chat state ────────────────────────────────────────────────────────────────
if "wasde_messages" not in st.session_state:
    st.session_state.wasde_messages = []

for msg in st.session_state.wasde_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ── Input ─────────────────────────────────────────────────────────────────────
prompt = st.chat_input("Ask about WASDE supply & demand…")

EXAMPLE_QUESTIONS = [
    "What are current US corn ending stocks for 2024/25?",
    "How has USDA revised soybean production this crop year?",
    "What does USDA typically do to corn ending stocks in October?",
    "Who are the top 5 wheat exporters this year?",
]

if not st.session_state.wasde_messages:
    style.sec("Example questions")
    cols = st.columns(2)
    for i, q in enumerate(EXAMPLE_QUESTIONS):
        with cols[i % 2]:
            if st.button(q, key=f"ex_{i}"):
                prompt = q

if not prompt:
    st.stop()

# ── Agentic tool loop ─────────────────────────────────────────────────────────
st.session_state.wasde_messages.append({"role": "user", "content": prompt})
with st.chat_message("user"):
    st.markdown(prompt)

api_messages = [{"role": m["role"], "content": m["content"]}
                for m in st.session_state.wasde_messages]

with st.chat_message("assistant"):
    with st.spinner("Thinking…"):
        # Tool loop — max 5 rounds
        for _ in range(5):
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=api_messages,
            )

            # Collect any text from this response
            reply_text = ""
            tool_calls = []
            for block in response.content:
                if hasattr(block, "text"):
                    reply_text += block.text
                elif block.type == "tool_use":
                    tool_calls.append(block)

            if response.stop_reason == "end_turn" or not tool_calls:
                break

            # Run tools
            tool_results = []
            for tc in tool_calls:
                result = _run_tool(tc.name, tc.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result,
                })

            api_messages.append({"role": "assistant", "content": response.content})
            api_messages.append({"role": "user", "content": tool_results})

        # Final text
        final_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                final_text += block.text

        st.markdown(final_text)
        st.session_state.wasde_messages.append({"role": "assistant", "content": final_text})

# ── Clear button ──────────────────────────────────────────────────────────────
if st.session_state.wasde_messages:
    if st.button("Clear conversation", key="clear_chat"):
        st.session_state.wasde_messages = []
        st.rerun()

style.disclaimer()
