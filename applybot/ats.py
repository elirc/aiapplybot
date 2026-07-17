"""Detect which applicant-tracking system (ATS) powers a career page.

Most "company website" applications are actually served by a handful of
ATS platforms. Detection is informational plus lets us give Claude
platform-specific hints.
"""

from __future__ import annotations

ATS_SIGNATURES: dict[str, list[str]] = {
    "Greenhouse": ["greenhouse.io", "boards.greenhouse", "grnh.se"],
    "Lever": ["lever.co", "jobs.lever"],
    "Ashby": ["ashbyhq.com"],
    "Workday": ["myworkdayjobs.com", "workday.com", "wd1.myworkday", "wd5.myworkday"],
    "SmartRecruiters": ["smartrecruiters.com"],
    "iCIMS": ["icims.com"],
    "BambooHR": ["bamboohr.com"],
    "Workable": ["workable.com", "apply.workable"],
    "JazzHR": ["applytojob.com", "jazz.co"],
    "Recruitee": ["recruitee.com"],
    "Jobvite": ["jobvite.com"],
    "Taleo": ["taleo.net"],
    "Breezy": ["breezy.hr"],
    "Rippling": ["ats.rippling.com", "rippling-ats"],
}

ATS_HINTS: dict[str, str] = {
    "Greenhouse": (
        "This is a Greenhouse form: usually a single page. EEO questions appear "
        "at the bottom as selects. Location fields may be autocomplete text inputs."
    ),
    "Lever": (
        "This is a Lever form: single page, simple named inputs. "
        "'Additional information' is a free-text catch-all."
    ),
    "Ashby": (
        "This is an Ashby form: single page. Some yes/no questions render as "
        "custom widgets that may not be scannable — the user will handle those manually."
    ),
    "Workday": (
        "This is Workday: a MULTI-PAGE wizard (My Information -> Experience -> "
        "Questions -> EEO -> Review). Fill only the fields visible on the current "
        "page. Many dropdowns are custom widgets that may not appear in the scan. "
        "An account sign-in may be required first."
    ),
    "SmartRecruiters": "SmartRecruiters: sections expand as you complete them; rescan between sections.",
    "iCIMS": "iCIMS: often uses iframes and multi-step pages; rescan after each step.",
    "Taleo": "Taleo: dated multi-step wizard; fill visible fields, then rescan on each step.",
}


def detect_ats(url: str, page_content: str = "") -> str | None:
    haystack = (url + " " + page_content[:20000]).lower()
    for name, needles in ATS_SIGNATURES.items():
        if any(n in haystack for n in needles):
            return name
    return None


def ats_hint(name: str | None) -> str:
    if not name:
        return "Unknown platform — likely a custom company form. Fill visible fields conservatively."
    return ATS_HINTS.get(name, f"Platform detected: {name}.")
