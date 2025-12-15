"""Email processor for sending queued emails via AWS SES"""
import os
import schedule
import time
from datetime import datetime
from notification_service import NotificationService

def process_email_queue():
    """Process pending emails from the queue"""
    
    service = NotificationService()
    sent_count = service.send_queued_emails(limit=20)
    
    return sent_count

def send_daily_digests():
    """Send daily digest emails"""
    
    service = NotificationService()
    service.send_daily_digest()
    

def setup_email_scheduler():
    """Set up scheduled email tasks"""
    
    # Process email queue every minute
    schedule.every(1).minutes.do(process_email_queue)
    
    # Send daily digest at 9 AM
    schedule.every().day.at("09:00").do(send_daily_digests)
    
    
    while True:
        schedule.run_pending()
        time.sleep(30)  # Check every 30 seconds

if __name__ == "__main__":
    # For development/testing, just process once
    if os.environ.get('RUN_ONCE'):
        process_email_queue()
    else:
        # Run the scheduler
        setup_email_scheduler()
