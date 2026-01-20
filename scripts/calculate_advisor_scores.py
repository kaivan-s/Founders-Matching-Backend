#!/usr/bin/env python3
"""
Automated script to calculate and save advisor scores for advisors who have been active for 90+ days.

This script can be run as a cron job or scheduled task to automatically calculate scores.

Usage:
    python calculate_advisor_scores.py [--workspace-id WORKSPACE_ID] [--advisor-id ADVISOR_ID] [--dry-run]

Examples:
    # Calculate scores for all eligible advisors
    python calculate_advisor_scores.py
    
    # Calculate score for specific workspace
    python calculate_advisor_scores.py --workspace-id <workspace_id>
    
    # Calculate score for specific advisor in specific workspace
    python calculate_advisor_scores.py --workspace-id <workspace_id> --advisor-id <advisor_id>
    
    # Dry run (don't save, just show what would be calculated)
    python calculate_advisor_scores.py --dry-run
"""

import sys
import os
import argparse
from datetime import datetime, timezone

# Add parent directory to path to import services
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.database import get_supabase
from services.advisor_scoring_service import calculate_and_save_advisor_score

def get_eligible_advisors(workspace_id=None, advisor_id=None):
    """
    Get list of advisors who have been active for 90+ days.
    
    Returns list of (workspace_id, advisor_user_id, joined_at) tuples.
    """
    supabase = get_supabase()
    now = datetime.now(timezone.utc)
    
    # Get all advisors from workspace_participants
    query = supabase.table('workspace_participants').select(
        'workspace_id, user_id, joined_at'
    ).eq('role', 'ADVISOR')
    
    if workspace_id:
        query = query.eq('workspace_id', workspace_id)
    
    if advisor_id:
        query = query.eq('user_id', advisor_id)
    
    result = query.execute()
    
    eligible = []
    for participant in (result.data or []):
        joined_at_str = participant.get('joined_at')
        if not joined_at_str:
            continue
        
        # Parse joined_at
        if isinstance(joined_at_str, str):
            joined_at = datetime.fromisoformat(joined_at_str.replace('Z', '+00:00'))
        else:
            joined_at = joined_at_str
        
        if joined_at.tzinfo is None:
            joined_at = joined_at.replace(tzinfo=timezone.utc)
        
        days_active = (now - joined_at).days
        
        if days_active >= 90:
            eligible.append((
                participant['workspace_id'],
                participant['user_id'],
                joined_at,
                days_active
            ))
    
    return eligible


def main():
    parser = argparse.ArgumentParser(description='Calculate and save advisor scores')
    parser.add_argument('--workspace-id', help='Specific workspace ID to process')
    parser.add_argument('--advisor-id', help='Specific advisor user ID to process')
    parser.add_argument('--dry-run', action='store_true', help='Calculate but do not save scores')
    parser.add_argument('--clerk-user-id', help='Clerk user ID for authentication (required for calculation)')
    
    args = parser.parse_args()
    
    # Get eligible advisors
    print("Finding eligible advisors (90+ days active)...")
    eligible = get_eligible_advisors(args.workspace_id, args.advisor_id)
    
    if not eligible:
        print("No eligible advisors found.")
        return
    
    print(f"Found {len(eligible)} eligible advisor(s).")
    
    if args.dry_run:
        print("\n[DRY RUN MODE - Scores will not be saved]\n")
    
    # Process each advisor
    success_count = 0
    error_count = 0
    
    for workspace_id, advisor_user_id, joined_at, days_active in eligible:
        print(f"\nProcessing advisor {advisor_user_id} in workspace {workspace_id}")
        print(f"  Days active: {days_active}")
        print(f"  Joined at: {joined_at.isoformat()}")
        
        try:
            # For automated calculation, we need a clerk_user_id
            # In production, you might want to use a system user or get it from workspace owner
            if not args.clerk_user_id:
                # Try to get workspace owner's clerk_user_id
                supabase = get_supabase()
                workspace = supabase.table('workspaces').select('owner_id').eq('id', workspace_id).execute()
                if workspace.data:
                    owner = supabase.table('founders').select('clerk_user_id').eq('id', workspace.data[0]['owner_id']).execute()
                    if owner.data:
                        clerk_user_id = owner.data[0].get('clerk_user_id')
                    else:
                        print(f"  ERROR: Could not find workspace owner's clerk_user_id")
                        error_count += 1
                        continue
                else:
                    print(f"  ERROR: Could not find workspace")
                    error_count += 1
                    continue
            else:
                clerk_user_id = args.clerk_user_id
            
            # Calculate score
            if args.dry_run:
                from services.advisor_scoring_service import calculate_advisor_score
                score_result = calculate_advisor_score(clerk_user_id, workspace_id, advisor_user_id)
                print(f"  Score: {score_result.get('final_score', 'N/A')}")
                print(f"  Can calculate: {score_result.get('can_calculate', False)}")
                if score_result.get('can_calculate'):
                    print(f"  Component scores: {score_result.get('component_scores', {})}")
            else:
                score_result = calculate_and_save_advisor_score(clerk_user_id, workspace_id, advisor_user_id)
                if score_result.get('saved'):
                    print(f"  ✓ Score calculated and saved: {score_result.get('final_score', 'N/A')}")
                    print(f"    Score ID: {score_result.get('saved_score_id')}")
                    success_count += 1
                else:
                    print(f"  ⚠ Score calculation not available: {score_result.get('message', 'Unknown reason')}")
                    error_count += 1
                    
        except Exception as e:
            print(f"  ✗ ERROR: {str(e)}")
            error_count += 1
    
    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Total processed: {len(eligible)}")
    print(f"  Successful: {success_count}")
    print(f"  Errors: {error_count}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
