"""
Equity Questionnaire Service

Handles CRUD operations for equity questionnaire responses, scenarios,
and document generation workflows.
"""

from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from config.database import get_supabase
from services.workspace_service import _get_founder_id, _verify_workspace_access, _can_edit_workspace, _log_audit
from services.equity_calculation_service import (
    generate_all_scenarios,
    validate_responses,
    validate_startup_context
)
from utils.logger import log_error, log_info


def _get_workspace_founders(workspace_id: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Get the two founder IDs for a workspace.
    Returns (founder_a_id, founder_b_id) where founder_a is the earlier participant.
    """
    supabase = get_supabase()
    
    # Get workspace participants who are NOT advisors (check both cases)
    participants = supabase.table('workspace_participants').select(
        'user_id, created_at, role'
    ).eq('workspace_id', workspace_id).order('created_at').execute()
    
    # Log workspace info without sensitive user data
    participant_count = len(participants.data) if participants.data else 0
    log_info(f"_get_workspace_founders: workspace_id={workspace_id}, participant_count={participant_count}")
    
    # Filter out advisors (case-insensitive)
    founders = [p for p in (participants.data or []) if (p.get('role') or '').upper() != 'ADVISOR']
    
    log_info(f"_get_workspace_founders: filtered founders (non-advisors)={founders}")
    
    if len(founders) < 2:
        log_info(f"_get_workspace_founders: Less than 2 founders found, returning (None, None)")
        # If we have at least one founder, return them as founder_a
        if len(founders) == 1:
            return (founders[0]['user_id'], None)
        return (None, None)
    
    # First two participants are founder A and B
    founder_a_id = founders[0]['user_id']
    founder_b_id = founders[1]['user_id']
    
    log_info(f"_get_workspace_founders: returning founder_a={founder_a_id}, founder_b={founder_b_id}")
    
    return (founder_a_id, founder_b_id)


def save_questionnaire_response(
    clerk_user_id: str,
    workspace_id: str,
    responses: Dict[str, Any],
    is_complete: bool = False
) -> Dict[str, Any]:
    """
    Save or update a founder's questionnaire responses.
    
    Args:
        clerk_user_id: Clerk user ID of the founder
        workspace_id: Workspace ID
        responses: Questionnaire responses as dict
        is_complete: Whether the questionnaire is fully completed
    
    Returns:
        The saved response record
    """
    founder_id = _can_edit_workspace(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    log_info(f"save_questionnaire_response: founder_id={founder_id}, is_complete={is_complete}")
    
    # Check if response already exists - get full record to preserve existing data
    existing = supabase.table('equity_questionnaire_responses').select('*').eq(
        'workspace_id', workspace_id
    ).eq('founder_id', founder_id).execute()
    
    if existing.data:
        # Merge new responses with existing ones
        # Smart merge: only overwrite if new value is non-empty
        existing_responses = existing.data[0].get('responses', {})
        merged_responses = {**existing_responses}
        
        for key, value in responses.items():
            # Only update if new value is not empty/falsy (except for explicit False booleans)
            if value is not None:
                # For strings, only update if non-empty
                if isinstance(value, str) and value == '':
                    continue
                # For dicts, only update if non-empty or if it has non-empty values
                if isinstance(value, dict):
                    # Check if dict has any non-empty values
                    has_content = any(
                        v not in (None, '', {}, []) and v is not False
                        for v in value.values()
                    ) if value else False
                    if not has_content and key in existing_responses:
                        continue
                merged_responses[key] = value
        
        # Determine the is_complete status:
        # - If is_complete=True is passed, mark as complete
        # - If is_complete=False is passed but already complete, KEEP it complete (don't downgrade)
        # - Only mark incomplete if it was never complete
        existing_is_complete = existing.data[0].get('is_complete', False)
        final_is_complete = is_complete or existing_is_complete  # Preserve complete status
        
        response_data = {
            'responses': merged_responses,
            'is_complete': final_is_complete,
        }
        # Only update completed_at when transitioning to complete, don't overwrite existing timestamp
        if final_is_complete and not existing_is_complete:
            response_data['completed_at'] = datetime.now(timezone.utc).isoformat()
        
        log_info(f"Updating existing record id={existing.data[0]['id']}, existing_is_complete={existing_is_complete}, requested is_complete={is_complete}, final_is_complete={final_is_complete}")
        
        # Update existing
        result = supabase.table('equity_questionnaire_responses').update(
            response_data
        ).eq('id', existing.data[0]['id']).execute()
        
        log_info(f"Update result: {result.data}")
    else:
        # Insert new
        response_data = {
            'workspace_id': workspace_id,
            'founder_id': founder_id,
            'responses': responses,
            'is_complete': is_complete,
            'completed_at': datetime.now(timezone.utc).isoformat() if is_complete else None,
        }
        log_info(f"Inserting new record with is_complete={is_complete}")
        result = supabase.table('equity_questionnaire_responses').insert(
            response_data
        ).execute()
        log_info(f"Insert result: {result.data}")
    
    if not result.data:
        raise ValueError("Failed to save questionnaire response")
    
    _log_audit(
        workspace_id, founder_id, 
        'save_equity_questionnaire', 
        'equity_questionnaire_response', 
        result.data[0]['id'],
        {'is_complete': is_complete}
    )
    
    return result.data[0]


def get_questionnaire_responses(
    clerk_user_id: str,
    workspace_id: str
) -> Dict[str, Any]:
    """
    Get all questionnaire responses for a workspace.
    
    Args:
        clerk_user_id: Clerk user ID
        workspace_id: Workspace ID
    
    Returns:
        Dict with responses for each founder and completion status
    """
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Get all responses for this workspace
    # Include clerk_user_id so frontend can identify the current user
    responses = supabase.table('equity_questionnaire_responses').select(
        '*, founder:founders!founder_id(id, name, email, clerk_user_id)'
    ).eq('workspace_id', workspace_id).execute()
    
    # Get workspace founders to determine A/B
    founder_a_id, founder_b_id = _get_workspace_founders(workspace_id)
    
    # Organize responses
    result = {
        'founder_a': None,
        'founder_b': None,
        'startup_context': None,
        'both_complete': False,
        'founder_a_id': founder_a_id,
        'founder_b_id': founder_b_id,
    }
    
    for resp in (responses.data or []):
        founder_info = resp.get('founder', {})
        response_data = {
            'id': resp['id'],
            'founder_id': resp['founder_id'],
            'clerk_user_id': founder_info.get('clerk_user_id'),  # For frontend user identification
            'founder_name': founder_info.get('name', 'Unknown'),
            'responses': resp['responses'],
            'is_complete': resp['is_complete'],
            'completed_at': resp.get('completed_at'),
            'updated_at': resp['updated_at'],
        }
        
        if resp['founder_id'] == founder_a_id:
            result['founder_a'] = response_data
            # Extract startup context from founder A's responses
            if resp['responses'].get('startup_context'):
                result['startup_context'] = resp['responses']['startup_context']
        elif resp['founder_id'] == founder_b_id:
            result['founder_b'] = response_data
    
    # Check if both are complete
    result['both_complete'] = (
        result['founder_a'] is not None and 
        result['founder_a'].get('is_complete', False) and
        result['founder_b'] is not None and 
        result['founder_b'].get('is_complete', False)
    )
    
    return result


def calculate_equity(
    clerk_user_id: str,
    workspace_id: str
) -> Dict[str, Any]:
    """
    Calculate equity scenarios based on questionnaire responses.
    Requires both founders to have completed their questionnaires.
    
    Args:
        clerk_user_id: Clerk user ID
        workspace_id: Workspace ID
    
    Returns:
        Dict with recommended, equal, and custom scenario options
    """
    _verify_workspace_access(clerk_user_id, workspace_id)
    
    # Get all responses
    responses_data = get_questionnaire_responses(clerk_user_id, workspace_id)
    
    if not responses_data['both_complete']:
        raise ValueError("Both founders must complete the questionnaire before calculating equity")
    
    # Extract response data
    founder_a_responses = responses_data['founder_a']['responses']
    founder_b_responses = responses_data['founder_b']['responses']
    startup_context = responses_data.get('startup_context', {})
    
    # Validate responses
    is_valid_a, errors_a = validate_responses(founder_a_responses)
    if not is_valid_a:
        raise ValueError(f"Founder A responses incomplete: {', '.join(errors_a)}")
    
    is_valid_b, errors_b = validate_responses(founder_b_responses)
    if not is_valid_b:
        raise ValueError(f"Founder B responses incomplete: {', '.join(errors_b)}")
    
    is_valid_ctx, errors_ctx = validate_startup_context(startup_context)
    if not is_valid_ctx:
        raise ValueError(f"Startup context incomplete: {', '.join(errors_ctx)}")
    
    # Extract advisor equity from vesting terms (can be set by either founder)
    # Use the one that has it set, or default to 0
    vesting_a = founder_a_responses.get('vesting_terms', {})
    vesting_b = founder_b_responses.get('vesting_terms', {})
    advisor_equity_percent = (
        vesting_a.get('advisor_equity_percent') or 
        vesting_b.get('advisor_equity_percent') or 
        0.0
    )
    
    # Generate all scenarios (with advisor equity deducted)
    scenarios = generate_all_scenarios(
        founder_a_responses,
        founder_b_responses,
        startup_context,
        advisor_equity_percent
    )
    
    # Add founder names to response
    scenarios['founder_a_name'] = responses_data['founder_a']['founder_name']
    scenarios['founder_b_name'] = responses_data['founder_b']['founder_name']
    scenarios['founder_a_id'] = responses_data['founder_a_id']
    scenarios['founder_b_id'] = responses_data['founder_b_id']
    
    # Include advisor vesting terms if advisor equity is set
    if advisor_equity_percent > 0:
        scenarios['advisor_vesting'] = {
            'equity_percent': advisor_equity_percent,
            'vesting_years': vesting_a.get('advisor_vesting_years') or vesting_b.get('advisor_vesting_years') or 2,
            'cliff_months': vesting_a.get('advisor_cliff_months') or vesting_b.get('advisor_cliff_months') or 3,
        }
    
    return scenarios


def create_equity_scenario(
    clerk_user_id: str,
    workspace_id: str,
    scenario_type: str,
    founder_a_percent: float,
    founder_b_percent: float,
    vesting_terms: Optional[Dict[str, Any]] = None,
    calculation_breakdown: Optional[Dict[str, Any]] = None,
    advisor_percent: Optional[float] = None
) -> Dict[str, Any]:
    """
    Create an equity scenario from a selected option.
    
    Args:
        clerk_user_id: Clerk user ID
        workspace_id: Workspace ID
        scenario_type: 'recommended', 'equal', or 'custom'
        founder_a_percent: Equity percentage for founder A
        founder_b_percent: Equity percentage for founder B
        vesting_terms: Optional vesting configuration
        calculation_breakdown: Optional calculation breakdown (for recommended)
        advisor_percent: Optional advisor equity percentage
    
    Returns:
        The created scenario record
    """
    founder_id = _can_edit_workspace(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Validate percentages (founder equity + advisor equity should equal 100)
    advisor_pct = advisor_percent or 0.0
    total = founder_a_percent + founder_b_percent + advisor_pct
    if abs(total - 100) > 0.01:
        raise ValueError(f"Equity percentages must sum to 100% (got {total}%)")
    
    if scenario_type not in ('recommended', 'equal', 'custom'):
        raise ValueError("Invalid scenario type. Must be 'recommended', 'equal', or 'custom'")
    
    # Get founder IDs
    founder_a_id, founder_b_id = _get_workspace_founders(workspace_id)
    if not founder_a_id or not founder_b_id:
        raise ValueError("Could not determine workspace founders")
    
    # Default vesting terms
    if vesting_terms is None:
        vesting_terms = {
            'has_vesting': True,
            'years': 4,
            'cliff_months': 12,
            'acceleration': 'none',
            'jurisdiction': 'other'
        }
    
    scenario_data = {
        'workspace_id': workspace_id,
        'scenario_type': scenario_type,
        'founder_a_id': founder_a_id,
        'founder_b_id': founder_b_id,
        'founder_a_percent': founder_a_percent,
        'founder_b_percent': founder_b_percent,
        'calculation_breakdown': calculation_breakdown,
        'vesting_terms': vesting_terms,
        'status': 'pending_approval',
        'is_current': False,
    }
    
    # Add advisor equity if present
    if advisor_pct > 0:
        scenario_data['advisor_percent'] = advisor_pct
    
    result = supabase.table('equity_scenarios').insert(scenario_data).execute()
    
    if not result.data:
        raise ValueError("Failed to create equity scenario")
    
    _log_audit(
        workspace_id, founder_id,
        'create_equity_scenario',
        'equity_scenario',
        result.data[0]['id'],
        {'scenario_type': scenario_type}
    )
    
    return result.data[0]


def get_equity_scenarios(
    clerk_user_id: str,
    workspace_id: str
) -> Dict[str, Any]:
    """
    Get all equity scenarios for a workspace.
    
    Args:
        clerk_user_id: Clerk user ID
        workspace_id: Workspace ID
    
    Returns:
        Dict with scenarios list and current scenario
    """
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    scenarios = supabase.table('equity_scenarios').select(
        '*, founder_a:founders!founder_a_id(id, name), founder_b:founders!founder_b_id(id, name)'
    ).eq('workspace_id', workspace_id).order('created_at', desc=True).execute()
    
    current = None
    all_scenarios = []
    
    for s in (scenarios.data or []):
        scenario = {
            'id': s['id'],
            'workspace_id': s['workspace_id'],
            'scenario_type': s['scenario_type'],
            'founder_a': s.get('founder_a', {}),
            'founder_b': s.get('founder_b', {}),
            'founder_a_percent': float(s['founder_a_percent']),
            'founder_b_percent': float(s['founder_b_percent']),
            'advisor_percent': float(s.get('advisor_percent', 0)),
            'calculation_breakdown': s.get('calculation_breakdown'),
            'vesting_terms': s.get('vesting_terms'),
            'status': s['status'],
            'is_current': s['is_current'],
            'approved_by_founder_a_at': s.get('approved_by_founder_a_at'),
            'approved_by_founder_b_at': s.get('approved_by_founder_b_at'),
            'note': s.get('note'),
            'created_at': s['created_at'],
            'updated_at': s['updated_at'],
        }
        
        if s['is_current']:
            current = scenario
        
        all_scenarios.append(scenario)
    
    return {
        'scenarios': all_scenarios,
        'current': current,
    }


def approve_scenario(
    clerk_user_id: str,
    workspace_id: str,
    scenario_id: str
) -> Dict[str, Any]:
    """
    Record approval for a scenario by the current user.
    When both founders approve, scenario becomes 'approved'.
    
    Args:
        clerk_user_id: Clerk user ID
        workspace_id: Workspace ID
        scenario_id: Scenario ID to approve
    
    Returns:
        The updated scenario record
    """
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Get scenario
    scenario = supabase.table('equity_scenarios').select('*').eq('id', scenario_id).execute()
    if not scenario.data:
        raise ValueError("Scenario not found")
    
    scenario_data = scenario.data[0]
    
    if scenario_data['workspace_id'] != workspace_id:
        raise ValueError("Scenario does not belong to this workspace")
    
    if scenario_data['status'] == 'approved':
        raise ValueError("Scenario is already approved")
    
    # Determine which founder is approving
    update_data = {}
    now = datetime.now(timezone.utc).isoformat()
    
    if founder_id == scenario_data['founder_a_id']:
        update_data['approved_by_founder_a_at'] = now
    elif founder_id == scenario_data['founder_b_id']:
        update_data['approved_by_founder_b_at'] = now
    else:
        raise ValueError("Only workspace founders can approve scenarios")
    
    # Update scenario
    result = supabase.table('equity_scenarios').update(update_data).eq('id', scenario_id).execute()
    
    if not result.data:
        raise ValueError("Failed to record approval")
    
    # Check if both founders have now approved
    updated = result.data[0]
    both_approved = (
        (updated.get('approved_by_founder_a_at') or scenario_data.get('approved_by_founder_a_at')) and
        (updated.get('approved_by_founder_b_at') or scenario_data.get('approved_by_founder_b_at'))
    )
    
    if both_approved:
        # Mark as approved and set as current
        # First, unset any existing current scenario
        supabase.table('equity_scenarios').update({
            'is_current': False
        }).eq('workspace_id', workspace_id).eq('is_current', True).execute()
        
        # Then mark this one as approved and current
        final_result = supabase.table('equity_scenarios').update({
            'status': 'approved',
            'is_current': True
        }).eq('id', scenario_id).execute()
        
        if final_result.data:
            updated = final_result.data[0]
    
    _log_audit(
        workspace_id, founder_id,
        'approve_equity_scenario',
        'equity_scenario',
        scenario_id,
        {'both_approved': both_approved}
    )
    
    return updated


def reject_scenario(
    clerk_user_id: str,
    workspace_id: str,
    scenario_id: str,
    reason: Optional[str] = None
) -> Dict[str, Any]:
    """
    Reject a scenario.
    
    Args:
        clerk_user_id: Clerk user ID
        workspace_id: Workspace ID
        scenario_id: Scenario ID to reject
        reason: Optional rejection reason
    
    Returns:
        The updated scenario record
    """
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Get scenario
    scenario = supabase.table('equity_scenarios').select('*').eq('id', scenario_id).execute()
    if not scenario.data:
        raise ValueError("Scenario not found")
    
    scenario_data = scenario.data[0]
    
    if scenario_data['workspace_id'] != workspace_id:
        raise ValueError("Scenario does not belong to this workspace")
    
    # Verify user is a founder
    if founder_id not in (scenario_data['founder_a_id'], scenario_data['founder_b_id']):
        raise ValueError("Only workspace founders can reject scenarios")
    
    # Update scenario
    update_data = {
        'status': 'rejected',
        'note': reason or scenario_data.get('note'),
    }
    
    result = supabase.table('equity_scenarios').update(update_data).eq('id', scenario_id).execute()
    
    if not result.data:
        raise ValueError("Failed to reject scenario")
    
    _log_audit(
        workspace_id, founder_id,
        'reject_equity_scenario',
        'equity_scenario',
        scenario_id,
        {'reason': reason}
    )
    
    return result.data[0]


def update_vesting_terms(
    clerk_user_id: str,
    workspace_id: str,
    vesting_terms: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Update vesting terms for the startup context.
    Stored in the questionnaire responses for now.
    
    Args:
        clerk_user_id: Clerk user ID
        workspace_id: Workspace ID
        vesting_terms: Vesting configuration
    
    Returns:
        Updated response record
    """
    founder_id = _can_edit_workspace(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Get current response
    existing = supabase.table('equity_questionnaire_responses').select('*').eq(
        'workspace_id', workspace_id
    ).eq('founder_id', founder_id).execute()
    
    if not existing.data:
        raise ValueError("No questionnaire response found. Please complete the questionnaire first.")
    
    response = existing.data[0]
    responses = response.get('responses', {})
    responses['vesting_terms'] = vesting_terms
    
    result = supabase.table('equity_questionnaire_responses').update({
        'responses': responses
    }).eq('id', response['id']).execute()
    
    if not result.data:
        raise ValueError("Failed to update vesting terms")
    
    return result.data[0]


def get_startup_context(
    clerk_user_id: str,
    workspace_id: str
) -> Dict[str, Any]:
    """
    Get the startup context (shared data from Step 1).
    
    Args:
        clerk_user_id: Clerk user ID
        workspace_id: Workspace ID
    
    Returns:
        Startup context dict
    """
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Get founder A's response which contains startup context
    founder_a_id, _ = _get_workspace_founders(workspace_id)
    if not founder_a_id:
        return {}
    
    response = supabase.table('equity_questionnaire_responses').select(
        'responses'
    ).eq('workspace_id', workspace_id).eq('founder_id', founder_a_id).execute()
    
    if not response.data:
        return {}
    
    return response.data[0].get('responses', {}).get('startup_context', {})


def save_startup_context(
    clerk_user_id: str,
    workspace_id: str,
    startup_context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Save startup context (Stage, Idea Origin, IP).
    This is stored in founder A's responses but is shared context.
    Any founder can edit it, but it's always stored in founder A's record.
    
    Args:
        clerk_user_id: Clerk user ID
        workspace_id: Workspace ID
        startup_context: Startup context data
    
    Returns:
        Updated response record
    """
    # Verify user can edit this workspace - this returns the founder_id of the current user
    current_founder_id = _can_edit_workspace(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Try to get founder A, but fall back to current user if not enough participants yet
    founder_a_id, _ = _get_workspace_founders(workspace_id)
    if not founder_a_id:
        # If we can't determine founder order yet, use the current user's record
        founder_a_id = current_founder_id
        log_info(f"Using current founder {founder_a_id} as founder A for startup context")
    
    # Get or create response for founder A
    existing = supabase.table('equity_questionnaire_responses').select('*').eq(
        'workspace_id', workspace_id
    ).eq('founder_id', founder_a_id).execute()
    
    if existing.data:
        responses = existing.data[0].get('responses', {})
        responses['startup_context'] = startup_context
        
        result = supabase.table('equity_questionnaire_responses').update({
            'responses': responses
        }).eq('id', existing.data[0]['id']).execute()
    else:
        result = supabase.table('equity_questionnaire_responses').insert({
            'workspace_id': workspace_id,
            'founder_id': founder_a_id,
            'responses': {'startup_context': startup_context},
            'is_complete': False,
        }).execute()
    
    if not result.data:
        raise ValueError("Failed to save startup context")
    
    return result.data[0]
