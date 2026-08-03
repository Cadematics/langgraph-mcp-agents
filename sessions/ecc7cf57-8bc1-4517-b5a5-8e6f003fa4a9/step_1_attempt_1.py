import os
import sys
import json
import requests
from requests.auth import HTTPBasicAuth

# Define the Onshape API endpoint URL
url = "https://cad.onshape.com/api/v16/partstudios/d/2522d956c5986dc4a4d60e20/w/11aa163db6bf10fd2aa0d97c/e/ac775850a6fea04e0606985d/features"

# Define the payload for creating a circle sketch on the top face of the cylinder
payload = {
    "feature": {
        "btType": "BTMSketch-151",
        "featureType": "newSketch",
        "name": "Top Face Circle Sketch",
        "parameters": [
            {
                "btType": "BTMParameterQueryList-148",
                "parameterId": "sketchPlane",
                "queries": [
                    {
                        "btType": "BTMIndividualQuery-138",
                        "queryString": "query=qCreatedBy(makeId('Fx4JDE0nbDx8tes_1'), EntityType.FACE);"
                    }
                ]
            }
        ],
        "entities": [
            {
                "btType": "BTMSketchCurve-4",
                "geometry": {
                    "btType": "BTCurveGeometryCircle-115",
                    "radius": 0.005,  # radius in meters (1 cm diameter)
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
    # Make the POST request to create the sketch
    response = requests.post(
        url,
        headers={'Content-Type': 'application/json'},
        auth=HTTPBasicAuth(os.environ.get('ONSHAPE_ACCESS_KEY'), os.environ.get('ONSHAPE_SECRET_KEY')),
        data=json.dumps(payload)
    )

    # Check if the request was successful
    if response.status_code in [200, 201]:
        feature_id = response.json()['feature']['featureId']
        print(f"SUCCESS: Feature ID: {feature_id}")
    else:
        print(f"ERROR: {response.text}", file=sys.stderr)
        sys.exit(1)

except Exception as e:
    print(f"EXCEPTION: {str(e)}", file=sys.stderr)
    sys.exit(1)