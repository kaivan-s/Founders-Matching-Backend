"""Payment service for Polar integration"""
import os
import hmac
import hashlib
import traceback
from config.database import get_supabase
from polar_sdk import Polar

# Polar API configuration
POLAR_ACCESS_TOKEN = os.getenv('POLAR_ACCESS_TOKEN')

def create_checkout_session(clerk_user_id, product_id, credits_amount):
    """
    DEPRECATED: Credit purchase system has been removed in favor of plan-based features.
    This function is kept for backward compatibility but should not be used for new integrations.
    Use subscription_service for plan subscriptions instead.
    
    Args:
        clerk_user_id: The Clerk user ID
        product_id: The Polar product ID for the credit package (deprecated)
        credits_amount: The number of credits being purchased (deprecated, ignored)
    
    Returns:
        dict: Checkout session data with checkout_url
    """
    if not POLAR_ACCESS_TOKEN:
        raise ValueError("Polar API not configured. Please set POLAR_ACCESS_TOKEN.")
    
    # Get user's email from profile for checkout
    supabase = get_supabase()
    profile = supabase.table('founders').select('email, name').eq('clerk_user_id', clerk_user_id).execute()
    
    if not profile.data:
        raise ValueError("Profile not found")
    
    user_email = profile.data[0].get('email')
    user_name = profile.data[0].get('name', '')
    
    try:
        # Create checkout session using Polar SDK
        with Polar(access_token=POLAR_ACCESS_TOKEN) as polar:
            res = polar.checkouts.create(request={
                "products": [product_id],
                "success_url": f"{os.getenv('FRONTEND_URL', 'http://localhost:3000')}?purchase=success",
                "customer_email": user_email,
                "customer_metadata": {
                        "clerk_user_id": clerk_user_id,
                    "credits_amount": str(credits_amount)
                    }
            })
            
            return {
                "checkout_url": res.url,
                "checkout_id": res.id
                    }
        
    except Exception as e:
        error_msg = str(e)
        raise ValueError(f"Failed to create checkout session: {error_msg}")

def verify_webhook_signature(payload, signature):
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

def handle_webhook(webhook_data):
    """
    DEPRECATED: Handle Polar webhook events for legacy credit purchases.
    Credits system has been removed - this is kept for backward compatibility.
    Webhooks will process payments but will NOT add credits.
    
    Events handled:
    - checkout.created: When a checkout is created (legacy)
    - order.created: When an order is created and paid (legacy - no credits added)
    
    Returns:
        dict: Result of webhook processing
    """
    event_type = webhook_data.get('type')
    
    if event_type == 'checkout.created':
        return handle_checkout_created(webhook_data)
    elif event_type == 'order.created':
        return handle_order_created(webhook_data)
    else:
        return {"status": "ignored", "message": f"Event {event_type} not handled"}

def handle_checkout_created(webhook_data):
    """DEPRECATED: Handle checkout.created webhook for legacy credit purchases.
    Credits system removed - payment is recorded but no credits are added."""
    try:
        # Extract checkout data from webhook
        data = webhook_data.get('data', {})
        checkout_id = data.get('id')
        metadata = data.get('metadata', {})
        clerk_user_id = metadata.get('clerk_user_id')
        
        if not clerk_user_id:
            return {"status": "error", "message": "Missing clerk_user_id"}
        
        # Get payment details
        product_id = data.get('product_id')
        amount = data.get('amount', 0)
        currency = data.get('currency', 'USD')
        customer_email = data.get('customer_email')
        
        supabase = get_supabase()
        
        # Get user's founder profile (no credits needed)
        profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
        
        if not profile.data:
            return {"status": "error", "message": "User profile not found"}
        
        founder_id = profile.data[0]['id']
        
        # Store payment record (for history) but don't add credits
        payment_data = {
            'clerk_user_id': clerk_user_id,
            'founder_id': founder_id,
            'polar_checkout_id': checkout_id,
            'polar_order_id': None,
            'product_id': product_id,
            'credits_amount': 0,  # No credits system
            'amount_paid': float(amount) / 100 if amount else None,
            'currency': currency,
            'status': 'succeeded',
            'customer_email': customer_email,
            'metadata': {'legacy_credit_purchase': True, 'credits_added': False},
            'webhook_data': webhook_data
        }
        
        # Check if payment record already exists
        existing_payment = supabase.table('payments').select('id').eq('polar_checkout_id', checkout_id).execute()
        
        if not existing_payment.data:
            # Create payment record
            supabase.table('payments').insert(payment_data).execute()
        
        return {
            "status": "success",
            "message": "Payment processed (legacy credit purchase - no credits added)",
            "clerk_user_id": clerk_user_id,
            "checkout_id": checkout_id
        }
        
    except Exception as e:
        error_trace = traceback.format_exc()
        return {"status": "error", "message": str(e)}

def handle_order_created(webhook_data):
    """DEPRECATED: Handle order.created webhook for legacy credit purchases.
    Credits system removed - payment is recorded but no credits are added."""
    try:
        # Extract order data
        data = webhook_data.get('data', {})
        order_id = data.get('id')
        product_id = data.get('product_id')
        amount = data.get('amount', 0)
        currency = data.get('currency', 'USD')
        
        # Get custom metadata from order
        metadata = data.get('metadata', {})
        clerk_user_id = metadata.get('clerk_user_id')
        
        if not clerk_user_id:
            return {"status": "error", "message": "Missing clerk_user_id in order"}
        
        customer_email = data.get('customer', {}).get('email') if isinstance(data.get('customer'), dict) else None
        
        supabase = get_supabase()
        
        # Get user's founder profile (no credits needed)
        profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
        
        if not profile.data:
            return {"status": "error", "message": "User profile not found"}
        
        founder_id = profile.data[0]['id']
        
        # Check if order already processed
        existing_payment = supabase.table('payments').select('id').eq('polar_order_id', order_id).execute()
        
        if existing_payment.data:
            return {"status": "success", "message": "Order already processed (legacy - no credits)"}
        
        # Create payment record (for history) but don't add credits
        payment_data = {
            'clerk_user_id': clerk_user_id,
            'founder_id': founder_id,
            'polar_order_id': order_id,
            'polar_checkout_id': data.get('checkout', {}).get('id') if isinstance(data.get('checkout'), dict) else None,
            'product_id': product_id,
            'credits_amount': 0,  # No credits system
            'amount_paid': float(amount) / 100 if amount else None,
            'currency': currency,
            'status': 'succeeded',
            'customer_email': customer_email,
            'metadata': {'legacy_credit_purchase': True, 'credits_added': False},
            'webhook_data': webhook_data
        }
        
        supabase.table('payments').insert(payment_data).execute()
        
        return {
            "status": "success",
            "message": "Payment processed (legacy credit purchase - no credits added)",
            "clerk_user_id": clerk_user_id,
            "order_id": order_id
        }
    except Exception as e:
        error_trace = traceback.format_exc()
        return {"status": "error", "message": str(e)}

def get_payment_history(clerk_user_id):
    """
    Get payment history for a user
    
    Args:
        clerk_user_id: The Clerk user ID
    
    Returns:
        list: Payment records ordered by most recent first
    """
    supabase = get_supabase()
    
    payments = supabase.table('payments').select('*').eq('clerk_user_id', clerk_user_id).order('created_at', desc=True).execute()
    
    if not payments.data:
        return []
    
    return payments.data

def get_payment_by_checkout_id(checkout_id):
    """
    Get a payment record by Polar checkout ID
    
    Args:
        checkout_id: The Polar checkout ID
    
    Returns:
        dict: Payment record or None
    """
    supabase = get_supabase()
    
    payment = supabase.table('payments').select('*').eq('polar_checkout_id', checkout_id).execute()
    
    if payment.data:
        return payment.data[0]
    return None

# DEPRECATED: add_credits_manually function removed - credits system no longer exists
# Use plan_service.update_founder_plan() for plan upgrades instead
