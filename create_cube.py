import os
import sys
import json
import base64
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=True)

# Target Onshape Document Identifiers
DOC_ID = os.environ.get("TEST_DOC_ID", "6fabc0118136a1b41f69f2b8")
WORKSPACE_ID = os.environ.get("TEST_WORKSPACE_ID", "787631b2571c5c29d6c07a6f")
ELEMENT_ID = os.environ.get("TEST_ELEMENT_ID", "8e6f891cb99f3c612919b517")

def get_auth_headers():
    """Constructs HTTP Basic Auth headers using Onshape API keys."""
    access_key = os.environ.get("ONSHAPE_ACCESS_KEY")
    secret_key = os.environ.get("ONSHAPE_SECRET_KEY")
    
    if not access_key or not secret_key:
        print("❌ ERROR: ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY environment variables are missing.")
        sys.exit(1)
    
    auth_str = f"{access_key}:{secret_key}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    
    return {
        "Authorization": f"Basic {b64_auth}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }


def test_create_rectangle_sketch(width_cm: float = 2.0, height_cm: float = 2.0, name: str = "Test 2cm Square"):
    """
    Creates a closed 4-sided square/rectangle sketch on the Top plane using Onshape's native
    BTCurveGeometryLine-117 schema (pntX, pntY, dirX, dirY, startParam, endParam) and
    coincident endpoint constraints.
    """
    print(f"\n--- 1. Testing create_rectangle_sketch ({width_cm}cm x {height_cm}cm) ---")
    
    url = f"https://cad.onshape.com/api/v16/partstudios/d/{DOC_ID}/w/{WORKSPACE_ID}/e/{ELEMENT_ID}/features"
    
    # Convert dimensions from centimeters to meters for Onshape API
    w_m = float(width_cm) / 100.0
    h_m = float(height_cm) / 100.0

    hw_m = w_m / 2.0
    hh_m = h_m / 2.0

    # Corner coordinates centered at (0,0) in meters
    x_min, x_max = -hw_m, hw_m
    y_min, y_max = -hh_m, hh_m

    payload = {
        "feature": {
            "btType": "BTMSketch-151",
            "featureType": "newSketch",
            "name": name,
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
            # 4 Lines defined using Onshape's pnt/dir vector serialization
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
            # Coincident constraints connecting line corner endpoints into a closed region face
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
                        {"btType": "BTMParameterString-149", "parameterId": "localLeft.start"}
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
        print(f"HTTP Status Code: {response.status_code}")
        
        data = response.json()
        if response.status_code == 200:
            feature_id = data.get("feature", {}).get("featureId")
            print(f"✅ SUCCESS: Created Sketch '{name}'")
            print(f"   Feature ID: {feature_id}")
            return feature_id
        else:
            print(f"❌ FAILED to create sketch: {response.text[:300]}")
            return None
    except Exception as e:
        print(f"❌ Exception: {str(e)}")
        return None


def test_extrude_sketch(sketch_feature_id: str, depth_cm: float = 2.0, name: str = "Test 2cm Extrude"):
    """
    Tests extruding all closed region faces of a sketch using
    Onshape's native BTMIndividualSketchRegionQuery-140 schema.
    """
    print(f"\n--- 2. Testing extrude_sketch for Feature ID '{sketch_feature_id}' ({depth_cm}cm depth) ---")
    
    url = f"https://cad.onshape.com/api/v16/partstudios/d/{DOC_ID}/w/{WORKSPACE_ID}/e/{ELEMENT_ID}/features"

    payload = {
        "feature": {
            "btType": "BTMFeature-134",
            "featureType": "extrude",
            "name": name,
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
        print(f"HTTP Status Code: {response.status_code}")
        
        data = response.json()
        if response.status_code == 200:
            feature_id = data.get("feature", {}).get("featureId")
            print(f"✅ SUCCESS: Created Extrude '{name}'")
            print(f"   Feature ID: {feature_id}")
            return feature_id
        else:
            print(f"❌ FAILED to create extrude: {response.text[:300]}")
            return None
    except Exception as e:
        print(f"❌ Exception: {str(e)}")
        return None


if __name__ == "__main__":
    print("🚀 Starting Standalone Onshape Macro API Test...")
    print(f"Target Document ID: {DOC_ID}")
    print(f"Target Workspace ID: {WORKSPACE_ID}")
    print(f"Target Element ID: {ELEMENT_ID}")

    # Step 1: Create 2cm x 2cm Square Sketch
    sketch_id = test_create_rectangle_sketch(width_cm=2.0, height_cm=2.0, name="Direct Test 2cm Square")

    # Step 2: Extrude sketch region 2cm high
    if sketch_id:
        extrude_id = test_extrude_sketch(sketch_feature_id=sketch_id, depth_cm=2.0, name="Direct Test 2cm Cube")
        if extrude_id:
            print("\n🎉 ALL MACRO TESTS PASSED! Check your Onshape document viewport.")
        else:
            print("\n⚠️ Extrude test failed.")
    else:
        print("\n⚠️ Sketch test failed.")