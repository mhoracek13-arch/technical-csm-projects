def analyze_pipeline():
    """
    Automates processing enterprise portfolio accounts,
    calculating total ARR by health tier, and flagging expansion targets.
    """
    print("📊 ENTERPRISE PORTFOLIO & ARR HEALTH ANALYZER 📊\n")
    
    accounts = [
        {"name": "Alpha Corp", "arr": 150000, "health": "Green", "expansion_potential": "High"},
        {"name": "Beta Logistics", "arr": 85000, "health": "Yellow", "expansion_potential": "Medium"},
        {"name": "Gamma Media", "arr": 220000, "health": "Green", "expansion_potential": "High"},
        {"name": "Delta Tech", "arr": 95000, "health": "Red", "expansion_potential": "Low"},
    ]
    
    total_arr = sum(acc['arr'] for acc in accounts)
    green_arr = sum(acc['arr'] for acc in accounts if acc['health'] == 'Green')
    
    print(f"💰 Total Managed Portfolio ARR: ${total_arr:,}")
    print(f"🟢 Healthy (Green) ARR: ${green_arr:,} ({int((green_arr/total_arr)*100)}% of book)")
    print("-" * 60)
    
    print("🚀 High-Priority Expansion Targets:")
    for acc in accounts:
        if acc['expansion_potential'] == 'High' and acc['health'] == 'Green':
            print(f"  • {acc['name']} (${acc['arr']:,} ARR) - Ready for cross-sell / expansion pitch.")
    print("-" * 60)
    print("✅ Pipeline analysis complete. Ready for executive review.")

if __name__ == "__main__":
    analyze_pipeline()
