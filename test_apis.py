import json
from app import app
from ai_agent_api import _generate_agent_token

with app.test_client() as client:
    # First test /api/agent/overview direct execution
    token = _generate_agent_token()
    headers = {"Authorization": f"Bearer {token}"}
    
    print("Testing /api/agent/exposed-endpoints")
    res = client.get("/api/agent/exposed-endpoints", headers=headers)
    print("Status:", res.status_code)
    try:
        data = res.get_json()
        print("Count:", data.get("count"))
        if data.get("apis"):
            print("First API:", json.dumps(data["apis"][0], indent=2))
            
            # Find overview to check sample
            for api in data["apis"]:
                if "overview" in api["endpoint"]:
                    print("\nOverview API:", json.dumps(api, indent=2))
                    break
    except Exception as e:
        print("Error parsing json:", e)
        print("Response:", res.data)

