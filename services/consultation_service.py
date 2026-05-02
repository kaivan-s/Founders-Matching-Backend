"""
Advisor consultation service — pay-per-consultation booking flow.

Lifecycle:
    pending_advisor_confirmation
        └── advisor accepts → pending_payment
            └── founder marks payment sent → pending_payment_confirmation
                └── advisor confirms received → confirmed (Daily.co room created)
                    └── call happens, both mark complete → completed

Money flow:
    Founder pays advisor DIRECTLY via UPI/PayPal/Razorpay link.
    The platform does NOT process this money — we only record that both parties
    confirmed the payment so we have an audit trail for disputes/reviews.

Trigger for advisor monetization:
    When a consultation transitions to 'confirmed' (advisor confirmed payment
    receipt), if it's the advisor's first ever confirmed booking, we set
    advisor_profiles.first_booking_at and start the 30-day trial. After that,
    they need a Pro Advisor subscription to keep accepting new bookings.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from config.database import get_supabase
from utils.logger import log_info, log_error, log_warning
from services import plan_service


# Status constants — keep in sync with the CHECK constraint in
# migrations/025_advisor_consultations.sql
STATUS_PENDING_ADVISOR = 'pending_advisor_confirmation'
STATUS_PENDING_PAYMENT = 'pending_payment'
STATUS_PENDING_PAYMENT_CONFIRMATION = 'pending_payment_confirmation'
STATUS_CONFIRMED = 'confirmed'
STATUS_COMPLETED = 'completed'
STATUS_CANCELLED = 'cancelled'
STATUS_DECLINED = 'declined'
STATUS_NO_SHOW = 'no_show'
STATUS_REFUND_REQUESTED = 'refund_requested'

ALLOWED_DURATIONS = (30, 60)
ALLOWED_PAYMENT_METHODS = ('upi', 'paypal', 'razorpay_link', 'bank_transfer', 'other')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_founder_id(clerk_user_id: str) -> str:
    """Get the founder ID for a Clerk user. Caches per-request."""
    try:
        from utils.request_cache import get_cached_founder_id, set_cached_founder_id
        cached = get_cached_founder_id(clerk_user_id)
        if cached:
            return cached
    except ImportError:
        pass

    supabase = get_supabase()
    res = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not res.data:
        raise ValueError("Profile not found")
    founder_id = res.data[0]['id']

    try:
        from utils.request_cache import set_cached_founder_id
        set_cached_founder_id(clerk_user_id, founder_id)
    except ImportError:
        pass
    return founder_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_consultation(consultation_id: str) -> Dict[str, Any]:
    """Fetch a consultation row or raise."""
    supabase = get_supabase()
    res = supabase.table('advisor_consultations').select('*').eq('id', consultation_id).execute()
    if not res.data:
        raise ValueError("Consultation not found")
    return res.data[0]


def _ensure_party(consultation: Dict[str, Any], founder_id: str, role: str) -> None:
    """Verify caller is the right party for this action.

    role: 'founder' or 'advisor'.
    """
    if role == 'founder' and consultation.get('founder_id') != founder_id:
        raise ValueError("Only the founder who booked can perform this action")
    if role == 'advisor' and consultation.get('advisor_id') != founder_id:
        raise ValueError("Only the advisor on this consultation can perform this action")


def _ensure_status(consultation: Dict[str, Any], expected: List[str]) -> None:
    """Raise if consultation is not in one of the expected statuses."""
    current = consultation.get('status')
    if current not in expected:
        raise ValueError(
            f"Consultation is in status '{current}'; expected one of: {', '.join(expected)}"
        )


def _enrich_consultation(row: Dict[str, Any]) -> Dict[str, Any]:
    """Add lightweight related party info for UI display."""
    supabase = get_supabase()
    advisor_id = row.get('advisor_id')
    founder_id = row.get('founder_id')

    party_ids = [pid for pid in (advisor_id, founder_id) if pid]
    parties = {}
    if party_ids:
        res = supabase.table('founders').select('id, name, email, profile_picture_url').in_('id', party_ids).execute()
        for p in (res.data or []):
            parties[p['id']] = p

    return {
        **row,
        'advisor': parties.get(advisor_id),
        'founder': parties.get(founder_id),
    }


# ---------------------------------------------------------------------------
# Booking — founder side
# ---------------------------------------------------------------------------

def book_consultation(
    clerk_user_id: str,
    advisor_user_id: str,
    duration_min: int,
    proposed_time_iso: Optional[str] = None,
    timezone_name: Optional[str] = None,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """Founder requests a consultation slot with an advisor.

    Args:
        clerk_user_id:    Clerk user id of the founder
        advisor_user_id:  founders.id of the advisor (NOT clerk_user_id)
        duration_min:     30 or 60
        proposed_time_iso: ISO8601 timestamp founder wants to meet (advisor confirms)
        timezone_name:    Founder's timezone for display
        topic:            What the founder wants to discuss

    Returns the created consultation row (status=pending_advisor_confirmation).
    """
    if duration_min not in ALLOWED_DURATIONS:
        raise ValueError(f"duration_min must be one of {ALLOWED_DURATIONS}")

    founder_id = _get_founder_id(clerk_user_id)
    if founder_id == advisor_user_id:
        raise ValueError("You cannot book a consultation with yourself")

    supabase = get_supabase()

    # Look up the advisor + their consultation rate
    advisor_res = supabase.table('advisor_profiles').select(
        'user_id, status, is_discoverable, consultation_rate_30min_usd, '
        'consultation_rate_60min_usd, subscription_status, trial_ends_at, '
        'subscription_current_period_end'
    ).eq('user_id', advisor_user_id).execute()

    if not advisor_res.data:
        raise ValueError("Advisor not found")

    advisor = advisor_res.data[0]
    if advisor.get('status') != 'APPROVED' or not advisor.get('is_discoverable'):
        raise ValueError("Advisor is not currently accepting bookings")

    # Confirm the advisor can accept new bookings (free, trial, or active sub)
    if not _advisor_can_accept_bookings(advisor):
        raise ValueError("Advisor's subscription has lapsed and they cannot accept new bookings right now")

    rate_field = 'consultation_rate_30min_usd' if duration_min == 30 else 'consultation_rate_60min_usd'
    rate = advisor.get(rate_field)
    if rate is None:
        raise ValueError(
            f"Advisor has not set a price for {duration_min}-minute consultations"
        )
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        raise ValueError("Advisor's rate is invalid")

    # Validate proposed_time
    scheduled_at = None
    if proposed_time_iso:
        try:
            scheduled_at = datetime.fromisoformat(proposed_time_iso.replace('Z', '+00:00'))
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
            if scheduled_at < datetime.now(timezone.utc):
                raise ValueError("Scheduled time must be in the future")
        except (ValueError, AttributeError):
            raise ValueError("Invalid proposed_time_iso format")

    payload = {
        'advisor_id': advisor_user_id,
        'founder_id': founder_id,
        'duration_min': duration_min,
        'amount_usd': rate,
        'scheduled_at': scheduled_at.isoformat() if scheduled_at else None,
        'timezone': timezone_name,
        'topic': (topic or '').strip()[:1000] or None,
        'status': STATUS_PENDING_ADVISOR,
    }

    res = supabase.table('advisor_consultations').insert(payload).execute()
    if not res.data:
        raise ValueError("Failed to create consultation")

    consultation = res.data[0]

    # Notify advisor
    try:
        _notify_party(
            recipient_founder_id=advisor_user_id,
            actor_founder_id=founder_id,
            event_type='CONSULTATION_REQUESTED',
            title='New consultation request',
            message=(topic or 'A founder wants to book a consultation with you'),
            consultation_id=consultation['id'],
        )
    except Exception as e:
        log_warning(f"Failed to send CONSULTATION_REQUESTED notification: {e}")

    return _enrich_consultation(consultation)


def cancel_consultation(clerk_user_id: str, consultation_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
    """Either party can cancel before payment is confirmed."""
    founder_id = _get_founder_id(clerk_user_id)
    consultation = _get_consultation(consultation_id)

    if consultation.get('advisor_id') != founder_id and consultation.get('founder_id') != founder_id:
        raise ValueError("You are not part of this consultation")

    _ensure_status(consultation, [
        STATUS_PENDING_ADVISOR,
        STATUS_PENDING_PAYMENT,
        STATUS_PENDING_PAYMENT_CONFIRMATION,
    ])

    supabase = get_supabase()
    update = {
        'status': STATUS_CANCELLED,
        'cancelled_at': _now_iso(),
        'cancelled_by': founder_id,
        'cancellation_reason': (reason or '').strip()[:500] or None,
    }
    res = supabase.table('advisor_consultations').update(update).eq('id', consultation_id).execute()
    return _enrich_consultation(res.data[0] if res.data else {**consultation, **update})


# ---------------------------------------------------------------------------
# Advisor responses
# ---------------------------------------------------------------------------

def accept_consultation(
    clerk_user_id: str,
    consultation_id: str,
    confirmed_time_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Advisor accepts a pending consultation request.

    Optionally accepts a different scheduled_at than what the founder proposed
    (e.g. if the original slot doesn't work).
    """
    founder_id = _get_founder_id(clerk_user_id)
    consultation = _get_consultation(consultation_id)
    _ensure_party(consultation, founder_id, 'advisor')
    _ensure_status(consultation, [STATUS_PENDING_ADVISOR])

    update: Dict[str, Any] = {'status': STATUS_PENDING_PAYMENT}

    if confirmed_time_iso:
        try:
            t = datetime.fromisoformat(confirmed_time_iso.replace('Z', '+00:00'))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t < datetime.now(timezone.utc):
                raise ValueError("Scheduled time must be in the future")
            update['scheduled_at'] = t.isoformat()
        except (ValueError, AttributeError):
            raise ValueError("Invalid confirmed_time_iso")

    supabase = get_supabase()
    res = supabase.table('advisor_consultations').update(update).eq('id', consultation_id).execute()
    updated = res.data[0] if res.data else {**consultation, **update}

    # Notify founder
    try:
        _notify_party(
            recipient_founder_id=consultation['founder_id'],
            actor_founder_id=consultation['advisor_id'],
            event_type='CONSULTATION_ACCEPTED',
            title='Consultation accepted',
            message='Your advisor accepted the consultation. Please send payment to confirm.',
            consultation_id=consultation_id,
        )
    except Exception as e:
        log_warning(f"Failed to send CONSULTATION_ACCEPTED notification: {e}")

    return _enrich_consultation(updated)


def decline_consultation(clerk_user_id: str, consultation_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
    """Advisor declines a pending request."""
    founder_id = _get_founder_id(clerk_user_id)
    consultation = _get_consultation(consultation_id)
    _ensure_party(consultation, founder_id, 'advisor')
    _ensure_status(consultation, [STATUS_PENDING_ADVISOR])

    supabase = get_supabase()
    update = {
        'status': STATUS_DECLINED,
        'declined_at': _now_iso(),
        'decline_reason': (reason or '').strip()[:500] or None,
    }
    res = supabase.table('advisor_consultations').update(update).eq('id', consultation_id).execute()
    updated = res.data[0] if res.data else {**consultation, **update}

    try:
        _notify_party(
            recipient_founder_id=consultation['founder_id'],
            actor_founder_id=consultation['advisor_id'],
            event_type='CONSULTATION_DECLINED',
            title='Consultation declined',
            message=(reason or 'The advisor was unable to accept this consultation'),
            consultation_id=consultation_id,
        )
    except Exception as e:
        log_warning(f"Failed to send CONSULTATION_DECLINED notification: {e}")

    return _enrich_consultation(updated)


# ---------------------------------------------------------------------------
# Payment confirmation (direct, off-platform)
# ---------------------------------------------------------------------------

def mark_payment_sent(
    clerk_user_id: str,
    consultation_id: str,
    payment_method: str,
    payment_reference: Optional[str] = None,
) -> Dict[str, Any]:
    """Founder marks that they've sent payment to the advisor (off-platform).

    Records payment_method + payment_reference for the audit trail.
    Status moves to pending_payment_confirmation; advisor must confirm receipt.
    """
    if payment_method not in ALLOWED_PAYMENT_METHODS:
        raise ValueError(f"payment_method must be one of {ALLOWED_PAYMENT_METHODS}")

    founder_id = _get_founder_id(clerk_user_id)
    consultation = _get_consultation(consultation_id)
    _ensure_party(consultation, founder_id, 'founder')
    _ensure_status(consultation, [STATUS_PENDING_PAYMENT])

    supabase = get_supabase()
    update = {
        'status': STATUS_PENDING_PAYMENT_CONFIRMATION,
        'payment_method': payment_method,
        'payment_reference': (payment_reference or '').strip()[:200] or None,
        'payment_marked_sent_at': _now_iso(),
    }
    res = supabase.table('advisor_consultations').update(update).eq('id', consultation_id).execute()
    updated = res.data[0] if res.data else {**consultation, **update}

    try:
        _notify_party(
            recipient_founder_id=consultation['advisor_id'],
            actor_founder_id=consultation['founder_id'],
            event_type='CONSULTATION_PAYMENT_SENT',
            title='Founder marked payment as sent',
            message='Please verify receipt and confirm to lock in the consultation.',
            consultation_id=consultation_id,
        )
    except Exception as e:
        log_warning(f"Failed to send CONSULTATION_PAYMENT_SENT notification: {e}")

    return _enrich_consultation(updated)


def confirm_payment_received(clerk_user_id: str, consultation_id: str) -> Dict[str, Any]:
    """Advisor confirms they received the payment.

    On confirmation:
      - Status -> 'confirmed'
      - Daily.co room is created (best-effort; failure does not block)
      - If this is the advisor's FIRST confirmed booking, start the 30-day trial
        (sets advisor_profiles.first_booking_at + trial_ends_at).
    """
    founder_id = _get_founder_id(clerk_user_id)
    consultation = _get_consultation(consultation_id)
    _ensure_party(consultation, founder_id, 'advisor')
    _ensure_status(consultation, [STATUS_PENDING_PAYMENT_CONFIRMATION])

    supabase = get_supabase()
    update: Dict[str, Any] = {
        'status': STATUS_CONFIRMED,
        'payment_confirmed_at': _now_iso(),
    }

    # Best-effort: create a Daily.co room for the call
    try:
        from services import video_service
        if video_service.is_daily_configured():
            duration = consultation.get('duration_min') or 30
            # Reuse the founder-date room helper (it accepts arbitrary IDs/stages
            # via room name; we just need a unique room URL)
            import time
            room_name = f"adv-{consultation_id[:8]}-{int(time.time())}"
            room = video_service.create_founder_date_room(
                founder_date_id=consultation_id,  # used only as part of name prefix
                stage=1,
                duration_minutes=duration,
            )
            update['video_room_url'] = room.get('url')
            update['video_room_id'] = room.get('name')
    except Exception as e:
        # Don't block confirmation if video room creation fails — frontend can
        # offer a "Generate video link" retry button.
        log_warning(f"Daily.co room creation failed for consultation {consultation_id}: {e}")

    res = supabase.table('advisor_consultations').update(update).eq('id', consultation_id).execute()
    updated = res.data[0] if res.data else {**consultation, **update}

    # First-booking trigger: start the 30-day trial if not already triggered
    try:
        _maybe_start_trial(consultation['advisor_id'])
    except Exception as e:
        log_error(f"Failed to start advisor trial for {consultation['advisor_id']}: {e}")

    try:
        _notify_party(
            recipient_founder_id=consultation['founder_id'],
            actor_founder_id=consultation['advisor_id'],
            event_type='CONSULTATION_CONFIRMED',
            title='Consultation confirmed',
            message='Your advisor confirmed receipt of payment. The call is locked in.',
            consultation_id=consultation_id,
        )
    except Exception as e:
        log_warning(f"Failed to send CONSULTATION_CONFIRMED notification: {e}")

    return _enrich_consultation(updated)


# ---------------------------------------------------------------------------
# Completion + post-call actions
# ---------------------------------------------------------------------------

def mark_completed(clerk_user_id: str, consultation_id: str) -> Dict[str, Any]:
    """Either party can mark a confirmed consultation as completed (after the call)."""
    founder_id = _get_founder_id(clerk_user_id)
    consultation = _get_consultation(consultation_id)

    if consultation.get('advisor_id') != founder_id and consultation.get('founder_id') != founder_id:
        raise ValueError("You are not part of this consultation")

    _ensure_status(consultation, [STATUS_CONFIRMED])

    supabase = get_supabase()
    update = {'status': STATUS_COMPLETED, 'completed_at': _now_iso()}
    res = supabase.table('advisor_consultations').update(update).eq('id', consultation_id).execute()
    return _enrich_consultation(res.data[0] if res.data else {**consultation, **update})


def request_refund(clerk_user_id: str, consultation_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
    """Founder flags a refund request within 7 days of the call.

    The platform does NOT process the refund (payment was direct). This just
    records the request and notifies the advisor for off-platform resolution.
    """
    founder_id = _get_founder_id(clerk_user_id)
    consultation = _get_consultation(consultation_id)
    _ensure_party(consultation, founder_id, 'founder')

    if consultation.get('status') not in (STATUS_CONFIRMED, STATUS_COMPLETED, STATUS_NO_SHOW):
        raise ValueError("Refunds can only be requested on a confirmed/completed/no-show consultation")

    # 7-day refund window from the scheduled time (or completion time if completed)
    reference_time = consultation.get('completed_at') or consultation.get('scheduled_at')
    if reference_time:
        try:
            ref = datetime.fromisoformat(str(reference_time).replace('Z', '+00:00'))
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - ref > timedelta(days=7):
                raise ValueError("Refund window has closed (7 days after the call)")
        except (ValueError, AttributeError):
            pass  # If date parsing fails, allow the request and let humans sort it out

    supabase = get_supabase()
    update = {
        'status': STATUS_REFUND_REQUESTED,
        'refund_requested_at': _now_iso(),
        'refund_reason': (reason or '').strip()[:1000] or None,
    }
    res = supabase.table('advisor_consultations').update(update).eq('id', consultation_id).execute()
    updated = res.data[0] if res.data else {**consultation, **update}

    try:
        _notify_party(
            recipient_founder_id=consultation['advisor_id'],
            actor_founder_id=consultation['founder_id'],
            event_type='CONSULTATION_REFUND_REQUESTED',
            title='Refund requested',
            message=(reason or 'The founder requested a refund for this consultation. Please resolve directly.'),
            consultation_id=consultation_id,
        )
    except Exception as e:
        log_warning(f"Failed to send CONSULTATION_REFUND_REQUESTED notification: {e}")

    return _enrich_consultation(updated)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def list_consultations(
    clerk_user_id: str,
    role: str = 'any',
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List consultations involving the current user.

    role: 'advisor' (only ones I'm advising), 'founder' (only ones I booked),
          or 'any' (default — both).
    status: optional status filter
    """
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()

    query = supabase.table('advisor_consultations').select('*')
    if role == 'advisor':
        query = query.eq('advisor_id', founder_id)
    elif role == 'founder':
        query = query.eq('founder_id', founder_id)
    else:
        query = query.or_(f"advisor_id.eq.{founder_id},founder_id.eq.{founder_id}")

    if status:
        query = query.eq('status', status)

    query = query.order('created_at', desc=True).limit(min(limit, 200))
    res = query.execute()
    return [_enrich_consultation(r) for r in (res.data or [])]


def get_consultation(clerk_user_id: str, consultation_id: str) -> Dict[str, Any]:
    """Fetch a single consultation; caller must be one of the parties."""
    founder_id = _get_founder_id(clerk_user_id)
    consultation = _get_consultation(consultation_id)
    if consultation.get('advisor_id') != founder_id and consultation.get('founder_id') != founder_id:
        raise ValueError("You are not part of this consultation")
    return _enrich_consultation(consultation)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _advisor_can_accept_bookings(advisor_row: Dict[str, Any]) -> bool:
    """Return True if the advisor's subscription state allows new bookings.

    free                       -> True (no first booking yet)
    trial + trial_ends_at>now  -> True
    active + period_end>now    -> True
    everything else            -> False (soft cutoff)
    """
    status = advisor_row.get('subscription_status') or 'free'
    now = datetime.now(timezone.utc)

    if status == 'free':
        return True
    if status == 'trial':
        ends = advisor_row.get('trial_ends_at')
        if not ends:
            return True  # trial flag without an end → allow defensively
        try:
            t = datetime.fromisoformat(str(ends).replace('Z', '+00:00'))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t > now
        except (ValueError, AttributeError):
            return False
    if status == 'active':
        ends = advisor_row.get('subscription_current_period_end')
        if not ends:
            return True
        try:
            t = datetime.fromisoformat(str(ends).replace('Z', '+00:00'))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t > now
        except (ValueError, AttributeError):
            return False
    return False


def _maybe_start_trial(advisor_user_id: str) -> None:
    """If this is the advisor's first confirmed booking, start the 30-day trial."""
    supabase = get_supabase()

    res = supabase.table('advisor_profiles').select(
        'first_booking_at, subscription_status'
    ).eq('user_id', advisor_user_id).execute()

    if not res.data:
        return  # No profile (shouldn't happen, but be safe)

    advisor = res.data[0]
    if advisor.get('first_booking_at'):
        return  # Trial already started; nothing to do
    if advisor.get('subscription_status') == 'active':
        return  # Already a paying subscriber; trial irrelevant

    trial_days = plan_service.ADVISOR_PRICING['trialDaysAfterFirstBooking']
    now = datetime.now(timezone.utc)
    trial_ends = now + timedelta(days=trial_days)

    supabase.table('advisor_profiles').update({
        'first_booking_at': now.isoformat(),
        'trial_ends_at': trial_ends.isoformat(),
        'subscription_status': 'trial',
    }).eq('user_id', advisor_user_id).execute()

    log_info(
        f"Started Pro Advisor trial for advisor {advisor_user_id}; "
        f"trial ends {trial_ends.isoformat()}"
    )


def _notify_party(
    recipient_founder_id: str,
    actor_founder_id: str,
    event_type: str,
    title: str,
    message: str,
    consultation_id: str,
) -> None:
    """Notify a party about a consultation event.

    NOTE: The current `notifications` table is keyed on workspace_id, and
    consultations don't belong to any workspace. We intentionally don't insert
    rows there for now — the consultations dashboard is the source of truth
    until a global notifications channel is added (planned).

    Logs the intent so we can audit/replay later if needed.
    """
    log_info(
        f"[consultation_notify] event={event_type} consultation={consultation_id} "
        f"recipient={recipient_founder_id} actor={actor_founder_id} "
        f"title={title!r} message={message!r}"
    )


# ---------------------------------------------------------------------------
# Reviews — post-consultation ratings and feedback
# ---------------------------------------------------------------------------

def submit_review(
    clerk_user_id: str,
    consultation_id: str,
    rating: int,
    review_text: Optional[str] = None,
    is_public: bool = True,
) -> Dict[str, Any]:
    """Submit a review for a completed consultation.

    Either party can review the other. Founder reviews advisor, advisor reviews
    founder. Founder reviews (of advisors) are displayed on the advisor's
    public marketplace profile.

    Args:
        clerk_user_id:    Clerk user id of the reviewer
        consultation_id:  The consultation being reviewed
        rating:           1-5 stars
        review_text:      Optional written review
        is_public:        Whether to show on public profile (default True)

    Returns the created review row.
    """
    if not 1 <= rating <= 5:
        raise ValueError("Rating must be between 1 and 5")

    founder_id = _get_founder_id(clerk_user_id)
    consultation = _get_consultation(consultation_id)

    # Only completed consultations can be reviewed
    _ensure_status(consultation, [STATUS_COMPLETED])

    # Determine reviewer role and reviewee
    if consultation.get('founder_id') == founder_id:
        reviewer_role = 'founder'
        reviewee_id = consultation['advisor_id']
    elif consultation.get('advisor_id') == founder_id:
        reviewer_role = 'advisor'
        reviewee_id = consultation['founder_id']
    else:
        raise ValueError("You are not part of this consultation")

    supabase = get_supabase()

    # Check if already reviewed
    existing = supabase.table('advisor_consultation_reviews').select('id').eq(
        'consultation_id', consultation_id
    ).eq('reviewer_role', reviewer_role).execute()

    if existing.data:
        raise ValueError("You have already reviewed this consultation")

    payload = {
        'consultation_id': consultation_id,
        'reviewer_id': founder_id,
        'reviewee_id': reviewee_id,
        'reviewer_role': reviewer_role,
        'rating': rating,
        'review_text': (review_text or '').strip()[:2000] or None,
        'is_public': is_public,
    }

    res = supabase.table('advisor_consultation_reviews').insert(payload).execute()
    if not res.data:
        raise ValueError("Failed to submit review")

    review = res.data[0]

    # Notify the reviewee
    try:
        _notify_party(
            recipient_founder_id=reviewee_id,
            actor_founder_id=founder_id,
            event_type='CONSULTATION_REVIEW_RECEIVED',
            title='You received a review',
            message=f'{rating}-star review received for your consultation',
            consultation_id=consultation_id,
        )
    except Exception as e:
        log_warning(f"Failed to send CONSULTATION_REVIEW_RECEIVED notification: {e}")

    return review


def get_consultation_reviews(
    clerk_user_id: str,
    consultation_id: str,
) -> List[Dict[str, Any]]:
    """Get all reviews for a consultation (both sides if they exist).

    Caller must be a party to the consultation.
    """
    founder_id = _get_founder_id(clerk_user_id)
    consultation = _get_consultation(consultation_id)

    if consultation.get('advisor_id') != founder_id and consultation.get('founder_id') != founder_id:
        raise ValueError("You are not part of this consultation")

    supabase = get_supabase()
    res = supabase.table('advisor_consultation_reviews').select('*').eq(
        'consultation_id', consultation_id
    ).execute()

    return res.data or []


def get_advisor_public_reviews(
    advisor_user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Get public reviews for an advisor (founder reviews only).

    These are displayed on the advisor's marketplace profile. Only includes
    reviews where reviewer_role='founder' and is_public=true.

    Returns:
        {
            "reviews": [...],
            "stats": {
                "avg_rating": 4.5,
                "total_reviews": 12,
                "rating_breakdown": {5: 8, 4: 3, 3: 1, 2: 0, 1: 0}
            }
        }
    """
    supabase = get_supabase()

    # Fetch reviews (founder reviews of this advisor)
    query = supabase.table('advisor_consultation_reviews').select(
        '*, reviewer:reviewer_id(id, name, profile_picture_url)'
    ).eq('reviewee_id', advisor_user_id).eq(
        'reviewer_role', 'founder'
    ).eq('is_public', True).order(
        'created_at', desc=True
    ).range(offset, offset + limit - 1)

    res = query.execute()
    reviews = res.data or []

    # Calculate stats
    stats = get_advisor_rating_stats(advisor_user_id)

    return {
        'reviews': reviews,
        'stats': stats,
    }


def get_advisor_rating_stats(advisor_user_id: str) -> Dict[str, Any]:
    """Get aggregate rating statistics for an advisor.

    Only considers public founder reviews.
    """
    supabase = get_supabase()

    # Fetch all public founder reviews for this advisor
    res = supabase.table('advisor_consultation_reviews').select('rating').eq(
        'reviewee_id', advisor_user_id
    ).eq('reviewer_role', 'founder').eq('is_public', True).execute()

    reviews = res.data or []
    total = len(reviews)

    if total == 0:
        return {
            'avg_rating': None,
            'total_reviews': 0,
            'rating_breakdown': {5: 0, 4: 0, 3: 0, 2: 0, 1: 0},
        }

    ratings = [r['rating'] for r in reviews]
    avg = sum(ratings) / total
    breakdown = {i: ratings.count(i) for i in range(1, 6)}

    return {
        'avg_rating': round(avg, 2),
        'total_reviews': total,
        'rating_breakdown': breakdown,
    }


def can_review_consultation(clerk_user_id: str, consultation_id: str) -> Dict[str, Any]:
    """Check if the current user can submit a review for a consultation.

    Returns:
        {
            "can_review": bool,
            "reason": str or None,
            "reviewer_role": "founder" | "advisor" | None,
            "already_reviewed": bool
        }
    """
    try:
        founder_id = _get_founder_id(clerk_user_id)
    except ValueError:
        return {
            'can_review': False,
            'reason': 'Profile not found',
            'reviewer_role': None,
            'already_reviewed': False,
        }

    try:
        consultation = _get_consultation(consultation_id)
    except ValueError:
        return {
            'can_review': False,
            'reason': 'Consultation not found',
            'reviewer_role': None,
            'already_reviewed': False,
        }

    # Determine role
    if consultation.get('founder_id') == founder_id:
        reviewer_role = 'founder'
    elif consultation.get('advisor_id') == founder_id:
        reviewer_role = 'advisor'
    else:
        return {
            'can_review': False,
            'reason': 'You are not part of this consultation',
            'reviewer_role': None,
            'already_reviewed': False,
        }

    # Must be completed
    if consultation.get('status') != STATUS_COMPLETED:
        return {
            'can_review': False,
            'reason': 'Consultation must be completed before reviewing',
            'reviewer_role': reviewer_role,
            'already_reviewed': False,
        }

    # Check if already reviewed
    supabase = get_supabase()
    existing = supabase.table('advisor_consultation_reviews').select('id').eq(
        'consultation_id', consultation_id
    ).eq('reviewer_role', reviewer_role).execute()

    if existing.data:
        return {
            'can_review': False,
            'reason': 'You have already reviewed this consultation',
            'reviewer_role': reviewer_role,
            'already_reviewed': True,
        }

    return {
        'can_review': True,
        'reason': None,
        'reviewer_role': reviewer_role,
        'already_reviewed': False,
    }
