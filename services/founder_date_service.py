"""
Founder Date methodology service.

The Founder Date is a structured 3-stage co-founder evaluation:
  Stage 1 - Discovery (30 min)  -> vibe check
  Stage 2 - Deep Dive (60 min)  -> working style + commitment
  Stage 3 - Decision (90 min)   -> equity terms + exit scenarios

Both founders must rate vibe_rating >= 4 to advance to the next stage.
This module owns the stage definitions, the gating logic, and the workflow
state transitions. Video room creation is delegated to video_service.
"""
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

from config.database import get_supabase
from services import video_service
from utils.logger import log_info, log_error


# ============================================================
# Stage definitions (the "methodology" — this is the IP)
# ============================================================
ADVANCE_THRESHOLD = 4  # both founders must rate >= 4 to unlock next stage

FOUNDER_DATE_STAGES: Dict[int, Dict[str, Any]] = {
    1: {
        'stage': 1,
        'name': 'Discovery',
        'duration_minutes': 30,
        'goal': 'Vibe check and basic alignment',
        'description': (
            'A short first call to gauge mutual interest and surface red flags '
            'before investing more time.'
        ),
        'prompts': [
            'Why this idea, and why now?',
            'What does success look like for you in 5 years?',
            'What kills this for you in month 1? Month 6?',
            'How many hours per week can you realistically commit?',
            'What is your salary requirement in months 1–6?',
        ],
        'evaluation_fields': ['vibe_rating', 'continue_decision'],
        'unlocks_when': f'both founders rate vibe_rating >= {ADVANCE_THRESHOLD}',
    },
    2: {
        'stage': 2,
        'name': 'Deep Dive',
        'duration_minutes': 60,
        'goal': 'Working style, conflict, and commitment',
        'description': (
            'A longer call to understand how you actually work together under '
            'pressure, conflict, and ambiguity.'
        ),
        'prompts': [
            'Walk me through a previous conflict and how you resolved it.',
            'Tell me about your last failure and what you took from it.',
            'What is your role split philosophy (CEO / CTO / equal)?',
            'What does your week look like when things are going badly?',
            'How do you make irreversible decisions when you disagree?',
        ],
        'evaluation_fields': [
            'vibe_rating', 'continue_decision',
            'working_style_score', 'communication_score',
        ],
        'unlocks_when': f'both founders rate vibe_rating >= {ADVANCE_THRESHOLD}',
    },
    3: {
        'stage': 3,
        'name': 'Decision',
        'duration_minutes': 90,
        'goal': 'Equity terms, vesting, exit scenarios',
        'description': (
            'The commitment conversation. After this call, if both want to '
            'proceed, open the equity calculator together and start a workspace.'
        ),
        'prompts': [
            'What is your view on 50/50 vs unequal splits?',
            'Are you comfortable with a 1-year cliff and 4-year vesting?',
            'What happens if one of us wants to quit at month 6?',
            'How do we handle pre-existing IP? Reverse vesting?',
            'What is each of our worst-case dilution tolerance?',
            'In which scenarios would we sell vs. keep going?',
        ],
        'evaluation_fields': [
            'vibe_rating', 'continue_decision',
            'working_style_score', 'communication_score', 'alignment_score',
        ],
        'unlocks_when': 'completion -> proceed to workspace + equity calculator',
    },
}


def get_stage_definitions() -> List[Dict[str, Any]]:
    """Return all stage definitions (for the frontend to render the UI)."""
    return [FOUNDER_DATE_STAGES[i] for i in (1, 2, 3)]


# ============================================================
# Helpers
# ============================================================
def _get_founder_id(clerk_user_id: str) -> str:
    """Resolve clerk_user_id -> founders.id, raising ValueError if missing."""
    supabase = get_supabase()
    result = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not result.data:
        raise ValueError("Founder not found")
    return result.data[0]['id']


def _ordered_pair(a: str, b: str) -> Tuple[str, str]:
    """Order two founder IDs deterministically so (a, b) and (b, a) are the same pair."""
    return (a, b) if a < b else (b, a)


def _is_participant(founder_id: str, fd_row: Dict[str, Any]) -> bool:
    return fd_row.get('founder_a_id') == founder_id or fd_row.get('founder_b_id') == founder_id


# ============================================================
# Founder Date lifecycle
# ============================================================
def get_or_create_founder_date(
    initiator_clerk_user_id: str,
    other_founder_id: str,
    match_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get the existing founder date between two founders, or create one.
    Idempotent: calling twice returns the same row.
    """
    initiator_id = _get_founder_id(initiator_clerk_user_id)
    if initiator_id == other_founder_id:
        raise ValueError("Cannot start a founder date with yourself")

    a_id, b_id = _ordered_pair(initiator_id, other_founder_id)
    supabase = get_supabase()

    existing = supabase.table('founder_dates').select('*').eq(
        'founder_a_id', a_id
    ).eq('founder_b_id', b_id).execute()

    if existing.data:
        return existing.data[0]

    new_row = {
        'founder_a_id': a_id,
        'founder_b_id': b_id,
        'match_id': match_id,
        'project_id': project_id,
        'initiated_by': initiator_id,
        'current_stage': 1,
        'overall_status': 'IN_PROGRESS',
    }
    result = supabase.table('founder_dates').insert(new_row).execute()
    if not result.data:
        raise ValueError("Failed to create founder date")

    log_info(f"Founder date created: {result.data[0]['id']}")
    return result.data[0]


def list_founder_dates(clerk_user_id: str) -> List[Dict[str, Any]]:
    """List founder dates the current user is part of, with last-call summary."""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()

    # OR query: founder_id is either founder_a or founder_b
    result = supabase.table('founder_dates').select(
        '*, founder_a:founders!founder_a_id(id, name, profile_picture_url, linkedin_verified, github_verified),'
        ' founder_b:founders!founder_b_id(id, name, profile_picture_url, linkedin_verified, github_verified)'
    ).or_(f'founder_a_id.eq.{founder_id},founder_b_id.eq.{founder_id}').order(
        'updated_at', desc=True
    ).execute()

    return result.data or []


def get_founder_date_detail(clerk_user_id: str, founder_date_id: str) -> Dict[str, Any]:
    """
    Get a founder date with full state: stage, calls, evaluations, prompts,
    next-action hint.
    """
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()

    fd_result = supabase.table('founder_dates').select(
        '*, founder_a:founders!founder_a_id(id, name, profile_picture_url, cal_username, cal_event_type_id, timezone),'
        ' founder_b:founders!founder_b_id(id, name, profile_picture_url, cal_username, cal_event_type_id, timezone)'
    ).eq('id', founder_date_id).execute()

    if not fd_result.data:
        raise ValueError("Founder date not found")

    fd = fd_result.data[0]
    if not _is_participant(founder_id, fd):
        raise ValueError("Not authorized for this founder date")

    # All calls + evaluations
    calls_result = supabase.table('founder_date_calls').select('*').eq(
        'founder_date_id', founder_date_id
    ).order('stage').order('created_at').execute()
    calls = calls_result.data or []

    call_ids = [c['id'] for c in calls]
    evaluations: List[Dict[str, Any]] = []
    if call_ids:
        eval_result = supabase.table('founder_date_evaluations').select('*').in_(
            'call_id', call_ids
        ).execute()
        evaluations = eval_result.data or []

    # Group evaluations by call_id
    evals_by_call: Dict[str, List[Dict[str, Any]]] = {}
    for e in evaluations:
        evals_by_call.setdefault(e['call_id'], []).append(e)

    enriched_calls = []
    for c in calls:
        c_evals = evals_by_call.get(c['id'], [])
        # Filter evaluator's own private notes from the response (privacy)
        sanitized_evals = []
        for e in c_evals:
            ev_copy = dict(e)
            if e['evaluator_id'] != founder_id:
                ev_copy.pop('private_notes', None)
            sanitized_evals.append(ev_copy)
        enriched_calls.append({**c, 'evaluations': sanitized_evals})

    current_stage = fd['current_stage']
    stage_def = FOUNDER_DATE_STAGES[current_stage]

    # Determine next action for the current user
    next_action = _compute_next_action(fd, enriched_calls, founder_id)

    return {
        **fd,
        'calls': enriched_calls,
        'current_stage_definition': stage_def,
        'all_stages': get_stage_definitions(),
        'next_action': next_action,
        'viewer_founder_id': founder_id,
    }


def _compute_next_action(fd: Dict[str, Any], calls: List[Dict[str, Any]], viewer_id: str) -> Dict[str, Any]:
    """
    Compute what the viewing user should do next:
      SCHEDULE_CALL  - no call exists for current stage yet
      JOIN_CALL      - call is scheduled and within join window (15min before -> end)
      EVALUATE_CALL  - call ended but viewer hasn't submitted their evaluation
      WAIT_FOR_PEER  - viewer has evaluated; waiting for the other founder
      ADVANCE_STAGE  - both evaluated favorably; ready to advance
      DATE_COMPLETE  - all 3 stages cleared; create workspace
      DATE_ABANDONED - someone chose STOP at any stage
    """
    if fd.get('overall_status') == 'ABANDONED':
        return {'action': 'DATE_ABANDONED', 'message': 'This founder date has been ended.'}
    if fd.get('overall_status') == 'COMPLETED':
        return {'action': 'DATE_COMPLETE', 'message': 'All 3 stages complete. Create a workspace to start building.'}

    current_stage = fd['current_stage']
    stage_calls = [c for c in calls if c['stage'] == current_stage]

    if not stage_calls:
        return {
            'action': 'SCHEDULE_CALL',
            'stage': current_stage,
            'message': f'Schedule the {FOUNDER_DATE_STAGES[current_stage]["name"]} call.',
        }

    # Use the latest call attempt for this stage
    latest = max(stage_calls, key=lambda c: c.get('created_at') or '')

    if latest['status'] == 'SCHEDULED':
        return {
            'action': 'JOIN_CALL',
            'call_id': latest['id'],
            'scheduled_at': latest.get('scheduled_at'),
            'room_url': latest.get('daily_room_url'),
            'message': 'Your call is scheduled. Join when ready.',
        }

    if latest['status'] in ('CANCELLED', 'NO_SHOW'):
        return {
            'action': 'SCHEDULE_CALL',
            'stage': current_stage,
            'message': 'Last call did not happen. Schedule again.',
        }

    if latest['status'] == 'COMPLETED':
        viewer_eval = next(
            (e for e in latest.get('evaluations', []) if e['evaluator_id'] == viewer_id),
            None,
        )
        if not viewer_eval:
            return {
                'action': 'EVALUATE_CALL',
                'call_id': latest['id'],
                'message': 'Submit your evaluation to advance.',
            }

        peer_eval = next(
            (e for e in latest.get('evaluations', []) if e['evaluator_id'] != viewer_id),
            None,
        )
        if not peer_eval:
            return {
                'action': 'WAIT_FOR_PEER',
                'call_id': latest['id'],
                'message': 'Waiting for the other founder to submit their evaluation.',
            }

        # Both evaluated. Check if we should advance.
        both_continue = all(e['continue_decision'] == 'CONTINUE' for e in (viewer_eval, peer_eval))
        both_high_rated = all(e['vibe_rating'] >= ADVANCE_THRESHOLD for e in (viewer_eval, peer_eval))

        if both_continue and both_high_rated:
            if current_stage < 3:
                return {
                    'action': 'ADVANCE_STAGE',
                    'next_stage': current_stage + 1,
                    'message': f'Both rated highly! Ready for Stage {current_stage + 1}.',
                }
            return {
                'action': 'DATE_COMPLETE',
                'message': 'All stages complete. Time to build together.',
            }
        return {
            'action': 'DATE_ABANDONED',
            'message': 'One or both founders chose not to continue.',
        }

    return {'action': 'JOIN_CALL', 'call_id': latest['id'], 'message': 'Call in progress.'}


# ============================================================
# Calls
# ============================================================
def schedule_call(
    clerk_user_id: str,
    founder_date_id: str,
    scheduled_at: Optional[str] = None,
    cal_booking_id: Optional[str] = None,
    cal_booking_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Schedule a call for the current stage. Creates a Daily.co room.

    `scheduled_at` is the ISO timestamp the call is scheduled for. It can be
    set manually or come from a Cal.com booking webhook.
    """
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()

    fd_result = supabase.table('founder_dates').select('*').eq('id', founder_date_id).execute()
    if not fd_result.data:
        raise ValueError("Founder date not found")
    fd = fd_result.data[0]
    if not _is_participant(founder_id, fd):
        raise ValueError("Not authorized for this founder date")
    if fd['overall_status'] != 'IN_PROGRESS':
        raise ValueError(f"Founder date is {fd['overall_status']}; cannot schedule new calls")

    current_stage = fd['current_stage']
    stage_def = FOUNDER_DATE_STAGES[current_stage]

    # Cancel any prior unfinished call for this stage
    prior = supabase.table('founder_date_calls').select('id, status').eq(
        'founder_date_id', founder_date_id
    ).eq('stage', current_stage).in_('status', ['SCHEDULED', 'IN_PROGRESS']).execute()
    if prior.data:
        for p in prior.data:
            supabase.table('founder_date_calls').update({'status': 'CANCELLED'}).eq('id', p['id']).execute()

    # Create the Daily.co room (or skip if not configured — call still saved)
    room: Dict[str, Any] = {}
    if video_service.is_daily_configured():
        try:
            room = video_service.create_founder_date_room(
                founder_date_id=founder_date_id,
                stage=current_stage,
                duration_minutes=stage_def['duration_minutes'],
            )
        except Exception as e:
            log_error(f"Failed to create video room for founder date {founder_date_id}: {e}")
            # Not fatal — call can still be scheduled; the user can join via Cal.com link

    new_call = {
        'founder_date_id': founder_date_id,
        'stage': current_stage,
        'status': 'SCHEDULED',
        'scheduled_at': scheduled_at,
        'cal_booking_id': cal_booking_id,
        'cal_booking_url': cal_booking_url,
        'daily_room_name': room.get('name'),
        'daily_room_url': room.get('url'),
        'daily_room_expires_at': room.get('expires_at_iso'),
    }
    result = supabase.table('founder_date_calls').insert(new_call).execute()
    if not result.data:
        raise ValueError("Failed to schedule call")

    # Activation milestones based on the stage being scheduled
    try:
        from services import activation_service
        activation_service.record_milestone(
            founder_id, activation_service.Milestone.FIRST_CALL_SCHEDULED,
            {'founder_date_id': founder_date_id, 'stage': current_stage},
        )
        if current_stage == 2:
            activation_service.record_milestone(
                founder_id, activation_service.Milestone.FOUNDER_DATE_STAGE_2,
                {'founder_date_id': founder_date_id},
            )
        elif current_stage == 3:
            activation_service.record_milestone(
                founder_id, activation_service.Milestone.FOUNDER_DATE_STAGE_3,
                {'founder_date_id': founder_date_id},
            )
    except Exception:
        pass

    return result.data[0]


def start_call(clerk_user_id: str, call_id: str) -> Dict[str, Any]:
    """Mark a call as IN_PROGRESS and return the room URL."""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()

    call_result = supabase.table('founder_date_calls').select(
        '*, founder_dates(founder_a_id, founder_b_id)'
    ).eq('id', call_id).execute()
    if not call_result.data:
        raise ValueError("Call not found")

    call = call_result.data[0]
    fd = call.get('founder_dates') or {}
    if founder_id not in (fd.get('founder_a_id'), fd.get('founder_b_id')):
        raise ValueError("Not authorized for this call")

    if call['status'] in ('COMPLETED', 'CANCELLED', 'NO_SHOW'):
        raise ValueError(f"Call is already {call['status']}")

    # Idempotent: only set started_at the first time
    update: Dict[str, Any] = {'status': 'IN_PROGRESS'}
    if not call.get('started_at'):
        update['started_at'] = datetime.now(timezone.utc).isoformat()

    supabase.table('founder_date_calls').update(update).eq('id', call_id).execute()
    return {**call, **update}


def complete_call(clerk_user_id: str, call_id: str) -> Dict[str, Any]:
    """Mark a call as COMPLETED. Computes duration."""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()

    call_result = supabase.table('founder_date_calls').select(
        '*, founder_dates(founder_a_id, founder_b_id)'
    ).eq('id', call_id).execute()
    if not call_result.data:
        raise ValueError("Call not found")

    call = call_result.data[0]
    fd = call.get('founder_dates') or {}
    if founder_id not in (fd.get('founder_a_id'), fd.get('founder_b_id')):
        raise ValueError("Not authorized for this call")

    now = datetime.now(timezone.utc)
    duration_seconds = None
    if call.get('started_at'):
        try:
            started = datetime.fromisoformat(call['started_at'].replace('Z', '+00:00'))
            duration_seconds = int((now - started).total_seconds())
        except Exception:
            pass

    update = {
        'status': 'COMPLETED',
        'ended_at': now.isoformat(),
        'duration_seconds': duration_seconds,
    }
    supabase.table('founder_date_calls').update(update).eq('id', call_id).execute()

    # Activation: FIRST_CALL_COMPLETED (idempotent)
    try:
        from services import activation_service
        activation_service.record_milestone(
            founder_id, activation_service.Milestone.FIRST_CALL_COMPLETED,
            {'call_id': call_id, 'duration_seconds': duration_seconds},
        )
    except Exception:
        pass

    # Best-effort cleanup of the Daily.co room (saves bandwidth)
    if call.get('daily_room_name'):
        video_service.delete_room(call['daily_room_name'])

    return {**call, **update}


# ============================================================
# Evaluations and stage transitions
# ============================================================
def submit_evaluation(
    clerk_user_id: str,
    call_id: str,
    vibe_rating: int,
    continue_decision: str,
    working_style_score: Optional[int] = None,
    communication_score: Optional[int] = None,
    alignment_score: Optional[int] = None,
    private_notes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Submit a post-call evaluation. If both founders have submitted, transition
    the founder date to the next stage (or complete / abandon it).
    """
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()

    if vibe_rating < 1 or vibe_rating > 5:
        raise ValueError("vibe_rating must be 1-5")
    if continue_decision not in ('CONTINUE', 'PAUSE', 'STOP'):
        raise ValueError("continue_decision must be CONTINUE, PAUSE, or STOP")

    call_result = supabase.table('founder_date_calls').select(
        '*, founder_dates(*)'
    ).eq('id', call_id).execute()
    if not call_result.data:
        raise ValueError("Call not found")

    call = call_result.data[0]
    fd = call.get('founder_dates') or {}
    if founder_id not in (fd.get('founder_a_id'), fd.get('founder_b_id')):
        raise ValueError("Not authorized for this call")

    if call['status'] != 'COMPLETED':
        raise ValueError("Call must be COMPLETED before evaluating")

    eval_row = {
        'call_id': call_id,
        'evaluator_id': founder_id,
        'vibe_rating': vibe_rating,
        'continue_decision': continue_decision,
        'working_style_score': working_style_score,
        'communication_score': communication_score,
        'alignment_score': alignment_score,
        'private_notes': private_notes,
    }
    # Upsert (one eval per evaluator per call)
    try:
        supabase.table('founder_date_evaluations').upsert(
            eval_row, on_conflict='call_id,evaluator_id'
        ).execute()
    except Exception as e:
        log_error(f"Evaluation upsert failed: {e}")
        raise

    # Did both founders submit?
    all_evals = supabase.table('founder_date_evaluations').select('*').eq('call_id', call_id).execute()
    evaluations = all_evals.data or []

    new_state = _maybe_transition_stage(fd, call, evaluations)

    return {
        'evaluation': eval_row,
        'transition': new_state,
    }


def _maybe_transition_stage(
    fd: Dict[str, Any],
    call: Dict[str, Any],
    evaluations: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    If both founders have evaluated this stage's call, transition the founder
    date forward (or abandon). Returns the new state, or None if waiting.
    """
    supabase = get_supabase()

    # Need exactly 2 distinct evaluators
    evaluator_ids = {e['evaluator_id'] for e in evaluations}
    if {fd['founder_a_id'], fd['founder_b_id']} - evaluator_ids:
        return None  # Still waiting for the peer's eval

    both_continue = all(e['continue_decision'] == 'CONTINUE' for e in evaluations)
    both_high = all(e['vibe_rating'] >= ADVANCE_THRESHOLD for e in evaluations)

    fd_id = fd['id']
    current_stage = fd['current_stage']

    if not (both_continue and both_high):
        # Either one stopped, paused, or rated too low -> abandon
        supabase.table('founder_dates').update({
            'overall_status': 'ABANDONED',
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }).eq('id', fd_id).execute()
        log_info(f"Founder date {fd_id} abandoned at stage {current_stage}")
        return {'action': 'ABANDONED', 'stage_when_abandoned': current_stage}

    if current_stage < 3:
        next_stage = current_stage + 1
        supabase.table('founder_dates').update({
            'current_stage': next_stage,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }).eq('id', fd_id).execute()
        log_info(f"Founder date {fd_id} advanced from stage {current_stage} -> {next_stage}")
        return {'action': 'ADVANCED', 'next_stage': next_stage}

    # Stage 3 cleared -> COMPLETED
    supabase.table('founder_dates').update({
        'overall_status': 'COMPLETED',
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }).eq('id', fd_id).execute()
    log_info(f"Founder date {fd_id} COMPLETED")

    # Activation: FOUNDER_DATE_COMPLETED for both founders
    try:
        from services import activation_service
        for fid in (fd['founder_a_id'], fd['founder_b_id']):
            activation_service.record_milestone(
                fid, activation_service.Milestone.FOUNDER_DATE_COMPLETED,
                {'founder_date_id': fd_id},
            )
    except Exception:
        pass

    return {'action': 'COMPLETED'}


def abandon_founder_date(clerk_user_id: str, founder_date_id: str) -> Dict[str, Any]:
    """Either founder can abandon a founder date at any time."""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()

    fd_result = supabase.table('founder_dates').select('*').eq('id', founder_date_id).execute()
    if not fd_result.data:
        raise ValueError("Founder date not found")
    fd = fd_result.data[0]
    if not _is_participant(founder_id, fd):
        raise ValueError("Not authorized for this founder date")

    supabase.table('founder_dates').update({
        'overall_status': 'ABANDONED',
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }).eq('id', founder_date_id).execute()

    log_info(f"Founder date {founder_date_id} abandoned by {founder_id}")
    return {'success': True, 'overall_status': 'ABANDONED'}


# ============================================================
# Cal.com integration (lightweight — frontend embeds the widget)
# ============================================================
def update_cal_settings(
    clerk_user_id: str,
    cal_username: Optional[str] = None,
    cal_event_type_id: Optional[int] = None,
    timezone_str: Optional[str] = None,
) -> Dict[str, Any]:
    """Update the founder's Cal.com username + event type for embedding."""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()

    update_data: Dict[str, Any] = {}
    if cal_username is not None:
        update_data['cal_username'] = cal_username.strip().lower() or None
    if cal_event_type_id is not None:
        update_data['cal_event_type_id'] = cal_event_type_id
    if timezone_str is not None:
        update_data['timezone'] = timezone_str.strip() or None

    if not update_data:
        raise ValueError("Nothing to update")

    result = supabase.table('founders').update(update_data).eq('id', founder_id).execute()
    if not result.data:
        raise ValueError("Failed to update Cal settings")

    return result.data[0]
