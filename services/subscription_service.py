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

def create_advisor_project_accept_checkout(clerk_user_id: str, request_id: str) -> Dict[str, str]:
    """
    Create a Polar checkout session for advisor to accept a project.
    This is a per-project fee charged each time an advisor accepts a project.
    
    Args:
        clerk_user_id: The Clerk user ID
        request_id: The advisor request ID being accepted
    
    Returns:
        dict: Checkout session data with checkout_url
    """
    # Use the same product as onboarding (same $69 fee)
    if not POLAR_ACCESS_TOKEN or not POLAR_PRODUCT_ADVISOR_ONBOARDING_ID:
        raise ValueError("Polar API or product ID not configured for advisor project accept.")
    
    supabase = get_supabase()
    
    # Get email from founders table
    profile = supabase.table('founders').select('id, email, name').eq('clerk_user_id', clerk_user_id).execute()
    
    if not profile.data:
        raise ValueError("Profile not found. Please complete your advisor registration first.")
    
    user_email = profile.data[0].get('email', '').strip()
    
    # If email is missing, try to get from Clerk
    if not user_email:
        try:
            clerk_email = get_clerk_user_email(clerk_user_id)
            if clerk_email and clerk_email.strip():
                user_email = clerk_email.strip()
                founder_id = profile.data[0].get('id')
                supabase.table('founders').update({'email': user_email}).eq('id', founder_id).execute()
        except Exception:
            pass
    
    if not user_email:
        raise ValueError("Email address is required for checkout.")
    
    # Validate email format
    import re
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, user_email):
        raise ValueError(f"Invalid email address format: {user_email}")
    
    try:
        with Polar(access_token=POLAR_ACCESS_TOKEN) as polar:
            res = polar.checkouts.create(request={
                "products": [POLAR_PRODUCT_ADVISOR_ONBOARDING_ID],
                "success_url": f"{FRONTEND_URL}/advisor/dashboard?payment=success&request_id={request_id}",
                "customer_email": user_email,
                "customer_metadata": {
                    "clerk_user_id": clerk_user_id,
                    "subscription_type": "advisor_project_accept",
                    "request_id": request_id
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
    - checkout.updated: When checkout status changes (including completion)
    - order.created: When an order is created (may still be pending)
    - order.paid: When an order payment is confirmed - activate subscription
    - subscription.created: When a subscription is created
    - subscription.updated: When subscription status changes
    - subscription.canceled: When subscription is canceled
    
    Returns:
        dict: Result of webhook processing
    """
    from utils.logger import log_info
    
    event_type = webhook_data.get('type')
    
    # Log webhook event for debugging
    log_info(f"Received webhook event: {event_type}", metadata={
        "event_type": event_type,
        "data_keys": list(webhook_data.get('data', {}).keys()) if webhook_data.get('data') else []
    })
    
    if event_type == 'checkout.created':
        return handle_checkout_created(webhook_data)
    elif event_type == 'checkout.updated':
        return handle_checkout_updated(webhook_data)
    elif event_type == 'order.created':
        return handle_order_created(webhook_data)
    elif event_type == 'order.paid':
        # order.paid is the definitive payment confirmation
        return handle_order_paid(webhook_data)
    elif event_type == 'subscription.created':
        return handle_subscription_created(webhook_data)
    elif event_type == 'subscription.updated':
        return handle_subscription_updated(webhook_data)
    elif event_type == 'subscription.canceled':
        return handle_subscription_canceled(webhook_data)
    else:
        return {"status": "ignored", "message": f"Event {event_type} not handled"}

def _extract_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract customer metadata from webhook data.
    Polar stores metadata in different locations depending on the event type:
    - checkout events: data.customer_metadata or data.metadata
    - order events: data.customer.metadata or data.metadata
    - subscription events: data.customer.metadata or data.metadata
    """
    from utils.logger import log_info
    
    metadata = {}
    
    # Try direct metadata field first
    if data.get('metadata'):
        metadata = data.get('metadata', {})
    
    # Try customer_metadata (used in checkout creation)
    if not metadata and data.get('customer_metadata'):
        metadata = data.get('customer_metadata', {})
    
    # Try nested customer.metadata
    if not metadata and data.get('customer'):
        customer = data.get('customer', {})
        if customer.get('metadata'):
            metadata = customer.get('metadata', {})
    
    # Try checkout field (for orders linked to checkouts)
    if not metadata and data.get('checkout'):
        checkout = data.get('checkout', {})
        if checkout.get('customer_metadata'):
            metadata = checkout.get('customer_metadata', {})
        elif checkout.get('metadata'):
            metadata = checkout.get('metadata', {})
    
    # Log where we found metadata for debugging
    log_info(f"Extracted metadata", metadata={
        "found_metadata": bool(metadata),
        "metadata_keys": list(metadata.keys()) if metadata else [],
        "data_keys": list(data.keys())
    })
    
    return metadata


def handle_checkout_created(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle checkout.created webhook - store checkout info"""
    try:
        data = webhook_data.get('data', {})
        metadata = _extract_metadata(data)
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


def handle_checkout_updated(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle checkout.updated webhook - process completed checkouts"""
    from utils.logger import log_info
    
    try:
        data = webhook_data.get('data', {})
        checkout_id = data.get('id')
        status = data.get('status')
        
        log_info(f"Checkout updated: {checkout_id} -> {status}")
        
        # Only process succeeded checkouts
        if status != 'succeeded':
            return {"status": "ignored", "message": f"Checkout status {status} - not processing"}
        
        metadata = _extract_metadata(data)
        subscription_type = metadata.get('subscription_type')
        clerk_user_id = metadata.get('clerk_user_id')
        
        if not clerk_user_id:
            log_info(f"No clerk_user_id in checkout metadata, checking customer email")
            # Try to find user by email
            customer_email = data.get('customer_email') or data.get('customer', {}).get('email')
            if customer_email:
                supabase = get_supabase()
                founder = supabase.table('founders').select('clerk_user_id').eq('email', customer_email).execute()
                if founder.data:
                    clerk_user_id = founder.data[0].get('clerk_user_id')
        
        if not clerk_user_id:
            return {"status": "error", "message": "Could not identify user from checkout"}
        
        supabase = get_supabase()
        
        if subscription_type == 'founder_plan':
            plan_id = metadata.get('plan_id')
            if not plan_id:
                return {"status": "error", "message": "Missing plan_id in checkout"}
            
            # Activate the plan
            current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
            
            plan_service.update_founder_plan(
                clerk_user_id,
                plan_id,
                subscription_id=data.get('subscription_id'),
                subscription_status='active',
                current_period_end=current_period_end
            )
            
            # Update checkout status
            try:
                supabase.table('subscription_checkouts').update({
                    'status': 'completed'
                }).eq('checkout_id', checkout_id).execute()
            except Exception:
                pass
            
            log_info(f"Plan activated via checkout.updated: {plan_id} for {clerk_user_id}")
            return {"status": "success", "message": f"Plan {plan_id} activated via checkout"}
            
        elif subscription_type == 'advisor_onboarding':
            plan_service.update_advisor_billing(clerk_user_id, onboarding_paid=True)
            log_info(f"Advisor onboarding activated via checkout.updated for {clerk_user_id}")
            return {"status": "success", "message": "Advisor onboarding paid via checkout"}
            
        elif subscription_type == 'advisor_renewal':
            plan_service.renew_advisor_subscription(clerk_user_id)
            log_info(f"Advisor renewal activated via checkout.updated for {clerk_user_id}")
            return {"status": "success", "message": "Advisor subscription renewed via checkout"}
            
        elif subscription_type == 'advisor_project_accept':
            request_id = metadata.get('request_id')
            if request_id:
                try:
                    supabase.table('advisor_project_payments').insert({
                        'clerk_user_id': clerk_user_id,
                        'request_id': request_id,
                        'order_id': checkout_id,
                        'paid_at': datetime.now(timezone.utc).isoformat(),
                        'amount_usd': 69
                    }).execute()
                except Exception:
                    pass
                log_info(f"Advisor project accept payment recorded via checkout.updated for {clerk_user_id}")
                return {"status": "success", "message": f"Project accept payment recorded for request {request_id}"}
        
        return {"status": "ignored", "message": f"Unknown subscription type: {subscription_type}"}
    except Exception as e:
        from utils.logger import log_error
        log_error(f"Error handling checkout.updated: {e}")
        return {"status": "error", "message": str(e)}

def handle_order_created(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle order.created webhook.
    
    NOTE: order.created may arrive with status 'pending' (not yet paid).
    For payment confirmation, we now primarily rely on order.paid event.
    This handler now only logs the order and waits for order.paid to activate.
    """
    from utils.logger import log_info
    
    try:
        data = webhook_data.get('data', {})
        order_id = data.get('id')
        order_status = data.get('status')
        is_paid = data.get('paid', False)
        
        log_info(f"Order created: {order_id}, status: {order_status}, paid: {is_paid}")
        
        # If order is already paid (sometimes Polar sends it this way), process it
        if is_paid or order_status == 'paid':
            log_info(f"Order {order_id} is already paid, processing immediately")
            return _process_paid_order(webhook_data, 'order.created')
        
        # Otherwise, just log and wait for order.paid event
        metadata = _extract_metadata(data)
        clerk_user_id = metadata.get('clerk_user_id')
        subscription_type = metadata.get('subscription_type')
        
        supabase = get_supabase()
        
        # Store pending order for tracking
        try:
            supabase.table('webhook_processing_log').insert({
                'webhook_id': order_id,
                'webhook_type': 'order.created',
                'processed_at': datetime.now(timezone.utc).isoformat(),
                'status': 'pending',
                'error_message': f"Waiting for payment. clerk_user_id: {clerk_user_id}, type: {subscription_type}"
            }).execute()
        except Exception:
            pass
        
        return {"status": "success", "message": f"Order {order_id} created, waiting for payment confirmation"}
        
    except Exception as e:
        from utils.logger import log_error
        log_error(f"Error handling order.created: {e}")
        return {"status": "error", "message": str(e)}


def handle_order_paid(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle order.paid webhook - this is the definitive payment confirmation.
    Activates subscriptions and processes payments here.
    """
    from utils.logger import log_info
    
    log_info("Processing order.paid webhook")
    return _process_paid_order(webhook_data, 'order.paid')


def _process_paid_order(webhook_data: Dict[str, Any], event_type: str) -> Dict[str, Any]:
    """
    Common handler for processing a paid order.
    Called by both order.created (if already paid) and order.paid events.
    """
    from utils.logger import log_info, log_error
    
    try:
        data = webhook_data.get('data', {})
        order_id = data.get('id')
        
        if not order_id:
            return {"status": "error", "message": "Missing order ID"}
        
        supabase = get_supabase()
        
        # Check if this order was already processed (idempotency)
        try:
            processed_check = supabase.table('webhook_processing_log').select('id, status').eq('webhook_id', order_id).eq('status', 'success').execute()
            if processed_check.data:
                log_info(f"Order {order_id} already processed successfully")
                return {"status": "success", "message": "Order already processed", "idempotent": True}
        except Exception:
            pass
        
        # Extract metadata using the robust extractor
        metadata = _extract_metadata(data)
        subscription_type = metadata.get('subscription_type')
        clerk_user_id = metadata.get('clerk_user_id')
        
        log_info(f"Processing paid order: {order_id}", metadata={
            "subscription_type": subscription_type,
            "clerk_user_id": clerk_user_id,
            "has_metadata": bool(metadata)
        })
        
        # If no clerk_user_id in metadata, try to find by email
        if not clerk_user_id:
            log_info("No clerk_user_id in metadata, attempting lookup by email")
            customer_email = data.get('customer_email') or (data.get('customer', {}) or {}).get('email')
            if customer_email:
                founder = supabase.table('founders').select('clerk_user_id').eq('email', customer_email).execute()
                if founder.data:
                    clerk_user_id = founder.data[0].get('clerk_user_id')
                    log_info(f"Found clerk_user_id by email: {clerk_user_id}")
        
        if not clerk_user_id:
            log_error(f"Cannot process order {order_id}: no clerk_user_id found")
            return {"status": "error", "message": "Missing clerk_user_id - cannot identify user"}
        
        # Try to determine subscription_type from product if not in metadata
        if not subscription_type:
            log_info("No subscription_type in metadata, checking product")
            # Check product IDs to determine type
            items = data.get('items', []) or data.get('line_items', [])
            for item in items:
                product_id = item.get('product_id') or (item.get('product', {}) or {}).get('id')
                if product_id == POLAR_PRODUCT_PRO_ID:
                    subscription_type = 'founder_plan'
                    metadata['plan_id'] = 'PRO'
                    break
                elif product_id == POLAR_PRODUCT_PRO_PLUS_ID:
                    subscription_type = 'founder_plan'
                    metadata['plan_id'] = 'PRO_PLUS'
                    break
                elif product_id == POLAR_PRODUCT_ADVISOR_ONBOARDING_ID:
                    subscription_type = 'advisor_onboarding'
                    break
                elif product_id == POLAR_PRODUCT_ADVISOR_RENEWAL_ID:
                    subscription_type = 'advisor_renewal'
                    break
            
            log_info(f"Determined subscription_type from product: {subscription_type}")
        
        if subscription_type == 'founder_plan':
            plan_id = metadata.get('plan_id')
            if not plan_id:
                log_error(f"Missing plan_id for order {order_id}")
                return {"status": "error", "message": "Missing plan_id"}
            
            # Get subscription period from webhook data
            current_period_end = None
            if data.get('current_period_end'):
                period_end_value = data.get('current_period_end')
                if isinstance(period_end_value, (int, float)):
                    current_period_end = datetime.fromtimestamp(period_end_value, tz=timezone.utc)
                elif isinstance(period_end_value, str):
                    current_period_end = datetime.fromisoformat(period_end_value.replace('Z', '+00:00'))
            
            # Also try subscription object for period info
            subscription = data.get('subscription', {})
            if not current_period_end and subscription.get('current_period_end'):
                period_end_value = subscription.get('current_period_end')
                if isinstance(period_end_value, (int, float)):
                    current_period_end = datetime.fromtimestamp(period_end_value, tz=timezone.utc)
                elif isinstance(period_end_value, str):
                    current_period_end = datetime.fromisoformat(period_end_value.replace('Z', '+00:00'))
            
            if not current_period_end:
                current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
            
            # Get subscription_id
            subscription_id = data.get('subscription_id') or subscription.get('id')
            
            # Update user's plan
            plan_service.update_founder_plan(
                clerk_user_id,
                plan_id,
                subscription_id=subscription_id,
                subscription_status='active',
                current_period_end=current_period_end
            )
            
            # Log successful processing
            try:
                supabase.table('webhook_processing_log').upsert({
                    'webhook_id': order_id,
                    'webhook_type': event_type,
                    'processed_at': datetime.now(timezone.utc).isoformat(),
                    'status': 'success'
                }, on_conflict='webhook_id').execute()
            except Exception:
                pass
            
            log_info(f"Plan {plan_id} activated for {clerk_user_id}")
            return {"status": "success", "message": f"Plan {plan_id} activated"}
            
        elif subscription_type == 'advisor_onboarding':
            plan_service.update_advisor_billing(clerk_user_id, onboarding_paid=True)
            
            try:
                supabase.table('webhook_processing_log').upsert({
                    'webhook_id': order_id,
                    'webhook_type': event_type,
                    'processed_at': datetime.now(timezone.utc).isoformat(),
                    'status': 'success'
                }, on_conflict='webhook_id').execute()
            except Exception:
                pass
            
            log_info(f"Advisor onboarding paid for {clerk_user_id}")
            return {"status": "success", "message": "Advisor onboarding paid"}
            
        elif subscription_type == 'advisor_renewal':
            plan_service.renew_advisor_subscription(clerk_user_id)
            
            try:
                supabase.table('webhook_processing_log').upsert({
                    'webhook_id': order_id,
                    'webhook_type': event_type,
                    'processed_at': datetime.now(timezone.utc).isoformat(),
                    'status': 'success'
                }, on_conflict='webhook_id').execute()
            except Exception:
                pass
            
            log_info(f"Advisor subscription renewed for {clerk_user_id}")
            return {"status": "success", "message": "Advisor subscription renewed"}
        
        elif subscription_type == 'advisor_project_accept':
            request_id = metadata.get('request_id')
            if not request_id:
                return {"status": "error", "message": "Missing request_id for project accept"}
            
            try:
                supabase.table('advisor_project_payments').insert({
                    'clerk_user_id': clerk_user_id,
                    'request_id': request_id,
                    'order_id': order_id,
                    'paid_at': datetime.now(timezone.utc).isoformat(),
                    'amount_usd': 69
                }).execute()
            except Exception:
                pass
            
            try:
                supabase.table('webhook_processing_log').upsert({
                    'webhook_id': order_id,
                    'webhook_type': event_type,
                    'processed_at': datetime.now(timezone.utc).isoformat(),
                    'status': 'success'
                }, on_conflict='webhook_id').execute()
            except Exception:
                pass
            
            log_info(f"Project accept payment recorded for {clerk_user_id}, request {request_id}")
            return {"status": "success", "message": f"Project accept payment recorded for request {request_id}"}
        
        log_info(f"Unknown or missing subscription_type: {subscription_type}")
        return {"status": "ignored", "message": f"Unknown subscription type: {subscription_type}"}
        
    except Exception as e:
        from utils.logger import log_error
        log_error(f"Error processing paid order: {e}")
        try:
            supabase = get_supabase()
            supabase.table('webhook_processing_log').upsert({
                'webhook_id': webhook_data.get('data', {}).get('id', 'unknown'),
                'webhook_type': event_type,
                'processed_at': datetime.now(timezone.utc).isoformat(),
                'status': 'error',
                'error_message': str(e)
            }, on_conflict='webhook_id').execute()
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

