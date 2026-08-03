import os
import sys
import json
import requests
from requests.auth import HTTPBasicAuth

# Define the Onshape API endpoint URL
url = "https://cad.onshape.com/api/v16/partstudios/d/2522d956c5986dc4a4d60e20/w/11aa163db6bf10fd2aa0d97c/e/ac775850a6fea04e0606985d/features"

# Define the payload for the extrude cut operation
payload = {
    "feature": {
        "btType": "BTMFeature-134",
        "featureType": "extrude",
        "name": "Extrude Cut Feature",
        "parameters": [
            {
                "btType": "BTMParameterEnum-145",
                "parameterId": "operationType",
                "value": "REMOVE"
            },
            {
                "btType": "BTMParameterEnum-145",
                "parameterId": "endBoundType",
                "value": "THROUGH_ALL"
            },
            {
                "btType": "BTMParameterQueryList-148",
                "parameterId": "entities",
                "queries": [
                    {
                        "btType": "BTMIndividualSketchRegionQuery-140",
                        "featureId": "F1gA5HybTbQ8afn_2"
                    }
                ]
            }
        ]
    }
}

try:
    # Make the POST request to the Onshape API
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
