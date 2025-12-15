"""Database configuration and Supabase client initialization"""
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Supabase configuration
# These must be set as environment variables - no defaults for security
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_ANON_KEY')

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError(
        "Missing required environment variables: SUPABASE_URL and SUPABASE_ANON_KEY must be set. "
        "Please configure these in your environment or .env file."
    )

# Initialize Supabase client
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"Error initializing Supabase client: {e}")
    supabase = None

def get_supabase():
    """Get the Supabase client instance"""
    return supabase

