import json

def capacity_guardian(team_data, capacity_threshold=80):
    """
    Simulates an AI agent monitoring team workload.
    Flags members over capacity and suggests reassignments.
    """
    overloaded = []
    available = []

    # Analyze current capacity from the mocked API data
    for member in team_data:
        if member['current_workload_percent'] > capacity_threshold:
            overloaded.append(member)
        elif member['current_workload_percent'] < (capacity_threshold - 20):
            available.append(member)

    # Generate the executive insights
    print("🛡️ CAPACITY GUARDIAN REPORT 🛡️\n")
    
    if not overloaded:
        print("✅ All team members are within healthy capacity limits.")
        return

    for person in overloaded:
        print(f"⚠️ WARNING: {person['name']} is at {person['current_workload_percent']}% capacity.")
        
        # Suggesting task reallocation to prevent burnout
        if available:
            helper = available[0] 
            print(f"   💡 SUGGESTION: Reassign '{person['tasks'][0]}' to {helper['name']} (Currently at {helper['current_workload_percent']}%).")
        else:
            print("   🚨 ALERT: No available team members for overflow. Flagging to management.")
        print("-" * 50)

# Dummy Data simulating a JSON response from a project management tool
mock_api_data = [
    {"name": "Alice", "current_workload_percent": 95, "tasks": ["Enterprise Migration", "API Documentation"]},
    {"name": "Bob", "current_workload_percent": 45, "tasks": ["Client Onboarding"]},
    {"name": "Charlie", "current_workload_percent": 75, "tasks": ["QBR Prep", "Success Plan Drafts"]}
]

if __name__ == "__main__":
    capacity_guardian(mock_api_data)
