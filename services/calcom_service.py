"""Cal.com scheduling URLs (pasted booking links).

Advisors save a full https Cal.com booking URL on their profile.
No OAuth or Cal API calls are required for listing or resolving links.
"""

from typing import Any, Dict, Optional

from config.database import get_supabase


def normalize_cal_booking_url(url: Optional[str]) -> Optional[str]:
    """Normalize a pasted scheduling URL (https). Empty / invalid → None."""
    if url is None:
        return None
    raw = str(url).strip()
    if not raw:
        return None
    if len(raw) > 2048:
        raise ValueError("Scheduling link is too long (max 2048 characters)")
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw.lstrip('/')}"
    elif raw.startswith("http://"):
        raw = "https://" + raw[len("http://") :]
    if not raw.startswith("https://"):
        raise ValueError("Scheduling link must use https")
    return raw.split("?", 1)[0].rstrip("/")


def resolve_advisor_cal_booking_url(profile_row: Optional[Dict[str, Any]]) -> Optional[str]:
    """Resolve advisor public scheduling URL.

    Prefer pasted ``calcom_booking_url``. Otherwise fall back to legacy
    ``calcom_connected`` + ``calcom_username`` → ``https://cal.com/{username}``.
    """
    if not profile_row:
        return None
    try:
        direct = normalize_cal_booking_url(profile_row.get("calcom_booking_url"))
    except ValueError:
        direct = None
    if direct:
        return direct

    legacy_connected = profile_row.get("calcom_connected")
    if legacy_connected:
        un = (profile_row.get("calcom_username") or "").strip().lstrip("@")
        if not un:
            return None
        slug = un.split("/")[0].lower()
        if not slug:
            return None
        return f"https://cal.com/{slug}"
    return None


def get_advisor_booking_link(advisor_user_id: str) -> Optional[str]:
    supabase = get_supabase()

    advisor = supabase.table("advisor_profiles").select(
        "calcom_connected, calcom_booking_url, calcom_username"
    ).eq("user_id", advisor_user_id).execute()

    if advisor.data:
        return resolve_advisor_cal_booking_url(advisor.data[0])

    return None


def get_advisor_availability(
    advisor_user_id: str,
    start_date: str,
    end_date: str,
    duration_minutes: int = 30,
) -> Dict[str, Any]:
    """Slot grid is rendered on Cal.com; we only expose the booking URL."""
    link = get_advisor_booking_link(advisor_user_id)
    return {
        "has_calcom": link is not None,
        "slots": [],
        "booking_url": link,
        "message": None if link else "Advisor has not added a Cal.com scheduling link yet",
    }


def create_booking(
    advisor_user_id: str,
    founder_name: str,
    founder_email: str,
    start_time: str,
    duration_minutes: int,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    raise ValueError(
        "Programmatic booking is disabled. Use the advisor's Cal.com booking link "
        "(shown in the marketplace booking dialog when they have saved a scheduling URL)."
    )
