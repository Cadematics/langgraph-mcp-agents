import os
import json
import base64
import requests
from typing import Optional, Any
from mcp.server.fastmcp import FastMCP

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
    doc_id: str = "", 
    workspace_id: str = "", 
    element_id: str = "", 
    radius_cm: float = 1.5,
    diameter_cm: float = 3.0,
    sketch_name: str = "Circle Profile",
    x_center_cm: float = 0.0,
    y_center_cm: float = 0.0
) -> str:
    """
    Creates a circular sketch on the Top plane in an Onshape Part Studio.
    Ideal for creating solid cylinders, disks, or pins when extruded.
    
    Args:
        doc_id: Onshape Document ID
        workspace_id: Onshape Workspace ID
        element_id: Onshape Part Studio Element ID
        radius_cm: Radius in centimeters (e.g. 1.5)
        diameter_cm: Diameter in centimeters (e.g. 3.0)
        sketch_name: Name for the created sketch
        x_center_cm: X coordinate of center in centimeters
        y_center_cm: Y coordinate of center in centimeters
    """
    if not doc_id or not workspace_id or not element_id:
        return "ERROR: Missing required identifiers (doc_id, workspace_id, or element_id)."

    try:
        radius = float(radius_cm) if radius_cm else float(diameter_cm) / 2.0
        cx = float(x_center_cm) / 100.0
        cy = float(y_center_cm) / 100.0
    except (ValueError, TypeError):
        radius, cx, cy = 1.5, 0.0, 0.0

    url = f"https://cad.onshape.com/api/v16/partstudios/d/{doc_id}/w/{workspace_id}/e/{element_id}/features"
    radius_meters = float(radius) / 100.0

    payload = {
        "feature": {
            "btType": "BTMSketch-151",
            "featureType": "newSketch",
            "name": sketch_name,
            "parameters": [
                {
                    "btType": "BTMParameterQueryList-148",
                    "parameterId": "sketchPlane",
                    "queries": [{"btType": "BTMIndividualQuery-138", "queryString": "query=qCreatedBy(makeId('Top'), EntityType.FACE);"}]
                }
            ],
            "entities": [
                {
                    "btType": "BTMSketchCurve-4",
                    "geometry": {
                        "btType": "BTCurveGeometryCircle-115",
                        "radius": radius_meters,
                        "xCenter": cx,
                        "yCenter": cy,
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
        return f"SUCCESS: Circle sketch '{sketch_name}' created. Feature ID: {feature_id}"
    except Exception as e:
        err_body = response.text if 'response' in locals() and response is not None else str(e)
        return f"ERROR creating circle sketch: {str(e)} - {err_body}"


@mcp.tool()
def create_rectangle_sketch(
    doc_id: str = "",
    workspace_id: str = "",
    element_id: str = "",
    width_cm: float = 5.0,
    height_cm: float = 5.0,
    sketch_name: str = "Rectangle Profile",
    x_center_cm: float = 0.0,
    y_center_cm: float = 0.0
) -> str:
    """
    Creates a solid RECTANGLE or SQUARE sketch on the Top plane in an Onshape Part Studio.
    Uses BTCurveGeometryLine-117 vector serialization and coincident endpoint constraints.
    Ideal for creating solid cubes, rectangular blocks, plates, or prisms when extruded.
    
    Args:
        doc_id: Onshape Document ID
        workspace_id: Onshape Workspace ID
        element_id: Onshape Part Studio Element ID
        width_cm: Width of rectangle in centimeters (e.g. 5.0)
        height_cm: Height of rectangle in centimeters (e.g. 5.0)
        sketch_name: Name for the sketch feature
        x_center_cm: X coordinate of center in centimeters
        y_center_cm: Y coordinate of center in centimeters
    """
    if not doc_id or not workspace_id or not element_id:
        return "ERROR: Missing required identifiers (doc_id, workspace_id, or element_id)."

    try:
        w_m = float(width_cm) / 100.0
        h_m = float(height_cm) / 100.0
        cx = float(x_center_cm) / 100.0
        cy = float(y_center_cm) / 100.0
    except (ValueError, TypeError):
        w_m, h_m, cx, cy = 0.05, 0.05, 0.0, 0.0

    hw_m = w_m / 2.0
    hh_m = h_m / 2.0

    x_min = cx - hw_m
    x_max = cx + hw_m
    y_min = cy - hh_m
    y_max = cy + hh_m

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
                    "queries": [{"btType": "BTMIndividualQuery-138", "queryString": "query=qCreatedBy(makeId('Top'), EntityType.FACE);"}]
                }
            ],
            "entities": [
                # Bottom Line: from (x_min, y_min) towards +X for length w_m
                {
                    "btType": "BTMSketchCurveSegment-155",
                    "geometry": {
                        "btType": "BTCurveGeometryLine-117",
                        "pntX": x_min,
                        "pntY": y_min,
                        "dirX": 1.0,
                        "dirY": 0.0
                    },
                    "startPointId": "lineBottom.start",
                    "endPointId": "lineBottom.end",
                    "startParam": 0.0,
                    "endParam": w_m,
                    "entityId": "lineBottom"
                },
                # Right Line: from (x_max, y_min) towards +Y for length h_m
                {
                    "btType": "BTMSketchCurveSegment-155",
                    "geometry": {
                        "btType": "BTCurveGeometryLine-117",
                        "pntX": x_max,
                        "pntY": y_min,
                        "dirX": 0.0,
                        "dirY": 1.0
                    },
                    "startPointId": "lineRight.start",
                    "endPointId": "lineRight.end",
                    "startParam": 0.0,
                    "endParam": h_m,
                    "entityId": "lineRight"
                },
                # Top Line: from (x_max, y_max) towards -X for length w_m
                {
                    "btType": "BTMSketchCurveSegment-155",
                    "geometry": {
                        "btType": "BTCurveGeometryLine-117",
                        "pntX": x_max,
                        "pntY": y_max,
                        "dirX": -1.0,
                        "dirY": 0.0
                    },
                    "startPointId": "lineTop.start",
                    "endPointId": "lineTop.end",
                    "startParam": 0.0,
                    "endParam": w_m,
                    "entityId": "lineTop"
                },
                # Left Line: from (x_min, y_max) towards -Y for length h_m
                {
                    "btType": "BTMSketchCurveSegment-155",
                    "geometry": {
                        "btType": "BTCurveGeometryLine-117",
                        "pntX": x_min,
                        "pntY": y_max,
                        "dirX": 0.0,
                        "dirY": -1.0
                    },
                    "startPointId": "lineLeft.start",
                    "endPointId": "lineLeft.end",
                    "startParam": 0.0,
                    "endParam": h_m,
                    "entityId": "lineLeft"
                }
            ],
            "constraints": [
                {
                    "btType": "BTMSketchConstraint-2",
                    "constraintType": "COINCIDENT",
                    "parameters": [
                        {"btType": "BTMParameterString-149", "parameterId": "localFirst", "value": "lineBottom.end"},
                        {"btType": "BTMParameterString-149", "parameterId": "localSecond", "value": "lineRight.start"}
                    ]
                },
                {
                    "btType": "BTMSketchConstraint-2",
                    "constraintType": "COINCIDENT",
                    "parameters": [
                        {"btType": "BTMParameterString-149", "parameterId": "localFirst", "value": "lineRight.end"},
                        {"btType": "BTMParameterString-149", "parameterId": "localSecond", "value": "lineTop.start"}
                    ]
                },
                {
                    "btType": "BTMSketchConstraint-2",
                    "constraintType": "COINCIDENT",
                    "parameters": [
                        {"btType": "BTMParameterString-149", "parameterId": "localFirst", "value": "lineTop.end"},
                        {"btType": "BTMParameterString-149", "parameterId": "localSecond", "value": "lineLeft.start"}
                    ]
                },
                {
                    "btType": "BTMSketchConstraint-2",
                    "constraintType": "COINCIDENT",
                    "parameters": [
                        {"btType": "BTMParameterString-149", "parameterId": "localFirst", "value": "lineLeft.end"},
                        {"btType": "BTMParameterString-149", "parameterId": "lineBottom.start"}
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
        return f"SUCCESS: Rectangle sketch '{sketch_name}' created. Feature ID: {feature_id}"
    except Exception as e:
        err_body = response.text if 'response' in locals() and response is not None else str(e)
        return f"ERROR creating rectangle sketch: {str(e)} - {err_body}"


@mcp.tool()
def create_rectangle_with_hole_sketch(
    doc_id: str = "",
    workspace_id: str = "",
    element_id: str = "",
    width_cm: float = 5.0,
    height_cm: float = 5.0,
    hole_diameter_cm: float = 2.0,
    sketch_name: str = "Hollow Rectangle Profile"
) -> str:
    """
    Creates a sketch with a RECTANGLE/SQUARE outer boundary and a CENTERED CIRCULAR HOLE on the Top plane.
    Ideal for creating hollow blocks or cubes with center holes when extruded.
    
    Args:
        doc_id: Onshape Document ID
        workspace_id: Onshape Workspace ID
        element_id: Onshape Part Studio Element ID
        width_cm: Width of rectangle in centimeters (e.g. 5.0)
        height_cm: Height of rectangle in centimeters (e.g. 5.0)
        hole_diameter_cm: Diameter of central hole in centimeters (e.g. 2.0)
        sketch_name: Name for the sketch feature
    """
    if not doc_id or not workspace_id or not element_id:
        return "ERROR: Missing required identifiers (doc_id, workspace_id, or element_id)."

    try:
        w_m = float(width_cm) / 100.0
        h_m = float(height_cm) / 100.0
        hole_r_m = (float(hole_diameter_cm) / 2.0) / 100.0
    except (ValueError, TypeError):
        w_m, h_m, hole_r_m = 0.05, 0.05, 0.01

    hw_m = w_m / 2.0
    hh_m = h_m / 2.0

    x_min, x_max = -hw_m, hw_m
    y_min, y_max = -hh_m, hh_m

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
                    "queries": [{"btType": "BTMIndividualQuery-138", "queryString": "query=qCreatedBy(makeId('Top'), EntityType.FACE);"}]
                }
            ],
            "entities": [
                # Bottom Line
                {
                    "btType": "BTMSketchCurveSegment-155",
                    "geometry": {
                        "btType": "BTCurveGeometryLine-117",
                        "pntX": x_min,
                        "pntY": y_min,
                        "dirX": 1.0,
                        "dirY": 0.0
                    },
                    "startPointId": "lineBottom.start",
                    "endPointId": "lineBottom.end",
                    "startParam": 0.0,
                    "endParam": w_m,
                    "entityId": "lineBottom"
                },
                # Right Line
                {
                    "btType": "BTMSketchCurveSegment-155",
                    "geometry": {
                        "btType": "BTCurveGeometryLine-117",
                        "pntX": x_max,
                        "pntY": y_min,
                        "dirX": 0.0,
                        "dirY": 1.0
                    },
                    "startPointId": "lineRight.start",
                    "endPointId": "lineRight.end",
                    "startParam": 0.0,
                    "endParam": h_m,
                    "entityId": "lineRight"
                },
                # Top Line
                {
                    "btType": "BTMSketchCurveSegment-155",
                    "geometry": {
                        "btType": "BTCurveGeometryLine-117",
                        "pntX": x_max,
                        "pntY": y_max,
                        "dirX": -1.0,
                        "dirY": 0.0
                    },
                    "startPointId": "lineTop.start",
                    "endPointId": "lineTop.end",
                    "startParam": 0.0,
                    "endParam": w_m,
                    "entityId": "lineTop"
                },
                # Left Line
                {
                    "btType": "BTMSketchCurveSegment-155",
                    "geometry": {
                        "btType": "BTCurveGeometryLine-117",
                        "pntX": x_min,
                        "pntY": y_max,
                        "dirX": 0.0,
                        "dirY": -1.0
                    },
                    "startPointId": "lineLeft.start",
                    "endPointId": "lineLeft.end",
                    "startParam": 0.0,
                    "endParam": h_m,
                    "entityId": "lineLeft"
                },
                # Inner Hole Circle
                {
                    "btType": "BTMSketchCurve-4",
                    "geometry": {
                        "btType": "BTCurveGeometryCircle-115",
                        "radius": hole_r_m,
                        "xCenter": 0.0,
                        "yCenter": 0.0,
                        "xDir": 1.0,
                        "yDir": 0.0,
                        "clockwise": False
                    },
                    "centerId": "holeCenter",
                    "entityId": "holeCircle"
                }
            ],
            "constraints": [
                {
                    "btType": "BTMSketchConstraint-2",
                    "constraintType": "COINCIDENT",
                    "parameters": [
                        {"btType": "BTMParameterString-149", "parameterId": "localFirst", "value": "lineBottom.end"},
                        {"btType": "BTMParameterString-149", "parameterId": "localSecond", "value": "lineRight.start"}
                    ]
                },
                {
                    "btType": "BTMSketchConstraint-2",
                    "constraintType": "COINCIDENT",
                    "parameters": [
                        {"btType": "BTMParameterString-149", "parameterId": "localFirst", "value": "lineRight.end"},
                        {"btType": "BTMParameterString-149", "parameterId": "localSecond", "value": "lineTop.start"}
                    ]
                },
                {
                    "btType": "BTMSketchConstraint-2",
                    "constraintType": "COINCIDENT",
                    "parameters": [
                        {"btType": "BTMParameterString-149", "parameterId": "localFirst", "value": "lineTop.end"},
                        {"btType": "BTMParameterString-149", "parameterId": "localSecond", "value": "lineLeft.start"}
                    ]
                },
                {
                    "btType": "BTMSketchConstraint-2",
                    "constraintType": "COINCIDENT",
                    "parameters": [
                        {"btType": "BTMParameterString-149", "parameterId": "localFirst", "value": "lineLeft.end"},
                        {"btType": "BTMParameterString-149", "parameterId": "localSecond", "value": "lineBottom.start"}
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
        return f"SUCCESS: Hollow rectangle sketch '{sketch_name}' created. Feature ID: {feature_id}"
    except Exception as e:
        err_body = response.text if 'response' in locals() and response is not None else str(e)
        return f"ERROR creating rectangle with hole sketch: {str(e)} - {err_body}"


@mcp.tool()
def extrude_sketch(
    doc_id: str = "",
    workspace_id: str = "",
    element_id: str = "",
    sketch_feature_id: str = "",
    depth_cm: float = 5.0,
    extrude_name: str = "Extrude Feature"
) -> str:
    """
    Extrudes all closed region faces of a sketch feature in an Onshape Part Studio by depth_cm.
    Uses BTMIndividualSketchRegionQuery-140 schema to select sketch faces.
    
    Args:
        doc_id: Onshape Document ID
        workspace_id: Onshape Workspace ID
        element_id: Part Studio Element ID
        sketch_feature_id: Feature ID of the sketch to extrude
        depth_cm: Extrusion depth in centimeters (e.g. 5.0)
        extrude_name: Name for the extrude feature
    """
    if not doc_id or not workspace_id or not element_id:
        return "ERROR: Missing required identifiers (doc_id, workspace_id, or element_id)."

    if not sketch_feature_id:
        return "ERROR: Missing sketch_feature_id. Provide the feature ID of the sketch to extrude."

    try:
        d_cm = float(depth_cm)
    except (ValueError, TypeError):
        d_cm = 5.0

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
                    "expression": f"{d_cm} cm"
                },
                {
                    "btType": "BTMParameterQueryList-148",
                    "parameterId": "entities",
                    "queries": [
                        {
                            "btType": "BTMIndividualSketchRegionQuery-140",
                            "featureId": sketch_feature_id
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
    except Exception as e:
        err_body = response.text if 'response' in locals() and response is not None else str(e)
        return f"ERROR creating extrude: {str(e)} - {err_body}"

if __name__ == "__main__":
    mcp.run()