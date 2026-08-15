"""Server-owned zone/check applicability shared by API and orchestration."""

ZONE_CHECK_CODES = {
    "Arrival & entrance signage": ["SIG-01", "SEC-01", "OSHA-WALK-01"],
    "Parking / accessible parking": ["ADA-PARK-01", "SIG-01"],
    "Clubhouse exterior": ["OSHA-WALK-01", "GA-FIRE-01"],
    "Lobby / check-in": ["OPS-01", "OSHA-WALK-01"],
    "Pro shop": ["OSHA-WALK-01", "GA-FIRE-01"],
    "Restrooms": ["CLN-01", "ADA-GOLF-01"],
    "Food & beverage area": ["GA-FOOD-01", "OSHA-WALK-01"],
    "Cart staging": ["OSHA-WALK-01", "SAF-02"],
    "Maintenance & chemical storage": ["OSHA-HAZCOM-01", "GA-PEST-01", "GA-BMP-IPM-01"],
    "Driving range": ["ADA-GOLF-01", "OSHA-WALK-01"],
    "Starter / first tee": ["WCGC-PACE-01", "ADA-GOLF-01"],
    "On-course facilities": ["ADA-GOLF-01", "GA-BMP-WATER-01", "CRS-01"],
    "18th hole / departure": ["WCGC-PACE-01", "OSHA-WALK-01"],
    "Charging bays": ["EVS-01", "EVE-01", "EVG-01"],
    "Battery storage room": ["EVS-02"],
    "Staging & dispatch": ["EVO-01", "EVC-01"],
    "Driver rest area": ["EVC-01"],
    "Customer handover point": ["EVC-01", "EVG-01"],
    "Yard & perimeter": ["EVG-01", "EVC-01"],
}
