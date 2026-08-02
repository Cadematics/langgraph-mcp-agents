import json
import re
from typing import Annotated, Any, Dict, List, TypedDict, Literal
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langgraph.graph.message import add_messages
from langgraph.graph import StateGraph, START, END
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from session_manager import SessionManager


class Step(BaseModel):
    step_id: int = Field(description="Unique incremental integer ID for the step.")
    description: str = Field(description="Human-readable description of what this CAD step achieves.")
    tool_name: str = Field(description="Name of the MCP tool to invoke. MUST be selected strictly from the available tools list.")
    tool_args: Dict[str, Any] = Field(default_factory=dict, description="Structured argument dictionary for the tool.")
    status: Literal["pending", "success", "failed"] = Field(default="pending", description="Status of step execution.")
    verification_check: str = Field(default="", description="Verification description or query to perform after execution.")
    error_feedback: str = Field(default="", description="Error message captured if execution or verification fails.")

class Plan(BaseModel):
    steps: List[Step] = Field(description="Sequential list of CAD operations to execute.")


class CADAgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    session_id: str
    user_prompt: str
    available_tools: List[Dict[str, Any]]
    plan: List[Dict[str, Any]]
    current_step_index: int
    step_retries: int
    max_retries: int
    replan_count: int
    max_replans: int
    execution_logs: List[str]

HYBRID_PLANNER_PROMPT = """You are an expert Mechanical Design Automation Planner for Onshape CAD.
Your job is to break down a user request into an explicit, ordered sequence of CAD operations.

CRITICAL RULE FOR TOOL SELECTION:
You MUST set `tool_name` ONLY to a tool name from the ALLOWED TOOLS list below.
DO NOT fabricate, invent, or prefix tool names (e.g. NEVER use 'functions.list_tools', 'create_sketch', or 'initialize_cad_environment').

ALLOWED REGISTERED TOOLS:
{allowed_tools_list}

TOOL SCHEMAS & DESCRIPTIONS:
{tools_info}

Current Onshape Model State Mirror (Local Active Context):
{onshape_state_json}

OPERATIONAL GUIDE FOR GEOMETRY:
1. HOLLOW CUBES / RECTANGULAR BLOCKS WITH HOLES:
   - Step 1: Use `create_rectangle_with_hole_sketch` (params: doc_id, workspace_id, element_id, width_cm, height_cm, hole_diameter_cm).
   - Step 2: Use `extrude_sketch` (params: doc_id, workspace_id, element_id, sketch_feature_id, depth_cm).
2. SOLID CYLINDERS / CIRCLES:
   - Step 1: Use `create_circle_sketch`.
   - Step 2: Use `extrude_sketch`.
3. META / INFORMATIONAL QUESTIONS:
   - If the user asks informational questions (e.g., 'what are the available tools?'), use `onshape_get_started` or `onshape_list_resources`.

Output a valid structured Plan containing step_id, description, tool_name, and tool_args."""

REPLANNER_SYSTEM_PROMPT = """A CAD execution step failed. Re-evaluate the user request, current state mirror, and error feedback to produce an updated plan starting from the failing step.

CRITICAL RULE: `tool_name` MUST be chosen ONLY from this registered tool list:
{allowed_tools_list}

Error Feedback:
{error_feedback}

Current Onshape Model State Mirror:
{onshape_state_json}

Output an updated structured Plan to recover from this failure."""

def plan_steps_node(state: CADAgentState, llm: BaseChatModel) -> Dict[str, Any]:
    """Planner Node: Generates or updates the step-by-step CAD plan with state context."""
    user_prompt = state.get("user_prompt", "")
    available_tools = state.get("available_tools", [])
    step_retries = state.get("step_retries", 0)
    replan_count = state.get("replan_count", 0)
    session_id = state.get("session_id", "default")

    session_mgr = SessionManager(session_id)
    current_mirror_state = session_mgr.get_onshape_state()
    mirror_str = json.dumps(current_mirror_state, indent=2)

    allowed_names = [t["name"] for t in available_tools]
    allowed_str = json.dumps(allowed_names)
    tools_str = json.dumps(available_tools, indent=2)

    planner_llm = llm.with_structured_output(Plan, method="function_calling")

    if step_retries > 0 and state.get("plan"):
        current_idx = state.get("current_step_index", 0)
        failed_step = state["plan"][current_idx] if current_idx < len(state["plan"]) else {}
        error = failed_step.get("error_feedback", "Unknown execution error.")
        
        sys_msg = SystemMessage(content=REPLANNER_SYSTEM_PROMPT.format(
            allowed_tools_list=allowed_str,
            error_feedback=error,
            onshape_state_json=mirror_str
        ))
        prompt_content = f"User Request: {user_prompt}\nFailing Step Context: {json.dumps(failed_step)}"
        replan_count += 1
    else:
        sys_msg = SystemMessage(content=HYBRID_PLANNER_PROMPT.format(
            allowed_tools_list=allowed_str,
            tools_info=tools_str,
            onshape_state_json=mirror_str
        ))
        prompt_content = user_prompt

    try:
        plan_output: Plan = planner_llm.invoke([sys_msg, HumanMessage(content=prompt_content)])
        steps_dict = []

        # Validate and sanitize tool names against allowed list
        for step in plan_output.steps:
            s_data = step.model_dump()
            raw_name = s_data["tool_name"].replace("functions.", "")
            if raw_name in allowed_names:
                s_data["tool_name"] = raw_name
            else:
                matches = [a for a in allowed_names if raw_name in a or a in raw_name]
                if matches:
                    s_data["tool_name"] = matches[0]
            steps_dict.append(s_data)

        session_mgr.save_plan(steps_dict)

        log_msg = f"📋 Generated plan with {len(steps_dict)} sequential CAD steps."
        return {
            "plan": steps_dict,
            "current_step_index": 0,
            "step_retries": 0,
            "replan_count": replan_count,
            "execution_logs": state.get("execution_logs", []) + [log_msg]
        }
    except Exception as e:
        error_msg = f"❌ Planning failed: {str(e)}"
        return {
            "plan": [],
            "execution_logs": state.get("execution_logs", []) + [error_msg]
        }

async def execute_step_node(state: CADAgentState, tools_map: Dict[str, BaseTool]) -> Dict[str, Any]:
    """Step Executor Node: Extracts parameters from prompt/mirror, invokes MCP tool, and updates state mirror."""
    plan = list(state["plan"])
    idx = state["current_step_index"]
    session_id = state.get("session_id", "default")
    session_mgr = SessionManager(session_id)

    if idx >= len(plan):
        return {"plan": plan}

    step = plan[idx]
    tool_name = step["tool_name"]
    tool_args = dict(step.get("tool_args", {}))

    # Regex extraction from user prompt for Document ID, Workspace ID, and Element ID
    prompt_text = state.get("user_prompt", "")
    doc_match = re.search(r'(?:Doc(?:ument)?(?:\s*ID)?[:\s]+)([a-f0-9]{24})', prompt_text, re.IGNORECASE)
    work_match = re.search(r'(?:Work(?:space)?(?:\s*ID)?[:\s]+)([a-f0-9]{24})', prompt_text, re.IGNORECASE)
    elem_match = re.search(r'(?:Elem(?:ent)?(?:\s*ID)?[:\s]+)([a-f0-9]{24})', prompt_text, re.IGNORECASE)

    extracted_doc = doc_match.group(1) if doc_match else None
    extracted_work = work_match.group(1) if work_match else None
    extracted_elem = elem_match.group(1) if elem_match else None

    # Context autofill from extracted prompt IDs or onshape_state.json mirror
    onshape_mirror = session_mgr.get_onshape_state()
    
    final_doc = tool_args.get("doc_id") or tool_args.get("did") or extracted_doc or onshape_mirror.get("document_id")
    final_work = tool_args.get("workspace_id") or tool_args.get("wvmid") or extracted_work or onshape_mirror.get("workspace_id")
    final_elem = tool_args.get("element_id") or tool_args.get("eid") or extracted_elem or onshape_mirror.get("element_id")

    if final_doc:
        tool_args["doc_id"] = final_doc
    if final_work:
        tool_args["workspace_id"] = final_work
    if final_elem:
        tool_args["element_id"] = final_elem

    # Update active identifiers in onshape_state.json mirror
    session_mgr.update_onshape_state(
        doc_id=final_doc,
        workspace_id=final_work,
        element_id=final_elem
    )

    if tool_name == "extrude_sketch" and not tool_args.get("sketch_feature_id") and onshape_mirror.get("last_created_sketch_id"):
        tool_args["sketch_feature_id"] = onshape_mirror["last_created_sketch_id"]

    log_msg = f"⚡ Executing Step {idx + 1}/{len(plan)}: [{tool_name}] - {step['description']}"
    logs = state.get("execution_logs", []) + [log_msg]

    if tool_name not in tools_map:
        step["status"] = "failed"
        step["error_feedback"] = f"Tool '{tool_name}' is not registered in the available tools list."
        session_mgr.save_plan(plan)
        return {"plan": plan, "execution_logs": logs + [f"❌ {step['error_feedback']}"]}

    tool = tools_map[tool_name]
    try:
        result = await tool.ainvoke(tool_args)
        result_str = str(result)
        res_lower = result_str.lower()

        error_keywords = ["error", "exception", "failed", "bad request", "invalid", "missing", "endpoint not found"]
        if any(kw in res_lower for kw in error_keywords):
            step["status"] = "failed"
            step["error_feedback"] = result_str
            logs.append(f"❌ Execution Failure ({tool_name}): {result_str[:300]}")
        else:
            step["status"] = "success"
            step["error_feedback"] = ""
            logs.append(f"Result ({tool_name}): {result_str[:300]}...")

            match = re.search(r"Feature ID:\s*([A-Za-z0-9_\-]+)", result_str)
            extracted_fid = match.group(1) if match else None

            session_mgr.update_onshape_state(
                doc_id=final_doc,
                workspace_id=final_work,
                element_id=final_elem,
                feature_name=step["description"],
                feature_id=extracted_fid,
                feature_type=tool_name,
                params=tool_args
            )

    except Exception as e:
        err_msg = str(e) or repr(e)
        step["status"] = "failed"
        step["error_feedback"] = f"Tool invocation exception: {err_msg}"
        logs.append(f"❌ Exception in {tool_name}: {err_msg}")

    session_mgr.save_plan(plan)
    return {"plan": plan, "execution_logs": logs}

def verify_step_node(state: CADAgentState) -> Dict[str, Any]:
    """CAD Evaluator Node."""
    plan = list(state["plan"])
    idx = state["current_step_index"]

    if idx >= len(plan):
        return {"plan": plan}

    step = plan[idx]
    retries = state.get("step_retries", 0)
    logs = list(state.get("execution_logs", []))

    if step["status"] == "success":
        logs.append(f"✅ Verified Step {idx + 1}: Success")
        return {
            "plan": plan,
            "current_step_index": idx + 1,
            "step_retries": 0,
            "execution_logs": logs
        }
    else:
        logs.append(f"⚠️ Step {idx + 1} Failed Verification (Attempt {retries + 1}): {step.get('error_feedback', '')}")
        return {
            "plan": plan,
            "step_retries": retries + 1,
            "execution_logs": logs
        }


def route_next(state: CADAgentState) -> Literal["execute_step", "plan_steps", "finish"]:
    """Determines next node routing."""
    plan = state.get("plan", [])
    idx = state.get("current_step_index", 0)
    retries = state.get("step_retries", 0)
    max_retries = state.get("max_retries", 3)
    replan_count = state.get("replan_count", 0)
    max_replans = state.get("max_replans", 3)

    if not plan or idx >= len(plan):
        return "finish"

    if plan[idx].get("status") == "failed":
        if retries < max_retries:
            return "execute_step"
        elif replan_count < max_replans:
            return "plan_steps"
        else:
            return "finish"

    return "execute_step"


def build_cad_orchestrator_graph(llm: BaseChatModel, tools: List[BaseTool]) -> StateGraph:
    """Constructs and compiles the hybrid CAD orchestrator graph."""
    tools_map = {tool.name: tool for tool in tools}

    workflow = StateGraph(CADAgentState)

    def plan_node_wrapper(state: CADAgentState):
        return plan_steps_node(state, llm)

    async def execute_node_wrapper(state: CADAgentState):
        return await execute_step_node(state, tools_map)

    workflow.add_node("plan_steps", plan_node_wrapper)
    workflow.add_node("execute_step", execute_node_wrapper)
    workflow.add_node("verify_step", verify_step_node)

    workflow.add_edge(START, "plan_steps")
    workflow.add_edge("plan_steps", "execute_step")
    workflow.add_edge("execute_step", "verify_step")

    workflow.add_conditional_edges(
        "verify_step",
        route_next,
        {
            "execute_step": "execute_step",
            "plan_steps": "plan_steps",
            "finish": END
        }
    )

    return workflow.compile()