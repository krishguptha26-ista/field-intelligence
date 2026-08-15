"""Sourced Wolf Creek checklist pack used by the POC field guide.

This is not a substitute for a lawyer, regulator, permit file, or BroadPeak's
own operating standards.  The metadata deliberately separates binding sources,
conditional requirements, industry practice, and the venue's public policy so
the UI never collapses all four into a generic "compliance" badge.
"""
from __future__ import annotations


WOLF_CREEK_JURISDICTION = {
    "display": "City of South Fulton, Fulton County, Georgia",
    "postal_address": "3000 Union Rd SW, Atlanta, GA 30331",
    "basis": (
        "The venue publishes this street address; City of South Fulton planning "
        "material identifies Wolf Creek Golf Course as a privately owned course in the city."
    ),
    "verified_on": "2026-08-15",
    "sources": [
        {
            "label": "Wolf Creek Golf Club — Contact",
            "url": "https://wolfcreekgc.com/contact/",
        },
        {
            "label": "City of South Fulton — 2026 Comprehensive Plan appendix",
            "url": "https://www.cityofsouthfultonga.gov/DocumentCenter/View/15670/CoSF-Comp-Plan-Appendix_Pages-1",
        },
    ],
}


# Fields are intentionally presentation-safe. ``authoritative_source`` means
# the linked publisher is the competent authority; it does not mean a field
# consultant has made a legally binding determination.
WOLF_CREEK_STANDARD_DEFS = [
    {
        "category": "security_presence",
        "code": "SEC-01",
        "text": (
            "Where the approved staffing or security plan schedules entrance coverage, "
            "confirm the assigned guard or officer is present; document an uncovered post, "
            "the scheduled period and the manager escalation."
        ),
        "severity": "HIGH",
        "source_label": "REPRESENTATIVE OPERATING PROMPT · CONTROLLED PLAN REQUIRED",
        "authority_type": "REPRESENTATIVE_CONTROL_PLACEHOLDER",
        "authority_badge": "OPERATING PLAN · VERIFY",
        "authoritative_source": False,
        "source_title": "BroadPeak-controlled security/staffing plan not supplied",
        "source_url": "",
        "citation": "POC operating prompt; replace with the approved site security/staffing plan",
        "applicability": "Applies only when a current approved plan or roster requires entrance coverage; this is not a claim that law mandates a guard.",
    },
    {
        "category": "accessibility",
        "code": "ADA-PARK-01",
        "text": (
            "Confirm the accessible parking space(s), access aisle, signs and route to the "
            "clubhouse entrance are present, unobstructed and usable; record any obstruction "
            "or damaged route without declaring legal non-compliance."
        ),
        "severity": "HIGH",
        "source_label": "FEDERAL_REQUIREMENT · ADA 2010 Standards",
        "authority_type": "FEDERAL_REQUIREMENT",
        "authority_badge": "FEDERAL · VERIFY",
        "authoritative_source": True,
        "source_title": "2010 ADA Standards for Accessible Design",
        "source_url": "https://www.ada.gov/law-and-regs/design-standards/2010-stds/",
        "citation": "Accessible parking and routes: Chapters 2, 4 and 5",
        "applicability": "Applies to covered public accommodations; existing-facility and alteration duties require qualified review.",
    },
    {
        "category": "accessibility",
        "code": "ADA-GOLF-01",
        "text": (
            "Confirm accessible routes or permitted golf-car passages connect required golf "
            "elements, including rental/bag-drop, course toilet rooms, required practice areas, "
            "tees, greens and weather shelters where provided; capture the exact barrier."
        ),
        "severity": "HIGH",
        "source_label": "FEDERAL_REQUIREMENT · ADA §§206.2.15, 238, 1006",
        "authority_type": "FEDERAL_REQUIREMENT",
        "authority_badge": "FEDERAL · GOLF",
        "authoritative_source": True,
        "source_title": "2010 ADA Standards — Golf Facilities",
        "source_url": "https://www.ada.gov/law-and-regs/design-standards/2010-stds/",
        "citation": "Sections 206.2.15, 238 and 1006",
        "applicability": "Technical requirements vary for existing facilities, new construction and alterations; escalate measurements to an accessibility specialist.",
    },
    {
        "category": "worker_safety",
        "code": "OSHA-WALK-01",
        "text": (
            "Check employee walking-working surfaces and access/egress for spills, leaks, "
            "protrusions, damaged surfaces or other hazards; guard an uncorrected hazard from use."
        ),
        "severity": "HIGH",
        "source_label": "FEDERAL_REQUIREMENT · OSHA 29 CFR 1910.22",
        "authority_type": "FEDERAL_REQUIREMENT",
        "authority_badge": "OSHA · WORKPLACE",
        "authoritative_source": True,
        "source_title": "OSHA 29 CFR 1910.22 — General requirements",
        "source_url": "https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.22",
        "citation": "29 CFR 1910.22(a) and (d)",
        "applicability": "Employee workplace requirement; guest-safety implications may overlap but are not adjudicated by this check.",
    },
    {
        "category": "chemical_safety",
        "code": "OSHA-HAZCOM-01",
        "text": (
            "Verify hazardous-chemical containers retain legible workplace labels, safety data "
            "sheets are immediately accessible during the shift, and staff training/program "
            "records can be produced."
        ),
        "severity": "CRITICAL",
        "source_label": "FEDERAL_REQUIREMENT · OSHA 29 CFR 1910.1200",
        "authority_type": "FEDERAL_REQUIREMENT",
        "authority_badge": "OSHA · CHEMICALS",
        "authoritative_source": True,
        "source_title": "OSHA 29 CFR 1910.1200 — Hazard Communication",
        "source_url": "https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.1200",
        "citation": "29 CFR 1910.1200(e)–(h)",
        "applicability": "Applies where employees may be exposed to hazardous chemicals under normal use or foreseeable emergency.",
    },
    {
        "category": "pesticide_safety",
        "code": "GA-PEST-01",
        "text": (
            "For restricted-use turf or aquatic pesticides, verify the appropriate Georgia "
            "applicator credential and category, product label, application record and secured "
            "storage; if a contractor applies for a fee, verify contractor licensing too."
        ),
        "severity": "CRITICAL",
        "source_label": "GEORGIA_REQUIREMENT · GDA Pesticide Program",
        "authority_type": "STATE_REQUIREMENT_CONDITIONAL",
        "authority_badge": "GEORGIA · CONDITIONAL",
        "authoritative_source": True,
        "source_title": "Georgia Department of Agriculture — Agricultural Pest Control",
        "source_url": "https://agr.georgia.gov/agricultural-pest-control",
        "citation": "Georgia Pesticide Use and Application Act; applicator categories 24 and 26 as applicable",
        "applicability": "Credential depends on restricted-use status, employment/property relationship, application category and whether service is for a fee.",
    },
    {
        "category": "food_safety",
        "code": "GA-FOOD-01",
        "text": (
            "If food is prepared or served, verify a current Fulton County food-service permit "
            "and inspection report are available, then spot-check handwashing, food temperatures, "
            "cross-contamination controls and sanitation records."
        ),
        "severity": "CRITICAL",
        "source_label": "COUNTY/STATE REQUIREMENT · Food Service",
        "authority_type": "LOCAL_STATE_REQUIREMENT_CONDITIONAL",
        "authority_badge": "FULTON/GA · IF FOOD",
        "authoritative_source": True,
        "source_title": "Fulton County Board of Health — Food Service",
        "source_url": "https://fultoncountyboh.com/environmental-health/food-service/",
        "citation": "Georgia DPH Chapter 511-6-1; Fulton County permit and inspection program",
        "applicability": "Applies when the operation meets the food-service-establishment definition; packaged-only service may be treated differently.",
    },
    {
        "category": "fire_life_safety",
        "code": "GA-FIRE-01",
        "text": (
            "Keep required exits, egress paths and fire-safety equipment visible and unobstructed; "
            "record missing/damaged equipment or blocked routes for the fire/code authority to assess."
        ),
        "severity": "CRITICAL",
        "source_label": "GEORGIA CODE · FIRE/LIFE SAFETY",
        "authority_type": "STATE_LOCAL_CODE",
        "authority_badge": "GEORGIA · CODE",
        "authoritative_source": True,
        "source_title": "Georgia DCA — Current State Minimum Codes for Construction",
        "source_url": "https://dca.georgia.gov/community-assistance/construction-codes/current-state-minimum-codes-construction",
        "citation": "2024 IFC and Georgia State Minimum Fire Safety Standards, as currently adopted",
        "applicability": "Exact occupancy, equipment and inspection duties must be confirmed with the City of South Fulton/State Fire Marshal.",
    },
    {
        "category": "environmental_bmp",
        "code": "GA-BMP-IPM-01",
        "text": (
            "Confirm the course has a site-specific integrated pest-management plan with scouting, "
            "treatment thresholds, application/weather records and post-treatment review."
        ),
        "severity": "MEDIUM",
        "source_label": "GEORGIA GOLF INDUSTRY BMP · NOT LAW",
        "authority_type": "INDUSTRY_BEST_PRACTICE",
        "authority_badge": "GEORGIA GOLF BMP",
        "authoritative_source": False,
        "source_title": "GCSAA — Best Management Practices for Georgia Golf Courses",
        "source_url": "https://magazine.ggcsa.com/HTML5/GGCSA-Best-Management-Practices-Manual",
        "citation": "Georgia golf-course BMP manual — Integrated Pest Management",
        "applicability": "Industry best practice, not an independent legal requirement; product-label and pesticide-law duties remain binding.",
    },
    {
        "category": "environmental_bmp",
        "code": "GA-BMP-WATER-01",
        "text": (
            "Look for irrigation leaks, runoff, overspray and stressed areas; confirm water-use "
            "monitoring, irrigation scheduling and corrective-maintenance records exist."
        ),
        "severity": "MEDIUM",
        "source_label": "GEORGIA GOLF INDUSTRY BMP · NOT LAW",
        "authority_type": "INDUSTRY_BEST_PRACTICE",
        "authority_badge": "GEORGIA GOLF BMP",
        "authoritative_source": False,
        "source_title": "GCSAA — State BMP Guides (Georgia)",
        "source_url": "https://www.gcsaa.org/facility/environment-hub/state-bmp-guides",
        "citation": "Georgia golf-course BMP resources — water conservation",
        "applicability": "Industry best practice; permits, withdrawal limits and discharge duties require site-specific document review.",
    },
    {
        "category": "operations",
        "code": "WCGC-PACE-01",
        "text": (
            "Compare actual pace-of-play monitoring and interventions with Wolf Creek's published "
            "four-hours-or-less policy; record the tee time, elapsed time and staff response."
        ),
        "severity": "MEDIUM",
        "source_label": "VENUE-PUBLISHED POLICY · WOLF CREEK",
        "authority_type": "VENUE_PUBLISHED_POLICY",
        "authority_badge": "WOLF CREEK POLICY",
        "authoritative_source": False,
        "source_title": "Wolf Creek Golf Club — Course Information",
        "source_url": "https://wolfcreekgc.com/course-information/",
        "citation": "Club Policies — Pace of Play",
        "applicability": "Publicly posted venue policy; confirm the current controlled operating procedure with management.",
    },
]


STANDARD_METADATA = {item["code"]: item for item in WOLF_CREEK_STANDARD_DEFS}


def standard_metadata(code: str) -> dict:
    """Return only fields intended for an API response."""
    item = STANDARD_METADATA.get(code, {})
    return {key: value for key, value in item.items()
            if key not in {"category", "text", "severity", "source_label"}}
