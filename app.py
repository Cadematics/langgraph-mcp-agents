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
        },
        "onshape_macros": {
            "command": sys.executable,
            "args": [os.path.abspath("mcp_server_onshape_macros.py")],
            "env": {
                "ONSHAPE_ACCESS_KEY": os.environ.get("ONSHAPE_ACCESS_KEY", ""),
                "ONSHAPE_SECRET_KEY": os.environ.get("ONSHAPE_SECRET_KEY", "")
            },
            "transport": "stdio"
        }
    }
    try:
        if os.path.exists(CONFIG_FILE_PATH):
            with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return cfg
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

st.title("🏗️ Plan ➔ Execute ➔ Validate CAD Orchestrator")
st.markdown("✨ Sequential, self-healing CAD agent architecture powered by LangGraph & MCP.")

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

async def cleanup_mcp_client():
    """Safely closes active MCP client instances."""
    if "mcp_client" in st.session_state and st.session_state.mcp_client is not None:
        mc = st.session_state.mcp_client
        if isinstance(mc, dict):
            for s_name, client in mc.items():
                try:
                    await client.close()
                except Exception:
                    pass
        else:
            try:
                await mc.close()
            except Exception:
                pass
        st.session_state.mcp_client = None

async def initialize_session(mcp_config=None):
    """Initializes MultiServerMCPClient per server, extracts tools safely, and builds CAD agent."""
    await cleanup_mcp_client()

    st.session_state.init_error = None
    st.session_state.init_traceback = None
    st.session_state.server_status = {}

    if mcp_config is None:
        mcp_config = st.session_state.pending_mcp_config

    resolved_config = dict(mcp_config)
    all_tools = []
    clients = {}
    server_errors = []

    for srv_name, srv_raw_cfg in resolved_config.items():
        srv_cfg = dict(srv_raw_cfg)
        cmd = srv_cfg.get("command", "")

        # Auto-resolve generic python commands to current active virtual environment python
        if cmd in ["python", "./.venv/bin/python", "python3"] or cmd.endswith("/python"):
            srv_cfg["command"] = sys.executable

        # Resolve relative python script paths in arguments to absolute file paths
        args = list(srv_cfg.get("args", []))
        for i, arg in enumerate(args):
            if isinstance(arg, str) and arg.endswith(".py") and os.path.exists(os.path.abspath(arg)):
                args[i] = os.path.abspath(arg)
        srv_cfg["args"] = args

        # Ensure both environment variable key patterns are supplied
        env = dict(srv_cfg.get("env", {}))
        if "ONSHAPE_ACCESS_KEY" in env:
            env["ONSHAPE_MCP_AUTH__ACCESS_KEY"] = env.get("ONSHAPE_MCP_AUTH__ACCESS_KEY") or env["ONSHAPE_ACCESS_KEY"]
        if "ONSHAPE_SECRET_KEY" in env:
            env["ONSHAPE_MCP_AUTH__SECRET_KEY"] = env.get("ONSHAPE_MCP_AUTH__SECRET_KEY") or env["ONSHAPE_SECRET_KEY"]
        srv_cfg["env"] = env

        try:
            single_client = MultiServerMCPClient({srv_name: srv_cfg})
            
            # Extract tools for this individual server
            res = single_client.get_tools()
            if inspect.isawaitable(res):
                srv_tools = await res
            else:
                srv_tools = res

            if not srv_tools:
                async with single_client:
                    res = single_client.get_tools()
                    if inspect.isawaitable(res):
                        srv_tools = await res
                    else:
                        srv_tools = res

            if srv_tools:
                all_tools.extend(srv_tools)
                clients[srv_name] = single_client
                st.session_state.server_status[srv_name] = f"✅ Loaded {len(srv_tools)} tools"
            else:
                st.session_state.server_status[srv_name] = "⚠️ Server connected but returned 0 tools"
                server_errors.append(f"Server '{srv_name}': Returned 0 tools. Check command arguments or credentials.")

        except Exception as e:
            tb_str = traceback.format_exc()
            st.session_state.server_status[srv_name] = f"❌ Error: {str(e)}"
            server_errors.append(f"Server '{srv_name}' Connection Exception: {str(e)}\n{tb_str}")

    if not all_tools:
        err_combined = "\n\n".join(server_errors) if server_errors else "No tools returned from any registered MCP server."
        st.session_state.init_error = err_combined
        st.session_state.init_traceback = err_combined
        st.session_state.session_initialized = False
        return False

    st.session_state.tool_count = len(all_tools)
    st.session_state.mcp_client = clients
    st.session_state.raw_tools = all_tools

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

        cad_graph = build_cad_orchestrator_graph(model, all_tools)
        st.session_state.agent = cad_graph
        st.session_state.session_initialized = True
        return True

    except Exception as e:
        err_msg = str(e)
        tb_str = traceback.format_exc()
        st.session_state.init_error = err_msg
        st.session_state.init_traceback = tb_str
        st.session_state.session_initialized = False
        return False

async def process_cad_query(query: str, status_container):
    """Executes the CAD LangGraph workflow and streams live step feedback."""
    if not st.session_state.agent:
        return [], ["🚫 Agent is not initialized."]

    tools_info = [
        {"name": t.name, "description": t.description}
        for t in st.session_state.raw_tools
    ]

    initial_state: CADAgentState = {
        "messages": [HumanMessage(content=query)],
        "user_prompt": query,
        "available_tools": tools_info,
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
                        for log in final_logs[-2:]:
                            st.text(log)
                            
        status_container.update(label="✅ CAD Workflow Execution Finished!", state="complete", expanded=True)
        return final_plan, final_logs
    except Exception as e:
        status_container.update(label="❌ Workflow Execution Error", state="error", expanded=True)
        return [], [f"Error during execution: {str(e)}"]

def print_message_history():
    """Displays message history and step details."""
    for msg in st.session_state.history:
        if msg["role"] == "user":
            st.chat_message("user", avatar="🧑‍💻").markdown(msg["content"])
        elif msg["role"] == "assistant":
            with st.chat_message("assistant", avatar="🤖"):
                st.markdown(msg["content"])
                if "plan" in msg and msg["plan"]:
                    with st.expander("📋 Executed Step Plan Details", expanded=False):
                        for step in msg["plan"]:
                            icon = "✅" if step.get("status") == "success" else "❌" if step.get("status") == "failed" else "⏳"
                            st.markdown(f"- {icon} **Step {step.get('step_id')}:** `{step.get('tool_name')}` - {step.get('description')}")

with st.sidebar:
    st.sidebar.markdown("### ✍️ Plan-and-Execute CAD Agent 🚀")
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
        "🔁 Max Step Retries", min_value=1, max_value=5, value=st.session_state.max_retries
    )

    st.divider()
    st.subheader("🔧 Tool Management")

    # Add MCP Tool Expander
    with st.expander("🧰 Add MCP Tool (JSON)", expanded=False):
        st.markdown("Enter **one tool** configuration in JSON format:")
        example_json = {
            "onshape_macros": {
                "command": "python",
                "args": ["mcp_server_onshape_macros.py"],
                "env": {
                    "ONSHAPE_ACCESS_KEY": "YOUR_KEY",
                    "ONSHAPE_SECRET_KEY": "YOUR_SECRET"
                },
                "transport": "stdio"
            }
        }
        default_text = json.dumps(example_json, indent=2, ensure_ascii=False)
        new_tool_json = st.text_area("Tool JSON", default_text, height=180)

        if st.button("Add Tool", type="primary", use_container_width=True):
            try:
                parsed = json.loads(new_tool_json)
                if "mcpServers" in parsed:
                    parsed = parsed["mcpServers"]
                for name, cfg in parsed.items():
                    st.session_state.pending_mcp_config[name] = cfg
                st.success("✅ Tool added to pending configuration! Click 'Apply Settings' below.")
                st.rerun()
            except Exception as e:
                st.error(f"Invalid JSON: {str(e)}")

    # Registered Servers Config Expander
    with st.expander("📋 Registered MCP Servers Config", expanded=True):
        for server_name in list(st.session_state.pending_mcp_config.keys()):
            status_badge = st.session_state.get("server_status", {}).get(server_name, "")
            st.markdown(f"**{server_name}** {f'({status_badge})' if status_badge else ''}")
            c1, c2 = st.columns([7, 3])
            if c2.button("Delete", key=f"del_{server_name}"):
                del st.session_state.pending_mcp_config[server_name]
                st.success(f"Deleted {server_name}. Click 'Apply Settings'.")
                st.rerun()

    # Active Tools Inspector Expander
    with st.expander("🛠️ Active Loaded MCP Tools Inspector", expanded=True):
        raw_tools = st.session_state.get("raw_tools", [])
        if raw_tools:
            st.markdown(f"**Loaded Tools ({len(raw_tools)}):**")
            for t in raw_tools:
                desc_snippet = t.description[:70] + "..." if len(t.description) > 70 else t.description
                st.markdown(f"• **`{t.name}`**: _{desc_snippet}_")
        else:
            st.info("No active tools loaded yet. Click 'Apply Settings / Initialize' below.")

    st.divider()
    st.subheader("📊 System Actions")
    st.write(f"🛠️ Active Tools Count: **{st.session_state.get('tool_count', 0)}**")

    if st.button("Apply Settings / Initialize", key="apply_button", type="primary", use_container_width=True):
        apply_status = st.empty()
        with apply_status.container():
            st.warning("🔄 Connecting MCP tools and building graph...")
            progress_bar = st.progress(0)

            # Resolve python command paths before saving
            cfg_to_save = dict(st.session_state.pending_mcp_config)
            for s_name, s_cfg in cfg_to_save.items():
                if s_cfg.get("command") in ["python", "./.venv/bin/python", "python3"] or str(s_cfg.get("command", "")).endswith("/python"):
                    s_cfg["command"] = sys.executable

            save_config_to_json(cfg_to_save)
            progress_bar.progress(30)

            st.session_state.session_initialized = False
            st.session_state.agent = None

            success = st.session_state.event_loop.run_until_complete(
                initialize_session(cfg_to_save)
            )
            progress_bar.progress(100)

            if success:
                st.toast(f"✅ {st.session_state.get('tool_count', 0)} MCP Tools initialized successfully!", icon="🚀")
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

if st.session_state.get("init_error"):
    st.error(f"❌ MCP Connection Error: {st.session_state.init_error}")
    with st.expander("🔍 Detailed Initialization Stack Trace", expanded=True):
        st.code(st.session_state.init_traceback or "No stack trace available.")

if not st.session_state.session_initialized and not st.session_state.get("init_error"):
    st.info("💡 Please click **Apply Settings / Initialize** in the sidebar to connect your MCP servers.")

print_message_history()

user_query = st.chat_input("💬 Enter CAD prompt (e.g. 'Build a 4cm tall cylinder with a 2cm outer diameter and 1cm center hole')")

if user_query:
    if st.session_state.session_initialized:
        st.chat_message("user", avatar="🧑‍💻").markdown(user_query)
        
        with st.chat_message("assistant", avatar="🤖"):
            status_box = st.status("🚀 Running Plan-and-Execute CAD Workflow...", expanded=True)
            
            plan, logs = st.session_state.event_loop.run_until_complete(
                process_cad_query(user_query, status_box)
            )

            summary_text = "### 🏁 CAD Execution Summary\n"
            if plan:
                completed = sum(1 for s in plan if s.get("status") == "success")
                summary_text += f"Completed **{completed}/{len(plan)}** steps successfully.\n\n"
                for s in plan:
                    status_emoji = "✅" if s.get("status") == "success" else "❌"
                    summary_text += f"* {status_emoji} **Step {s.get('step_id')}:** `{s.get('tool_name')}` - {s.get('description')}\n"
            else:
                summary_text += "No valid plan steps were produced."

            st.markdown(summary_text)

            st.session_state.history.append({"role": "user", "content": user_query})
            st.session_state.history.append({
                "role": "assistant", 
                "content": summary_text,
                "plan": plan
            })
    else:
        st.warning("⚠️ Agent is not initialized. Please click 'Apply Settings / Initialize' in the sidebar.")