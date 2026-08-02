import os
import json
from typing import Dict, Any, List, Optional

SESSIONS_BASE_DIR = "sessions"

class SessionManager:
    """
    Manages session directory creation and persistence for:
    1. conversation_history.json
    2. cad_plan.json
    3. onshape_state.json (Live mirror of Onshape CAD document state & feature IDs)
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.folder_path = os.path.join(SESSIONS_BASE_DIR, session_id)
        os.makedirs(self.folder_path, exist_ok=True)

        self.history_file = os.path.join(self.folder_path, "conversation_history.json")
        self.plan_file = os.path.join(self.folder_path, "cad_plan.json")
        self.state_file = os.path.join(self.folder_path, "onshape_state.json")

        self._ensure_files()

    def _ensure_files(self):
        """Initializes state JSON files if they don't exist yet."""
        if not os.path.exists(self.history_file):
            self.write_json(self.history_file, [])
        if not os.path.exists(self.plan_file):
            self.write_json(self.plan_file, {"session_id": self.session_id, "steps": []})
        if not os.path.exists(self.state_file):
            self.write_json(self.state_file, {
                "session_id": self.session_id,
                "document_id": None,
                "workspace_id": None,
                "element_id": None,
                "last_created_sketch_id": None,
                "last_created_feature_id": None,
                "features": []
            })

    def write_json(self, filepath: str, data: Any):
        """Helper to write JSON formatted data safely."""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def read_json(self, filepath: str) -> Any:
        """Helper to read JSON data safely."""
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    def save_history(self, history: List[Dict[str, Any]]):
        """Saves conversation history to local folder."""
        self.write_json(self.history_file, history)

    def save_plan(self, plan: List[Dict[str, Any]]):
        """Saves active step plan to local folder."""
        self.write_json(self.plan_file, {"session_id": self.session_id, "steps": plan})

    def get_onshape_state(self) -> Dict[str, Any]:
        """Reads current Onshape CAD model mirror state."""
        return self.read_json(self.state_file) or {}

    def update_onshape_state(
        self,
        doc_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        element_id: Optional[str] = None,
        feature_name: Optional[str] = None,
        feature_id: Optional[str] = None,
        feature_type: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Updates the state mirror with active IDs and appended features.
        Automatically updates last_created_sketch_id and last_created_feature_id.
        """
        state = self.get_onshape_state()

        if doc_id:
            state["document_id"] = doc_id
        if workspace_id:
            state["workspace_id"] = workspace_id
        if element_id:
            state["element_id"] = element_id

        if feature_id or feature_name:
            feat_entry = {
                "name": feature_name or "Unnamed Feature",
                "feature_id": feature_id,
                "type": feature_type or "unknown",
                "parameters": params or {}
            }
            state["features"].append(feat_entry)

            if feature_id:
                state["last_created_feature_id"] = feature_id
                if feature_type and "sketch" in feature_type.lower():
                    state["last_created_sketch_id"] = feature_id

        self.write_json(self.state_file, state)
        return state