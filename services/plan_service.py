"""Plan and billing service for founders and advisors"""
from config.database import get_supabase
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Any, Literal
from enum import Enum

FounderPlan = Literal["FREE", "PRO", "PRO_PLUS"]

FOUNDER_PLANS: Dict[FounderPlan, Dict[str, Any]] = {
    "FREE": {
        "id": "FREE",
        "monthlyPriceUSD": 0,
        "maxWorkspaces": 1,
        "discovery": {
            "maxMatchesPerMonth": 10,
            "showCompatDimensions": False,
        },
        "workspaceFeatures": {
            "equityFull": False,
            "kpiFull": True,
            "decisionsFull": True,
            "tasksBoard": False,
            "weeklyCheckins": True,
            "notifications": True,
        },
        "accountability": {
            "canUseMarketplace": False,
            "priorityAdvisors": False,
        },
        "investorFeatures": {
            "advancedCompatAnalytics": False,
            "investorProfile": False,
        },
    },
    "PRO": {
        "id": "PRO",
        "monthlyPriceUSD": 15,
        "maxWorkspaces": 2,
        "discovery": {
            "maxMatchesPerMonth": "UNLIMITED",
            "showCompatDimensions": True,
        },
        "workspaceFeatures": {
            "equityFull": True,
            "kpiFull": True,
            "decisionsFull": True,
            "tasksBoard": True,
            "weeklyCheckins": True,
            "notifications": True,
        },
        "accountability": {
            "canUseMarketplace": True,
            "priorityPartners": False,
        },
        "investorFeatures": {
            "advancedCompatAnalytics": False,
            "investorProfile": False,
        },
    },
    "PRO_PLUS": {
        "id": "PRO_PLUS",
        "monthlyPriceUSD": 35,
        "maxWorkspaces": 5,
        "discovery": {
            "maxMatchesPerMonth": "UNLIMITED",
            "showCompatDimensions": True,
        },
        "workspaceFeatures": {
            "equityFull": True,
            "kpiFull": True,
            "decisionsFull": True,
            "tasksBoard": True,
            "weeklyCheckins": True,
            "notifications": True,
        },
        "accountability": {
            "canUseMarketplace": True,
            "priorityAdvisors": True,
        },
        "investorFeatures": {
            "advancedCompatAnalytics": True,
            "investorProfile": True,
        },
    },
}

ADVISOR_PRICING = {
    "onboardingFeeUSD": 69,
    "annualRenewalUSD": 39,
    "minMonthlyRateUSD": 50,
    "maxMonthlyRateUSD": 150,
    "platformFeePercent": 25,
}

def _get_founder_id(clerk_user_id: str, email: str = None) -> str:
    """Helper to get founder ID from clerk_user_id - auto-creates minimal record if missing.
    If email is provided and a founder exists with that email but different clerk_user_id,
    updates the clerk_user_id to link the accounts.
    """
    supabase = get_supabase()
    user_profile = supabase.table('founders').select('id, email').eq('clerk_user_id', clerk_user_id).execute()
    
    if not user_profile.data:
        # Check by email if provided (case-insensitive)
        if email and email.strip():
            email_lower = email.strip().lower()
            all_founders = supabase.table('founders').select('id, email, clerk_user_id').execute()
            if all_founders.data:
                for founder in all_founders.data:
                    founder_email = founder.get('email', '').strip().lower()
                    if founder_email == email_lower:
                        # Found existing founder with same email - update clerk_user_id
                        supabase.table('founders').update({'clerk_user_id': clerk_user_id}).eq('id', founder['id']).execute()
                        return founder['id']
        
        # Auto-create minimal founder record for authenticated users
        # This prevents 400 errors when user is signed in but hasn't completed onboarding
        # The record will be updated with full details during onboarding
        founder_data = {
            'clerk_user_id': clerk_user_id,
            'name': '',  # Will be updated during onboarding
            'email': email or '',  # Will be updated during onboarding
            'purpose': None,  # Will be set during onboarding
            'location': '',
            'looking_for': '',
            'skills': [],
            'onboarding_completed': False,  # Still requires onboarding
            'plan': 'FREE',
        }
        
        result = supabase.table('founders').insert(founder_data).execute()
        if not result.data:
            raise ValueError("Failed to auto-create founder record")
        
        return result.data[0]['id']
    
    return user_profile.data[0]['id']

def get_founder_plan(clerk_user_id: str) -> Dict[str, Any]:
    """Get founder's current plan"""
    try:
        founder_id = _get_founder_id(clerk_user_id)
    except ValueError:
        # User hasn't completed onboarding - return FREE plan as default
        return FOUNDER_PLANS['FREE'].copy()
    
    supabase = get_supabase()
    
    founder = supabase.table('founders').select('plan, subscription_status, subscription_current_period_end').eq('id', founder_id).execute()
    if not founder.data:
        # Founder record exists but query failed - return FREE plan
        return FOUNDER_PLANS['FREE'].copy()
    
    plan_id = founder.data[0].get('plan', 'FREE')
    plan_config = FOUNDER_PLANS.get(plan_id, FOUNDER_PLANS['FREE']).copy()
    
    # Add subscription info
    plan_config['subscription_status'] = founder.data[0].get('subscription_status')
    plan_config['subscription_current_period_end'] = founder.data[0].get('subscription_current_period_end')
    
    return plan_config

def check_feature_access(clerk_user_id: str, feature_path: str) -> bool:
    """
    Check if user has access to a feature.
    feature_path format: "workspaceFeatures.equityFull" or "accountability.canUseMarketplace"
    """
    plan_config = get_founder_plan(clerk_user_id)
    
    parts = feature_path.split('.')
    value = plan_config
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return False
    
    return bool(value) if value is not None else False

def check_workspace_limit(clerk_user_id: str) -> tuple[bool, int, int]:
    """
    Check if user can create more workspaces.
    Returns: (can_create, current_count, max_allowed)
    """
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    plan_config = get_founder_plan(clerk_user_id)
    max_workspaces = plan_config.get('maxWorkspaces', 1)
    
    # Count active workspaces
    workspaces = supabase.table('workspace_participants').select('workspace_id').eq('user_id', founder_id).execute()
    unique_workspaces = set()
    if workspaces.data:
        for wp in workspaces.data:
            unique_workspaces.add(wp['workspace_id'])
    
    current_count = len(unique_workspaces)
    can_create = current_count < max_workspaces
    
    return (can_create, current_count, max_workspaces)

def check_discovery_limit(clerk_user_id: str) -> tuple[bool, int, int]:
    """
    Check if user can perform more discovery swipes using 30-day rolling window.
    Returns: (can_swipe, current_count, max_allowed)
    """
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    plan_config = get_founder_plan(clerk_user_id)
    max_matches = plan_config.get('discovery', {}).get('maxMatchesPerMonth', 10)
    
    if max_matches == "UNLIMITED":
        return (True, 0, -1)  # -1 means unlimited
    
    # Use 30-day rolling window instead of calendar month
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)
    
    # Count right swipes in the last 30 days from swipe_history table
    swipe_count_result = supabase.table('swipe_history').select('id', count='exact').eq('user_id', founder_id).eq('swipe_type', 'right').gte('swipe_date', thirty_days_ago.isoformat()).execute()
    
    # Fallback to discovery_usage if swipe_history doesn't have data yet
    if swipe_count_result.count is None or swipe_count_result.count == 0:
        # Try legacy discovery_usage table
        month_year = now.strftime('%Y-%m')
        usage = supabase.table('discovery_usage').select('swipe_count').eq('user_id', founder_id).eq('month_year', month_year).execute()
        current_count = usage.data[0].get('swipe_count', 0) if usage.data else 0
    else:
        current_count = swipe_count_result.count
    
    can_swipe = current_count < max_matches
    
    return (can_swipe, current_count, max_matches)

def increment_discovery_usage(clerk_user_id: str) -> None:
    """Increment discovery swipe count - now uses 30-day rolling window via swipe_history"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    now = datetime.now(timezone.utc)
    month_year = now.strftime('%Y-%m')
    
    # Also maintain legacy discovery_usage table for backward compatibility
    existing = supabase.table('discovery_usage').select('id, swipe_count').eq('user_id', founder_id).eq('month_year', month_year).execute()
    
    if existing.data:
        # Update existing
        new_count = existing.data[0].get('swipe_count', 0) + 1
        supabase.table('discovery_usage').update({'swipe_count': new_count}).eq('id', existing.data[0]['id']).execute()
    else:
        # Create new
        supabase.table('discovery_usage').insert({
            'user_id': founder_id,
            'month_year': month_year,
            'swipe_count': 1,
        }).execute()

def record_swipe_in_history(clerk_user_id: str, swipe_type: str, project_id: str = None) -> None:
    """Record swipe in swipe_history table for 30-day rolling window tracking"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    try:
        supabase.table('swipe_history').insert({
            'user_id': founder_id,
            'swipe_type': swipe_type,
            'project_id': project_id,
            'swipe_date': datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        # Log but don't fail - swipe_history is for tracking, not critical
        from utils.logger import log_warning
        log_warning(f"Failed to record swipe in history: {e}")

def update_founder_plan(clerk_user_id: str, new_plan: FounderPlan, subscription_id: Optional[str] = None, subscription_status: Optional[str] = None, current_period_end: Optional[datetime] = None, workspace_to_keep: Optional[str] = None) -> Dict[str, Any]:
    """Update founder's plan and handle workspace limits on downgrade with user consent"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Get current plan
    founder = supabase.table('founders').select('plan').eq('id', founder_id).execute()
    if not founder.data:
        raise ValueError("Founder not found")
    
    old_plan = founder.data[0].get('plan', 'FREE')
    
    # Check if this is a downgrade that would exceed workspace limits
    is_downgrade = not _is_upgrade(old_plan, new_plan) and old_plan != new_plan
    if is_downgrade:
        # This is a downgrade - check workspace limits
        new_plan_config = FOUNDER_PLANS.get(new_plan, FOUNDER_PLANS['FREE'])
        max_workspaces = new_plan_config.get('maxWorkspaces', 1)
        
        # Get user's current workspaces with more details
        workspaces_query = supabase.table('workspace_participants').select(
            'workspace_id, workspaces!inner(id, created_at, status)'
        ).eq('user_id', founder_id).execute()
        
        unique_workspaces = {}
        workspace_details = {}
        if workspaces_query.data:
            for wp in workspaces_query.data:
                workspace_id = wp['workspace_id']
                workspace_info = wp.get('workspaces', {})
                if workspace_id not in unique_workspaces:
                    unique_workspaces[workspace_id] = workspace_info.get('created_at')
                    workspace_details[workspace_id] = {
                        'created_at': workspace_info.get('created_at'),
                        'status': workspace_info.get('status', 'ACTIVE')
                    }
        
        current_count = len(unique_workspaces)
        
        # If user has more workspaces than new plan allows, require explicit selection
        if current_count > max_workspaces:
            if not workspace_to_keep:
                # User must explicitly select which workspaces to keep
                # Return error with workspace list so frontend can prompt user
                workspace_list = [
                    {
                        'id': wid,
                        'created_at': details.get('created_at'),
                        'status': details.get('status')
                    }
                    for wid, details in workspace_details.items()
                ]
                raise ValueError(
                    f"Plan downgrade requires selecting which {max_workspaces} workspace(s) to keep. "
                    f"You currently have {current_count} workspaces. Please specify workspace_to_keep parameter."
                )
            
            # Validate workspace_to_keep
            if workspace_to_keep not in unique_workspaces:
                raise ValueError(f"Workspace {workspace_to_keep} not found or you don't have access")
            
            # Remove user from all other workspaces (with history tracking)
            workspaces_to_remove = [wid for wid in unique_workspaces.keys() if wid != workspace_to_keep]
            
            for workspace_id in workspaces_to_remove:
                # Record in history before removal
                try:
                    supabase.table('workspace_participant_history').insert({
                        'workspace_id': workspace_id,
                        'user_id': founder_id,
                        'action': 'REMOVED',
                        'reason': f'Plan downgrade from {old_plan} to {new_plan}',
                        'removed_by': founder_id
                    }).execute()
                except Exception:
                    pass  # History tracking is optional
                
                # Remove from workspace
                supabase.table('workspace_participants').delete().eq('workspace_id', workspace_id).eq('user_id', founder_id).execute()
    
    # Update plan
    update_data = {'plan': new_plan}
    if subscription_id:
        update_data['subscription_id'] = subscription_id
    if subscription_status:
        update_data['subscription_status'] = subscription_status
    if current_period_end:
        update_data['subscription_current_period_end'] = current_period_end.isoformat()
    
    supabase.table('founders').update(update_data).eq('id', founder_id).execute()
    
    # Log telemetry
    event_type = 'UPGRADE' if _is_upgrade(old_plan, new_plan) else 'DOWNGRADE' if old_plan != new_plan else 'ACTIVATION'
    log_plan_telemetry(founder_id, event_type, old_plan, new_plan)
    
    return get_founder_plan(clerk_user_id)

def _is_upgrade(old_plan: str, new_plan: str) -> bool:
    """Check if plan change is an upgrade"""
    plan_order = {'FREE': 0, 'PRO': 1, 'PRO_PLUS': 2}
    return plan_order.get(new_plan, 0) > plan_order.get(old_plan, 0)

def log_plan_telemetry(user_id: str, event_type: str, from_plan: Optional[str] = None, to_plan: Optional[str] = None, metadata: Optional[Dict] = None) -> None:
    """Log plan-related events for analytics"""
    supabase = get_supabase()
    supabase.table('plan_telemetry').insert({
        'user_id': user_id,
        'event_type': event_type,
        'from_plan': from_plan,
        'to_plan': to_plan,
        'metadata': metadata or {},
    }).execute()

def get_advisor_billing_profile(clerk_user_id: str) -> Dict[str, Any]:
    """Get advisor billing profile"""
    try:
        founder_id = _get_founder_id(clerk_user_id)
    except ValueError:
        # User doesn't have founder record - return default empty profile
        return {
            'onboarding_paid': False,
            'renewal_paid_until': None,
            'monthly_rate_usd': None,
            'is_discoverable': False,
        }
    
    supabase = get_supabase()
    
    # Use monthly_rate_inr column to store USD values (column name is legacy)
    profile = supabase.table('advisor_profiles').select(
        'onboarding_paid, onboarding_paid_at, renewal_paid_until, monthly_rate_inr'
    ).eq('user_id', founder_id).execute()
    
    if not profile.data:
        return {
            'onboarding_paid': False,
            'renewal_paid_until': None,
            'monthly_rate_usd': None,
            'is_discoverable': False,
        }
    
    data = profile.data[0]
    renewal_until = data.get('renewal_paid_until')
    is_paid = data.get('onboarding_paid', False)
    # The monthly_rate_inr column stores USD values (legacy column name)
    monthly_rate_usd = data.get('monthly_rate_inr')
    
    # Check if renewal is still valid
    renewal_valid = False
    if renewal_until:
        renewal_dt = datetime.fromisoformat(renewal_until.replace('Z', '+00:00')) if isinstance(renewal_until, str) else renewal_until
        if renewal_dt.tzinfo is None:
            renewal_dt = renewal_dt.replace(tzinfo=timezone.utc)
        renewal_valid = renewal_dt >= datetime.now(timezone.utc)
    
    is_discoverable = is_paid and renewal_valid
    
    return {
        'onboarding_paid': is_paid,
        'onboarding_paid_at': data.get('onboarding_paid_at'),
        'renewal_paid_until': renewal_until,
        'monthly_rate_usd': monthly_rate_usd,
        'is_discoverable': is_discoverable,
    }

def update_advisor_billing(clerk_user_id: str, onboarding_paid: Optional[bool] = None, monthly_rate_usd: Optional[int] = None, monthly_rate_inr: Optional[int] = None) -> Dict[str, Any]:
    """Update advisor billing information"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Handle monthly rate - use USD throughout (stored in monthly_rate_inr column due to legacy naming)
    monthly_rate_value = None
    if monthly_rate_usd is not None:
        # Validate USD range
        if monthly_rate_usd < ADVISOR_PRICING['minMonthlyRateUSD'] or monthly_rate_usd > ADVISOR_PRICING['maxMonthlyRateUSD']:
            raise ValueError(f"Monthly rate must be between ${ADVISOR_PRICING['minMonthlyRateUSD']} and ${ADVISOR_PRICING['maxMonthlyRateUSD']} USD")
        monthly_rate_value = monthly_rate_usd
    elif monthly_rate_inr is not None:
        # Support legacy INR parameter (treating it as USD)
        monthly_rate_value = monthly_rate_inr
    
    update_data = {}
    if onboarding_paid is not None:
        update_data['onboarding_paid'] = onboarding_paid
        if onboarding_paid:
            update_data['onboarding_paid_at'] = datetime.now(timezone.utc).isoformat()
    
    # Store USD value in monthly_rate_inr column (legacy column name)
    if monthly_rate_value is not None:
        update_data['monthly_rate_inr'] = monthly_rate_value
    
    # Update profile
    existing = supabase.table('advisor_profiles').select('id').eq('user_id', founder_id).execute()
    if not existing.data:
        raise ValueError("Advisor profile not found")
    
    supabase.table('advisor_profiles').update(update_data).eq('id', existing.data[0]['id']).execute()
    
    return get_advisor_billing_profile(clerk_user_id)

def renew_advisor_subscription(clerk_user_id: str) -> Dict[str, Any]:
    """Renew advisor annual subscription"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Calculate renewal end date (1 year from now)
    renewal_end = datetime.now(timezone.utc).replace(year=datetime.now(timezone.utc).year + 1)
    
    existing = supabase.table('advisor_profiles').select('id').eq('user_id', founder_id).execute()
    if not existing.data:
        raise ValueError("Advisor profile not found")
    
    supabase.table('advisor_profiles').update({
        'renewal_paid_until': renewal_end.isoformat()
    }).eq('id', existing.data[0]['id']).execute()
    
    # Log telemetry
    log_plan_telemetry(founder_id, 'RENEWAL', metadata={'type': 'advisor_renewal'})
    
    return get_advisor_billing_profile(clerk_user_id)

def calculate_advisor_pricing(monthly_rate_usd: int) -> Dict[str, float]:
    """Calculate advisor and platform share from monthly rate"""
    platform_fee_percent = ADVISOR_PRICING['platformFeePercent']
    platform_share = monthly_rate_usd * platform_fee_percent / 100
    advisor_share = monthly_rate_usd - platform_share
    
    return {
        'display_price_usd': round(monthly_rate_usd * 1.25, 2),  # Founder sees this
        'advisor_share_usd': round(advisor_share, 2),
        'platform_share_usd': round(platform_share, 2),
        'platform_fee_percent': platform_fee_percent,
    }

