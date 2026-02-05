"""
Equity Calculation Service

Implements the weighted equity calculation formula for co-founder equity splits.
Based on factors: Time, Capital, Expertise, Risk, Network, and Idea Origination.
"""

from typing import Dict, List, Tuple, Any, Optional
from decimal import Decimal, ROUND_HALF_UP

# ============================================================================
# Weight Configuration
# ============================================================================

WEIGHTS = {
    'time_commitment': Decimal('0.30'),      # 30%
    'capital_contribution': Decimal('0.25'), # 25%
    'domain_expertise': Decimal('0.20'),     # 20%
    'risk_taken': Decimal('0.10'),           # 10%
    'network': Decimal('0.10'),              # 10%
    'idea_origination': Decimal('0.05'),     # 5%
}

# ============================================================================
# Score Mappings (0-10 scale)
# ============================================================================

TIME_COMMITMENT_SCORES = {
    'full_time_now': 10,
    'full_time_soon': 8,
    'part_time_20plus': 5,
    'part_time_under_20': 3,
    'advisor': 1,
}

EXPERTISE_LEVEL_SCORES = {
    'beginner': 3,
    'intermediate': 6,
    'expert': 9,
    'leader': 10,
}

NETWORK_SCORES = {
    'none': 1,
    'some': 4,
    'strong': 7,
    'exceptional': 10,
}

IDEA_ORIGINATION_SCORES = {
    'sole': 10,      # This founder came up with the idea alone
    'joint': 5,      # Both founders contributed equally
    'other': 0,      # The other founder came up with the idea
}


def calculate_time_score(time_commitment: str) -> int:
    """Calculate time commitment score (0-10)"""
    return TIME_COMMITMENT_SCORES.get(time_commitment, 5)


def calculate_capital_score(
    founder_capital: float, 
    other_founder_capital: float
) -> int:
    """
    Calculate capital contribution score (0-10).
    Normalized: highest contributor gets 10, others proportionally less.
    """
    if founder_capital == 0 and other_founder_capital == 0:
        return 5  # Equal if neither contributed
    
    max_capital = max(founder_capital, other_founder_capital)
    if max_capital == 0:
        return 5
    
    # Normalize to 0-10 scale
    score = (founder_capital / max_capital) * 10
    return round(score)


def calculate_expertise_score(skill_level: str) -> int:
    """Calculate domain expertise score (0-10)"""
    return EXPERTISE_LEVEL_SCORES.get(skill_level, 6)


def calculate_risk_score(leaving_job: bool, personal_guarantee: bool) -> int:
    """
    Calculate risk taken score (0-10).
    - Leaving job + personal guarantee = 10
    - Leaving job only = 7
    - Personal guarantee only = 5
    - Neither = 3
    """
    if leaving_job and personal_guarantee:
        return 10
    elif leaving_job:
        return 7
    elif personal_guarantee:
        return 5
    else:
        return 3


def calculate_network_score(network_level: str) -> int:
    """Calculate network score (0-10)"""
    return NETWORK_SCORES.get(network_level, 4)


def calculate_idea_score(idea_origin: str, is_founder_a: bool) -> int:
    """
    Calculate idea origination score (0-10).
    - idea_origin: 'founder_a', 'founder_b', 'joint', 'other'
    """
    if idea_origin == 'joint':
        return 5
    elif idea_origin == 'other':
        return 0
    elif idea_origin == 'founder_a':
        return 10 if is_founder_a else 0
    elif idea_origin == 'founder_b':
        return 0 if is_founder_a else 10
    else:
        return 5  # Default to joint


def calculate_founder_scores(
    responses: Dict[str, Any],
    other_responses: Dict[str, Any],
    startup_context: Dict[str, Any],
    is_founder_a: bool
) -> Dict[str, int]:
    """
    Calculate all factor scores for a single founder.
    
    Args:
        responses: This founder's questionnaire responses
        other_responses: The other founder's responses (for normalization)
        startup_context: Shared startup context (idea origin, etc.)
        is_founder_a: Whether this is founder A (for idea attribution)
    
    Returns:
        Dict with scores for each factor
    """
    # Extract values with defaults
    time_commitment = responses.get('time_commitment', 'part_time_20plus')
    
    risk_data = responses.get('risk', {})
    leaving_job = risk_data.get('leaving_job', False)
    personal_guarantee = risk_data.get('personal_guarantee', False)
    
    capital_data = responses.get('capital_contribution', {})
    capital_amount = float(capital_data.get('exact_amount', 0) or 0)
    
    other_capital_data = other_responses.get('capital_contribution', {})
    other_capital_amount = float(other_capital_data.get('exact_amount', 0) or 0)
    
    expertise_data = responses.get('expertise', {})
    skill_level = expertise_data.get('skill_level', 'intermediate')
    
    network_level = responses.get('network', 'some')
    
    idea_origin = startup_context.get('idea_origin', 'joint')
    
    return {
        'time': calculate_time_score(time_commitment),
        'capital': calculate_capital_score(capital_amount, other_capital_amount),
        'expertise': calculate_expertise_score(skill_level),
        'risk': calculate_risk_score(leaving_job, personal_guarantee),
        'network': calculate_network_score(network_level),
        'idea': calculate_idea_score(idea_origin, is_founder_a),
    }


def calculate_weighted_total(scores: Dict[str, int]) -> Decimal:
    """
    Calculate the weighted total score for a founder.
    
    Args:
        scores: Dict with scores for each factor
    
    Returns:
        Weighted total as Decimal
    """
    total = Decimal('0')
    
    total += Decimal(scores['time']) * WEIGHTS['time_commitment']
    total += Decimal(scores['capital']) * WEIGHTS['capital_contribution']
    total += Decimal(scores['expertise']) * WEIGHTS['domain_expertise']
    total += Decimal(scores['risk']) * WEIGHTS['risk_taken']
    total += Decimal(scores['network']) * WEIGHTS['network']
    total += Decimal(scores['idea']) * WEIGHTS['idea_origination']
    
    return total


def calculate_equity_split(
    founder_a_responses: Dict[str, Any],
    founder_b_responses: Dict[str, Any],
    startup_context: Dict[str, Any],
    advisor_equity_percent: float = 0.0
) -> Dict[str, Any]:
    """
    Calculate the recommended equity split based on questionnaire responses.
    
    Args:
        founder_a_responses: Founder A's questionnaire responses
        founder_b_responses: Founder B's questionnaire responses
        startup_context: Shared startup context (stage, idea origin, IP, etc.)
        advisor_equity_percent: Percentage allocated to advisor (deducted from pool first)
    
    Returns:
        Dict containing:
        - founder_a_percent: Decimal
        - founder_b_percent: Decimal
        - advisor_percent: Decimal (if advisor equity > 0)
        - breakdown: Dict with detailed scores for both founders
    """
    # Calculate scores for each founder
    founder_a_scores = calculate_founder_scores(
        founder_a_responses,
        founder_b_responses,
        startup_context,
        is_founder_a=True
    )
    
    founder_b_scores = calculate_founder_scores(
        founder_b_responses,
        founder_a_responses,
        startup_context,
        is_founder_a=False
    )
    
    # Calculate weighted totals
    founder_a_total = calculate_weighted_total(founder_a_scores)
    founder_b_total = calculate_weighted_total(founder_b_scores)
    
    # Add weighted totals to scores
    founder_a_scores['weighted_total'] = float(founder_a_total)
    founder_b_scores['weighted_total'] = float(founder_b_total)
    
    # Calculate available equity pool after advisor allocation
    advisor_percent = Decimal(str(advisor_equity_percent or 0)).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )
    available_pool = Decimal('100') - advisor_percent
    
    # Normalize founder shares to the available pool (after advisor deduction)
    total = founder_a_total + founder_b_total
    
    if total == 0:
        # Edge case: both have zero scores
        founder_a_percent = (available_pool / 2).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        founder_b_percent = (available_pool - founder_a_percent).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        # Calculate relative shares and scale to available pool
        founder_a_ratio = founder_a_total / total
        founder_a_percent = (founder_a_ratio * available_pool).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        founder_b_percent = (available_pool - founder_a_percent).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
    
    result = {
        'founder_a_percent': float(founder_a_percent),
        'founder_b_percent': float(founder_b_percent),
        'breakdown': {
            'founder_a': founder_a_scores,
            'founder_b': founder_b_scores,
            'weights': {k: float(v) for k, v in WEIGHTS.items()},
        }
    }
    
    # Include advisor equity in result if present
    if advisor_percent > 0:
        result['advisor_percent'] = float(advisor_percent)
    
    return result


def generate_all_scenarios(
    founder_a_responses: Dict[str, Any],
    founder_b_responses: Dict[str, Any],
    startup_context: Dict[str, Any],
    advisor_equity_percent: float = 0.0
) -> Dict[str, Any]:
    """
    Generate all three scenario types: recommended, equal, and custom placeholder.
    
    Args:
        founder_a_responses: Founder A's questionnaire responses
        founder_b_responses: Founder B's questionnaire responses
        startup_context: Shared startup context
        advisor_equity_percent: Percentage allocated to advisor
    
    Returns:
        Dict with three scenarios:
        - recommended: Calculated based on weighted formula
        - equal: Equal split (after advisor deduction)
        - custom: Null (to be filled by user)
        - advisor_percent: Advisor's allocation (if > 0)
    """
    # Calculate recommended split (factors in advisor equity)
    recommended = calculate_equity_split(
        founder_a_responses,
        founder_b_responses,
        startup_context,
        advisor_equity_percent
    )
    
    # For equal split, also deduct advisor equity first
    advisor_pct = float(advisor_equity_percent or 0)
    available_pool = 100.0 - advisor_pct
    equal_split = available_pool / 2
    
    result = {
        'recommended': {
            'founder_a_percent': recommended['founder_a_percent'],
            'founder_b_percent': recommended['founder_b_percent'],
            'breakdown': recommended['breakdown'],
        },
        'equal': {
            'founder_a_percent': round(equal_split, 2),
            'founder_b_percent': round(equal_split, 2),
            'breakdown': None,  # No calculation needed for equal split
        },
        'custom': None,  # User defines custom split
    }
    
    # Include advisor percent in all scenarios if present
    if advisor_pct > 0:
        result['advisor_percent'] = advisor_pct
        result['recommended']['advisor_percent'] = advisor_pct
        result['equal']['advisor_percent'] = advisor_pct
    
    return result


def validate_responses(responses: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate that questionnaire responses have all required fields.
    
    Args:
        responses: Founder's questionnaire responses
    
    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    
    # Required fields
    if not responses.get('time_commitment'):
        errors.append('Time commitment is required')
    
    if not responses.get('risk'):
        errors.append('Risk information is required')
    
    if not responses.get('capital_contribution'):
        errors.append('Capital contribution is required')
    
    if not responses.get('expertise'):
        errors.append('Expertise information is required')
    
    if not responses.get('network'):
        errors.append('Network level is required')
    
    if not responses.get('role'):
        errors.append('Role information is required')
    
    return (len(errors) == 0, errors)


def validate_startup_context(context: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate startup context has all required fields.
    
    Args:
        context: Startup context data
    
    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    
    if not context.get('stage'):
        errors.append('Startup stage is required')
    
    if not context.get('idea_origin'):
        errors.append('Idea origin is required')
    
    return (len(errors) == 0, errors)
