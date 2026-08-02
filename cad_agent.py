import json
from typing import Annotated, Any, Dict, List, TypedDict, Literal
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langgraph.graph.message import add_messages
from langgraph.graph import StateGraph, START, END
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool


class Step(BaseModel):
    step_id: int = Field(description="Unique incremental integer ID for the step.")
    description: str = Field(description="Human-readable description of what this CAD step achieves.")
    tool_name: str = Field(description="Name of the MCP tool to invoke.")
    tool_args: Dict[str, Any] = Field(default_factory=dict, description="Structured argument dictionary for the tool.")
    status: Literal["pending", "success", "failed"] = Field(default="pending", description="Status of step execution.")
    verification_check: str = Field(default="", description="Verification description or query to perform after execution.")
    error_feedback: str = Field(default="", description="Error message captured if execution or verification fails.")

class Plan(BaseModel):
    steps: List[Step] = Field(description="Sequential list of CAD operations to execute.")


class CADAgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    user_prompt: str
    available_tools: List[Dict[str, Any]]
    plan: List[Dict[str, Any]]  # Serialized steps for LangGraph compatibility
    current_step_index: int
    step_retries: int
    max_retries: int
    replan_count: int
    max_replans: int
    execution_logs: List[str]


PLANNER_SYSTEM_PROMPT = """You are an expert Mechanical Design Automation Planner for Onshape CAD.
Your job is to break down a user request into an explicit, ordered sequence of CAD operations.

Available MCP Tools:
{tools_info}

CAD Hierarchy & Operational Rules:
1. TOOL SELECTION RULES:
   - Prefer custom macro tools (e.g. `create_circle_sketch`, `extrude_sketch`) whenever available instead of raw REST API tools.
   - If using `onshape_api_call`, you MUST structure `tool_args` with:
     * `endpoint`: The specific Onshape API endpoint (e.g., "addPartStudioFeature")
     * `parameters`: Path parameters dictionary containing `did`, `wvm` ("w"), `wvmid`, and `eid`
     * `body`: Request body dictionary containing feature specifications
2. SKETCH & FEATURE RULES:
   - Sketches MUST precede Extrusions or 3D features.
   - Keep the plan deterministic, granular, and minimal.

Output a valid structured Plan containing step_id, description, tool_name, and tool_args."""

REPLANNER_SYSTEM_PROMPT = """A CAD execution step failed. Re-evaluate the user request, progress, and error feedback to produce an updated plan starting from the failing step.

Error Feedback:
{error_feedback}

Output an updated structured Plan to recover from this failure."""


def plan_steps_node(state: CADAgentState, llm: BaseChatModel) -> Dict[str, Any]:
    """Planner Node: Generates or updates the step-by-step CAD plan."""
    user_prompt = state.get("user_prompt", "")
    available_tools = state.get("available_tools", [])
    step_retries = state.get("step_retries", 0)
    replan_count = state.get("replan_count", 0)
    
    tools_str = json.dumps(available_tools, indent=2)
    
    # Use function_calling method to allow arbitrary Dict[str, Any] arguments without strict schema errors
    planner_llm = llm.with_structured_output(Plan, method="function_calling")
    
    if step_retries > 0 and state.get("plan"):
        # Re-planning scenario
        current_idx = state.get("current_step_index", 0)
        failed_step = state["plan"][current_idx] if current_idx < len(state["plan"]) else {}
        error = failed_step.get("error_feedback", "Unknown execution error.")
        
        sys_msg = SystemMessage(content=REPLANNER_SYSTEM_PROMPT.format(error_feedback=error))
        prompt_content = f"User Request: {user_prompt}\nFailing Step Context: {json.dumps(failed_step)}"
        replan_count += 1
    else:
        # Initial planning scenario
        sys_msg = SystemMessage(content=PLANNER_SYSTEM_PROMPT.format(tools_info=tools_str))
        prompt_content = user_prompt

    try:
        plan_output: Plan = planner_llm.invoke([sys_msg, HumanMessage(content=prompt_content)])
        steps_dict = [step.model_dump() for step in plan_output.steps]
        
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
    """Step Executor Node: Invokes the specific MCP tool for the current step."""
    plan = list(state["plan"])
    idx = state["current_step_index"]
    
    if idx >= len(plan):
        return {"plan": plan}

    step = plan[idx]
    tool_name = step["tool_name"]
    tool_args = step.get("tool_args", {})
    
    log_msg = f"⚡ Executing Step {idx + 1}/{len(plan)}: [{tool_name}] - {step['description']}"
    logs = state.get("execution_logs", []) + [log_msg]
    
    if tool_name not in tools_map:
        step["status"] = "failed"
        step["error_feedback"] = f"Tool '{tool_name}' is not registered in the available tools list."
        return {"plan": plan, "execution_logs": logs + [f"❌ {step['error_feedback']}"]}

    tool = tools_map[tool_name]
    try:
        # Asynchronously invoke the MCP tool
        result = await tool.ainvoke(tool_args)
        result_str = str(result)
        
        # Check if response string contains Onshape or MCP execution errors
        if "API error" in result_str or "invalid" in result_str.lower() or "missing" in result_str.lower():
            step["status"] = "failed"
            step["error_feedback"] = result_str
            logs.append(f"❌ Execution Failure ({tool_name}): {result_str[:300]}")
        else:
            step["status"] = "success"
            step["error_feedback"] = ""
            logs.append(f"Result ({tool_name}): {result_str[:300]}...")
    except Exception as e:
        step["status"] = "failed"
        step["error_feedback"] = f"Tool invocation exception: {str(e)}"
        logs.append(f"❌ Exception in {tool_name}: {str(e)}")
        
    return {"plan": plan, "execution_logs": logs}


def verify_step_node(state: CADAgentState) -> Dict[str, Any]:
    """CAD Evaluator & Verification Node: Evaluates step success and increments retries/indices."""
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
    """Determines next node based on step status, retry counts, and replan limits."""
    plan = state.get("plan", [])
    idx = state.get("current_step_index", 0)
    retries = state.get("step_retries", 0)
    max_retries = state.get("max_retries", 3)
    replan_count = state.get("replan_count", 0)
    max_replans = state.get("max_replans", 3)

    if not plan or idx >= len(plan):
        return "finish"

    # If the current step failed
    if plan[idx].get("status") == "failed":
        if retries < max_retries:
            return "execute_step"  # Retry current step
        elif replan_count < max_replans:
            return "plan_steps"    # Re-plan remaining steps
        else:
            return "finish"        # Reached hard replan limit

    return "execute_step"


def build_cad_orchestrator_graph(llm: BaseChatModel, tools: List[BaseTool]) -> StateGraph:
    """Constructs and compiles the Plan-and-Execute CAD LangGraph."""
    tools_map = {tool.name: tool for tool in tools}
    
    workflow = StateGraph(CADAgentState)

    def plan_node_wrapper(state: CADAgentState):
        return plan_steps_node(state, llm)

    async def execute_node_wrapper(state: CADAgentState):
        return await execute_step_node(state, tools_map)

    # Add Nodes
    workflow.add_node("plan_steps", plan_node_wrapper)
    workflow.add_node("execute_step", execute_node_wrapper)
    workflow.add_node("verify_step", verify_step_node)

    # Add Edges
    workflow.add_edge(START, "plan_steps")
    workflow.add_edge("plan_steps", "execute_step")
    workflow.add_edge("execute_step", "verify_step")

    # Conditional Routing
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