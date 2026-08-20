import json

def extract_action_items(transcript_text):
    """
    Simulates an AI tool parsing a meeting transcript 
    to automatically extract action items, assignees, and priorities.
    """
    print("📝 AI MEETING ACTION ITEM EXTRACTOR 📝\n")
    print(f"Raw Input Transcript Snippet:\n\"{transcript_text[:90]}...\"\n")
    print("-" * 60)
    
    # Simulated structured outputs from the LLM parsing engine
    extracted_items = [
        {"task": "Audit and update Q4 account close plans", "assignee": "Milan Horacek", "priority": "High"},
        {"task": "Verify $150k forecast adjustment with finance", "assignee": "Milan Horacek", "priority": "Urgent"},
        {"task": "Submit technical support request via new DSR form", "assignee": "Milan / Management", "priority": "Medium"}
    ]
    
    print("🎯 Extracted Structured Action Items:")
    for idx, item in enumerate(extracted_items, 1):
        print(f"  {idx}. [ {item['priority'].upper()} ] {item['task']}")
        print(f"     👤 Assignee: {item['assignee']}")
    print("-" * 60)
    print("✅ Success: Action items formatted and ready to sync to workspace.")

if __name__ == "__main__":
    sample_transcript = "Hey team, we need to audit all Q4 close plans today. Also, Milan needs to verify that 150k forecast adjustment with finance, and make sure we submit the support request via the new form this week."
    extract_action_items(sample_transcript)
