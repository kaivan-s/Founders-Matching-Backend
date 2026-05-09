"""
Credit Service - Handles user credits for platform services.

Credits are used for:
1. Anti-spam individual actions: create project (5), apply to project (2)
2. Workspace-level premium services: advisor sessions, equity calculator, agreements

Workspace services split costs equally among all workspace members.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from config.database import get_supabase
from utils.logger import log_info, log_error, log_warning

# Signup bonus credits for new users
SIGNUP_BONUS_CREDITS = 20

# Service costs (also stored in DB for admin flexibility)
SERVICE_COSTS = {
    # Individual services (anti-spam)
    'create_project': {'credits': 5, 'workspace_level': False},
    'apply_to_project': {'credits': 2, 'workspace_level': False},
    
    # Workspace services (split among members)
    'advisor_session_30': {'credits': 40, 'workspace_level': True},
    'advisor_session_60': {'credits': 70, 'workspace_level': True},
    'equity_calculator': {'credits': 30, 'workspace_level': True},
    'equity_agreement': {'credits': 50, 'workspace_level': True},
    'post_match_support_30': {'credits': 100, 'workspace_level': True},
}

# Credit pack definitions (also stored in DB)
CREDIT_PACKS = {
    'starter': {'credits': 50, 'price_cents': 500, 'name': 'Starter'},
    'growth': {'credits': 150, 'price_cents': 1200, 'name': 'Growth'},
    'pro': {'credits': 400, 'price_cents': 2900, 'name': 'Pro'},
}


def _get_founder_id(clerk_user_id: str) -> str:
    """Get founder ID from clerk_user_id."""
    supabase = get_supabase()
    result = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not result.data:
        raise ValueError("User profile not found")
    return result.data[0]['id']


def _ensure_credit_record(supabase, user_id: str) -> Dict[str, Any]:
    """Ensure user has a credit record, create with signup bonus if not."""
    result = supabase.table('user_credits').select('*').eq('user_id', user_id).execute()
    
    if result.data:
        return result.data[0]
    
    # Create new record with signup bonus
    new_record = supabase.table('user_credits').insert({
        'user_id': user_id,
        'balance': SIGNUP_BONUS_CREDITS,
        'lifetime_earned': SIGNUP_BONUS_CREDITS,
        'lifetime_spent': 0,
    }).execute()
    
    if not new_record.data:
        raise ValueError("Failed to create credit record")
    
    # Log the signup bonus transaction
    supabase.table('credit_transactions').insert({
        'user_id': user_id,
        'amount': SIGNUP_BONUS_CREDITS,
        'balance_after': SIGNUP_BONUS_CREDITS,
        'transaction_type': 'signup_bonus',
        'description': 'Welcome bonus credits',
    }).execute()
    
    log_info(f"Created credit record with signup bonus for user {user_id}")
    return new_record.data[0]


def get_user_credits(clerk_user_id: str) -> Dict[str, Any]:
    """
    Get user's current credit balance and summary.
    
    Returns:
        {
            'balance': int,
            'lifetime_earned': int,
            'lifetime_spent': int,
        }
    """
    user_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    record = _ensure_credit_record(supabase, user_id)
    
    return {
        'balance': record['balance'],
        'lifetime_earned': record['lifetime_earned'],
        'lifetime_spent': record['lifetime_spent'],
    }


def get_credit_transactions(
    clerk_user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Get user's credit transaction history."""
    user_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    result = supabase.table('credit_transactions').select(
        '*'
    ).eq('user_id', user_id).order(
        'created_at', desc=True
    ).range(offset, offset + limit - 1).execute()
    
    return result.data or []


def check_credits(clerk_user_id: str, amount: int) -> Tuple[bool, int]:
    """
    Check if user has enough credits.
    
    Returns: (has_enough, current_balance)
    """
    user_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    record = _ensure_credit_record(supabase, user_id)
    balance = record['balance']
    
    return (balance >= amount, balance)


def add_credits(
    clerk_user_id: str,
    amount: int,
    transaction_type: str,
    description: Optional[str] = None,
    metadata: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Add credits to user's balance.
    
    Args:
        clerk_user_id: User's Clerk ID
        amount: Number of credits to add (must be positive)
        transaction_type: 'purchase', 'referral', 'refund', 'admin_adjustment'
        description: Human-readable description
        metadata: Additional data (e.g., payment_id)
    
    Returns:
        {'balance': new_balance, 'added': amount}
    """
    if amount <= 0:
        raise ValueError("Amount must be positive")
    
    user_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    record = _ensure_credit_record(supabase, user_id)
    new_balance = record['balance'] + amount
    new_lifetime_earned = record['lifetime_earned'] + amount
    
    # Update balance
    supabase.table('user_credits').update({
        'balance': new_balance,
        'lifetime_earned': new_lifetime_earned,
    }).eq('user_id', user_id).execute()
    
    # Log transaction
    supabase.table('credit_transactions').insert({
        'user_id': user_id,
        'amount': amount,
        'balance_after': new_balance,
        'transaction_type': transaction_type,
        'description': description or f'Added {amount} credits',
        'metadata': metadata or {},
    }).execute()
    
    log_info(f"Added {amount} credits to user {user_id}, new balance: {new_balance}")
    
    return {'balance': new_balance, 'added': amount}


def deduct_credits(
    clerk_user_id: str,
    amount: int,
    service_name: str,
    description: Optional[str] = None,
    related_entity_id: Optional[str] = None,
    related_entity_type: Optional[str] = None,
    metadata: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Deduct credits from user's balance for an individual service.
    
    Args:
        clerk_user_id: User's Clerk ID
        amount: Number of credits to deduct (must be positive)
        service_name: Service key (e.g., 'create_project', 'apply_to_project')
        description: Human-readable description
        related_entity_id: Related entity UUID
        related_entity_type: Type of related entity
        metadata: Additional data
    
    Returns:
        {'balance': new_balance, 'deducted': amount}
    
    Raises:
        ValueError: If insufficient credits
    """
    if amount <= 0:
        raise ValueError("Amount must be positive")
    
    user_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    record = _ensure_credit_record(supabase, user_id)
    
    if record['balance'] < amount:
        raise ValueError(
            f"Insufficient credits. Required: {amount}, Available: {record['balance']}. "
            f"Please purchase more credits to continue."
        )
    
    new_balance = record['balance'] - amount
    new_lifetime_spent = record['lifetime_spent'] + amount
    
    # Update balance
    supabase.table('user_credits').update({
        'balance': new_balance,
        'lifetime_spent': new_lifetime_spent,
    }).eq('user_id', user_id).execute()
    
    # Log transaction
    supabase.table('credit_transactions').insert({
        'user_id': user_id,
        'amount': -amount,
        'balance_after': new_balance,
        'transaction_type': 'service_charge',
        'service_name': service_name,
        'related_entity_id': related_entity_id,
        'related_entity_type': related_entity_type,
        'description': description or f'Service: {service_name}',
        'metadata': metadata or {},
    }).execute()
    
    log_info(f"Deducted {amount} credits from user {user_id} for {service_name}, new balance: {new_balance}")
    
    return {'balance': new_balance, 'deducted': amount}


def check_service_credits(clerk_user_id: str, service_name: str) -> Tuple[bool, int, int]:
    """
    Check if user has enough credits for a service.
    
    Returns: (has_enough, current_balance, required_amount)
    """
    service = SERVICE_COSTS.get(service_name)
    if not service:
        raise ValueError(f"Unknown service: {service_name}")
    
    required = service['credits']
    has_enough, balance = check_credits(clerk_user_id, required)
    
    return (has_enough, balance, required)


def deduct_service_credits(
    clerk_user_id: str,
    service_name: str,
    related_entity_id: Optional[str] = None,
    related_entity_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Deduct credits for a known service.
    
    Returns:
        {'balance': new_balance, 'deducted': amount, 'service': service_name}
    """
    service = SERVICE_COSTS.get(service_name)
    if not service:
        raise ValueError(f"Unknown service: {service_name}")
    
    if service['workspace_level']:
        raise ValueError(f"Service {service_name} is a workspace-level service. Use deduct_workspace_service_credits instead.")
    
    result = deduct_credits(
        clerk_user_id=clerk_user_id,
        amount=service['credits'],
        service_name=service_name,
        related_entity_id=related_entity_id,
        related_entity_type=related_entity_type,
    )
    
    result['service'] = service_name
    return result


def check_workspace_service_credits(workspace_id: str, service_name: str) -> Dict[str, Any]:
    """
    Check if all workspace members have enough credits for a workspace service.
    
    The cost is split equally among all active workspace participants.
    
    Returns:
        {
            'can_proceed': bool,
            'total_cost': int,
            'cost_per_member': int,
            'members': [
                {'user_id': str, 'name': str, 'balance': int, 'has_enough': bool},
                ...
            ],
            'members_short': [{'user_id': str, 'name': str, 'short_by': int}, ...]
        }
    """
    service = SERVICE_COSTS.get(service_name)
    if not service:
        raise ValueError(f"Unknown service: {service_name}")
    
    if not service['workspace_level']:
        raise ValueError(f"Service {service_name} is not a workspace-level service")
    
    supabase = get_supabase()
    
    # Get workspace participants
    participants = supabase.table('workspace_participants').select(
        'user_id, founders!workspace_participants_user_id_fkey(id, name, clerk_user_id)'
    ).eq('workspace_id', workspace_id).execute()
    
    if not participants.data:
        raise ValueError("Workspace not found or has no participants")
    
    member_count = len(participants.data)
    total_cost = service['credits']
    cost_per_member = total_cost // member_count
    
    # If there's a remainder, first member pays extra (or could distribute differently)
    remainder = total_cost % member_count
    
    members = []
    members_short = []
    can_proceed = True
    
    for i, participant in enumerate(participants.data):
        founder = participant.get('founders', {})
        user_id = founder.get('id') or participant.get('user_id')
        name = founder.get('name') or 'Unknown'
        
        # Get user's credit balance
        credit_record = _ensure_credit_record(supabase, user_id)
        balance = credit_record['balance']
        
        # First member pays the remainder
        member_cost = cost_per_member + (remainder if i == 0 else 0)
        has_enough = balance >= member_cost
        
        members.append({
            'user_id': user_id,
            'name': name,
            'balance': balance,
            'cost': member_cost,
            'has_enough': has_enough,
        })
        
        if not has_enough:
            can_proceed = False
            members_short.append({
                'user_id': user_id,
                'name': name,
                'short_by': member_cost - balance,
            })
    
    return {
        'can_proceed': can_proceed,
        'total_cost': total_cost,
        'cost_per_member': cost_per_member,
        'member_count': member_count,
        'members': members,
        'members_short': members_short,
    }


def deduct_workspace_service_credits(
    workspace_id: str,
    service_name: str,
    initiated_by_clerk_user_id: str,
    related_entity_id: Optional[str] = None,
    related_entity_type: Optional[str] = None,
    allow_initiator_cover: bool = False,
) -> Dict[str, Any]:
    """
    Deduct credits from all workspace members for a workspace-level service.
    
    Args:
        workspace_id: Workspace ID
        service_name: Service key
        initiated_by_clerk_user_id: Who initiated the service
        related_entity_id: Related entity (e.g., consultation_id)
        related_entity_type: Type of related entity
        allow_initiator_cover: If True, initiator can cover for members who are short
    
    Returns:
        {
            'success': bool,
            'total_deducted': int,
            'deductions': [{'user_id': str, 'amount': int, 'new_balance': int}, ...]
        }
    
    Raises:
        ValueError: If any member has insufficient credits (unless allow_initiator_cover)
    """
    # First check if everyone has enough
    check_result = check_workspace_service_credits(workspace_id, service_name)
    
    if not check_result['can_proceed']:
        if not allow_initiator_cover:
            short_names = [m['name'] for m in check_result['members_short']]
            raise ValueError(
                f"Some workspace members don't have enough credits: {', '.join(short_names)}. "
                f"Each member needs {check_result['cost_per_member']} credits."
            )
        # TODO: Implement initiator covering logic if needed
    
    supabase = get_supabase()
    deductions = []
    total_deducted = 0
    
    for member in check_result['members']:
        user_id = member['user_id']
        amount = member['cost']
        
        # Get current balance
        credit_record = supabase.table('user_credits').select('*').eq('user_id', user_id).execute()
        if not credit_record.data:
            continue
        
        record = credit_record.data[0]
        new_balance = record['balance'] - amount
        new_lifetime_spent = record['lifetime_spent'] + amount
        
        # Update balance
        supabase.table('user_credits').update({
            'balance': new_balance,
            'lifetime_spent': new_lifetime_spent,
        }).eq('user_id', user_id).execute()
        
        # Log transaction
        supabase.table('credit_transactions').insert({
            'user_id': user_id,
            'amount': -amount,
            'balance_after': new_balance,
            'transaction_type': 'workspace_service',
            'service_name': service_name,
            'workspace_id': workspace_id,
            'related_entity_id': related_entity_id,
            'related_entity_type': related_entity_type,
            'description': f'Workspace service: {service_name} (split cost)',
            'metadata': {
                'initiated_by': initiated_by_clerk_user_id,
                'total_cost': check_result['total_cost'],
                'member_count': check_result['member_count'],
            },
        }).execute()
        
        deductions.append({
            'user_id': user_id,
            'name': member['name'],
            'amount': amount,
            'new_balance': new_balance,
        })
        total_deducted += amount
    
    log_info(
        f"Deducted workspace service credits for {service_name} in workspace {workspace_id}. "
        f"Total: {total_deducted} credits from {len(deductions)} members."
    )
    
    return {
        'success': True,
        'total_deducted': total_deducted,
        'service_name': service_name,
        'workspace_id': workspace_id,
        'deductions': deductions,
    }


def get_service_costs() -> Dict[str, Any]:
    """Get all service costs for display."""
    return {
        'individual': {
            k: {'credits': v['credits'], 'display_name': k.replace('_', ' ').title()}
            for k, v in SERVICE_COSTS.items() if not v['workspace_level']
        },
        'workspace': {
            k: {'credits': v['credits'], 'display_name': k.replace('_', ' ').title()}
            for k, v in SERVICE_COSTS.items() if v['workspace_level']
        },
    }


def get_credit_packs() -> List[Dict[str, Any]]:
    """Get available credit packs for purchase."""
    return [
        {
            'id': key,
            'name': pack['name'],
            'credits': pack['credits'],
            'price_cents': pack['price_cents'],
            'price_display': f"${pack['price_cents'] / 100:.0f}",
        }
        for key, pack in CREDIT_PACKS.items()
    ]


def grant_referral_credits(
    referrer_clerk_user_id: str,
    referred_clerk_user_id: str,
    credits: int = 10,
) -> Dict[str, Any]:
    """Grant referral bonus credits to the referrer."""
    return add_credits(
        clerk_user_id=referrer_clerk_user_id,
        amount=credits,
        transaction_type='referral',
        description=f'Referral bonus for inviting a new user',
        metadata={'referred_user_id': referred_clerk_user_id},
    )


def refund_credits(
    clerk_user_id: str,
    amount: int,
    reason: str,
    original_transaction_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Refund credits to a user."""
    return add_credits(
        clerk_user_id=clerk_user_id,
        amount=amount,
        transaction_type='refund',
        description=f'Refund: {reason}',
        metadata={'original_transaction_id': original_transaction_id},
    )
