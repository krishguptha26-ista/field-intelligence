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


# A photo can strengthen most issue reports, but requiring one for every issue
# contradicts the assessment's explicit text/photo-description path and can be
# counterproductive for record-based checks.  Keep the non-negotiable set
# narrow and deterministic: these are immediate, visually inspectable hazards
# where the consultant may leave the scene and the condition may disappear.
PHOTO_REQUIRED_STANDARD_CODES = {
    "OSHA-WALK-01",
    "GA-FIRE-01",
    "SAF-01",
    "SAF-02",
    "EVS-01",
    "EVS-02",
}


def issue_photo_policy(standard_code: str, *, category: str = "",
                       severity: str = "") -> dict:
    """Return the server-owned evidence policy for a reported issue.

    ``REQUIRED`` is a hard gate. ``RECOMMENDED`` is an explicit consultant
    choice: attach a photo or continue to human review with lower confidence.
    The model can explain the recommendation, but cannot silently make the
    hard-gate decision itself.
    """
    if standard_code in PHOTO_REQUIRED_STANDARD_CODES:
        return {
            "level": "REQUIRED",
            "label": "Photo required",
            "reason": (
                "This is an immediate, visually inspectable safety condition. "
                "Capture it before leaving the area so the review packet can be checked."
            ),
        }
    return {
        "level": "RECOMMENDED",
        "label": "AI recommends a photo",
        "reason": (
            "A photo would strengthen this report, but detailed consultant text "
            "may continue to human review without one at lower confidence."
        ),
    }
