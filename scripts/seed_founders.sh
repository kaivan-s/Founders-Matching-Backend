#!/bin/bash

# Seed script for creating fake founder profiles and projects
# Usage: SUPABASE_URL=your_url SUPABASE_SERVICE_KEY=your_key ./seed_founders.sh

if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_SERVICE_KEY" ]; then
    echo "Error: Please set SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables"
    echo "Usage: SUPABASE_URL=https://xxx.supabase.co SUPABASE_SERVICE_KEY=your_service_role_key ./seed_founders.sh"
    exit 1
fi

API="$SUPABASE_URL/rest/v1"
AUTH="apikey: $SUPABASE_SERVICE_KEY"
AUTH2="Authorization: Bearer $SUPABASE_SERVICE_KEY"
CT="Content-Type: application/json"
PREFER="Prefer: return=representation"

echo "Creating 25 founder profiles with projects..."

# Function to create founder and project
create_founder_with_project() {
    local founder_json="$1"
    local project_json="$2"
    
    # Create founder
    response=$(curl -s -X POST "$API/founders" \
        -H "$AUTH" -H "$AUTH2" -H "$CT" -H "$PREFER" \
        -d "$founder_json")
    
    founder_id=$(echo "$response" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
    
    if [ -n "$founder_id" ]; then
        echo "Created founder: $founder_id"
        
        # Create project for this founder
        project_with_founder=$(echo "$project_json" | sed "s/FOUNDER_ID_PLACEHOLDER/$founder_id/g")
        
        proj_response=$(curl -s -X POST "$API/projects" \
            -H "$AUTH" -H "$AUTH2" -H "$CT" -H "$PREFER" \
            -d "$project_with_founder")
        
        proj_id=$(echo "$proj_response" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
        echo "  -> Created project: $proj_id"
    else
        echo "Failed to create founder: $response"
    fi
}

# Founder 1 - AI/ML Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_001",
    "name": "Arjun Mehta",
    "email": "arjun.mehta.seed@guildspace.co",
    "location": "San Francisco, CA",
    "skills": ["Machine Learning", "Python", "TensorFlow", "Product Strategy"],
    "looking_for": "Technical co-founder with backend expertise to build AI-powered analytics platform",
    "linkedin_url": "https://linkedin.com/in/arjunmehta",
    "compatibility_answers": {"work_style": "async", "commitment": "full_time", "equity_expectation": "equal"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "InsightAI - AI-Powered Business Intelligence",
    "description": "Building an AI platform that automatically analyzes business data and provides actionable insights. We use advanced ML models to detect patterns, predict trends, and recommend strategic decisions for SMBs.",
    "stage": "mvp",
    "genre": "AI/ML",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Backend Development", "Cloud Infrastructure", "Data Engineering"],
    "display_order": 0
}'

# Founder 2 - FinTech Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_002",
    "name": "Priya Sharma",
    "email": "priya.sharma.seed@guildspace.co",
    "location": "Mumbai, India",
    "skills": ["Finance", "Product Management", "UX Design", "Growth Marketing"],
    "looking_for": "Full-stack developer who understands payments and financial compliance",
    "linkedin_url": "https://linkedin.com/in/priyasharma",
    "compatibility_answers": {"work_style": "sync", "commitment": "full_time", "equity_expectation": "negotiable"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "PayFlow - Instant B2B Payments for India",
    "description": "Revolutionizing B2B payments in India with instant settlements, automated reconciliation, and smart credit lines for small businesses. Targeting the $2T B2B payments market.",
    "stage": "idea",
    "genre": "FinTech",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Full Stack Development", "Payment Systems", "Compliance"],
    "display_order": 0
}'

# Founder 3 - HealthTech Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_003",
    "name": "Dr. Sarah Chen",
    "email": "sarah.chen.seed@guildspace.co",
    "location": "Boston, MA",
    "skills": ["Healthcare", "Medical Research", "Clinical Operations", "Regulatory Affairs"],
    "looking_for": "Technical co-founder to build telemedicine platform with AI diagnostics",
    "linkedin_url": "https://linkedin.com/in/drsarahchen",
    "compatibility_answers": {"work_style": "hybrid", "commitment": "full_time", "equity_expectation": "equal"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "MedAssist AI - AI-Powered Diagnostic Assistant",
    "description": "Helping doctors make faster, more accurate diagnoses with AI. Our platform analyzes patient symptoms, medical history, and lab results to suggest potential diagnoses and treatment plans.",
    "stage": "idea",
    "genre": "HealthTech",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["AI/ML", "Mobile Development", "HIPAA Compliance"],
    "display_order": 0
}'

# Founder 4 - EdTech Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_004",
    "name": "Marcus Johnson",
    "email": "marcus.johnson.seed@guildspace.co",
    "location": "Austin, TX",
    "skills": ["Education", "Curriculum Design", "Community Building", "Content Creation"],
    "looking_for": "Developer with passion for education and gamification experience",
    "linkedin_url": "https://linkedin.com/in/marcusjohnson",
    "compatibility_answers": {"work_style": "async", "commitment": "part_time", "equity_expectation": "negotiable"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "SkillQuest - Gamified Professional Learning",
    "description": "Making professional development fun and engaging through gamification. Employees earn XP, unlock achievements, and compete on leaderboards while learning new skills relevant to their careers.",
    "stage": "mvp",
    "genre": "EdTech",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["React Native", "Game Development", "Backend APIs"],
    "display_order": 0
}'

# Founder 5 - SaaS Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_005",
    "name": "Emily Rodriguez",
    "email": "emily.rodriguez.seed@guildspace.co",
    "location": "New York, NY",
    "skills": ["Sales", "B2B Marketing", "CRM Systems", "Revenue Operations"],
    "looking_for": "Technical co-founder to build next-gen sales intelligence platform",
    "linkedin_url": "https://linkedin.com/in/emilyrodriguez",
    "compatibility_answers": {"work_style": "sync", "commitment": "full_time", "equity_expectation": "equal"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "DealPulse - AI Sales Intelligence",
    "description": "Helping sales teams close more deals with AI-powered insights. We analyze call recordings, emails, and CRM data to identify winning patterns and coach reps in real-time.",
    "stage": "revenue",
    "genre": "SaaS",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["NLP", "Full Stack Development", "Integrations"],
    "display_order": 0
}'

# Founder 6 - E-commerce Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_006",
    "name": "Raj Patel",
    "email": "raj.patel.seed@guildspace.co",
    "location": "London, UK",
    "skills": ["E-commerce", "Supply Chain", "Operations", "D2C Marketing"],
    "looking_for": "Full-stack developer to build sustainable fashion marketplace",
    "linkedin_url": "https://linkedin.com/in/rajpatel",
    "compatibility_answers": {"work_style": "hybrid", "commitment": "full_time", "equity_expectation": "negotiable"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "GreenThread - Sustainable Fashion Marketplace",
    "description": "Curated marketplace for sustainable and ethical fashion brands. We verify each brands sustainability credentials and make it easy for conscious consumers to shop responsibly.",
    "stage": "mvp",
    "genre": "E-commerce",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["React", "Node.js", "Stripe Integration"],
    "display_order": 0
}'

# Founder 7 - Marketplace Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_007",
    "name": "Lisa Wang",
    "email": "lisa.wang.seed@guildspace.co",
    "location": "Seattle, WA",
    "skills": ["Product Design", "UX Research", "Marketplace Dynamics", "Growth"],
    "looking_for": "Backend engineer with marketplace or gig economy experience",
    "linkedin_url": "https://linkedin.com/in/lisawang",
    "compatibility_answers": {"work_style": "sync", "commitment": "full_time", "equity_expectation": "equal"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "ExpertHour - On-Demand Expert Consultations",
    "description": "Connecting professionals with industry experts for quick 30-60 minute consultations. Think Cameo meets LinkedIn - get advice from successful founders, executives, and specialists.",
    "stage": "idea",
    "genre": "Marketplace",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Video Streaming", "Payment Systems", "Scheduling APIs"],
    "display_order": 0
}'

# Founder 8 - Consumer App Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_008",
    "name": "Alex Kim",
    "email": "alex.kim.seed@guildspace.co",
    "location": "Los Angeles, CA",
    "skills": ["Mobile Development", "iOS", "Swift", "UI Animation"],
    "looking_for": "Business co-founder with marketing and growth expertise",
    "linkedin_url": "https://linkedin.com/in/alexkim",
    "compatibility_answers": {"work_style": "async", "commitment": "full_time", "equity_expectation": "equal"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "MoodMix - AI Music for Every Moment",
    "description": "AI-powered music curation that adapts to your mood, activity, and time of day. We generate personalized playlists and even create unique AI-composed ambient music for focus and relaxation.",
    "stage": "mvp",
    "genre": "Consumer",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Growth Marketing", "Partnership Development", "Music Industry"],
    "display_order": 0
}'

# Founder 9 - B2B SaaS Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_009",
    "name": "David Okonkwo",
    "email": "david.okonkwo.seed@guildspace.co",
    "location": "Lagos, Nigeria",
    "skills": ["Enterprise Sales", "Go-to-Market", "Partnership Development", "Strategy"],
    "looking_for": "Technical co-founder to build HR tech platform for Africa",
    "linkedin_url": "https://linkedin.com/in/davidokonkwo",
    "compatibility_answers": {"work_style": "hybrid", "commitment": "full_time", "equity_expectation": "negotiable"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "TalentBridge - African Talent Management Platform",
    "description": "End-to-end HR platform built for African businesses. Payroll, benefits, compliance, and talent management tailored for the unique needs of companies operating across African markets.",
    "stage": "idea",
    "genre": "B2B",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Full Stack Development", "Payroll Systems", "Multi-currency"],
    "display_order": 0
}'

# Founder 10 - Hardware/IoT Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_010",
    "name": "Nina Petrov",
    "email": "nina.petrov.seed@guildspace.co",
    "location": "Berlin, Germany",
    "skills": ["Hardware Engineering", "IoT", "Embedded Systems", "Manufacturing"],
    "looking_for": "Software engineer to build companion app and cloud platform",
    "linkedin_url": "https://linkedin.com/in/ninapetrov",
    "compatibility_answers": {"work_style": "sync", "commitment": "full_time", "equity_expectation": "equal"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "GrowPod - Smart Indoor Farming System",
    "description": "Automated indoor farming pods for homes and restaurants. Our IoT-enabled system grows fresh herbs and vegetables year-round with minimal effort, using 95% less water than traditional farming.",
    "stage": "mvp",
    "genre": "Hardware",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Mobile Development", "Cloud IoT", "Data Visualization"],
    "display_order": 0
}'

# Founder 11 - PropTech Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_011",
    "name": "James McCarthy",
    "email": "james.mccarthy.seed@guildspace.co",
    "location": "Dublin, Ireland",
    "skills": ["Real Estate", "Property Management", "Finance", "Legal"],
    "looking_for": "Full-stack developer to build property investment platform",
    "linkedin_url": "https://linkedin.com/in/jamesmccarthy",
    "compatibility_answers": {"work_style": "async", "commitment": "full_time", "equity_expectation": "negotiable"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "FractoHome - Fractional Property Investment",
    "description": "Democratizing real estate investment by enabling fractional ownership of rental properties. Invest as little as $100 in curated properties and earn passive rental income.",
    "stage": "idea",
    "genre": "FinTech",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["React", "Node.js", "Blockchain", "Financial APIs"],
    "display_order": 0
}'

# Founder 12 - Climate Tech Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_012",
    "name": "Maria Santos",
    "email": "maria.santos.seed@guildspace.co",
    "location": "São Paulo, Brazil",
    "skills": ["Environmental Science", "Carbon Markets", "Policy", "Sustainability"],
    "looking_for": "Technical co-founder passionate about climate solutions",
    "linkedin_url": "https://linkedin.com/in/mariasantos",
    "compatibility_answers": {"work_style": "hybrid", "commitment": "full_time", "equity_expectation": "equal"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "CarbonTrack - Corporate Emissions Platform",
    "description": "Helping companies measure, track, and reduce their carbon footprint. Our AI analyzes supply chain data to identify emission hotspots and recommend reduction strategies.",
    "stage": "revenue",
    "genre": "SaaS",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Data Engineering", "AI/ML", "Supply Chain APIs"],
    "display_order": 0
}'

# Founder 13 - DevTools Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_013",
    "name": "Chris Anderson",
    "email": "chris.anderson.seed@guildspace.co",
    "location": "Denver, CO",
    "skills": ["DevOps", "Kubernetes", "Cloud Architecture", "Open Source"],
    "looking_for": "Business co-founder with enterprise sales experience",
    "linkedin_url": "https://linkedin.com/in/chrisanderson",
    "compatibility_answers": {"work_style": "async", "commitment": "full_time", "equity_expectation": "equal"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "DeployBot - One-Click Kubernetes Deployments",
    "description": "Making Kubernetes accessible to every developer. Our platform abstracts away K8s complexity with smart defaults, one-click deployments, and AI-powered troubleshooting.",
    "stage": "mvp",
    "genre": "SaaS",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Enterprise Sales", "Marketing", "Developer Relations"],
    "display_order": 0
}'

# Founder 14 - Social Impact Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_014",
    "name": "Aisha Mohammed",
    "email": "aisha.mohammed.seed@guildspace.co",
    "location": "Nairobi, Kenya",
    "skills": ["Community Development", "Nonprofit Management", "Fundraising", "Impact Measurement"],
    "looking_for": "Technical co-founder to build education access platform",
    "linkedin_url": "https://linkedin.com/in/aishamohammed",
    "compatibility_answers": {"work_style": "sync", "commitment": "full_time", "equity_expectation": "negotiable"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "LearnBridge - Education Access for Underserved Communities",
    "description": "Connecting volunteer tutors with students in underserved communities. Our platform matches tutors with students based on subjects, languages, and schedules, enabling free quality education.",
    "stage": "idea",
    "genre": "EdTech",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["React Native", "Video Chat", "Matching Algorithms"],
    "display_order": 0
}'

# Founder 15 - Legal Tech Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_015",
    "name": "Rachel Green",
    "email": "rachel.green.seed@guildspace.co",
    "location": "Chicago, IL",
    "skills": ["Law", "Contract Management", "Compliance", "Legal Operations"],
    "looking_for": "AI/ML engineer to build contract analysis platform",
    "linkedin_url": "https://linkedin.com/in/rachelgreen",
    "compatibility_answers": {"work_style": "hybrid", "commitment": "full_time", "equity_expectation": "equal"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "ContractAI - Intelligent Contract Review",
    "description": "AI-powered contract analysis that identifies risks, extracts key terms, and suggests improvements. Helping legal teams review contracts 10x faster with higher accuracy.",
    "stage": "mvp",
    "genre": "AI/ML",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["NLP", "Python", "Legal AI Training"],
    "display_order": 0
}'

# Founder 16 - Gaming Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_016",
    "name": "Kevin Park",
    "email": "kevin.park.seed@guildspace.co",
    "location": "Seoul, South Korea",
    "skills": ["Game Design", "Unity", "C#", "3D Modeling"],
    "looking_for": "Business co-founder with gaming industry connections",
    "linkedin_url": "https://linkedin.com/in/kevinpark",
    "compatibility_answers": {"work_style": "async", "commitment": "full_time", "equity_expectation": "equal"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "QuestVerse - Cross-Platform RPG Engine",
    "description": "Building the ultimate RPG creation toolkit. Our no-code/low-code engine lets creators build rich RPG games for mobile, PC, and console without traditional programming.",
    "stage": "mvp",
    "genre": "Consumer",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Business Development", "Partnerships", "Community Management"],
    "display_order": 0
}'

# Founder 17 - Logistics Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_017",
    "name": "Omar Hassan",
    "email": "omar.hassan.seed@guildspace.co",
    "location": "Dubai, UAE",
    "skills": ["Logistics", "Supply Chain", "Fleet Management", "Operations"],
    "looking_for": "Technical co-founder to build last-mile delivery platform",
    "linkedin_url": "https://linkedin.com/in/omarhassan",
    "compatibility_answers": {"work_style": "sync", "commitment": "full_time", "equity_expectation": "negotiable"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "SwiftMile - AI-Optimized Last-Mile Delivery",
    "description": "Making last-mile delivery faster and cheaper with AI route optimization. Our platform helps delivery companies reduce costs by 30% while improving delivery times.",
    "stage": "revenue",
    "genre": "SaaS",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Route Optimization", "Mobile Development", "Maps APIs"],
    "display_order": 0
}'

# Founder 18 - Mental Health Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_018",
    "name": "Dr. Michael Torres",
    "email": "michael.torres.seed@guildspace.co",
    "location": "Miami, FL",
    "skills": ["Psychology", "Therapy", "Mental Health", "Research"],
    "looking_for": "Technical co-founder to build mental wellness platform",
    "linkedin_url": "https://linkedin.com/in/drmichaeltorres",
    "compatibility_answers": {"work_style": "hybrid", "commitment": "full_time", "equity_expectation": "equal"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "MindfulPath - AI Mental Wellness Companion",
    "description": "AI-powered mental wellness app providing 24/7 support, guided exercises, and therapist matching. We make mental healthcare accessible and affordable for everyone.",
    "stage": "idea",
    "genre": "HealthTech",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Mobile Development", "AI/ML", "Healthcare Compliance"],
    "display_order": 0
}'

# Founder 19 - Food Tech Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_019",
    "name": "Sophie Laurent",
    "email": "sophie.laurent.seed@guildspace.co",
    "location": "Paris, France",
    "skills": ["Food Science", "Culinary Arts", "CPG", "Brand Building"],
    "looking_for": "Technical co-founder to build personalized nutrition platform",
    "linkedin_url": "https://linkedin.com/in/sophielaurent",
    "compatibility_answers": {"work_style": "sync", "commitment": "full_time", "equity_expectation": "negotiable"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "NutriGenius - Personalized Meal Planning AI",
    "description": "AI nutritionist that creates personalized meal plans based on your health goals, dietary restrictions, and taste preferences. Includes grocery lists and recipe recommendations.",
    "stage": "mvp",
    "genre": "HealthTech",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Mobile Development", "AI/ML", "Recipe APIs"],
    "display_order": 0
}'

# Founder 20 - Recruiting Tech Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_020",
    "name": "Jennifer Wu",
    "email": "jennifer.wu.seed@guildspace.co",
    "location": "Singapore",
    "skills": ["HR", "Recruiting", "Talent Acquisition", "People Analytics"],
    "looking_for": "AI/ML engineer to build skills-based hiring platform",
    "linkedin_url": "https://linkedin.com/in/jenniferwu",
    "compatibility_answers": {"work_style": "async", "commitment": "full_time", "equity_expectation": "equal"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "SkillMatch - AI-Powered Skills-Based Hiring",
    "description": "Eliminating bias in hiring with AI that evaluates candidates purely on skills and potential. Our platform uses work samples and assessments instead of resumes.",
    "stage": "idea",
    "genre": "SaaS",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["AI/ML", "Assessment Design", "ATS Integration"],
    "display_order": 0
}'

# Founder 21 - Crypto/Web3 Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_021",
    "name": "Tyler Brooks",
    "email": "tyler.brooks.seed@guildspace.co",
    "location": "Miami, FL",
    "skills": ["Blockchain", "Smart Contracts", "DeFi", "Tokenomics"],
    "looking_for": "Business co-founder with finance and compliance background",
    "linkedin_url": "https://linkedin.com/in/tylerbrooks",
    "compatibility_answers": {"work_style": "async", "commitment": "full_time", "equity_expectation": "negotiable"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "ChainVault - Institutional Crypto Custody",
    "description": "Enterprise-grade crypto custody solution for financial institutions. Multi-sig security, insurance coverage, and regulatory compliance built for banks and asset managers.",
    "stage": "mvp",
    "genre": "FinTech",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Compliance", "Enterprise Sales", "Risk Management"],
    "display_order": 0
}'

# Founder 22 - AgTech Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_022",
    "name": "Roberto Silva",
    "email": "roberto.silva.seed@guildspace.co",
    "location": "Buenos Aires, Argentina",
    "skills": ["Agriculture", "Agronomy", "Farm Management", "Sustainability"],
    "looking_for": "Software developer to build farm management platform",
    "linkedin_url": "https://linkedin.com/in/robertosilva",
    "compatibility_answers": {"work_style": "hybrid", "commitment": "full_time", "equity_expectation": "equal"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "AgriSense - Smart Farm Analytics",
    "description": "Using satellite imagery, IoT sensors, and AI to help farmers optimize crop yields. Our platform predicts irrigation needs, identifies disease early, and recommends fertilization schedules.",
    "stage": "revenue",
    "genre": "SaaS",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Satellite Data", "IoT", "Machine Learning"],
    "display_order": 0
}'

# Founder 23 - Travel Tech Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_023",
    "name": "Anna Kowalski",
    "email": "anna.kowalski.seed@guildspace.co",
    "location": "Warsaw, Poland",
    "skills": ["Travel Industry", "UX Design", "Content Strategy", "Partnerships"],
    "looking_for": "Full-stack developer to build AI travel planning platform",
    "linkedin_url": "https://linkedin.com/in/annakowalski",
    "compatibility_answers": {"work_style": "sync", "commitment": "full_time", "equity_expectation": "negotiable"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "Wanderly - AI Travel Concierge",
    "description": "Your personal AI travel planner that creates perfect itineraries based on your interests, budget, and travel style. From flights to restaurants, we handle everything.",
    "stage": "idea",
    "genre": "Consumer",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Full Stack", "Travel APIs", "AI/LLM"],
    "display_order": 0
}'

# Founder 24 - Security Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_024",
    "name": "Jake Miller",
    "email": "jake.miller.seed@guildspace.co",
    "location": "Tel Aviv, Israel",
    "skills": ["Cybersecurity", "Penetration Testing", "Security Architecture", "Compliance"],
    "looking_for": "Business co-founder with enterprise security sales experience",
    "linkedin_url": "https://linkedin.com/in/jakemiller",
    "compatibility_answers": {"work_style": "async", "commitment": "full_time", "equity_expectation": "equal"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "SecureShield - Automated Security Testing",
    "description": "Continuous automated security testing for modern applications. Our platform finds vulnerabilities before hackers do, with zero false positives and actionable remediation guides.",
    "stage": "mvp",
    "genre": "SaaS",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Enterprise Sales", "Marketing", "Channel Partnerships"],
    "display_order": 0
}'

# Founder 25 - Construction Tech Founder
create_founder_with_project '{
    "clerk_user_id": "seed_user_025",
    "name": "Linda Thompson",
    "email": "linda.thompson.seed@guildspace.co",
    "location": "Toronto, Canada",
    "skills": ["Construction Management", "Project Management", "Civil Engineering", "BIM"],
    "looking_for": "Software developer to build construction project management platform",
    "linkedin_url": "https://linkedin.com/in/lindathompson",
    "compatibility_answers": {"work_style": "hybrid", "commitment": "full_time", "equity_expectation": "negotiable"},
    "plan": "FREE"
}' '{
    "founder_id": "FOUNDER_ID_PLACEHOLDER",
    "title": "BuildSync - Construction Project Intelligence",
    "description": "AI-powered construction project management that predicts delays, optimizes schedules, and tracks progress with drone imagery. Helping builders finish on time and under budget.",
    "stage": "idea",
    "genre": "SaaS",
    "seeking_cofounder": true,
    "is_active": true,
    "needed_skills": ["Full Stack Development", "Computer Vision", "Mobile Apps"],
    "display_order": 0
}'

echo ""
echo "Done! Created 25 founder profiles with projects."
echo "All seed users have clerk_user_id starting with 'seed_user_' and emails ending in '@guildspace.co'"
