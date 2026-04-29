"""
Daily.co video room management for Founder Date calls.

Daily.co was chosen over Twilio/Whereby/Zoom because:
  - $0.004 / participant minute, 1k free min/month
  - Single REST API for room CRUD (no SDK needed server-side)
  - Embeddable iframe via @daily-co/daily-js on the frontend
  - Built-in recording, screenshare, chat

Rooms are short-lived: created when a call is scheduled, expire shortly
after the call ends. We do NOT pre-create rooms for the whole flow.
"""
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

import requests

from utils.logger import log_info, log_error, log_warning


DAILY_API_KEY = os.getenv('DAILY_API_KEY', '').strip()
DAILY_API_BASE = 'https://api.daily.co/v1'
DAILY_DOMAIN = os.getenv('DAILY_DOMAIN', '').strip()  # e.g. "yourapp.daily.co"

DEFAULT_ROOM_TTL_HOURS = 6


def is_daily_configured() -> bool:
    return bool(DAILY_API_KEY)


def _auth_headers() -> Dict[str, str]:
    return {
        'Authorization': f'Bearer {DAILY_API_KEY}',
        'Content-Type': 'application/json',
    }


def create_founder_date_room(
    founder_date_id: str,
    stage: int,
    duration_minutes: int = 90,
    enable_recording: bool = False,
) -> Dict[str, Any]:
    """
    Create a Daily.co room for a Founder Date call.

    Args:
        founder_date_id: ID of the parent founder_date row
        stage: 1, 2, or 3
        duration_minutes: Stage default + 30min buffer (room expires after this)
        enable_recording: Cloud recording (premium tier only)

    Returns:
        { name, url, expires_at_iso } or raises ValueError on failure.
    """
    if not is_daily_configured():
        raise ValueError(
            "Daily.co is not configured. Set DAILY_API_KEY (and optionally DAILY_DOMAIN)."
        )

    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=duration_minutes + 30  # buffer for late starts
    )
    expires_unix = int(expires_at.timestamp())

    # Room name: deterministic per (founder_date, stage, attempt) so retries reuse it
    # We append a timestamp to allow rescheduling (a new call is a new room)
    room_name = f"fd-{founder_date_id[:8]}-s{stage}-{int(time.time())}"

    properties: Dict[str, Any] = {
        'exp': expires_unix,
        'enable_chat': True,
        'enable_screenshare': True,
        'max_participants': 2,
        'eject_at_room_exp': True,
        'lang': 'en',
    }
    if enable_recording:
        properties['enable_recording'] = 'cloud'

    payload = {
        'name': room_name,
        'privacy': 'public',  # Anyone with URL can join (we control distribution)
        'properties': properties,
    }

    try:
        response = requests.post(
            f'{DAILY_API_BASE}/rooms',
            headers=_auth_headers(),
            json=payload,
            timeout=15,
        )
    except requests.RequestException as e:
        log_error(f"Daily.co room creation network error: {e}")
        raise ValueError(f"Failed to reach Daily.co API: {e}")

    if response.status_code not in (200, 201):
        log_error(f"Daily.co room creation failed [{response.status_code}]: {response.text}")
        raise ValueError(f"Failed to create Daily.co room: {response.text}")

    data = response.json()
    room_url = data.get('url') or (
        f"https://{DAILY_DOMAIN}/{room_name}" if DAILY_DOMAIN else None
    )

    if not room_url:
        raise ValueError("Daily.co response did not include a room URL")

    log_info(f"Created Daily.co room {room_name} for founder_date {founder_date_id}, stage {stage}")

    return {
        'name': room_name,
        'url': room_url,
        'expires_at_iso': expires_at.isoformat(),
    }


def delete_room(room_name: str) -> bool:
    """Delete a Daily.co room (cleanup after call ends)."""
    if not is_daily_configured() or not room_name:
        return False

    try:
        response = requests.delete(
            f'{DAILY_API_BASE}/rooms/{room_name}',
            headers=_auth_headers(),
            timeout=10,
        )
        if response.status_code in (200, 204, 404):  # 404 = already gone
            return True
        log_warning(f"Daily.co room delete returned {response.status_code}: {response.text}")
        return False
    except Exception as e:
        log_warning(f"Daily.co room delete error: {e}")
        return False


def create_meeting_token(room_name: str, participant_name: str, is_owner: bool = False) -> Optional[str]:
    """
    Create a short-lived meeting token for a participant.
    Optional - public rooms work without tokens, but tokens let us:
      - Set the participant's display name
      - Mark someone as the room owner (controls recording start/stop)

    Returns the token string or None on failure.
    """
    if not is_daily_configured():
        return None

    expires_unix = int((datetime.now(timezone.utc) + timedelta(hours=DEFAULT_ROOM_TTL_HOURS)).timestamp())

    try:
        response = requests.post(
            f'{DAILY_API_BASE}/meeting-tokens',
            headers=_auth_headers(),
            json={
                'properties': {
                    'room_name': room_name,
                    'user_name': participant_name,
                    'is_owner': is_owner,
                    'exp': expires_unix,
                }
            },
            timeout=10,
        )
        if response.status_code in (200, 201):
            return response.json().get('token')
        log_warning(f"Daily.co token creation returned {response.status_code}: {response.text}")
    except Exception as e:
        log_warning(f"Daily.co token creation error: {e}")

    return None
