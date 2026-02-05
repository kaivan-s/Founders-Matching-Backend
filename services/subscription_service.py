"""Subscription service for Polar integration"""
import os
import hmac
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Any
from config.database import get_supabase
from polar_sdk import Polar
from services import plan_service
from utils.auth import get_clerk_user_email

# Polar API configuration
POLAR_ACCESS_TOKEN = os.getenv('POLAR_ACCESS_TOKEN')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:3000')

# Product IDs - These need to be set in Polar dashboard
# Set these as environment variables: POLAR_PRODUCT_PRO_ID, POLAR_PRODUCT_PRO_PLUS_ID
POLAR_PRODUCT_PRO_ID = os.getenv('POLAR_PRODUCT_PRO_ID')
POLAR_PRODUCT_PRO_PLUS_ID = os.getenv('POLAR_PRODUCT_PRO_PLUS_ID')
POLAR_PRODUCT_ADVISOR_ONBOARDING_ID = os.getenv('POLAR_PRODUCT_ADVISOR_ONBOARDING_ID')
POLAR_PRODUCT_ADVISOR_RENEWAL_ID = os.getenv('POLAR_PRODUCT_ADVISOR_RENEWAL_ID')

def create_subscription_checkout(clerk_user_id: str, plan_id: str) -> Dict[str, str]:
    """
    Create a Polar checkout session for a subscription plan
    
    Args:
        clerk_user_id: The Clerk user ID
        plan_id: The plan ID (PRO or PRO_PLUS)
    
    Returns:
        dict: Checkout session data with checkout_url
    """
    if not POLAR_ACCESS_TOKEN:
        raise ValueError("Polar API not configured. Please set POLAR_ACCESS_TOKEN.")
    
    # Get product ID for the plan
    product_id = None
    if plan_id == 'PRO':
        product_id = POLAR_PRODUCT_PRO_ID
    elif plan_id == 'PRO_PLUS':
        product_id = POLAR_PRODUCT_PRO_PLUS_ID
    else:
        raise ValueError(f"Invalid plan ID: {plan_id}")
    
    if not product_id:
        raise ValueError(f"Polar product ID not configured for plan {plan_id}")
    
    # Get user's email from profile, fallback to Clerk API
    supabase = get_supabase()
    profile = supabase.table('founders').select('email, name').eq('clerk_user_id', clerk_user_id).execute()
    
    user_email = None
    user_name = ''
    
    if profile.data:
        user_email = profile.data[0].get('email')
        user_name = profile.data[0].get('name', '')
    
    # If email is missing or empty, get it from Clerk API
    if not user_email or '@' not in user_email:
        user_email = get_clerk_user_email(clerk_user_id)
    
    if not user_email or '@' not in user_email:
        raise ValueError("User email not found. Please complete your profile or ensure your email is set in Clerk.")
    
    try:
        # Create checkout session using Polar SDK
        with Polar(access_token=POLAR_ACCESS_TOKEN) as polar:
            res = polar.checkouts.create(request={
                "products": [product_id],
                "success_url": f"{FRONTEND_URL}/pricing?subscription=success&plan={plan_id}",
                "customer_email": user_email,
                "customer_metadata": {
                    "clerk_user_id": clerk_user_id,
                    "plan_id": plan_id,
                    "subscription_type": "founder_plan"
                }
            })
            
            return {
                "checkout_url": res.url,
                "checkout_id": res.id
            }
        
    except Exception as e:
        error_msg = str(e)
        raise ValueError(f"Failed to create checkout session: {error_msg}")

def create_advisor_onboarding_checkout(clerk_user_id: str) -> Dict[str, str]:
    """
    Create a Polar checkout session for advisor onboarding fee
    
    Args:
        clerk_user_id: The Clerk user ID
    
    Returns:
        dict: Checkout session data with checkout_url
    """
    if not POLAR_ACCESS_TOKEN or not POLAR_PRODUCT_ADVISOR_ONBOARDING_ID:
        raise ValueError("Polar API or product ID not configured for advisor onboarding.")
    
    supabase = get_supabase()
    
    # Get email from founders table (should always exist after registration)
    profile = supabase.table('founders').select('id, email, name').eq('clerk_user_id', clerk_user_id).execute()
    
    if not profile.data:
        raise ValueError("Profile not found. Please complete your advisor registration first.")
    
    user_email = profile.data[0].get('email', '').strip()
    
    # If email is missing, try to get from Clerk and update founders table
    if not user_email:
        try:
            clerk_email = get_clerk_user_email(clerk_user_id)
            if clerk_email and clerk_email.strip():
                user_email = clerk_email.strip()
                # Update founders table with email
                founder_id = profile.data[0].get('id')
                supabase.table('founders').update({'email': user_email}).eq('id', founder_id).execute()
        except Exception as e:
            pass
    
    # Validate email exists
    if not user_email:
        raise ValueError("Email address is required for checkout. Please ensure your account has a valid email address.")
    
    # Validate email format
    import re
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, user_email):
        raise ValueError(f"Invalid email address format: {user_email}")
    
    try:
        with Polar(access_token=POLAR_ACCESS_TOKEN) as polar:
            res = polar.checkouts.create(request={
                "products": [POLAR_PRODUCT_ADVISOR_ONBOARDING_ID],
                "success_url": f"{FRONTEND_URL}/advisor/dashboard?onboarding=success",
                "customer_email": user_email,
                "customer_metadata": {
                    "clerk_user_id": clerk_user_id,
                    "subscription_type": "advisor_onboarding"
                }
            })
            
            return {
                "checkout_url": res.url,
                "checkout_id": res.id
            }
    except Exception as e:
        error_msg = str(e)
        raise ValueError(f"Failed to create checkout session: {error_msg}")

def create_advisor_renewal_checkout(clerk_user_id: str) -> Dict[str, str]:
    """
    Create a Polar checkout session for advisor annual renewal
    
    Args:
        clerk_user_id: The Clerk user ID
    
    Returns:
        dict: Checkout session data with checkout_url
    """
    if not POLAR_ACCESS_TOKEN or not POLAR_PRODUCT_ADVISOR_RENEWAL_ID:
        raise ValueError("Polar API or product ID not configured for advisor renewal.")
    
    supabase = get_supabase()
    profile = supabase.table('founders').select('email, name').eq('clerk_user_id', clerk_user_id).execute()
    
    user_email = None
    
    if profile.data:
        user_email = profile.data[0].get('email')
    
    # If email is missing or empty, get it from Clerk API
    if not user_email or '@' not in user_email:
        user_email = get_clerk_user_email(clerk_user_id)
    
    if not user_email or '@' not in user_email:
        raise ValueError("User email not found. Please complete your profile or ensure your email is set in Clerk.")
    
    try:
        with Polar(access_token=POLAR_ACCESS_TOKEN) as polar:
            res = polar.checkouts.create(request={
                "products": [POLAR_PRODUCT_ADVISOR_RENEWAL_ID],
                "success_url": f"{FRONTEND_URL}/advisor/dashboard?renewal=success",
                "customer_email": user_email,
                "customer_metadata": {
                    "clerk_user_id": clerk_user_id,
                    "subscription_type": "advisor_renewal"
                }
            })
            
            return {
                "checkout_url": res.url,
                "checkout_id": res.id
            }
    except Exception as e:
        error_msg = str(e)
        raise ValueError(f"Failed to create checkout session: {error_msg}")

def verify_webhook_signature(payload: str, signature: str) -> bool:
    """
    Verify Polar webhook signature
    
    Polar uses HMAC-SHA256 for webhook signatures.
    The signature is sent in the 'X-Polar-Webhook-Signature' header.
    """
    webhook_secret = os.getenv('POLAR_WEBHOOK_SECRET')
    
    # In production, webhook secret must be configured
    # In development, allow skipping if secret not set (but still verify if signature provided)
    if not webhook_secret:
        # If no secret configured but signature provided, verification should fail
        # This prevents accepting unsigned webhooks in production
        return False
    
    expected_signature = hmac.new(
        webhook_secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected_signature)

def handle_subscription_webhook(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle Polar webhook events for subscriptions
    
    Events handled:
    - checkout.created: When a checkout is created
    - order.created: When an order is created and paid - activate subscription
    - subscription.created: When a subscription is created
    - subscription.updated: When subscription status changes
    - subscription.canceled: When subscription is canceled
    
    Returns:
        dict: Result of webhook processing
    """
    event_type = webhook_data.get('type')
    
    if event_type == 'checkout.created':
        return handle_checkout_created(webhook_data)
    elif event_type == 'order.created':
        return handle_order_created(webhook_data)
    elif event_type == 'subscription.created':
        return handle_subscription_created(webhook_data)
    elif event_type == 'subscription.updated':
        return handle_subscription_updated(webhook_data)
    elif event_type == 'subscription.canceled':
        return handle_subscription_canceled(webhook_data)
    else:
        return {"status": "ignored", "message": f"Event {event_type} not handled"}

def handle_checkout_created(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle checkout.created webhook - store checkout info"""
    try:
        data = webhook_data.get('data', {})
        metadata = data.get('metadata', {})
        subscription_type = metadata.get('subscription_type')
        
        if subscription_type == 'founder_plan':
            # Store checkout info for founder subscription
            clerk_user_id = metadata.get('clerk_user_id')
            plan_id = metadata.get('plan_id')
            
            if not clerk_user_id or not plan_id:
                return {"status": "error", "message": "Missing metadata"}
            
            # Store in database for tracking
            supabase = get_supabase()
            supabase.table('subscription_checkouts').insert({
                'clerk_user_id': clerk_user_id,
                'checkout_id': data.get('id'),
                'plan_id': plan_id,
                'status': 'pending',
                'created_at': datetime.now(timezone.utc).isoformat()
            }).execute()
            
        elif subscription_type in ['advisor_onboarding', 'advisor_renewal']:
            # Store advisor checkout info
            clerk_user_id = metadata.get('clerk_user_id')
            if clerk_user_id:
                supabase = get_supabase()
                supabase.table('subscription_checkouts').insert({
                    'clerk_user_id': clerk_user_id,
                    'checkout_id': data.get('id'),
                    'plan_id': subscription_type,
                    'status': 'pending',
                    'created_at': datetime.now(timezone.utc).isoformat()
                }).execute()
        
        return {"status": "success", "message": "Checkout created"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def handle_order_created(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle order.created webhook - activate subscription or process advisor payment"""
    try:
        data = webhook_data.get('data', {})
        order_id = data.get('id')
        metadata = data.get('metadata', {})
        subscription_type = metadata.get('subscription_type')
        clerk_user_id = metadata.get('clerk_user_id')
        
        if not clerk_user_id:
            return {"status": "error", "message": "Missing clerk_user_id"}
        
        if not order_id:
            return {"status": "error", "message": "Missing order ID"}
        
        supabase = get_supabase()
        
        # Check if this webhook was already processed (idempotency)
        # Use a simple table to track processed webhooks
        try:
            processed_check = supabase.table('webhook_processing_log').select('id').eq('webhook_id', order_id).eq('webhook_type', 'order.created').execute()
            if processed_check.data:
                # Already processed - return success without re-processing
                return {"status": "success", "message": "Webhook already processed", "idempotent": True}
        except Exception:
            # Table might not exist yet - continue processing
            pass
        
        if subscription_type == 'founder_plan':
            plan_id = metadata.get('plan_id')
            if not plan_id:
                return {"status": "error", "message": "Missing plan_id"}
            
            # Get actual subscription period from webhook data
            # Polar webhooks include current_period_end, use that instead of hardcoding
            current_period_end = None
            if data.get('current_period_end'):
                # Convert Unix timestamp to datetime if needed
                period_end_value = data.get('current_period_end')
                if isinstance(period_end_value, (int, float)):
                    current_period_end = datetime.fromtimestamp(period_end_value, tz=timezone.utc)
                elif isinstance(period_end_value, str):
                    current_period_end = datetime.fromisoformat(period_end_value.replace('Z', '+00:00'))
            else:
                # Fallback: calculate from billing period if available
                # Default to 30 days if no period info available
                current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
            
            # Update user's plan
            plan_service.update_founder_plan(
                clerk_user_id,
                plan_id,
                subscription_id=data.get('subscription_id'),
                subscription_status='active',
                current_period_end=current_period_end
            )
            
            # Log successful processing
            try:
                supabase.table('webhook_processing_log').insert({
                    'webhook_id': order_id,
                    'webhook_type': 'order.created',
                    'processed_at': datetime.now(timezone.utc).isoformat(),
                    'status': 'success'
                }).execute()
            except Exception:
                # Log but don't fail if logging fails
                pass
            
            return {"status": "success", "message": f"Plan {plan_id} activated"}
            
        elif subscription_type == 'advisor_onboarding':
            # Mark advisor onboarding as paid
            plan_service.update_advisor_billing(clerk_user_id, onboarding_paid=True)
            
            # Log successful processing
            try:
                supabase.table('webhook_processing_log').insert({
                    'webhook_id': order_id,
                    'webhook_type': 'order.created',
                    'processed_at': datetime.now(timezone.utc).isoformat(),
                    'status': 'success'
                }).execute()
            except Exception:
                pass
            
            return {"status": "success", "message": "Advisor onboarding paid"}
            
        elif subscription_type == 'advisor_renewal':
            # Renew advisor subscription
            plan_service.renew_advisor_subscription(clerk_user_id)
            
            # Log successful processing
            try:
                supabase.table('webhook_processing_log').insert({
                    'webhook_id': order_id,
                    'webhook_type': 'order.created',
                    'processed_at': datetime.now(timezone.utc).isoformat(),
                    'status': 'success'
                }).execute()
            except Exception:
                pass
            
            return {"status": "success", "message": "Advisor subscription renewed"}
        
        return {"status": "ignored", "message": f"Unknown subscription type: {subscription_type}"}
    except Exception as e:
        # Log failed processing
        try:
            supabase = get_supabase()
            supabase.table('webhook_processing_log').insert({
                'webhook_id': webhook_data.get('data', {}).get('id', 'unknown'),
                'webhook_type': 'order.created',
                'processed_at': datetime.now(timezone.utc).isoformat(),
                'status': 'error',
                'error_message': str(e)
            }).execute()
        except Exception:
            pass
        return {"status": "error", "message": str(e)}

def handle_subscription_created(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle subscription.created webhook"""
    try:
        data = webhook_data.get('data', {})
        subscription_id = data.get('id')
        status = data.get('status')
        
        # Update subscription status in database
        supabase = get_supabase()
        supabase.table('founders').update({
            'subscription_id': subscription_id,
            'subscription_status': status
        }).eq('subscription_id', subscription_id).execute()
        
        return {"status": "success", "message": "Subscription created"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def handle_subscription_updated(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle subscription.updated webhook - update subscription status"""
    try:
        data = webhook_data.get('data', {})
        subscription_id = data.get('id')
        status = data.get('status')
        current_period_end = data.get('current_period_end')
        
        supabase = get_supabase()
        update_data = {
            'subscription_status': status
        }
        
        if current_period_end:
            update_data['subscription_current_period_end'] = datetime.fromisoformat(
                current_period_end.replace('Z', '+00:00')
            ).isoformat()
        
        supabase.table('founders').update(update_data).eq('subscription_id', subscription_id).execute()
        
        # If subscription is canceled or expired, check if period has ended before downgrading
        if status in ['canceled', 'expired', 'past_due']:
            founder = supabase.table('founders').select('clerk_user_id, subscription_current_period_end').eq('subscription_id', subscription_id).execute()
            if founder.data:
                clerk_user_id = founder.data[0]['clerk_user_id']
                period_end_str = founder.data[0].get('subscription_current_period_end')
                
                # Only downgrade if period has actually ended
                should_downgrade = False
                if status == 'expired':
                    # Expired means period has definitely ended
                    should_downgrade = True
                elif period_end_str:
                    # Check if current_period_end has passed
                    try:
                        from datetime import datetime, timezone
                        period_end = datetime.fromisoformat(period_end_str.replace('Z', '+00:00'))
                        if period_end.tzinfo is None:
                            period_end = period_end.replace(tzinfo=timezone.utc)
                        now = datetime.now(timezone.utc)
                        if period_end < now:
                            should_downgrade = True
                    except (ValueError, AttributeError):
                        # Invalid date - assume expired
                        should_downgrade = True
                else:
                    # No period_end date - assume expired
                    should_downgrade = True
                
                if should_downgrade:
                    plan_service.update_founder_plan(clerk_user_id, 'FREE')
        
        return {"status": "success", "message": "Subscription updated"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def handle_subscription_canceled(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle subscription.canceled webhook - mark as canceled but don't downgrade until period ends"""
    try:
        data = webhook_data.get('data', {})
        subscription_id = data.get('id')
        cancel_at_period_end = data.get('cancel_at_period_end', True)
        current_period_end = data.get('current_period_end')
        
        supabase = get_supabase()
        founder = supabase.table('founders').select('clerk_user_id, subscription_current_period_end').eq('subscription_id', subscription_id).execute()
        
        if founder.data:
            clerk_user_id = founder.data[0]['clerk_user_id']
            existing_period_end = founder.data[0].get('subscription_current_period_end')
            
            # Use period_end from webhook if available, otherwise use existing
            period_end_to_check = current_period_end or existing_period_end
            
            # Only downgrade immediately if cancel_at_period_end is False (immediate cancellation)
            # Otherwise, user keeps access until period_end
            if not cancel_at_period_end:
                plan_service.update_founder_plan(clerk_user_id, 'FREE')
            
            update_data = {
                'subscription_status': 'canceled',
                'subscription_cancel_at_period_end': cancel_at_period_end
            }
            
            if current_period_end:
                update_data['subscription_current_period_end'] = current_period_end
            
            supabase.table('founders').update(update_data).eq('subscription_id', subscription_id).execute()
        
        return {"status": "success", "message": "Subscription canceled"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

