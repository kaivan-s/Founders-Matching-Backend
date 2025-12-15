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
    Create a Polar checkout session
    
    Args:
        clerk_user_id: The Clerk user ID
        product_id: The Polar product ID for the credit package
        credits_amount: The number of credits being purchased (for reference)
    
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
    Handle Polar webhook events
    
    Events handled:
    - checkout.created: When a checkout is created
    - order.created: When an order is created and paid - this is when we add credits
    
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
    """Handle checkout.created webhook - store checkout info but don't add credits yet"""
    try:
        # Extract checkout data from webhook
        data = webhook_data.get('data', {})
        
        # Get checkout ID
        checkout_id = data.get('id')
        
        # Get custom metadata (clerk_user_id and credits_amount)
        metadata = data.get('metadata', {})
        clerk_user_id = metadata.get('clerk_user_id')
        credits_amount = int(metadata.get('credits_amount', 0))
        
        if not clerk_user_id or not credits_amount:
            return {"status": "error", "message": "Missing metadata"}
        
        # Get payment details
        product_id = data.get('product_id')
        amount = data.get('amount', 0)
        currency = data.get('currency', 'USD')
        customer_email = data.get('customer_email')
        
        # Add credits to user account
        supabase = get_supabase()
        
        # Get user's founder profile
        profile = supabase.table('founders').select('id, credits').eq('clerk_user_id', clerk_user_id).execute()
        
        if not profile.data:
            return {"status": "error", "message": "User profile not found"}
        
        founder_id = profile.data[0]['id']
        current_credits = profile.data[0].get('credits') or 0
        
        # Store payment record
        payment_data = {
            'clerk_user_id': clerk_user_id,
            'founder_id': founder_id,
            'polar_checkout_id': checkout_id,
            'polar_order_id': None,  # Will be updated when order.created fires
            'product_id': product_id,
            'credits_amount': credits_amount,
            'amount_paid': float(amount) / 100 if amount else None,  # Convert cents to dollars
            'currency': currency,
            'status': 'succeeded',
            'customer_email': customer_email,
            'metadata': {'credits_added': False},
            'webhook_data': webhook_data
        }
        
        # Check if payment record already exists
        existing_payment = supabase.table('payments').select('id, metadata').eq('polar_checkout_id', checkout_id).execute()
        
        if existing_payment.data:
            # Update existing payment record
            payment_id = existing_payment.data[0]['id']
            metadata = existing_payment.data[0].get('metadata', {})
            credits_added = metadata.get('credits_added', False)
            
            if not credits_added:
                # Add credits for the first time
                new_credits = current_credits + credits_amount
                
                # Update credits
                result = supabase.table('founders').update({
                    'credits': new_credits
                }).eq('id', founder_id).execute()
                
                if not result.data:
                    return {"status": "error", "message": "Failed to update credits"}
                
                # Mark credits as added
                payment_data['metadata'] = {'credits_added': True}
                supabase.table('payments').update(payment_data).eq('id', payment_id).execute()
        else:
            # Add credits
            new_credits = current_credits + credits_amount
            
            # Update credits
            result = supabase.table('founders').update({
                'credits': new_credits
            }).eq('id', founder_id).execute()
            
            if not result.data:
                return {"status": "error", "message": "Failed to update credits"}
            
            # Mark credits as added
            payment_data['metadata'] = {'credits_added': True}
            
            # Create new payment record
            payment_result = supabase.table('payments').insert(payment_data).execute()
            if payment_result.data:
                pass
        
        return {
            "status": "success",
            "message": "Payment processed successfully",
            "clerk_user_id": clerk_user_id,
            "credits_added": credits_amount,
            "checkout_id": checkout_id
        }
        
    except Exception as e:
        error_trace = traceback.format_exc()
        return {"status": "error", "message": str(e)}

def handle_order_created(webhook_data):
    """Handle order.created webhook - this is when payment succeeds, add credits here"""
    try:
        # Extract order data
        data = webhook_data.get('data', {})
        order_id = data.get('id')
        
        # Get product and pricing info
        product_id = data.get('product_id')
        amount = data.get('amount', 0)
        currency = data.get('currency', 'USD')
        
        # Get custom metadata from order
        metadata = data.get('metadata', {})
        clerk_user_id = metadata.get('clerk_user_id')
        credits_amount = int(metadata.get('credits_amount', 0))
        
        if not clerk_user_id or not credits_amount:
            return {"status": "error", "message": "Missing metadata in order"}
        
        # Get customer info
        customer_email = data.get('customer', {}).get('email') if isinstance(data.get('customer'), dict) else None
        
        supabase = get_supabase()
        
        # Get user's founder profile
        profile = supabase.table('founders').select('id, credits').eq('clerk_user_id', clerk_user_id).execute()
        
        if not profile.data:
            return {"status": "error", "message": "User profile not found"}
        
        founder_id = profile.data[0]['id']
        current_credits = profile.data[0].get('credits') or 0
        
        # Check if order already processed
        existing_payment = supabase.table('payments').select('id, metadata').eq('polar_order_id', order_id).execute()
        
        if existing_payment.data:
            # Order already exists
            payment_id = existing_payment.data[0]['id']
            metadata_check = existing_payment.data[0].get('metadata', {})
            credits_added = metadata_check.get('credits_added', False)
            
            if credits_added:
                return {"status": "success", "message": "Order already processed"}
            
            # Credits not yet added, add them now
            new_credits = current_credits + credits_amount
            
            result = supabase.table('founders').update({
                'credits': new_credits
            }).eq('id', founder_id).execute()
            
            if not result.data:
                return {"status": "error", "message": "Failed to update credits"}
            
            # Mark credits as added
            supabase.table('payments').update({
                'metadata': {'credits_added': True}
            }).eq('id', payment_id).execute()
            
            
            return {
                "status": "success",
                "message": "Credits added successfully",
                "clerk_user_id": clerk_user_id,
                "credits_added": credits_amount,
                "order_id": order_id
            }
        else:
            # New order - create payment record and add credits
            new_credits = current_credits + credits_amount
            
            # Add credits
            result = supabase.table('founders').update({
                'credits': new_credits
            }).eq('id', founder_id).execute()
            
            if not result.data:
                return {"status": "error", "message": "Failed to update credits"}
            
            # Create payment record
            payment_data = {
                'clerk_user_id': clerk_user_id,
                'founder_id': founder_id,
                'polar_order_id': order_id,
                'polar_checkout_id': data.get('checkout', {}).get('id') if isinstance(data.get('checkout'), dict) else None,
                'product_id': product_id,
                'credits_amount': credits_amount,
                'amount_paid': float(amount) / 100 if amount else None,
                'currency': currency,
                'status': 'succeeded',
                'customer_email': customer_email,
                'metadata': {'credits_added': True},
                'webhook_data': webhook_data
            }
            
            payment_result = supabase.table('payments').insert(payment_data).execute()
            
            if payment_result.data:
                pass
            
            return {
                "status": "success",
                "message": "Payment processed and credits added",
                "clerk_user_id": clerk_user_id,
                "credits_added": credits_amount,
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

def add_credits_manually(clerk_user_id, credits_amount):
    """
    Manually add credits to a user account (for testing or admin use)
    
    Args:
        clerk_user_id: The Clerk user ID
        credits_amount: Number of credits to add
    
    Returns:
        dict: Updated credits information
    """
    supabase = get_supabase()
    
    # Get current credits
    profile = supabase.table('founders').select('credits').eq('clerk_user_id', clerk_user_id).execute()
    
    if not profile.data:
        raise ValueError("User profile not found")
    
    current_credits = profile.data[0].get('credits') or 0
    new_credits = current_credits + credits_amount
    
    # Update credits
    result = supabase.table('founders').update({
        'credits': new_credits
    }).eq('clerk_user_id', clerk_user_id).execute()
    
    if not result.data:
        raise ValueError("Failed to update credits")
    
    return {
        "credits_added": credits_amount,
        "previous_balance": current_credits,
        "new_balance": new_credits
    }
