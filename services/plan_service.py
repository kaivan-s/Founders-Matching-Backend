"""Plan and billing service for founders and accountability partners"""
from config.database import get_supabase
from datetime import datetime, timezone
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
            "priorityPartners": False,
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
            "priorityPartners": True,
        },
        "investorFeatures": {
            "advancedCompatAnalytics": True,
            "investorProfile": True,
        },
    },
}

PARTNER_PRICING = {
    "onboardingFeeUSD": 69,
    "annualRenewalUSD": 39,
    "minMonthlyRateUSD": 50,
    "maxMonthlyRateUSD": 150,
    "platformFeePercent": 25,
}

def _get_founder_id(clerk_user_id: str) -> str:
    """Helper to get founder ID from clerk_user_id"""
    supabase = get_supabase()
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Founder not found")
    return user_profile.data[0]['id']

def get_founder_plan(clerk_user_id: str) -> Dict[str, Any]:
    """Get founder's current plan"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    founder = supabase.table('founders').select('plan, subscription_status, subscription_current_period_end').eq('id', founder_id).execute()
    if not founder.data:
        raise ValueError("Founder not found")
    
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
    Check if user can perform more discovery swipes this month.
    Returns: (can_swipe, current_count, max_allowed)
    """
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    plan_config = get_founder_plan(clerk_user_id)
    max_matches = plan_config.get('discovery', {}).get('maxMatchesPerMonth', 10)
    
    if max_matches == "UNLIMITED":
        return (True, 0, -1)  # -1 means unlimited
    
    # Get current month usage
    now = datetime.now(timezone.utc)
    month_year = now.strftime('%Y-%m')
    
    usage = supabase.table('discovery_usage').select('swipe_count').eq('user_id', founder_id).eq('month_year', month_year).execute()
    
    current_count = usage.data[0].get('swipe_count', 0) if usage.data else 0
    can_swipe = current_count < max_matches
    
    return (can_swipe, current_count, max_matches)

def increment_discovery_usage(clerk_user_id: str) -> None:
    """Increment discovery swipe count for current month"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    now = datetime.now(timezone.utc)
    month_year = now.strftime('%Y-%m')
    
    # Get or create usage record
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

def update_founder_plan(clerk_user_id: str, new_plan: FounderPlan, subscription_id: Optional[str] = None, subscription_status: Optional[str] = None, current_period_end: Optional[datetime] = None, workspace_to_keep: Optional[str] = None) -> Dict[str, Any]:
    """Update founder's plan and handle workspace limits on downgrade"""
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
        
        # Get user's current workspaces
        workspaces = supabase.table('workspace_participants').select('workspace_id, created_at').eq('user_id', founder_id).execute()
        unique_workspaces = {}
        if workspaces.data:
            for wp in workspaces.data:
                workspace_id = wp['workspace_id']
                if workspace_id not in unique_workspaces:
                    unique_workspaces[workspace_id] = wp.get('created_at')
        
        current_count = len(unique_workspaces)
        
        # If user has more workspaces than new plan allows, handle it
        if current_count > max_workspaces:
            if workspace_to_keep:
                # User specified which workspace to keep
                if workspace_to_keep not in unique_workspaces:
                    raise ValueError(f"Workspace {workspace_to_keep} not found or you don't have access")
                # Remove user from all other workspaces
                workspaces_to_remove = [wid for wid in unique_workspaces.keys() if wid != workspace_to_keep]
            else:
                # Auto-select: keep the most recent workspace
                # Sort by created_at (most recent first)
                sorted_workspaces = sorted(unique_workspaces.items(), key=lambda x: x[1] or '', reverse=True)
                workspace_to_keep = sorted_workspaces[0][0]
                workspaces_to_remove = [wid for wid, _ in sorted_workspaces[1:]]
            
            # Remove user from excess workspaces
            for workspace_id in workspaces_to_remove:
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

def get_partner_billing_profile(clerk_user_id: str) -> Dict[str, Any]:
    """Get accountability partner billing profile"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Use monthly_rate_inr column to store USD values (column name is legacy)
    profile = supabase.table('accountability_partner_profiles').select(
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

def update_partner_billing(clerk_user_id: str, onboarding_paid: Optional[bool] = None, monthly_rate_usd: Optional[int] = None, monthly_rate_inr: Optional[int] = None) -> Dict[str, Any]:
    """Update partner billing information"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Handle monthly rate - use USD throughout (stored in monthly_rate_inr column due to legacy naming)
    monthly_rate_value = None
    if monthly_rate_usd is not None:
        # Validate USD range
        if monthly_rate_usd < PARTNER_PRICING['minMonthlyRateUSD'] or monthly_rate_usd > PARTNER_PRICING['maxMonthlyRateUSD']:
            raise ValueError(f"Monthly rate must be between ${PARTNER_PRICING['minMonthlyRateUSD']} and ${PARTNER_PRICING['maxMonthlyRateUSD']} USD")
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
    existing = supabase.table('accountability_partner_profiles').select('id').eq('user_id', founder_id).execute()
    if not existing.data:
        raise ValueError("Partner profile not found")
    
    supabase.table('accountability_partner_profiles').update(update_data).eq('id', existing.data[0]['id']).execute()
    
    return get_partner_billing_profile(clerk_user_id)

def renew_partner_subscription(clerk_user_id: str) -> Dict[str, Any]:
    """Renew partner annual subscription"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Calculate renewal end date (1 year from now)
    renewal_end = datetime.now(timezone.utc).replace(year=datetime.now(timezone.utc).year + 1)
    
    existing = supabase.table('accountability_partner_profiles').select('id').eq('user_id', founder_id).execute()
    if not existing.data:
        raise ValueError("Partner profile not found")
    
    supabase.table('accountability_partner_profiles').update({
        'renewal_paid_until': renewal_end.isoformat()
    }).eq('id', existing.data[0]['id']).execute()
    
    # Log telemetry
    log_plan_telemetry(founder_id, 'RENEWAL', metadata={'type': 'partner_renewal'})
    
    return get_partner_billing_profile(clerk_user_id)

def calculate_partner_pricing(monthly_rate_usd: int) -> Dict[str, float]:
    """Calculate partner and platform share from monthly rate"""
    platform_fee_percent = PARTNER_PRICING['platformFeePercent']
    platform_share = monthly_rate_usd * platform_fee_percent / 100
    partner_share = monthly_rate_usd - platform_share
    
    return {
        'display_price_usd': round(monthly_rate_usd * 1.25, 2),  # Founder sees this
        'partner_share_usd': round(partner_share, 2),
        'platform_share_usd': round(platform_share, 2),
        'platform_fee_percent': platform_fee_percent,
    }

