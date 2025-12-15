"""Database configuration and Supabase client initialization"""
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Supabase configuration
SUPABASE_URL = os.environ.get('SUPABASE_URL', "https://stosfnfkclzixfacebyk.supabase.co")
SUPABASE_KEY = os.environ.get('SUPABASE_ANON_KEY', "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InN0b3NmbmZrY2x6aXhmYWNlYnlrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjE1NTE0OTYsImV4cCI6MjA3NzEyNzQ5Nn0.7BWuzE0ey6IyyT7RSP09Mzspsox52PNs5IB2ifVr5XY")

# Initialize Supabase client
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"Error initializing Supabase client: {e}")
    supabase = None

def get_supabase():
    """Get the Supabase client instance"""
    return supabase

