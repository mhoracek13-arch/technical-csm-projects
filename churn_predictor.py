import json

def analyze_customer_sentiment(customer_feedback_list):
    """
    Simulates an AI tool analyzing customer text feedback 
    to predict churn risk and recommend proactive interventions.
    """
    print("🔍 AI CUSTOMER SENTIMENT & CHURN RISK ANALYZER 🔍\n")
    
    # Simple keyword-based sentiment dictionary for demonstration
    red_flags = ["frustrated", "bug", "delayed", "cancelling", "expensive", "unresponsive"]
    green_flags = ["love", "efficient", "saving time", "great support", "renewing"]

    for client in customer_feedback_list:
        feedback_text = client['feedback'].lower()
        risk_score = 0
        
        # Calculate risk based on negative keywords
        for flag in red_flags:
            if flag in feedback_text:
                risk_score += 25
                
        # Reduce risk based on positive keywords
        for flag in green_flags:
            if flag in feedback_text:
                risk_score -= 15
                
        # Cap the risk score between 0 and 100
        risk_score = max(0, min(risk_score, 100))
        
        print(f"🏢 Client: {client['company']} (ARR: ${client['arr']:,})")
        print(f"   💬 Feedback: \"{client['feedback']}\"")
        print(f"   📊 Calculated Churn Risk Score: {risk_score}%")
        
        # Automated Action Recommendation
        if risk_score >= 50:
            print("   🚨 ACTION REQUIRED: High churn risk! Trigger Executive Success Plan & alert CSM.")
        elif risk_score > 0:
            print("   ⚠️ MONITOR: Moderate friction detected. Proactively send workflow optimization tips.")
        else:
            print("   ✅ HEALTHY: Account is stable. Look for expansion opportunities.")
        print("-" * 60)

# Dummy customer data simulating inputs from support tickets and emails
mock_client_data = [
    {
        "company": "Alpha Corp", 
        "arr": 65000, 
        "feedback": "We are quite frustrated with a persistent bug and the support team has been unresponsive."
    },
    {
        "company": "Beta Logistics", 
        "arr": 120000, 
        "feedback": "The new automation workflows are saving our team hours every week. We love the platform!"
    },
    {
        "company": "Gamma Media", 
        "arr": 45000, 
        "feedback": "Renewal is coming up and leadership thinks it's a bit expensive given our current delayed rollout."
    }
]

if __name__ == "__main__":
    analyze_customer_sentiment(mock_client_data)
