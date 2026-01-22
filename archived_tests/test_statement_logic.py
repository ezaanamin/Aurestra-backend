
from app import app, db, get_statement_report
from flask import json

# Setup context
ctx = app.app_context()
ctx.push()

# Create a dummy client
client = app.test_client()

print("--- Testing Statement Report Logic ---")

# 1. Test Without Input (Should Default to Previous Month)
# Currently it is Jan 2026 (from metadata), so it should fetch Dec 2025.
# But DB is empty, so we expect 0s but consistent dates.
response = client.post('/api/reports/statement', json={})
data = response.get_json()

print(f"Target Month: {data.get('target_month')}")
print(f"Status: {data['summary']['status']}")

# 2. Test With Specific Input (e.g. Feb 2025 -> Jan 2025)
response = client.post('/api/reports/statement', json={"month": "2025-02"})
data = response.get_json()
print(f"Input: 2025-02 -> Target: {data.get('target_month')}")

ctx.pop()
