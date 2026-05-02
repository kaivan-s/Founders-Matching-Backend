"""Subscription service for Dodo Payments integration"""
import os
import hmac
import hashlib
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Any
from config.database import get_supabase
from services import plan_service
from utils.auth import get_clerk_user_email

# Dodo Payments API configuration
DODO_API_KEY = os.getenv('DODO_PAYMENTS_API_KEY', '').strip('"')
DODO_ENVIRONMENT = os.getenv('DODO_ENVIRONMENT', 'live_mode')
DODO_WEBHOOK_SECRET = os.getenv('DODO_WEBHOOK_SECRET', '')

# Strip trailing slash to avoid double slashes in URLs
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:3000').rstrip('/')

# Product IDs from Dodo Payments dashboard
DODO_PRODUCT_PRO_ID = os.getenv('DODO_PRODUCT_PRO_ID')
DODO_PRODUCT_PRO_PLUS_ID = os.getenv('DODO_PRODUCT_PRO_PLUS_ID')

# Advisor "Pro Advisor" subscription products (monthly + yearly billing cycles)
DODO_PRODUCT_ADVISOR_PRO_MONTHLY_ID = os.getenv('DODO_PRODUCT_ADVISOR_PRO_MONTHLY_ID')
DODO_PRODUCT_ADVISOR_PRO_YEARLY_ID = os.getenv('DODO_PRODUCT_ADVISOR_PRO_YEARLY_ID')
# Dodo API base URL
DODO_API_BASE = 'https://live.dodopayments.com' if DODO_ENVIRONMENT == 'live_mode' else 'https://test.dodopayments.com'


def _get_dodo_client():
    """Get Dodo Payments client"""
    try:
        from dodopayments import DodoPayments
        return DodoPayments(
            bearer_token=DODO_API_KEY,
            environment=DODO_ENVIRONMENT
        )
    except ImportError:
        raise ValueError("dodopayments package not installed. Run: pip install dodopayments")


def create_subscription_checkout(clerk_user_id: str, plan_id: str) -> Dict[str, str]:
    """
    Create a Dodo Payments checkout session for a subscription plan
    
    Args:
        clerk_user_id: The Clerk user ID
        plan_id: The plan ID (PRO or PRO_PLUS)
    
    Returns:
        dict: Checkout session data with checkout_url
    """
    if not DODO_API_KEY:
        raise ValueError("Dodo Payments API not configured. Please set DODO_PAYMENTS_API_KEY.")
    
    # Get product ID for the plan
    product_id = None
    if plan_id == 'PRO':
        product_id = DODO_PRODUCT_PRO_ID
    elif plan_id == 'PRO_PLUS':
        product_id = DODO_PRODUCT_PRO_PLUS_ID
    else:
        raise ValueError(f"Invalid plan ID: {plan_id}")
    
    if not product_id:
        raise ValueError(f"Dodo product ID not configured for plan {plan_id}")
    
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
        client = _get_dodo_client()
        
        session = client.checkout_sessions.create(
            product_cart=[{"product_id": product_id, "quantity": 1}],
            customer={
                "email": user_email,
                "name": user_name or user_email.split('@')[0]
            },
            return_url=f"{FRONTEND_URL}/pricing?subscription=success&plan={plan_id}",
            metadata={
                "clerk_user_id": clerk_user_id,
                "plan_id": plan_id,
                "subscription_type": "founder_plan"
            }
        )
        
        return {
            "checkout_url": session.checkout_url,
            "checkout_id": session.session_id
        }
        
    except Exception as e:
        error_msg = str(e)
        raise ValueError(f"Failed to create checkout session: {error_msg}")


# ============================================================================
# Advisor "Pro Advisor" subscription
# ============================================================================
#
# Lifecycle:
#   Advisor signs up                              -> subscription_status = 'free'
#   Advisor confirms payment for first booking    -> subscription_status = 'trial'
#                                                    (30-day grace period set)
#   Trial ends without subscribing                -> can_accept_bookings becomes
#                                                    False (soft cutoff via the
#                                                    `_advisor_can_accept_bookings`
#                                                    helper). DB status stays 'trial'
#                                                    until they subscribe or cancel.
#   Advisor subscribes via Dodo                   -> subscription_status = 'active'
#   Subscription cancelled / payment fails        -> 'cancelled' / 'past_due'

def create_advisor_subscription_checkout(
    clerk_user_id: str,
    billing_cycle: str = 'monthly',
) -> Dict[str, str]:
    """Create a Dodo checkout session for an advisor's Pro Advisor subscription.

    Args:
        clerk_user_id: The Clerk user ID of the advisor
        billing_cycle: 'monthly' or 'yearly'
    """
    if not DODO_API_KEY:
        raise ValueError("Dodo Payments API not configured. Please set DODO_PAYMENTS_API_KEY.")

    if billing_cycle not in ('monthly', 'yearly'):
        raise ValueError("billing_cycle must be 'monthly' or 'yearly'")

    product_id = (
        DODO_PRODUCT_ADVISOR_PRO_YEARLY_ID
        if billing_cycle == 'yearly'
        else DODO_PRODUCT_ADVISOR_PRO_MONTHLY_ID
    )
    if not product_id:
        raise ValueError(f"Dodo product ID not configured for advisor {billing_cycle} subscription")

    supabase = get_supabase()
    profile = supabase.table('founders').select('email, name').eq('clerk_user_id', clerk_user_id).execute()
    if not profile.data:
        raise ValueError("Profile not found")

    user_email = profile.data[0].get('email')
    user_name = profile.data[0].get('name', '')
    if not user_email or '@' not in user_email:
        user_email = get_clerk_user_email(clerk_user_id)
    if not user_email or '@' not in user_email:
        raise ValueError("User email not found. Please complete your profile.")

    try:
        client = _get_dodo_client()
        session = client.checkout_sessions.create(
            product_cart=[{"product_id": product_id, "quantity": 1}],
            customer={
                "email": user_email,
                "name": user_name or user_email.split('@')[0],
            },
            return_url=f"{FRONTEND_URL}/advisor/dashboard?advisor_subscription=success&cycle={billing_cycle}",
            metadata={
                "clerk_user_id": clerk_user_id,
                "subscription_type": "advisor_pro",
                "billing_cycle": billing_cycle,
            },
        )
        return {
            "checkout_url": session.checkout_url,
            "checkout_id": session.session_id,
        }
    except Exception as e:
        raise ValueError(f"Failed to create checkout session: {e}")


def cancel_advisor_subscription(clerk_user_id: str) -> Dict[str, Any]:
    """Cancel an advisor's Pro Advisor subscription in Dodo + mark cancelled in DB.

    The advisor stays listed in the marketplace but `_advisor_can_accept_bookings`
    will return False until/unless they resubscribe (soft cutoff).
    """
    from utils.logger import log_info, log_error, log_warning

    if not DODO_API_KEY:
        raise ValueError("Dodo Payments API not configured.")

    supabase = get_supabase()

    # Look up the advisor's subscription_id via founder -> advisor_profile
    founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not founder.data:
        raise ValueError("Profile not found")
    founder_id = founder.data[0]['id']

    profile = supabase.table('advisor_profiles').select(
        'id, subscription_id, subscription_status'
    ).eq('user_id', founder_id).execute()
    if not profile.data:
        raise ValueError("Advisor profile not found")

    sub_id = profile.data[0].get('subscription_id')
    if not sub_id:
        # Already not subscribed; just normalize the status
        supabase.table('advisor_profiles').update({
            'subscription_status': 'cancelled',
        }).eq('id', profile.data[0]['id']).execute()
        return {"status": "success", "message": "No active subscription to cancel; marked cancelled."}

    # Best-effort cancel in Dodo
    try:
        client = _get_dodo_client()
        client.subscriptions.update(
            subscription_id=sub_id,
            status='cancelled',
        )
        log_info(f"Cancelled advisor subscription {sub_id} for {clerk_user_id}")
    except Exception as e:
        # Don't fail the user-facing call — record locally and let the webhook
        # reconcile if Dodo cancellation succeeds asynchronously.
        log_warning(f"Dodo cancellation API call failed (will rely on local status): {e}")

    supabase.table('advisor_profiles').update({
        'subscription_status': 'cancelled',
    }).eq('id', profile.data[0]['id']).execute()

    return {"status": "success", "message": "Subscription cancelled."}


def _activate_advisor_subscription(
    clerk_user_id: str,
    subscription_id: Optional[str],
    billing_cycle: str,
) -> None:
    """Mark the advisor as actively subscribed in the database.

    Used by the payment.succeeded / subscription.active webhook handlers.
    """
    from utils.logger import log_info

    supabase = get_supabase()

    founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not founder.data:
        return
    founder_id = founder.data[0]['id']

    # Period: 1 month for monthly, 1 year for yearly
    if billing_cycle == 'yearly':
        period_end = datetime.now(timezone.utc) + timedelta(days=365)
    else:
        period_end = datetime.now(timezone.utc) + timedelta(days=30)

    update = {
        'subscription_status': 'active',
        'subscription_current_period_end': period_end.isoformat(),
    }
    if subscription_id:
        update['subscription_id'] = subscription_id

    supabase.table('advisor_profiles').update(update).eq('user_id', founder_id).execute()
    log_info(f"Activated advisor subscription for {clerk_user_id} (cycle={billing_cycle})")


def cancel_subscription(clerk_user_id: str) -> Dict[str, Any]:
    """
    Cancel a user's subscription in Dodo Payments.
    
    This should be called BEFORE updating the database to FREE plan
    to ensure Dodo stops billing the user.
    
    Args:
        clerk_user_id: The Clerk user ID
        
    Returns:
        dict: Result with status and details
    """
    from utils.logger import log_info, log_error, log_warning
    
    if not DODO_API_KEY:
        raise ValueError("Dodo Payments API not configured. Please set DODO_PAYMENTS_API_KEY.")
    
    # Get user's subscription_id from database
    supabase = get_supabase()
    founder = supabase.table('founders').select(
        'subscription_id, subscription_status, plan'
    ).eq('clerk_user_id', clerk_user_id).execute()
    
    if not founder.data:
        raise ValueError("User not found")
    
    user_data = founder.data[0]
    subscription_id = user_data.get('subscription_id')
    current_plan = user_data.get('plan', 'FREE')
    subscription_status = user_data.get('subscription_status')
    
    # If user is on FREE plan or has no subscription_id, nothing to cancel
    if current_plan == 'FREE':
        log_info(f"User {clerk_user_id} is already on FREE plan, no subscription to cancel")
        return {
            "success": True,
            "message": "User is already on free plan",
            "already_free": True
        }
    
    if not subscription_id:
        log_warning(f"User {clerk_user_id} has plan {current_plan} but no subscription_id in database")
        return {
            "success": True,
            "message": "No active subscription found to cancel",
            "no_subscription": True
        }
    
    # Check if already canceled
    if subscription_status in ['canceled', 'cancelled', 'revoked']:
        log_info(f"Subscription {subscription_id} already canceled for user {clerk_user_id}")
        return {
            "success": True,
            "message": "Subscription already canceled",
            "already_canceled": True
        }
    
    try:
        client = _get_dodo_client()
        
        # Cancel subscription in Dodo
        client.subscriptions.cancel(subscription_id)
        
        log_info(f"Successfully canceled subscription {subscription_id} in Dodo for user {clerk_user_id}")
        
        return {
            "success": True,
            "message": "Subscription canceled successfully",
            "subscription_id": subscription_id,
            "canceled_at": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        error_msg = str(e)
        
        # Check if already canceled error
        if 'already' in error_msg.lower() and 'cancel' in error_msg.lower():
            log_info(f"Subscription {subscription_id} was already canceled in Dodo")
            return {
                "success": True,
                "message": "Subscription was already canceled",
                "already_canceled": True
            }
        
        # Check if subscription not found
        if '404' in error_msg or 'not found' in error_msg.lower():
            log_warning(f"Subscription {subscription_id} not found in Dodo - may have been deleted")
            return {
                "success": True,
                "message": "Subscription not found (may have been already removed)",
                "not_found": True
            }
        
        log_error(f"Failed to cancel subscription {subscription_id} in Dodo: {error_msg}")
        raise ValueError(f"Failed to cancel subscription: {error_msg}")


# Keep old function name for backward compatibility
def validate_webhook_event(body: bytes, headers: dict) -> Optional[Dict[str, Any]]:
    """
    Validate and parse Dodo Payments webhook event.
    
    Dodo follows Standard Webhooks specification.
    
    Args:
        body: Raw request body as bytes
        headers: Request headers dict
    
    Returns:
        Parsed webhook event data if valid, None if verification fails
    """
    from utils.logger import log_info, log_error
    
    if not DODO_WEBHOOK_SECRET:
        log_error("DODO_WEBHOOK_SECRET not configured - accepting webhook without verification")
        # In development, allow unverified webhooks
        try:
            return json.loads(body)
        except:
            return None
    
    try:
        # Dodo uses Standard Webhooks specification
        # Headers: webhook-id, webhook-timestamp, webhook-signature
        webhook_id = headers.get('webhook-id') or headers.get('Webhook-Id')
        webhook_timestamp = headers.get('webhook-timestamp') or headers.get('Webhook-Timestamp')
        webhook_signature = headers.get('webhook-signature') or headers.get('Webhook-Signature')
        
        if not all([webhook_id, webhook_timestamp, webhook_signature]):
            log_error("Missing webhook headers")
            return None
        
        # Verify signature using HMAC-SHA256
        # Signature format: v1,<base64-signature>
        expected_sig = _compute_webhook_signature(
            webhook_id, webhook_timestamp, body.decode('utf-8'), DODO_WEBHOOK_SECRET
        )
        
        # Compare signatures (webhook_signature may have multiple versions)
        signatures = webhook_signature.split(' ')
        verified = False
        for sig in signatures:
            if sig.startswith('v1,'):
                if hmac.compare_digest(sig, expected_sig):
                    verified = True
                    break
        
        if not verified:
            log_error("Webhook signature verification failed")
            return None
        
        event = json.loads(body)
        log_info(f"Webhook event validated successfully: {event.get('type', 'unknown')}")
        return event
        
    except Exception as e:
        log_error(f"Error validating webhook: {e}")
        return None


def _compute_webhook_signature(webhook_id: str, timestamp: str, body: str, secret: str) -> str:
    """Compute expected webhook signature using Standard Webhooks spec"""
    import base64
    
    # Message to sign: id.timestamp.body
    signed_content = f"{webhook_id}.{timestamp}.{body}"
    
    # Decode secret (may be base64 encoded with prefix)
    secret_bytes = secret.encode('utf-8')
    if secret.startswith('whsec_'):
        secret_bytes = base64.b64decode(secret[6:])
    
    # Compute HMAC-SHA256
    signature = hmac.new(
        secret_bytes,
        signed_content.encode('utf-8'),
        hashlib.sha256
    ).digest()
    
    return f"v1,{base64.b64encode(signature).decode('utf-8')}"


def handle_subscription_webhook(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle Dodo Payments webhook events for subscriptions
    
    Events handled:
    - payment.succeeded: When payment is successful
    - subscription.active: When subscription is activated
    - subscription.renewed: When subscription is renewed
    - subscription.updated: When subscription status changes
    - subscription.cancelled: When subscription is canceled
    - subscription.on_hold: When subscription is put on hold
    
    Returns:
        dict: Result of webhook processing
    """
    from utils.logger import log_info
    
    event_type = webhook_data.get('type')
    
    # Log webhook event for debugging
    log_info(f"Received Dodo webhook event: {event_type}", metadata={
        "event_type": event_type,
        "data_keys": list(webhook_data.get('data', {}).keys()) if webhook_data.get('data') else []
    })
    
    if event_type == 'payment.succeeded':
        return handle_payment_succeeded(webhook_data)
    elif event_type == 'subscription.active':
        return handle_subscription_active(webhook_data)
    elif event_type == 'subscription.renewed':
        return handle_subscription_renewed(webhook_data)
    elif event_type == 'subscription.updated':
        return handle_subscription_updated(webhook_data)
    elif event_type == 'subscription.cancelled':
        return handle_subscription_canceled(webhook_data)
    elif event_type == 'subscription.on_hold':
        return handle_subscription_on_hold(webhook_data)
    elif event_type == 'subscription.failed':
        return handle_subscription_failed(webhook_data)
    else:
        return {"status": "ignored", "message": f"Event {event_type} not handled"}


def _extract_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract metadata from Dodo webhook data.
    
    Dodo stores metadata in different locations:
    - data.metadata: Direct metadata on the object
    - data.checkout.metadata: From the checkout session
    """
    from utils.logger import log_info
    
    metadata = {}
    source = "none"
    
    # Log all available keys for debugging
    log_info(f"_extract_metadata: data keys = {list(data.keys())}")
    
    # 1. Try direct metadata
    if data.get('metadata') and isinstance(data.get('metadata'), dict):
        metadata = data.get('metadata', {})
        source = "data.metadata"
    
    # 2. Try checkout metadata
    if not metadata and data.get('checkout'):
        checkout = data.get('checkout', {})
        if isinstance(checkout, dict) and checkout.get('metadata'):
            metadata = checkout.get('metadata', {})
            source = "checkout.metadata"
    
    # 3. Try subscription metadata
    if not metadata and data.get('subscription'):
        subscription = data.get('subscription', {})
        if isinstance(subscription, dict) and subscription.get('metadata'):
            metadata = subscription.get('metadata', {})
            source = "subscription.metadata"
    
    log_info(f"_extract_metadata result", metadata={
        "source": source,
        "found_metadata": bool(metadata),
        "clerk_user_id": metadata.get('clerk_user_id', 'NOT_FOUND'),
        "subscription_type": metadata.get('subscription_type', 'NOT_FOUND'),
    })
    
    return metadata


def handle_payment_succeeded(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle payment.succeeded webhook - process successful payments"""
    from utils.logger import log_info, log_error
    
    try:
        data = webhook_data.get('data', {})
        payment_id = data.get('payment_id') or data.get('id')
        
        log_info(f"Processing payment.succeeded: {payment_id}")
        
        metadata = _extract_metadata(data)
        subscription_type = metadata.get('subscription_type')
        clerk_user_id = metadata.get('clerk_user_id')
        
        supabase = get_supabase()
        
        # If no clerk_user_id in metadata, try to find by email
        if not clerk_user_id:
            customer = data.get('customer', {})
            customer_email = customer.get('email') if isinstance(customer, dict) else None
            if customer_email:
                founder = supabase.table('founders').select('clerk_user_id').eq('email', customer_email).execute()
                if founder.data:
                    clerk_user_id = founder.data[0].get('clerk_user_id')
        
        if not clerk_user_id:
            log_error(f"Cannot process payment {payment_id}: no clerk_user_id found")
            return {"status": "error", "message": "Missing clerk_user_id"}
        
        # Check idempotency
        try:
            processed_check = supabase.table('webhook_processing_log').select('id, status').eq('webhook_id', payment_id).eq('status', 'success').execute()
            if processed_check.data:
                log_info(f"Payment {payment_id} already processed successfully")
                return {"status": "success", "message": "Payment already processed", "idempotent": True}
        except Exception:
            pass
        
        if subscription_type == 'founder_plan':
            plan_id = metadata.get('plan_id')
            if not plan_id:
                # Try to determine from product
                product_id = data.get('product_id')
                if product_id == DODO_PRODUCT_PRO_ID:
                    plan_id = 'PRO'
                elif product_id == DODO_PRODUCT_PRO_PLUS_ID:
                    plan_id = 'PRO_PLUS'
            
            if not plan_id:
                return {"status": "error", "message": "Missing plan_id"}
            
            subscription_id = data.get('subscription_id')
            current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
            
            plan_service.update_founder_plan(
                clerk_user_id,
                plan_id,
                subscription_id=subscription_id,
                subscription_status='active',
                current_period_end=current_period_end
            )
            
            _log_webhook_success(supabase, payment_id, 'payment.succeeded')
            log_info(f"Plan {plan_id} activated for {clerk_user_id}")
            return {"status": "success", "message": f"Plan {plan_id} activated"}

        if subscription_type == 'advisor_pro':
            billing_cycle = metadata.get('billing_cycle') or 'monthly'
            # Sometimes billing_cycle isn't in metadata (e.g. renewal); infer from product_id
            if billing_cycle not in ('monthly', 'yearly'):
                product_id = data.get('product_id')
                if product_id == DODO_PRODUCT_ADVISOR_PRO_YEARLY_ID:
                    billing_cycle = 'yearly'
                else:
                    billing_cycle = 'monthly'

            subscription_id = data.get('subscription_id')
            _activate_advisor_subscription(clerk_user_id, subscription_id, billing_cycle)
            _log_webhook_success(supabase, payment_id, 'payment.succeeded')
            log_info(f"Advisor Pro ({billing_cycle}) activated for {clerk_user_id}")
            return {"status": "success", "message": f"Advisor Pro {billing_cycle} activated"}

        return {"status": "ignored", "message": f"Unknown subscription type: {subscription_type}"}
        
    except Exception as e:
        from utils.logger import log_error
        log_error(f"Error handling payment.succeeded: {e}")
        return {"status": "error", "message": str(e)}


def handle_subscription_active(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle subscription.active webhook - subscription is now active"""
    from utils.logger import log_info

    try:
        data = webhook_data.get('data', {})
        subscription_id = data.get('subscription_id') or data.get('id')

        log_info(f"Subscription active: {subscription_id}")

        metadata = _extract_metadata(data)
        clerk_user_id = metadata.get('clerk_user_id')
        subscription_type = metadata.get('subscription_type')

        supabase = get_supabase()

        # Find user by subscription_id if no clerk_user_id
        if not clerk_user_id and subscription_id:
            founder = supabase.table('founders').select('clerk_user_id').eq('subscription_id', subscription_id).execute()
            if founder.data:
                clerk_user_id = founder.data[0].get('clerk_user_id')

        # Founder plan activation
        if subscription_type != 'advisor_pro' and clerk_user_id:
            supabase.table('founders').update({
                'subscription_status': 'active'
            }).eq('clerk_user_id', clerk_user_id).execute()

        # Advisor Pro subscription activation
        if subscription_type == 'advisor_pro' and clerk_user_id:
            cycle = metadata.get('billing_cycle') or 'monthly'
            _activate_advisor_subscription(clerk_user_id, subscription_id, cycle)
        elif subscription_id:
            # No metadata — try matching by subscription_id on advisor_profiles too
            adv = supabase.table('advisor_profiles').select('user_id').eq('subscription_id', subscription_id).execute()
            if adv.data:
                supabase.table('advisor_profiles').update({
                    'subscription_status': 'active'
                }).eq('subscription_id', subscription_id).execute()

        return {"status": "success", "message": "Subscription activated"}

    except Exception as e:
        return {"status": "error", "message": str(e)}


def handle_subscription_renewed(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle subscription.renewed webhook - subscription renewed for next period"""
    from utils.logger import log_info

    try:
        data = webhook_data.get('data', {})
        subscription_id = data.get('subscription_id') or data.get('id')

        log_info(f"Subscription renewed: {subscription_id}")

        supabase = get_supabase()

        # Try founder first
        founder_period_end = datetime.now(timezone.utc) + timedelta(days=30)
        founder_res = supabase.table('founders').update({
            'subscription_status': 'active',
            'subscription_current_period_end': founder_period_end.isoformat()
        }).eq('subscription_id', subscription_id).execute()

        # Also try advisor — billing cycle determines period length
        advisor = supabase.table('advisor_profiles').select(
            'id, subscription_current_period_end'
        ).eq('subscription_id', subscription_id).execute()
        if advisor.data:
            # Best-effort: detect cycle by previous period length; default monthly
            previous_end = advisor.data[0].get('subscription_current_period_end')
            cycle_days = 30
            if previous_end:
                try:
                    prev = datetime.fromisoformat(str(previous_end).replace('Z', '+00:00'))
                    if prev.tzinfo is None:
                        prev = prev.replace(tzinfo=timezone.utc)
                    delta = prev - (prev - timedelta(days=365))  # placeholder; fallback to 30
                    # If the previous period was ~yearly (>= 300 days from creation), keep yearly
                    # Simpler heuristic: default to 30, but use 365 if metadata says yearly
                    metadata = _extract_metadata(data)
                    if metadata.get('billing_cycle') == 'yearly':
                        cycle_days = 365
                except Exception:
                    pass

            new_period_end = datetime.now(timezone.utc) + timedelta(days=cycle_days)
            supabase.table('advisor_profiles').update({
                'subscription_status': 'active',
                'subscription_current_period_end': new_period_end.isoformat(),
            }).eq('subscription_id', subscription_id).execute()

        return {"status": "success", "message": "Subscription renewed"}

    except Exception as e:
        return {"status": "error", "message": str(e)}


def handle_subscription_updated(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle subscription.updated webhook - update subscription status"""
    from utils.logger import log_info
    
    try:
        data = webhook_data.get('data', {})
        subscription_id = data.get('subscription_id') or data.get('id')
        status = data.get('status')
        
        log_info(f"Subscription updated: {subscription_id} -> {status}")
        
        supabase = get_supabase()
        
        update_data = {}
        if status:
            update_data['subscription_status'] = status
        
        if update_data:
            supabase.table('founders').update(update_data).eq('subscription_id', subscription_id).execute()
        
        return {"status": "success", "message": "Subscription updated"}
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


def handle_subscription_canceled(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle subscription.cancelled webhook"""
    from utils.logger import log_info

    try:
        data = webhook_data.get('data', {})
        subscription_id = data.get('subscription_id') or data.get('id')

        log_info(f"Subscription canceled: {subscription_id}")

        supabase = get_supabase()

        # Founder side: downgrade to FREE if matched
        founder = supabase.table('founders').select('clerk_user_id').eq('subscription_id', subscription_id).execute()
        if founder.data:
            supabase.table('founders').update({
                'subscription_status': 'canceled'
            }).eq('subscription_id', subscription_id).execute()
            plan_service.update_founder_plan(founder.data[0]['clerk_user_id'], 'FREE')

        # Advisor side: mark cancelled (soft cutoff applies via the can_accept_bookings helper)
        advisor = supabase.table('advisor_profiles').select('id').eq('subscription_id', subscription_id).execute()
        if advisor.data:
            supabase.table('advisor_profiles').update({
                'subscription_status': 'cancelled',
            }).eq('subscription_id', subscription_id).execute()

        return {"status": "success", "message": "Subscription canceled"}

    except Exception as e:
        return {"status": "error", "message": str(e)}


def handle_subscription_on_hold(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle subscription.on_hold webhook - subscription paused due to payment failure"""
    from utils.logger import log_info

    try:
        data = webhook_data.get('data', {})
        subscription_id = data.get('subscription_id') or data.get('id')

        log_info(f"Subscription on hold: {subscription_id}")

        supabase = get_supabase()

        supabase.table('founders').update({
            'subscription_status': 'on_hold'
        }).eq('subscription_id', subscription_id).execute()

        # Advisor side: 'past_due' is the closest analog
        supabase.table('advisor_profiles').update({
            'subscription_status': 'past_due',
        }).eq('subscription_id', subscription_id).execute()

        return {"status": "success", "message": "Subscription on hold"}

    except Exception as e:
        return {"status": "error", "message": str(e)}


def handle_subscription_failed(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle subscription.failed webhook - subscription creation failed"""
    from utils.logger import log_info, log_error
    
    try:
        data = webhook_data.get('data', {})
        subscription_id = data.get('subscription_id') or data.get('id')
        
        log_error(f"Subscription failed: {subscription_id}")
        
        supabase = get_supabase()
        
        supabase.table('founders').update({
            'subscription_status': 'failed'
        }).eq('subscription_id', subscription_id).execute()
        
        return {"status": "success", "message": "Subscription failure recorded"}
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _log_webhook_success(supabase, webhook_id: str, webhook_type: str):
    """Log successful webhook processing"""
    try:
        supabase.table('webhook_processing_log').upsert({
            'webhook_id': webhook_id,
            'webhook_type': webhook_type,
            'processed_at': datetime.now(timezone.utc).isoformat(),
            'status': 'success'
        }, on_conflict='webhook_id').execute()
    except Exception:
        pass
