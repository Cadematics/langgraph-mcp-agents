import os
import json
import base64
import requests
from mcp.server.fastmcp import FastMCP

# Initialize the MCP Server
mcp = FastMCP("Onshape Macros")

def get_auth_headers():
    access_key = os.environ.get("ONSHAPE_ACCESS_KEY")
    secret_key = os.environ.get("ONSHAPE_SECRET_KEY")
    
    if not access_key or not secret_key:
        raise ValueError("ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY environment variables are missing.")
    
    auth_str = f"{access_key}:{secret_key}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    
    return {
        "Authorization": f"Basic {b64_auth}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

@mcp.tool()
def create_circle_sketch(
    doc_id: str, 
    workspace_id: str, 
    element_id: str, 
    outer_diameter_cm: float = 3.0,
    sketch_name: str = "Circle Profile"
) -> str:
    """
    Creates a single circular sketch on the Top plane in an Onshape Part Studio.
    
    Args:
        doc_id: The Onshape Document ID (e.g. '6fabc0118136a1b41f69f2b8')
        workspace_id: The Onshape Workspace ID (e.g. '787631b2571c5c29d6c07a6f')
        element_id: The Onshape Part Studio Element ID (e.g. '8e6f891cb99f3c612919b517')
        outer_diameter_cm: Diameter of the circle in centimeters (e.g. 3.0)
        sketch_name: Name for the created sketch feature in Onshape
    """
    url = f"https://cad.onshape.com/api/v16/partstudios/d/{doc_id}/w/{workspace_id}/e/{element_id}/features"
    radius_meters = (outer_diameter_cm / 2.0) / 100.0

    payload = {
        "feature": {
            "btType": "BTMSketch-151",
            "featureType": "newSketch",
            "name": sketch_name,
            "parameters": [
                {
                    "btType": "BTMParameterQueryList-148",
                    "parameterId": "sketchPlane",
                    "queries": [
                        {
                            "btType": "BTMIndividualQuery-138",
                            "queryString": "query=qCreatedBy(makeId('Top'), EntityType.FACE);"
                        }
                    ]
                }
            ],
            "entities": [
                {
                    "btType": "BTMSketchCurve-4",
                    "geometry": {
                        "btType": "BTCurveGeometryCircle-115",
                        "radius": radius_meters,
                        "xCenter": 0.0,
                        "yCenter": 0.0,
                        "xDir": 1.0,
                        "yDir": 0.0,
                        "clockwise": False
                    },
                    "centerId": "circleCenter",
                    "entityId": "circle1"
                }
            ],
            "constraints": []
        }
    }

    try:
        response = requests.post(url, headers=get_auth_headers(), json=payload)
        response.raise_for_status()
        data = response.json()
        
        feature_id = data.get("feature", {}).get("featureId", "Unknown ID")
        return f"SUCCESS: Sketch '{sketch_name}' created. Feature ID: {feature_id}"
        
    except requests.exceptions.RequestException as e:
        err_body = response.text if 'response' in locals() and response is not None else str(e)
        return f"ERROR creating sketch: {str(e)} - {err_body}"

@mcp.tool()
def create_concentric_circles_sketch(
    doc_id: str,
    workspace_id: str,
    element_id: str,
    outer_diameter_cm: float = 2.0,
    inner_diameter_cm: float = 1.0,
    sketch_name: str = "Hollow Cylinder Profile"
) -> str:
    """
    Creates a sketch with TWO concentric circles (outer boundary and inner hole) on the Top plane.
    When extruded, Onshape automatically extrudes the annular profile to form a cylinder with a hole.
    
    Args:
        doc_id: The Onshape Document ID
        workspace_id: The Onshape Workspace ID
        element_id: The Onshape Part Studio Element ID
        outer_diameter_cm: Outer diameter in centimeters (e.g. 2.0)
        inner_diameter_cm: Inner hole diameter in centimeters (e.g. 1.0)
        sketch_name: Name for the sketch feature
    """
    outer_radius_m = (outer_diameter_cm / 2.0) / 100.0
    inner_radius_m = (inner_diameter_cm / 2.0) / 100.0

    url = f"https://cad.onshape.com/api/v16/partstudios/d/{doc_id}/w/{workspace_id}/e/{element_id}/features"

    payload = {
        "feature": {
            "btType": "BTMSketch-151",
            "featureType": "newSketch",
            "name": sketch_name,
            "parameters": [
                {
                    "btType": "BTMParameterQueryList-148",
                    "parameterId": "sketchPlane",
                    "queries": [
                        {
                            "btType": "BTMIndividualQuery-138",
                            "queryString": "query=qCreatedBy(makeId('Top'), EntityType.FACE);"
                        }
                    ]
                }
            ],
            "entities": [
                {
                    "btType": "BTMSketchCurve-4",
                    "geometry": {
                        "btType": "BTCurveGeometryCircle-115",
                        "radius": outer_radius_m,
                        "xCenter": 0.0,
                        "yCenter": 0.0,
                        "xDir": 1.0,
                        "yDir": 0.0,
                        "clockwise": False
                    },
                    "centerId": "centerOuter",
                    "entityId": "outerCircle"
                },
                {
                    "btType": "BTMSketchCurve-4",
                    "geometry": {
                        "btType": "BTCurveGeometryCircle-115",
                        "radius": inner_radius_m,
                        "xCenter": 0.0,
                        "yCenter": 0.0,
                        "xDir": 1.0,
                        "yDir": 0.0,
                        "clockwise": False
                    },
                    "centerId": "centerInner",
                    "entityId": "innerCircle"
                }
            ],
            "constraints": []
        }
    }

    try:
        response = requests.post(url, headers=get_auth_headers(), json=payload)
        response.raise_for_status()
        data = response.json()
        feature_id = data.get("feature", {}).get("featureId", "Unknown ID")
        return f"SUCCESS: Concentric sketch '{sketch_name}' created. Feature ID: {feature_id}"
    except requests.exceptions.RequestException as e:
        err_body = response.text if 'response' in locals() and response is not None else str(e)
        return f"ERROR creating concentric sketch: {str(e)} - {err_body}"

@mcp.tool()
def extrude_sketch(
    doc_id: str,
    workspace_id: str,
    element_id: str,
    sketch_feature_id: str,
    depth_cm: float = 4.0,
    extrude_name: str = "Extrude Feature"
) -> str:
    """
    Extrudes a sketch feature in an Onshape Part Studio by a given depth in centimeters.
    
    Args:
        doc_id: Onshape Document ID
        workspace_id: Onshape Workspace ID
        element_id: Part Studio Element ID
        sketch_feature_id: Feature ID of the sketch to extrude (e.g. 'FAkEZLqnVdBJN1w_1')
        depth_cm: Extrusion depth in centimeters (e.g. 4.0)
        extrude_name: Name for the extrude feature
    """
    url = f"https://cad.onshape.com/api/v16/partstudios/d/{doc_id}/w/{workspace_id}/e/{element_id}/features"

    payload = {
        "feature": {
            "btType": "BTMFeature-134",
            "featureType": "extrude",
            "name": extrude_name,
            "parameters": [
                {
                    "btType": "BTMParameterEnum-145",
                    "parameterId": "extrudeType",
                    "value": "BLIND"
                },
                {
                    "btType": "BTMParameterQuantity-147",
                    "parameterId": "depth",
                    "expression": f"{depth_cm} cm"
                },
                {
                    "btType": "BTMParameterQueryList-148",
                    "parameterId": "entities",
                    "queries": [
                        {
                            "btType": "BTMIndividualQuery-138",
                            "queryString": f"query=qSketchRegion(makeId('{sketch_feature_id}'));"
                        }
                    ]
                }
            ]
        }
    }

    try:
        response = requests.post(url, headers=get_auth_headers(), json=payload)
        response.raise_for_status()
        data = response.json()
        feature_id = data.get("feature", {}).get("featureId", "Unknown ID")
        return f"SUCCESS: Extrude '{extrude_name}' created. Feature ID: {feature_id}"
    except requests.exceptions.RequestException as e:
        err_body = response.text if 'response' in locals() and response is not None else str(e)
        return f"ERROR creating extrude: {str(e)} - {err_body}"

if __name__ == "__main__":
    mcp.run()