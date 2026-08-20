import json

def simulate_api_sync():
    """
    Simulates fetching enterprise client usage data via a REST API,
    filtering the payload, and formatting it for an executive dashboard.
    """
    print("🔌 REST API DATA SYNC & FORMATTER 🔌\n")
    
    # Mock raw JSON response from a software platform API
    raw_api_response = """
    {
        "status": "success",
        "code": 200,
        "data": [
            {"account_id": "ACC-101", "name": "Global Logistics NV", "active_users": 420, "api_calls_daily": 15400, "health": "green"},
            {"account_id": "ACC-102", "name": "Nordic Finance Group", "active_users": 85, "api_calls_daily": 1200, "health": "yellow"},
            {"account_id": "ACC-103", "name": "Apex Retail Solutions", "active_users": 650, "api_calls_daily": 28900, "health": "green"}
        ]
    }
    """
    
    # Parse the raw JSON data string into a Python dictionary
    parsed_data = json.loads(raw_api_response)
    
    print(f"📡 API Connection Status: {parsed_data['status'].upper()} (Code: {parsed_data['code']})")
    print("-" * 60)
    
    # Process and filter the data for high-value reporting
    for account in parsed_data['data']:
        print(f"🏢 Client: {account['name']} ({account['account_id']})")
        print(f"   👥 Active Users: {account['active_users']}")
        print(f"   ⚡ Daily API Load: {account['api_calls_daily']:,} requests")
        print(f"   🟢 Account Health: {account['health'].upper()}")
        
        # Technical health logic check
        if account['api_calls_daily'] > 20000:
            print("   📈 INSIGHT: High API utilization. Prime candidate for enterprise tier expansion.")
        else:
            print("   🔍 INSIGHT: Stable operational utilization.")
        print("-" * 60)

if __name__ == "__main__":
    simulate_api_sync()
