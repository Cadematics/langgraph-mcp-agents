import streamlit as st
import asyncio
import nest_asyncio
import json
import os
import platform
import inspect
import sys
import traceback

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

nest_asyncio.apply()

if "event_loop" not in st.session_state:
    loop = asyncio.new_event_loop()
    st.session_state.event_loop = loop
    asyncio.set_event_loop(loop)

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from utils import random_uuid
from cad_agent import build_cad_orchestrator_graph, CADAgentState
from session_manager import SessionManager

load_dotenv(override=True)

CONFIG_FILE_PATH = "config.json"

def load_config_from_json():
    """Loads MCP server configurations from config.json."""
    default_config = {
        "onshape": {
            "command": "npx",
            "args": ["--yes", "onshape-mcp"],
            "env": {
                "ONSHAPE_MCP_AUTH__ACCESS_KEY": os.environ.get("ONSHAPE_ACCESS_KEY", ""),
                "ONSHAPE_MCP_AUTH__SECRET_KEY": os.environ.get("ONSHAPE_SECRET_KEY", ""),
                "ONSHAPE_ACCESS_KEY": os.environ.get("ONSHAPE_ACCESS_KEY", ""),
                "ONSHAPE_SECRET_KEY": os.environ.get("ONSHAPE_SECRET_KEY", "")
            },
            "transport": "stdio"
        }
    }
    try:
        if os.path.exists(CONFIG_FILE_PATH):
            with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        else:
            save_config_to_json(default_config)
            return default_config
    except Exception as e:
        st.error(f"Error loading settings file: {str(e)}")
        return default_config

def save_config_to_json(config):
    """Saves MCP server configurations to config.json."""
    try:
        with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        st.error(f"Error saving settings file: {str(e)}")
        return False

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

use_login = os.environ.get("USE_LOGIN", "false").lower() == "true"

if use_login and not st.session_state.authenticated:
    st.set_page_config(page_title="Plan-and-Execute CAD Agent", page_icon="🏗️")
    st.title("🔐 Login")
    st.markdown("Login is required to access the CAD Orchestrator.")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit_button = st.form_submit_button("Login")

        if submit_button:
            expected_username = os.environ.get("USER_ID")
            expected_password = os.environ.get("USER_PASSWORD")

            if username == expected_username and password == expected_password:
                st.session_state.authenticated = True
                st.success("✅ Login successful! Please wait...")
                st.rerun()
            else:
                st.error("❌ Username or password is incorrect.")
    st.stop()
else:
    st.set_page_config(page_title="Plan-and-Execute CAD Agent", page_icon="🏗️", layout="wide")

st.title("🏗️ Dynamic Code-Executing CAD Agent")
st.markdown("✨ Self-healing Python script execution pipeline with verbose debug logging.")

OUTPUT_TOKEN_INFO = {
    "gpt-4o": {"max_tokens": 16000},
    "gpt-4o-mini": {"max_tokens": 16000},
    "claude-3-7-sonnet-latest": {"max_tokens": 64000},
    "claude-3-5-sonnet-latest": {"max_tokens": 8192},
}

if "session_initialized" not in st.session_state:
    st.session_state.session_initialized = False
    st.session_state.agent = None
    st.session_state.history = []
    st.session_state.mcp_client = None
    st.session_state.raw_tools = []
    st.session_state.tool_count = 0
    st.session_state.selected_model = "gpt-4o"
    st.session_state.max_retries = 3
    st.session_state.init_error = None
    st.session_state.init_traceback = None
    st.session_state.server_status = {}

if "pending_mcp_config" not in st.session_state:
    st.session_state.pending_mcp_config = load_config_from_json()

if "thread_id" not in st.session_state:
    st.session_state.thread_id = random_uuid()

# Initialize local session file manager
session_mgr = SessionManager(st.session_state.thread_id)

async def initialize_session(mcp_config=None):
    """Initializes LLM and LangGraph script generator."""
    st.session_state.init_error = None
    st.session_state.init_traceback = None

    try:
        selected_model = st.session_state.selected_model

        if "claude" in selected_model:
            model = ChatAnthropic(
                model=selected_model,
                temperature=0,
                max_tokens=OUTPUT_TOKEN_INFO.get(selected_model, {}).get("max_tokens", 8192),
            )
        else:
            model = ChatOpenAI(
                model=selected_model,
                temperature=0,
                max_tokens=OUTPUT_TOKEN_INFO.get(selected_model, {}).get("max_tokens", 16000),
            )

        cad_graph = build_cad_orchestrator_graph(model)
        st.session_state.agent = cad_graph
        st.session_state.session_initialized = True
        session_mgr.log_debug("SYSTEM", "Dynamic CAD Agent initialized successfully.")
        return True

    except Exception as e:
        err_msg = str(e)
        tb_str = traceback.format_exc()
        st.session_state.init_error = err_msg
        st.session_state.init_traceback = tb_str
        st.session_state.session_initialized = False
        session_mgr.log_debug("ERROR", f"Agent initialization error: {err_msg}", tb_str)
        return False

async def process_cad_query(query: str, status_container):
    """Executes the dynamic CAD LangGraph workflow and streams live script feedback."""
    if not st.session_state.agent:
        return [], ["🚫 Agent is not initialized."]

    session_mgr.log_debug("USER_QUERY", f"Processing prompt: '{query}'")

    initial_state: CADAgentState = {
        "messages": [HumanMessage(content=query)],
        "session_id": st.session_state.thread_id,
        "user_prompt": query,
        "available_tools": [],
        "plan": [],
        "current_step_index": 0,
        "step_retries": 0,
        "max_retries": st.session_state.max_retries,
        "replan_count": 0,
        "max_replans": 3,
        "execution_logs": []
    }

    final_plan = []
    final_logs = []

    try:
        async for output in st.session_state.agent.astream(initial_state):
            for node_name, state_update in output.items():
                status_container.update(label=f"🔄 Active Node: [{node_name}]...", state="running")
                
                if "plan" in state_update and state_update["plan"]:
                    final_plan = state_update["plan"]
                if "execution_logs" in state_update:
                    final_logs = state_update["execution_logs"]
                    with status_container:
                        st.write("\n".join(final_logs[-3:]))
                            
        status_container.update(label="✅ CAD Script Execution Workflow Finished!", state="complete", expanded=True)
        return final_plan, final_logs
    except Exception as e:
        tb_str = traceback.format_exc()
        status_container.update(label="❌ Workflow Execution Error", state="error", expanded=True)
        session_mgr.log_debug("ERROR", f"Workflow execution exception: {str(e)}", tb_str)
        return [], [f"Error during execution: {str(e)}"]

def print_message_history():
    """Displays message history and generated script code details."""
    for msg in st.session_state.history:
        if msg["role"] == "user":
            st.chat_message("user", avatar="🧑‍💻").markdown(msg["content"])
        elif msg["role"] == "assistant":
            with st.chat_message("assistant", avatar="🤖"):
                st.markdown(msg["content"])
                if "plan" in msg and msg["plan"]:
                    with st.expander("📋 Executed Script Step Details", expanded=False):
                        for step in msg["plan"]:
                            icon = "✅" if step.get("status") == "success" else "❌" if step.get("status") == "failed" else "⏳"
                            st.markdown(f"### {icon} Step {step.get('step_id')}: {step.get('description')}")
                            if step.get("python_code"):
                                st.markdown("**Generated Python Script:**")
                                st.code(step["python_code"], language="python")
                            if step.get("error_feedback"):
                                st.markdown("**Execution Stderr / Error Feedback:**")
                                st.error(step["error_feedback"])

with st.sidebar:
    st.sidebar.markdown("### ✍️ Dynamic CAD Agent 🚀")
    st.sidebar.divider()

    st.subheader("⚙️ System Settings")
    
    available_models = []
    if os.environ.get("OPENAI_API_KEY"):
        available_models.extend(["gpt-4o", "gpt-4o-mini"])
    if os.environ.get("ANTHROPIC_API_KEY"):
        available_models.extend(["claude-3-7-sonnet-latest", "claude-3-5-sonnet-latest"])
    if not available_models:
        available_models = ["gpt-4o"]

    st.session_state.selected_model = st.selectbox(
        "🤖 Select LLM Model", options=available_models, index=0
    )

    st.session_state.max_retries = st.slider(
        "🔁 Max Self-Healing Retries", min_value=1, max_value=5, value=st.session_state.max_retries
    )

    st.divider()
    st.subheader("🔍 Real-Time Session Debugger")

    with st.expander(f"📂 Session Folder: `sessions/{st.session_state.thread_id[:8]}...`", expanded=True):
        current_state_mirror = session_mgr.get_onshape_state()
        st.markdown(f"**Active Doc:** `{current_state_mirror.get('document_id') or 'None'}`")
        st.markdown(f"**Active Workspace:** `{current_state_mirror.get('workspace_id') or 'None'}`")
        st.markdown(f"**Active Element:** `{current_state_mirror.get('element_id') or 'None'}`")
        st.markdown(f"**Created Features:** `{len(current_state_mirror.get('features', []))}`")

        if st.checkbox("Show onshape_state.json"):
            st.json(current_state_mirror)

    with st.expander("📜 Live Background Debug Execution Log", expanded=False):
        if st.button("🔄 Refresh Debug Log"):
            st.rerun()
        debug_log_content = session_mgr.get_debug_logs()
        st.code(debug_log_content, language="text")

    st.divider()
    st.subheader("📊 System Actions")

    if st.button("Initialize Agent Session", key="apply_button", type="primary", use_container_width=True):
        apply_status = st.empty()
        with apply_status.container():
            st.warning("🔄 Initializing CAD Agent session...")
            
            success = st.session_state.event_loop.run_until_complete(
                initialize_session()
            )

            if success:
                st.toast("✅ CAD Agent initialized successfully!", icon="🚀")
            else:
                st.toast("❌ Failed to initialize session.", icon="⚠️")
        st.rerun()

    if st.button("Reset Conversation", use_container_width=True):
        st.session_state.thread_id = random_uuid()
        st.session_state.history = []
        st.success("✅ Conversation reset.")
        st.rerun()

    if use_login and st.session_state.authenticated:
        st.divider()
        if st.button("Logout", use_container_width=True):
            st.session_state.authenticated = False
            st.rerun()

if not st.session_state.session_initialized:
    st.session_state.event_loop.run_until_complete(initialize_session())

print_message_history()

user_query = st.chat_input("💬 Enter CAD prompt (e.g. 'Create a 2 cm diameter 5 cm tall cylinder')")

if user_query:
    if st.session_state.session_initialized:
        st.chat_message("user", avatar="🧑‍💻").markdown(user_query)

        with st.chat_message("assistant", avatar="🤖"):
            status_box = st.status("🚀 Running Dynamic CAD Script Generation...", expanded=True)

            plan, logs = st.session_state.event_loop.run_until_complete(
                process_cad_query(user_query, status_box)
            )

            summary_text = "### 🏁 Dynamic CAD Script Summary\n"
            if plan:
                completed = sum(1 for s in plan if s.get("status") == "success")
                summary_text += f"Completed **{completed}/{len(plan)}** script steps successfully.\n\n"
                for s in plan:
                    status_emoji = "✅" if s.get("status") == "success" else "❌"
                    summary_text += f"* {status_emoji} **Step {s.get('step_id')}:** {s.get('description')}\n"
            else:
                summary_text += "No valid plan steps were produced."

            st.markdown(summary_text)

            st.session_state.history.append({"role": "user", "content": user_query})
            st.session_state.history.append({
                "role": "assistant", 
                "content": summary_text,
                "plan": plan
            })

            session_mgr.save_history(st.session_state.history)

    else:
        st.warning("⚠️ Agent is not initialized.")