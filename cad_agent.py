import os
import sys
import json
import re
import asyncio
import traceback
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
    tool_name: str = Field(default="python_script_execution", description="Execution tool name or strategy.")
    status: Literal["pending", "success", "failed"] = Field(default="pending", description="Status of step execution.")
    error_feedback: str = Field(default="", description="Captured stderr or exception trace if execution fails.")
    script_path: str = Field(default="", description="Path to the generated Python script for this step.")
    python_code: str = Field(default="", description="The generated Python script code.")


class Plan(BaseModel):
    steps: List[Step] = Field(description="Sequential list of CAD operations to execute.")


class ScriptOutput(BaseModel):
    python_code: str = Field(description="Complete, self-contained, executable Python script to perform the CAD step.")
    explanation: str = Field(description="Brief explanation of the REST API call or FeatureScript payload constructed.")


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


PLANNER_SYSTEM_PROMPT = """You are an expert CAD Design Automation Architect for Onshape.
Your role is to analyze a user prompt and generate a clean, step-by-step CAD plan.

Current Onshape Model State Mirror (Local Active Context):
{onshape_state_json}

INSTRUCTIONS & WORKFLOW RULES:
1. For creating solid 3D geometry (e.g. cylinders, cubes, blocks, prisms):
   - Always create at least 2 sequential steps:
     * Step 1: Create Top plane sketch containing the base profile (circle, rectangle, etc.) with dimensions in meters.
     * Step 2: Extrude the sketch region to height/depth (in meters or using unit strings like "5.0 cm").
2. For 2D sketches (e.g., drawing a rectangle, square, or circle without extrusion):
   - Create a single sketch step containing all lines/curves and coincident constraints.
3. Keep steps focused, sequential, and clear.
4. Reference active document, workspace, and element IDs from context if provided.

Output a valid structured Plan containing step_id and description for each step."""


SCRIPT_GEN_SYSTEM_PROMPT = """You are an expert CAD Automation Software Engineer specializing in Onshape REST API Python script generation.
Your goal is to write a standalone, executable Python script that performs a specific CAD operation in Onshape via its REST API.

Context & Identifiers:
- Document ID: {doc_id}
- Workspace ID: {workspace_id}
- Element ID: {element_id}
- Active Mirror State: {onshape_state_json}

Step Description:
{step_description}

CRITICAL PYTHON SYNTAX REQUIREMENTS:
- In Python code, always use capitalized Python booleans `True` and `False` (NOT lowercase JSON `true`/`false`).
- All geometry dimensions in Onshape REST payloads MUST be in METERS (1 cm = 0.01 meters, 2 cm = 0.02 meters, 3 cm = 0.03 meters).

VERIFIED ONSHAPE REST API EXACT PAYLOAD TEMPLATES:

1. Endpoint URL:
   `https://cad.onshape.com/api/v16/partstudios/d/{doc_id}/w/{workspace_id}/e/{element_id}/features`

2. RECTANGLE / SQUARE SKETCH PAYLOAD TEMPLATE (`BTMSketch-151` with `BTCurveGeometryLine-117` & `COINCIDENT` constraints):
   Use vector parametrization (`pntX`, `pntY`, `dirX`, `dirY`, `startParam`, `endParam`) and `BTMSketchConstraint-2` endpoint linking:

```python
   # Example for a rectangle 2 cm (0.02m) along X and 3 cm (0.03m) along Y centered at origin:
   w_m = 0.02  # width in meters
   h_m = 0.03  # height in meters
   hw = w_m / 2.0
   hh = h_m / 2.0
   x_min, x_max = -hw, hw
   y_min, y_max = -hh, hh

   payload = {{
       "feature": {{
           "btType": "BTMSketch-151",
           "featureType": "newSketch",
           "name": "Rectangle Sketch",
           "parameters": [
               {{
                   "btType": "BTMParameterQueryList-148",
                   "parameterId": "sketchPlane",
                   "queries": [
                       {{
                           "btType": "BTMIndividualQuery-138",
                           "queryString": "query=qCreatedBy(makeId('Top'), EntityType.FACE);"
                       }}
                   ]
               }}
           ],
           "entities": [
               # Line 1: Bottom (+X)
               {{
                   "btType": "BTMSketchCurveSegment-155",
                   "geometry": {{"btType": "BTCurveGeometryLine-117", "pntX": x_min, "pntY": y_min, "dirX": 1.0, "dirY": 0.0}},
                   "startPointId": "lineBottom.start", "endPointId": "lineBottom.end",
                   "startParam": 0.0, "endParam": w_m, "entityId": "lineBottom"
               }},
               # Line 2: Right (+Y)
               {{
                   "btType": "BTMSketchCurveSegment-155",
                   "geometry": {{"btType": "BTCurveGeometryLine-117", "pntX": x_max, "pntY": y_min, "dirX": 0.0, "dirY": 1.0}},
                   "startPointId": "lineRight.start", "endPointId": "lineRight.end",
                   "startParam": 0.0, "endParam": h_m, "entityId": "lineRight"
               }},
               # Line 3: Top (-X)
               {{
                   "btType": "BTMSketchCurveSegment-155",
                   "geometry": {{"btType": "BTCurveGeometryLine-117", "pntX": x_max, "pntY": y_max, "dirX": -1.0, "dirY": 0.0}},
                   "startPointId": "lineTop.start", "endPointId": "lineTop.end",
                   "startParam": 0.0, "endParam": w_m, "entityId": "lineTop"
               }},
               # Line 4: Left (-Y)
               {{
                   "btType": "BTMSketchCurveSegment-155",
                   "geometry": {{"btType": "BTCurveGeometryLine-117", "pntX": x_min, "pntY": y_max, "dirX": 0.0, "dirY": -1.0}},
                   "startPointId": "lineLeft.start", "endPointId": "lineLeft.end",
                   "startParam": 0.0, "endParam": h_m, "entityId": "lineLeft"
               }}
           ],
           "constraints": [
               {{
                   "btType": "BTMSketchConstraint-2",
                   "constraintType": "COINCIDENT",
                   "parameters": [
                       {{"btType": "BTMParameterString-149", "parameterId": "localFirst", "value": "lineBottom.end"}},
                       {{"btType": "BTMParameterString-149", "parameterId": "localSecond", "value": "lineRight.start"}}
                   ]
               }},
               {{
                   "btType": "BTMSketchConstraint-2",
                   "constraintType": "COINCIDENT",
                   "parameters": [
                       {{"btType": "BTMParameterString-149", "parameterId": "localFirst", "value": "lineRight.end"}},
                       {{"btType": "BTMParameterString-149", "parameterId": "localSecond", "value": "lineTop.start"}}
                   ]
               }},
               {{
                   "btType": "BTMSketchConstraint-2",
                   "constraintType": "COINCIDENT",
                   "parameters": [
                       {{"btType": "BTMParameterString-149", "parameterId": "localFirst", "value": "lineTop.end"}},
                       {{"btType": "BTMParameterString-149", "parameterId": "localSecond", "value": "lineLeft.start"}}
                   ]
               }},
               {{
                   "btType": "BTMSketchConstraint-2",
                   "constraintType": "COINCIDENT",
                   "parameters": [
                       {{"btType": "BTMParameterString-149", "parameterId": "localFirst", "value": "lineLeft.end"}},
                       {{"btType": "BTMParameterString-149", "parameterId": "localBottom.start"}}
                   ]
               }}
           ]
       }}
   }}
```

3. CIRCLE SKETCH TEMPLATE (`BTMSketch-151` with `BTCurveGeometryCircle-115`):
```python
   payload = {{
       "feature": {{
           "btType": "BTMSketch-151",
           "featureType": "newSketch",
           "name": "Base Circle Sketch",
           "parameters": [
               {{
                   "btType": "BTMParameterQueryList-148",
                   "parameterId": "sketchPlane",
                   "queries": [
                       {{
                           "btType": "BTMIndividualQuery-138",
                           "queryString": "query=qCreatedBy(makeId('Top'), EntityType.FACE);"
                       }}
                   ]
               }}
           ],
           "entities": [
               {{
                   "btType": "BTMSketchCurve-4",
                   "geometry": {{
                       "btType": "BTCurveGeometryCircle-115",
                       "radius": 0.01,  # radius in meters
                       "xCenter": 0.0,
                       "yCenter": 0.0,
                       "xDir": 1.0,
                       "yDir": 0.0,
                       "clockwise": False
                   }},
                   "centerId": "circleCenter",
                   "entityId": "circle1"
               }}
           ],
           "constraints": []
       }}
   }}
```

4. EXTRUDE TEMPLATE (`BTMFeature-134` with `BTMIndividualSketchRegionQuery-140`):
```python
   payload = {{
       "feature": {{
           "btType": "BTMFeature-134",
           "featureType": "extrude",
           "name": "Extrude Feature",
           "parameters": [
               {{
                   "btType": "BTMParameterEnum-145",
                   "parameterId": "extrudeType",
                   "value": "BLIND"
               }},
               {{
                   "btType": "BTMParameterQuantity-147",
                   "parameterId": "depth",
                   "expression": "5.0 cm"
               }},
               {{
                   "btType": "BTMParameterQueryList-148",
                   "parameterId": "entities",
                   "queries": [
                       {{
                           "btType": "BTMIndividualSketchRegionQuery-140",
                           "featureId": "<sketch_feature_id>"
                       }}
                   ]
               }}
           ]
       }}
   }}
```

SCRIPT OUTPUT REQUIREMENTS:
- Use `requests.post(url, headers={{'Content-Type': 'application/json'}}, auth=HTTPBasicAuth(os.environ.get('ONSHAPE_ACCESS_KEY'), os.environ.get('ONSHAPE_SECRET_KEY')), data=json.dumps(payload))`.
- Parse `response.json()`. On HTTP 200/201 success, extract `feature_id = response.json()['feature']['featureId']` and print `SUCCESS: Feature ID: <feature_id>` to stdout.
- If status code != 200/201 or exception occurs, print error response text to stderr and exit with code 1 (`sys.exit(1)`).
- Provide a completely self-contained script with imports (`os`, `sys`, `json`, `requests`, `from requests.auth import HTTPBasicAuth`).

Output a structured JSON object containing python_code and explanation."""


SCRIPT_REFACTOR_SYSTEM_PROMPT = """A previously generated Python script failed during execution.
Analyze the failing script code, stderr/traceback, Onshape error response, and the exact payload templates above to rewrite a working Python script.

Failing Script Code:
```python
{failing_code}
```

Execution stderr / Traceback:
{stderr_output}

Step Description:
{step_description}

Active Mirror State:
{onshape_state_json}

INSTRUCTIONS:
1. Use Python booleans `True` and `False` (capitalized) in Python dict code.
2. For line curves, use `BTMSketchCurveSegment-155` with `BTCurveGeometryLine-117` (`pntX`, `pntY`, `dirX`, `dirY`, `startParam`, `endParam`) and `COINCIDENT` constraints.
3. Use endpoint URL `https://cad.onshape.com/api/v16/partstudios/d/{doc_id}/w/{workspace_id}/e/{element_id}/features`.
4. Output corrected python_code and explanation."""


def plan_steps_node(state: CADAgentState, llm: BaseChatModel) -> Dict[str, Any]:
    """Planner Node: Generates initial CAD plan and logs full prompt/response."""
    user_prompt = state.get("user_prompt", "")
    session_id = state.get("session_id", "default")
    session_mgr = SessionManager(session_id)

    session_mgr.log_debug("PLANNER", f"Received user prompt: '{user_prompt}'")

    current_mirror_state = session_mgr.get_onshape_state()
    mirror_str = json.dumps(current_mirror_state, indent=2)

    planner_llm = llm.with_structured_output(Plan, method="function_calling")
    sys_msg = SystemMessage(content=PLANNER_SYSTEM_PROMPT.format(onshape_state_json=mirror_str))

    try:
        session_mgr.log_debug("PLANNER", "Invoking LLM for plan generation...", {
            "system_prompt": sys_msg.content,
            "user_prompt": user_prompt
        })

        plan_output: Plan = planner_llm.invoke([sys_msg, HumanMessage(content=user_prompt)])
        steps_dict = [s.model_dump() for s in plan_output.steps]

        session_mgr.save_plan(steps_dict)
        session_mgr.log_debug("PLANNER", f"Generated plan with {len(steps_dict)} steps.", steps_dict)

        log_msg = f"📋 Generated plan with {len(steps_dict)} dynamic Python execution steps."
        return {
            "plan": steps_dict,
            "current_step_index": 0,
            "step_retries": 0,
            "replan_count": 0,
            "execution_logs": state.get("execution_logs", []) + [log_msg]
        }
    except Exception as e:
        tb_str = traceback.format_exc()
        error_msg = f"❌ Dynamic Planning failed: {str(e)}"
        session_mgr.log_debug("ERROR", f"Planning Exception: {str(e)}", tb_str)
        return {
            "plan": [],
            "execution_logs": state.get("execution_logs", []) + [error_msg]
        }


async def generate_and_execute_script_node(state: CADAgentState, llm: BaseChatModel) -> Dict[str, Any]:
    """
    Code Generation & Subprocess Execution Node:
    Generates Python script for current step, runs subprocess, and logs full trace.
    """
    plan = list(state["plan"])
    idx = state["current_step_index"]
    retries = state.get("step_retries", 0)
    session_id = state.get("session_id", "default")
    session_mgr = SessionManager(session_id)

    if idx >= len(plan):
        return {"plan": plan}

    step = plan[idx]
    logs = list(state.get("execution_logs", []))

    # Regex extraction from prompt or mirror context
    prompt_text = state.get("user_prompt", "")
    doc_match = re.search(r'(?:Doc(?:ument)?(?:\s*ID)?[:\s]+)([a-f0-9]{24})', prompt_text, re.IGNORECASE)
    work_match = re.search(r'(?:Work(?:space)?(?:\s*ID)?[:\s]+)([a-f0-9]{24})', prompt_text, re.IGNORECASE)
    elem_match = re.search(r'(?:Elem(?:ent)?(?:\s*ID)?[:\s]+)([a-f0-9]{24})', prompt_text, re.IGNORECASE)

    onshape_mirror = session_mgr.get_onshape_state()
    final_doc = (doc_match.group(1) if doc_match else None) or onshape_mirror.get("document_id") or "2522d956c5986dc4a4d60e20"
    final_work = (work_match.group(1) if work_match else None) or onshape_mirror.get("workspace_id") or "11aa163db6bf10fd2aa0d97c"
    final_elem = (elem_match.group(1) if elem_match else None) or onshape_mirror.get("element_id") or "ac775850a6fea04e0606985d"

    session_mgr.update_onshape_state(doc_id=final_doc, workspace_id=final_work, element_id=final_elem)
    mirror_str = json.dumps(session_mgr.get_onshape_state(), indent=2)

    script_gen_llm = llm.with_structured_output(ScriptOutput, method="function_calling")

    # Step 1: Generate or Refactor Python Script
    if retries > 0 and step.get("script_path") and os.path.exists(step["script_path"]):
        with open(step["script_path"], "r", encoding="utf-8") as f:
            failing_code = f.read()

        sys_prompt = SCRIPT_REFACTOR_SYSTEM_PROMPT.format(
            failing_code=failing_code,
            stderr_output=step.get("error_feedback", "Script exited with failure code."),
            step_description=step["description"],
            doc_id=final_doc,
            workspace_id=final_work,
            element_id=final_elem,
            onshape_state_json=mirror_str
        )
        log_msg = f"🔄 Refactoring Python script for Step {idx + 1} (Attempt {retries + 1})..."
        session_mgr.log_debug("REFACTOR", f"Refactoring step {idx + 1} due to error", {
            "failing_code": failing_code,
            "error_feedback": step.get("error_feedback")
        })
    else:
        sys_prompt = SCRIPT_GEN_SYSTEM_PROMPT.format(
            doc_id=final_doc,
            workspace_id=final_work,
            element_id=final_elem,
            onshape_state_json=mirror_str,
            step_description=step["description"]
        )
        log_msg = f"🐍 Generating dynamic Python script for Step {idx + 1}: {step['description']}"
        session_mgr.log_debug("CODEGEN", f"Generating code for Step {idx + 1}: {step['description']}", {
            "doc_id": final_doc,
            "workspace_id": final_work,
            "element_id": final_elem
        })

    logs.append(log_msg)

    try:
        gen_response: ScriptOutput = script_gen_llm.invoke([
            SystemMessage(content=sys_prompt),
            HumanMessage(content=f"User Goal: {prompt_text}\nExecute Step: {step['description']}")
        ])
        python_code = gen_response.python_code

        # Save Python script
        script_filename = f"step_{idx + 1}_attempt_{retries + 1}.py"
        script_path = os.path.join(session_mgr.folder_path, script_filename)

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(python_code)

        step["script_path"] = script_path
        step["python_code"] = python_code

        session_mgr.log_debug("CODEGEN", f"Saved generated script to {script_filename}", {
            "explanation": gen_response.explanation,
            "code": python_code
        })

        # Step 2: Execute Python script in Subprocess
        logs.append(f"⚙️ Running script in subprocess: `{script_filename}`")
        session_mgr.log_debug("SUBPROCESS", f"Running `{sys.executable} {script_path}`...")

        env_vars = os.environ.copy()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env_vars
        )

        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout_str = stdout_bytes.decode(errors="replace").strip()
        stderr_str = stderr_bytes.decode(errors="replace").strip()

        session_mgr.log_debug("SUBPROCESS", f"Subprocess finished with exit code {proc.returncode}", {
            "exit_code": proc.returncode,
            "stdout": stdout_str,
            "stderr": stderr_str
        })

        if proc.returncode == 0:
            step["status"] = "success"
            step["error_feedback"] = ""
            logs.append(f"✅ Script stdout: {stdout_str[:200]}")

            match = re.search(r"Feature ID:\s*([A-Za-z0-9_\-]+)", stdout_str)
            extracted_fid = match.group(1) if match else None

            desc_lower = step["description"].lower()
            feat_type = "sketch" if "sketch" in desc_lower or "rectangle" in desc_lower or "circle" in desc_lower else "dynamic_python_script"

            session_mgr.update_onshape_state(
                doc_id=final_doc,
                workspace_id=final_work,
                element_id=final_elem,
                feature_name=step["description"],
                feature_id=extracted_fid,
                feature_type=feat_type,
                params={"script_path": script_path}
            )
        else:
            step["status"] = "failed"
            combined_err = f"Return Code: {proc.returncode}\nStderr: {stderr_str}\nStdout: {stdout_str}"
            step["error_feedback"] = combined_err
            logs.append(f"❌ Script Execution Error ({script_filename}): {stderr_str[:200] or stdout_str[:200]}")

    except Exception as e:
        tb_str = traceback.format_exc()
        step["status"] = "failed"
        step["error_feedback"] = f"Script execution exception: {str(e)}"
        logs.append(f"❌ Execution exception: {str(e)}")
        session_mgr.log_debug("ERROR", f"Execution Node Exception: {str(e)}", tb_str)

    session_mgr.save_plan(plan)
    return {"plan": plan, "execution_logs": logs}


def verify_step_node(state: CADAgentState) -> Dict[str, Any]:
    """Evaluator Node: Checks step outcome and handles retry or transition."""
    plan = list(state["plan"])
    idx = state["current_step_index"]
    session_id = state.get("session_id", "default")
    session_mgr = SessionManager(session_id)

    if idx >= len(plan):
        return {"plan": plan}

    step = plan[idx]
    retries = state.get("step_retries", 0)
    logs = list(state.get("execution_logs", []))

    if step["status"] == "success":
        logs.append(f"✅ Verified Step {idx + 1}: Success")
        session_mgr.log_debug("EVALUATOR", f"Step {idx + 1} passed verification.")
        return {
            "plan": plan,
            "current_step_index": idx + 1,
            "step_retries": 0,
            "execution_logs": logs
        }
    else:
        logs.append(f"⚠️ Step {idx + 1} Failed Verification (Attempt {retries + 1}): {step.get('error_feedback', '')[:200]}")
        session_mgr.log_debug("EVALUATOR", f"Step {idx + 1} failed verification (Attempt {retries + 1})", {
            "error_feedback": step.get("error_feedback")
        })
        return {
            "plan": plan,
            "step_retries": retries + 1,
            "execution_logs": logs
        }


def route_next(state: CADAgentState) -> Literal["execute_step", "plan_steps", "finish"]:
    """Determines next node routing based on plan completion or retries."""
    plan = state.get("plan", [])
    idx = state.get("current_step_index", 0)
    retries = state.get("step_retries", 0)
    max_retries = state.get("max_retries", 3)

    if not plan or idx >= len(plan):
        return "finish"

    if plan[idx].get("status") == "failed":
        if retries < max_retries:
            return "execute_step"
        else:
            return "finish"

    return "execute_step"


def build_cad_orchestrator_graph(llm: BaseChatModel, tools: List[BaseTool] = None) -> StateGraph:
    """Constructs and compiles the dynamic Python script execution graph."""
    workflow = StateGraph(CADAgentState)

    def plan_node_wrapper(state: CADAgentState):
        return plan_steps_node(state, llm)

    async def execute_node_wrapper(state: CADAgentState):
        return await generate_and_execute_script_node(state, llm)

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