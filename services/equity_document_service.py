"""
Equity Document Generation Service

Generates professional Co-Founder Agreement documents in PDF and DOCX formats.
Uses python-docx for DOCX and reportlab for PDF generation.
"""

import io
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from config.database import get_supabase, get_supabase_admin
from services.workspace_service import _verify_workspace_access, _get_founder_id, _log_audit
from services.equity_questionnaire_service import get_questionnaire_responses
from utils.logger import log_error, log_info


# ============================================================================
# Document Template (Markdown/MMD format)
# ============================================================================

AGREEMENT_TEMPLATE = """# CO-FOUNDER AGREEMENT

**Between:** {founder_a_name} and {founder_b_name}
**Company:** {company_name}
**Date:** {date}

---

## 1. Equity Allocation

The Founders agree to the following equity distribution:

| Stakeholder | Equity Percentage |
|---------|-------------------|
| {founder_a_name} | {founder_a_percent}% |
| {founder_b_name} | {founder_b_percent}% |

**Total:** 100%

---

## 2. Vesting Schedule

{vesting_description}

{advisor_vesting_section}

---

## 3. Roles & Responsibilities

### {founder_a_name} ({founder_a_role})
{founder_a_responsibilities}

### {founder_b_name} ({founder_b_role})
{founder_b_responsibilities}

---

## 4. Capital Contributions

{capital_details}

---

## 5. Intellectual Property

{ip_statement}

---

## 6. Decision Making

Major decisions affecting the company shall require mutual agreement between the Founders. This includes but is not limited to:
- Raising capital or taking on debt
- Hiring or terminating key employees
- Entering into significant contracts
- Changing the company's direction or business model
- Dissolution of the company

---

## 7. Dispute Resolution

Any disputes arising between the Founders shall be resolved through:
1. Good faith negotiation between the parties
2. Mediation by a mutually agreed third party
3. Binding arbitration under the rules of {jurisdiction}

---

## 8. Confidentiality

Both Founders agree to maintain confidentiality regarding:
- Company trade secrets and proprietary information
- Business strategies and plans
- Financial information
- Customer and partner data

---

## 9. Non-Compete

During the term of this agreement and for a period of 12 months following departure, Founders agree not to:
- Start or join a competing business
- Solicit company employees or contractors
- Solicit company customers or partners

---

## 10. Termination

This agreement may be terminated by:
- Mutual written consent of both Founders
- Material breach by either party
- Death or permanent incapacity of a Founder
- Dissolution of the company

---

## Appendix A: Calculation Breakdown

{calculation_breakdown_table}

---

## Signatures

By signing below, both Founders acknowledge that they have read, understood, and agree to the terms of this Co-Founder Agreement.

**{founder_a_name}**
Signature: _________________________
Date: _____________

**{founder_b_name}**
Signature: _________________________
Date: _____________

---

*DISCLAIMER: This document is a template intended to facilitate discussion between co-founders. It is NOT a legally binding contract. We strongly recommend consulting with a qualified attorney before finalizing any legal agreements.*
"""


def _format_vesting_description(vesting_terms: Dict[str, Any]) -> str:
    """Format vesting terms into readable text."""
    if not vesting_terms or not vesting_terms.get('has_vesting', True):
        return "The Founders have elected not to implement a vesting schedule. All equity is fully vested upon signing."
    
    years = vesting_terms.get('years', 4)
    cliff_months = vesting_terms.get('cliff_months', 12)
    acceleration = vesting_terms.get('acceleration', 'none')
    
    description = f"Equity shall vest over a period of **{years} years** with a **{cliff_months}-month cliff**.\n\n"
    description += "This means:\n"
    description += f"- No equity vests during the first {cliff_months} months (cliff period)\n"
    description += f"- After the cliff, {100 / years:.1f}% vests on the cliff date\n"
    description += f"- Remaining equity vests monthly over the following {years * 12 - cliff_months} months\n"
    
    if acceleration == 'single_trigger':
        description += "\n**Acceleration:** Single-trigger acceleration applies. Upon a change of control event (acquisition), all unvested equity immediately vests."
    elif acceleration == 'double_trigger':
        description += "\n**Acceleration:** Double-trigger acceleration applies. Unvested equity accelerates only if both (1) a change of control occurs AND (2) the Founder is terminated without cause within 12 months."
    
    return description


def _format_capital_details(
    founder_a_name: str,
    founder_b_name: str,
    founder_a_capital: float,
    founder_b_capital: float
) -> str:
    """Format capital contribution details."""
    total = founder_a_capital + founder_b_capital
    
    if total == 0:
        return "No initial capital contributions have been made by either Founder at the time of this agreement."
    
    details = "The following capital contributions have been made:\n\n"
    details += f"| Founder | Contribution |\n"
    details += f"|---------|-------------|\n"
    
    if founder_a_capital > 0:
        details += f"| {founder_a_name} | ${founder_a_capital:,.0f} |\n"
    else:
        details += f"| {founder_a_name} | $0 |\n"
    
    if founder_b_capital > 0:
        details += f"| {founder_b_name} | ${founder_b_capital:,.0f} |\n"
    else:
        details += f"| {founder_b_name} | $0 |\n"
    
    details += f"| **Total** | **${total:,.0f}** |\n"
    
    return details


def _format_ip_statement(startup_context: Dict[str, Any], founder_a_name: str, founder_b_name: str) -> str:
    """Format IP ownership statement."""
    has_ip = startup_context.get('has_ip', False)
    ip_owner = startup_context.get('ip_owner', '')
    
    if not has_ip:
        return "No pre-existing intellectual property is being contributed to the company. All IP developed after the formation of this partnership shall be owned by the company."
    
    statement = "The following intellectual property is being contributed to the company:\n\n"
    
    if ip_owner == 'founder_a':
        statement += f"- {founder_a_name} contributes pre-existing IP to the company\n"
    elif ip_owner == 'founder_b':
        statement += f"- {founder_b_name} contributes pre-existing IP to the company\n"
    elif ip_owner == 'joint':
        statement += "- Both Founders jointly contribute pre-existing IP to the company\n"
    
    statement += "\nAll contributed IP shall become the property of the company upon signing this agreement."
    
    return statement


def _format_calculation_breakdown(breakdown: Dict[str, Any], founder_a_name: str, founder_b_name: str) -> str:
    """Format calculation breakdown as a markdown table."""
    if not breakdown:
        return "*No calculation breakdown available (equal or custom split selected)*"
    
    weights = breakdown.get('weights', {})
    founder_a = breakdown.get('founder_a', {})
    founder_b = breakdown.get('founder_b', {})
    
    table = "| Factor | Weight | " + founder_a_name + " | " + founder_b_name + " |\n"
    table += "|--------|--------|"
    table += "-" * (len(founder_a_name) + 2) + "|"
    table += "-" * (len(founder_b_name) + 2) + "|\n"
    
    factors = [
        ('time', 'time_commitment', 'Time Commitment'),
        ('capital', 'capital_contribution', 'Capital Contribution'),
        ('expertise', 'domain_expertise', 'Domain Expertise'),
        ('risk', 'risk_taken', 'Risk Taken'),
        ('network', 'network', 'Network'),
        ('idea', 'idea_origination', 'Idea Origination'),
    ]
    
    for score_key, weight_key, label in factors:
        weight_pct = weights.get(weight_key, 0) * 100
        a_score = founder_a.get(score_key, '-')
        b_score = founder_b.get(score_key, '-')
        table += f"| {label} | {weight_pct:.0f}% | {a_score}/10 | {b_score}/10 |\n"
    
    # Add weighted totals
    a_total = founder_a.get('weighted_total', 0)
    b_total = founder_b.get('weighted_total', 0)
    table += f"| **Weighted Total** | - | **{a_total:.2f}** | **{b_total:.2f}** |\n"
    
    return table


def generate_mmd_document(
    scenario: Dict[str, Any],
    questionnaire_responses: Dict[str, Any],
    workspace_title: str
) -> str:
    """
    Generate comprehensive co-founder agreement document in MMD (Markdown) format.
    
    Args:
        scenario: The approved equity scenario
        questionnaire_responses: Questionnaire responses for both founders
        workspace_title: Workspace/company name
    
    Returns:
        The document content as a string
    """
    # Extract founder info
    founder_a = scenario.get('founder_a', {})
    founder_b = scenario.get('founder_b', {})
    founder_a_name = founder_a.get('name', 'Founder A')
    founder_b_name = founder_b.get('name', 'Founder B')
    
    # Extract percentages - ensure they are correctly extracted from the scenario
    founder_a_percent = float(scenario.get('founder_a_percent', 50))
    founder_b_percent = float(scenario.get('founder_b_percent', 50))
    advisor_percent = float(scenario.get('advisor_percent', 0))
    
    # Validate percentages sum to 100% (with small tolerance for floating point)
    total_percent = founder_a_percent + founder_b_percent + advisor_percent
    if abs(total_percent - 100.0) > 0.01:
        log_error(f"Warning: Equity percentages don't sum to 100%: {founder_a_percent}% + {founder_b_percent}% + {advisor_percent}% = {total_percent}%")
        # Normalize to ensure they sum to 100% (proportionally)
        if total_percent > 0:
            ratio = 100.0 / total_percent
            founder_a_percent = founder_a_percent * ratio
            founder_b_percent = founder_b_percent * ratio
            advisor_percent = advisor_percent * ratio
        else:
            # Fallback to 50/50 if both are 0 or invalid
            founder_a_percent = 50.0
            founder_b_percent = 50.0
            advisor_percent = 0
    
    # Extract vesting terms
    vesting_terms = scenario.get('vesting_terms', {})
    
    # Extract questionnaire data
    founder_a_resp = questionnaire_responses.get('founder_a', {})
    founder_b_resp = questionnaire_responses.get('founder_b', {})
    startup_context = questionnaire_responses.get('startup_context', {})
    
    founder_a_data = founder_a_resp.get('responses', {}) if founder_a_resp else {}
    founder_b_data = founder_b_resp.get('responses', {}) if founder_b_resp else {}
    
    # Extract roles
    founder_a_role_data = founder_a_data.get('role', {})
    founder_b_role_data = founder_b_data.get('role', {})
    founder_a_role = founder_a_role_data.get('title', 'Co-Founder')
    founder_b_role = founder_b_role_data.get('title', 'Co-Founder')
    
    # Extract responsibilities
    founder_a_responsibilities = founder_a_data.get('responsibilities', 'To be determined')
    founder_b_responsibilities = founder_b_data.get('responsibilities', 'To be determined')
    
    # Extract time commitment
    founder_a_time = founder_a_data.get('time_commitment', '')
    founder_b_time = founder_b_data.get('time_commitment', '')
    
    # Extract capital
    founder_a_capital_data = founder_a_data.get('capital_contribution', {})
    founder_b_capital_data = founder_b_data.get('capital_contribution', {})
    founder_a_capital = float(founder_a_capital_data.get('exact_amount', 0) or 0)
    founder_b_capital = float(founder_b_capital_data.get('exact_amount', 0) or 0)
    
    # Extract expertise
    founder_a_expertise = founder_a_data.get('expertise', {})
    founder_b_expertise = founder_b_data.get('expertise', {})
    
    # Extract startup context
    business_stage = startup_context.get('stage', '')
    business_description = startup_context.get('business_description', '')
    
    # Extract calculation breakdown
    breakdown = scenario.get('calculation_breakdown', {})
    
    # Format vesting details
    vesting_years = vesting_terms.get('years', 4)
    cliff_months = vesting_terms.get('cliff_months', 12)
    has_vesting = vesting_terms.get('has_vesting', True)
    acceleration = vesting_terms.get('acceleration', 'none')
    
    # Extract advisor vesting (if advisor equity is set)
    advisor_vesting_years = vesting_terms.get('advisor_vesting_years', 2)
    advisor_cliff_months = vesting_terms.get('advisor_cliff_months', 3)
    
    # Get advisor name from workspace participants (passed in scenario or default)
    advisor_name = scenario.get('advisor_name', 'Project Advisor')
    
    # Format date
    effective_date = datetime.now(timezone.utc)
    date_str = effective_date.strftime('%B %d, %Y')
    day_str = effective_date.strftime('%d')
    month_str = effective_date.strftime('%B')
    year_str = effective_date.strftime('%Y')
    
    # Format responsibilities as list
    def format_responsibilities(resp_text):
        if not resp_text or resp_text == 'To be determined':
            return '- To be determined'
        # Split by newlines or bullets and format
        lines = resp_text.replace('\n', '|').split('|')
        formatted = []
        for line in lines:
            line = line.strip()
            if line:
                if not line.startswith('-'):
                    formatted.append(f"- {line}")
                else:
                    formatted.append(line)
        return '\n'.join(formatted) if formatted else '- To be determined'
    
    founder_a_resp_formatted = format_responsibilities(founder_a_responsibilities)
    founder_b_resp_formatted = format_responsibilities(founder_b_responsibilities)
    
    # Format time commitment
    def format_time_commitment(time_str):
        if not time_str:
            return '[Time commitment to be determined]'
        # Handle various time commitment formats
        time_str_lower = time_str.lower().strip()
        time_map = {
            'full_time': 'Full-time (40+ hours/week)',
            'full_time_now': 'Full-time (40+ hours/week) - Currently active',
            'full_time_soon': 'Full-time (40+ hours/week) - Starting soon',
            'part_time': 'Part-time (20-30 hours/week)',
            'part_time_now': 'Part-time (20-30 hours/week) - Currently active',
            'part_time_soon': 'Part-time (20-30 hours/week) - Starting soon',
            'advisory': 'Advisory (10 hours/week)',
            'advisory_now': 'Advisory (10 hours/week) - Currently active',
            'advisory_soon': 'Advisory (10 hours/week) - Starting soon',
        }
        # Check exact match first
        if time_str_lower in time_map:
            return time_map[time_str_lower]
        # Check if it contains keywords
        if 'full_time' in time_str_lower or 'fulltime' in time_str_lower:
            if 'soon' in time_str_lower:
                return 'Full-time (40+ hours/week) - Starting soon'
            elif 'now' in time_str_lower or 'current' in time_str_lower:
                return 'Full-time (40+ hours/week) - Currently active'
            else:
                return 'Full-time (40+ hours/week)'
        elif 'part_time' in time_str_lower or 'parttime' in time_str_lower:
            if 'soon' in time_str_lower:
                return 'Part-time (20-30 hours/week) - Starting soon'
            elif 'now' in time_str_lower or 'current' in time_str_lower:
                return 'Part-time (20-30 hours/week) - Currently active'
            else:
                return 'Part-time (20-30 hours/week)'
        elif 'advisory' in time_str_lower:
            if 'soon' in time_str_lower:
                return 'Advisory (10 hours/week) - Starting soon'
            elif 'now' in time_str_lower or 'current' in time_str_lower:
                return 'Advisory (10 hours/week) - Currently active'
            else:
                return 'Advisory (10 hours/week)'
        # If no match, return as-is (might be custom text)
        return time_str
    
    founder_a_time_formatted = format_time_commitment(founder_a_time)
    founder_b_time_formatted = format_time_commitment(founder_b_time)
    
    # Format capital table
    capital_table = f"""| Founder | Cash (USD) | Assets/IP | Loans/Guarantees | Total Value (USD) |
|---------|------------|-----------|------------------|------------------|
| {founder_a_name} | ${founder_a_capital:,.0f} | [Assets/IP value] | [Loans/Guarantees] | ${founder_a_capital:,.0f} |
| {founder_b_name} | ${founder_b_capital:,.0f} | [Assets/IP value] | [Loans/Guarantees] | ${founder_b_capital:,.0f} |"""
    
    if founder_a_capital == 0 and founder_b_capital == 0:
        capital_table = "No initial capital contributions have been made by either Founder at the time of this agreement."
    
    # Format calculation breakdown table
    calc_table = _format_calculation_breakdown(breakdown, founder_a_name, founder_b_name)
    
    # Format vesting schedule
    if not has_vesting:
        vesting_schedule = "All equity allocated to the Founders shall be **fully vested** upon the Effective Date with no vesting schedule."
    else:
        vesting_schedule = f"""All equity allocated to the Founders shall be subject to vesting over a period of **{vesting_years} years** from the Effective Date (the "**Vesting Period**").

**Cliff Vesting:**
- **No shares shall vest during the first {cliff_months} months** from the Effective Date (the "**Cliff Period**").
- At the completion of {cliff_months} months, **{int(100 / vesting_years)}%** of the total allocated shares shall vest.

**Monthly Vesting:**
- After the Cliff Period, the remaining **{100 - int(100 / vesting_years)}%** of shares shall vest in **equal monthly installments over the next {vesting_years * 12 - cliff_months} months**.
- Vesting shall occur on the same day of each month as the Effective Date."""
        
        if acceleration == 'single_trigger':
            vesting_schedule += "\n\n**Single-Trigger Acceleration:** In the event of a Change of Control (acquisition, merger, asset sale), all unvested shares shall immediately vest."
        elif acceleration == 'double_trigger':
            vesting_schedule += "\n\n**Double-Trigger Acceleration:** In the event of (a) Change of Control AND (b) involuntary termination without cause within 12 months of the Change of Control, all unvested shares shall immediately vest."
    
    # Format business stage
    stage_map = {
        'idea': 'Idea',
        'pre_seed': 'Pre-seed',
        'mvp': 'MVP Development',
        'launched': 'Launched'
    }
    business_stage_formatted = stage_map.get(business_stage, business_stage or '[Business stage to be specified]')
    
    # Build advisor equity rows if advisor is allocated equity
    # Note: Different tables have different column counts
    advisor_equity_row_2col = ""  # For 2-column tables (Stakeholder, Equity %)
    advisor_equity_row_3col = ""  # For 3-column tables (Stakeholder, Equity %, Shares)
    advisor_section = ""
    if advisor_percent > 0:
        advisor_equity_row_2col = f"| {advisor_name} (Advisor) | {advisor_percent:.2f}% |"
        advisor_equity_row_3col = f"| {advisor_name} (Advisor) | {advisor_percent:.2f}% | [Number of shares] |"
        advisor_section = f"""
### Advisor Equity

**{advisor_name}** (the "Advisor") has been granted **{advisor_percent:.2f}%** equity in the Company in consideration for advisory services to be provided.

**Advisor Vesting Schedule:**
- Vesting Period: **{advisor_vesting_years} years** from the Effective Date
- Cliff Period: **{advisor_cliff_months} months** (no vesting during this period)
- After the Cliff Period, equity vests monthly over the remaining {advisor_vesting_years * 12 - advisor_cliff_months} months

*The Advisor's equity is separate from the Founders' equity and is subject to a separate Advisor Agreement.*
"""
    
    # Build comprehensive document
    document = f"""# CO-FOUNDER AGREEMENT

**THIS AGREEMENT** is made on this {day_str} day of {month_str}, {year_str} (the "Effective Date")

---

## EQUITY ALLOCATION SUMMARY

**The equity in the Company is allocated as follows:**

| Stakeholder | Equity Percentage |
|---------|-------------------|
| **{founder_a_name}** | **{founder_a_percent:.2f}%** |
| **{founder_b_name}** | **{founder_b_percent:.2f}%** |
{f"| **{advisor_name}** (Advisor) | **{advisor_percent:.2f}%** |" + chr(10) if advisor_percent > 0 else ""}| **Total** | **100.00%** |

*This equity allocation is based on the approved equity scenario and is subject to the vesting schedule and other terms set forth in this Agreement.*

---

**BETWEEN:**

1. **{founder_a_name}**, residing at [Full Address], Email: [Email], Phone: [Phone] (hereinafter referred to as "**Founder A**")

AND

2. **{founder_b_name}**, residing at [Full Address], Email: [Email], Phone: [Phone] (hereinafter referred to as "**Founder B**")

(Founder A and Founder B are collectively referred to as the "**Founders**" and individually as a "**Founder**")

**IN RESPECT OF:**

**{workspace_title or '[COMPANY NAME]'}**, a company having its registered office at [Registered Address] (hereinafter referred to as the "**Company**")

---

## RECITALS

**WHEREAS:**

A. The Founders have agreed to jointly establish and operate the Company for the purpose of {business_description or '[brief business description]'}.

B. The Company is engaged in the business of {business_description or '[detailed business description]'} (hereinafter referred to as the "**Business**").

C. The Founders desire to set forth their respective rights, duties, obligations, and the terms governing their relationship with each other and with the Company.

D. The Founders have agreed to allocate equity in the Company as follows: **{founder_a_name}** shall hold **{founder_a_percent:.2f}%** and **{founder_b_name}** shall hold **{founder_b_percent:.2f}%** of the total equity{f", with **{advisor_percent:.2f}%** allocated to **{advisor_name}** (Advisor)" if advisor_percent > 0 else ""}, subject to the vesting schedule and other terms set forth herein.

E. This Agreement shall govern the relationship between the Founders and the Company in accordance with the terms and conditions set forth herein.

**NOW, THEREFORE**, in consideration of the mutual covenants and agreements contained herein and for other good and valuable consideration, the receipt and sufficiency of which are hereby acknowledged, the Parties agree as follows:

---

## 1. PURPOSE AND BUSINESS CONCEPT

### 1.1 Business Description
The Company shall engage in {business_description or '[detailed description of business, products/services, target market, and value proposition]'}.

### 1.2 Business Stage
As of the Effective Date, the Company is at the **{business_stage_formatted}** stage.

### 1.3 Business Objectives
The Founders agree to work towards achieving the following key objectives:
- [Objective 1 - To be specified]
- [Objective 2 - To be specified]
- [Objective 3 - To be specified]

---

## 2. EQUITY ALLOCATION AND OWNERSHIP

### 2.1 Initial Equity Distribution

The equity in the Company shall be allocated among the Founders as follows:

| Stakeholder | Equity Percentage | Number of Shares |
|---------|------------------|------------------|
| {founder_a_name} | {founder_a_percent:.2f}% | [Number of shares] |
| {founder_b_name} | {founder_b_percent:.2f}% | [Number of shares] |
{advisor_equity_row_3col + chr(10) if advisor_equity_row_3col else ""}| **Total** | **100%** | **[Total Authorized Shares]** |

### 2.2 Basis of Equity Distribution
The equity split has been determined based on the following factors:
- Time commitment and availability
- Capital contribution
- Domain expertise and technical skills
- Risk undertaken
- Network and connections
- Idea origination and intellectual property
- Roles and responsibilities

**Detailed breakdown is provided in Schedule A (Equity Calculation Matrix).**

### 2.3 Authorized Share Capital
The authorized share capital of the Company is $[Amount] divided into [Number] equity shares of $[Face Value] each.

### 2.4 Future Dilution
The Founders acknowledge and agree that their equity percentages may be diluted in the future due to:
- Employee stock option pools (ESOP)
- Fundraising rounds (angel, seed, venture capital)
- Strategic advisor grants
- Convertible instruments (SAFEs, convertible notes)

Any dilution shall be proportional unless otherwise agreed in writing by all Founders.

---

## 3. VESTING SCHEDULE

### 3.1 Vesting Period
{vesting_schedule}
{advisor_section}
### 3.2 Continuous Service Requirement
Vesting is contingent upon the Founder's continuous active involvement and service with the Company. Any absence or leave exceeding [30/60/90] days may pause vesting, subject to mutual agreement.

### 3.3 Forfeiture of Unvested Shares
If a Founder's relationship with the Company terminates for any reason before full vesting, all unvested shares shall be forfeited and transferred back to the Company or as determined by the Board.

---

## 4. ROLES, RESPONSIBILITIES, AND TIME COMMITMENT

### 4.1 {founder_a_name} - {founder_a_role}

**Primary Responsibilities:**
{founder_a_resp_formatted}

**Time Commitment:**
{founder_a_time_formatted}

**Key Performance Areas:**
- [KPA 1 - To be specified]
- [KPA 2 - To be specified]

### 4.2 {founder_b_name} - {founder_b_role}

**Primary Responsibilities:**
{founder_b_resp_formatted}

**Time Commitment:**
{founder_b_time_formatted}

**Key Performance Areas:**
- [KPA 1 - To be specified]
- [KPA 2 - To be specified]

### 4.3 CEO and Leadership
[Founder A / Founder B / To be determined] shall serve as the Chief Executive Officer (CEO) and shall have final decision-making authority on day-to-day operational matters, subject to Section 7 (Decision-Making).

### 4.4 Commitment to Company
Each Founder agrees to:
- Devote the agreed time commitment exclusively to the Company's business
- Not engage in any competing business without prior written consent
- Prioritize Company matters during the committed hours
- Maintain regular communication and attendance at meetings

### 4.5 Modification of Roles
Roles and responsibilities may be modified by mutual written consent of all Founders and documented via amendment to this Agreement.

---

## 5. CAPITAL CONTRIBUTIONS

### 5.1 Initial Capital
The Founders have made the following capital contributions to the Company:

{capital_table}

### 5.2 Future Capital Requirements
Any future capital requirements shall be:
- Discussed and agreed upon by all Founders
- Contributed proportionally to equity ownership, OR
- Structured as founder loans with [X]% interest rate and repayment terms
- Failing agreement, external funding shall be sought

### 5.3 Personal Guarantees
[If applicable] The following Founders have provided personal guarantees for Company obligations:
- [Founder Name]: [Description of guarantee, amount, institution]

### 5.4 Reimbursement
Founders shall be reimbursed for reasonable business expenses incurred on behalf of the Company, subject to documentation and approval.

---

## 6. INTELLECTUAL PROPERTY RIGHTS

### 6.1 Assignment of IP
All intellectual property, including but not limited to:
- Ideas, inventions, and innovations
- Source code, algorithms, and technical documentation
- Designs, trademarks, and branding materials
- Business processes and methodologies
- Customer data and databases

created by any Founder in relation to the Business, whether before or after the Effective Date, shall be the **sole and exclusive property of the Company**.

### 6.2 Pre-Existing IP
{_format_ip_statement(startup_context, founder_a_name, founder_b_name)}

### 6.3 IP Assignment Documentation
Each Founder agrees to execute all necessary documents, including assignment deeds, to transfer and vest all IP rights in the Company.

### 6.4 Work for Hire
All work product created by Founders during their engagement shall be deemed "work made for hire" under applicable copyright law, with the Company as the author and owner.

### 6.5 Third-Party IP
Founders warrant that they have not and will not incorporate any third-party intellectual property into the Company's products without proper licenses.

---

## 7. DECISION-MAKING AND GOVERNANCE

### 7.1 Day-to-Day Decisions
Operational decisions within the defined roles and responsibilities of each Founder may be made independently.

### 7.2 Major Decisions Requiring Unanimous Consent
The following decisions require the written consent of **all Founders**:
- Changes to equity structure or issuance of new shares
- Fundraising, debt financing, or sale of significant assets
- Hiring or termination of C-level executives
- Changes to business model or pivot
- Entry into material contracts exceeding $[Amount]
- Sale, merger, or acquisition of the Company
- Admission of new co-founders
- Amendments to this Agreement
- Dissolution or winding up of the Company

### 7.3 Deadlock Resolution
In the event of a deadlock on major decisions:
1. Founders shall engage in good-faith mediation within 15 days
2. If unresolved, the matter shall be referred to a mutually agreed advisor/mentor
3. If still unresolved, the matter shall proceed to arbitration per Section 12

### 7.4 Board Composition
The Board of Directors shall initially consist of:
- {founder_a_name}
- {founder_b_name}
- [Independent Director - if applicable]

Board decisions shall require [simple majority / unanimous] approval.

---

## 8. COMPENSATION AND BENEFITS

### 8.1 Founder Salaries
Until the Company achieves [revenue milestone / funding milestone], Founders shall:
- [Draw no salary / Draw nominal salary of $[Amount] per month]

Post-milestone, salaries shall be determined by the Board based on:
- Company financial position
- Market benchmarks for similar roles
- Founder responsibilities and performance

### 8.2 Reimbursements
All reasonable business expenses shall be reimbursed upon submission of proper documentation.

### 8.3 Benefits
Founders shall be entitled to:
- [Health insurance]
- [Professional development budget]
- [Other benefits as approved by Board]

---

## 9. CONFIDENTIALITY AND NON-DISCLOSURE

### 9.1 Confidential Information
Each Founder acknowledges access to Confidential Information, including:
- Business plans, strategies, and financial projections
- Customer and supplier lists
- Technical specifications and trade secrets
- Marketing plans and pricing strategies
- Any information marked or reasonably understood as confidential

### 9.2 Obligations
Each Founder agrees to:
- Maintain strict confidentiality during and after termination
- Use Confidential Information solely for Company purposes
- Not disclose to any third party without prior written consent
- Return all confidential materials upon termination

### 9.3 Exceptions
Confidentiality obligations do not apply to information that:
- Is publicly available through no breach of this Agreement
- Was known to Founder before disclosure
- Is required to be disclosed by law or court order (with notice to Company)

### 9.4 Duration
Confidentiality obligations shall survive for [3/5/7] years after termination of this Agreement or the Founder's relationship with the Company.

---

## 10. NON-COMPETE AND NON-SOLICITATION

### 10.1 Non-Compete
During the term of this Agreement and for [12/24] months after termination, each Founder agrees not to:
- Directly or indirectly engage in any competing business
- Provide services to any competitor
- Invest in or advise any competing venture
- Establish a competing business

**Geographic Scope:** [To be specified]  
**Business Scope:** [Specific industry/market definition]

### 10.2 Non-Solicitation
For [12/24] months after termination, each Founder agrees not to:
- Solicit or hire any Company employees
- Solicit or divert any Company customers or clients
- Induce any suppliers or partners to terminate relationships with Company

### 10.3 Enforceability
If any provision is deemed unenforceable, it shall be modified to the minimum extent necessary to make it enforceable.

---

## 11. FOUNDER DEPARTURE AND EXIT

### 11.1 Voluntary Resignation
If a Founder voluntarily resigns:
- All unvested shares are immediately forfeited
- Vested shares may be subject to repurchase per Section 11.3
- Founder must provide [30/60/90] days notice

### 11.2 Termination for Cause
A Founder may be terminated for cause including:
- Material breach of this Agreement
- Gross negligence or willful misconduct
- Criminal conviction
- Prolonged absence without justification
- Violation of non-compete or confidentiality

Upon termination for cause:
- All unvested shares are forfeited
- Company has right to repurchase vested shares at fair market value

### 11.3 Share Repurchase (Buy-Sell)
Upon departure, the Company or remaining Founders shall have the right (but not obligation) to repurchase the departing Founder's vested shares:

**Valuation Method:**
- Pre-revenue: Lower of (a) original cost or (b) fair market value as determined by independent valuer
- Post-revenue: [X] times revenue or EBITDA, or fair market value

**Payment Terms:**
- [Lump sum within 90 days / Installments over 12-24 months]

### 11.4 Good Leaver vs. Bad Leaver
**Good Leaver** (death, disability, mutual agreement):
- Retains all vested shares
- Accelerated vesting of [X]% of unvested shares

**Bad Leaver** (termination for cause, breach):
- Forfeits unvested shares
- Vested shares repurchased at lower valuation

### 11.5 Drag-Along Rights
If [X]% of shareholders agree to sell the Company, minority shareholders (including departed Founders) must also sell on same terms.

### 11.6 Tag-Along Rights
If majority shareholders sell, minority shareholders have the right (but not obligation) to participate on same terms.

---

## 12. DISPUTE RESOLUTION

### 12.1 Good Faith Negotiation
In the event of any dispute, Founders shall first attempt to resolve through good-faith direct negotiation within 15 days.

### 12.2 Mediation
If negotiation fails, Founders agree to mediation by a mutually agreed mediator within 30 days. Cost shall be shared equally.

### 12.3 Arbitration
If mediation fails, disputes shall be resolved through arbitration:
- **Governing Law:** [Applicable jurisdiction's arbitration law]
- **Seat of Arbitration:** [City, State/Country]
- **Language:** English
- **Number of Arbitrators:** [1 / 3]
- **Arbitral Institution:** [To be specified]

### 12.4 Interim Relief
Either party may seek interim injunctive relief from courts of competent jurisdiction while arbitration is pending.

### 12.5 Costs
The prevailing party shall be entitled to recovery of reasonable legal costs and arbitration fees.

---

## 13. REPRESENTATIONS AND WARRANTIES

### 13.1 Each Founder represents and warrants that:
- They have full legal capacity to enter into this Agreement
- This Agreement does not conflict with any existing obligations
- They have disclosed all relevant information to other Founders
- All capital contributions are from legitimate sources
- They have not misrepresented their skills, experience, or connections

### 13.2 No Encumbrances
Founders warrant that their shares are free from any liens, charges, or encumbrances.

---

## 14. GENERAL PROVISIONS

### 14.1 Entire Agreement
This Agreement constitutes the entire understanding between Founders and supersedes all prior discussions, agreements, or understandings.

### 14.2 Amendments
This Agreement may only be amended by written document signed by all Founders.

### 14.3 Severability
If any provision is found invalid or unenforceable, the remaining provisions shall remain in full force.

### 14.4 Waiver
Failure to enforce any provision shall not constitute a waiver of future enforcement.

### 14.5 Governing Law
This Agreement shall be governed by and construed in accordance with the laws of [Jurisdiction].

### 14.6 Jurisdiction
The courts of [City, State] shall have exclusive jurisdiction, subject to arbitration clause.

### 14.7 Notices
All notices shall be in writing and delivered to the addresses mentioned above or such other address as may be notified.

### 14.8 Counterparts
This Agreement may be executed in counterparts, each constituting an original.

### 14.9 Successors and Assigns
This Agreement binds and benefits the parties and their respective heirs, legal representatives, and permitted assigns.

### 14.10 Assignment
No Founder may assign rights or obligations under this Agreement without written consent of all other Founders.

---

## 15. MISCELLANEOUS

### 15.1 Compliance with Laws
Founders shall ensure compliance with:
- Applicable corporate and company laws
- Applicable tax laws
- All applicable labor and employment laws
- Industry-specific regulations

### 15.2 Insurance
The Company shall obtain appropriate insurance coverage including:
- Key person insurance on Founders
- Directors and Officers (D&O) liability insurance
- Professional indemnity insurance

### 15.3 Financial Records
The Company shall maintain proper books of accounts and provide quarterly financial statements to all Founders.

### 15.4 Annual Review
This Agreement shall be reviewed annually and updated as necessary by mutual consent.

---

## SCHEDULE A: EQUITY CALCULATION MATRIX

{calc_table}

---

## SIGNATURES

**IN WITNESS WHEREOF**, the Founders have executed this Agreement on the date first written above.

**FOUNDER A**

Signature: ___________________________  
Name: {founder_a_name}  
Date: ___________________________  
Place: ___________________________

Witness 1:  
Name: ___________________________  
Signature: ___________________________  
Address: ___________________________


**FOUNDER B**

Signature: ___________________________  
Name: {founder_b_name}  
Date: ___________________________  
Place: ___________________________

Witness 1:  
Name: ___________________________  
Signature: ___________________________  
Address: ___________________________

---

## NOTARIZATION (Optional but Recommended)

Notarized before me on this _____ day of ________, {year_str}

Notary Public Signature: ___________________________  
Name: ___________________________  
Seal:

---

**DISCLAIMER:** This document is a template for informational purposes only and does not constitute legal advice. Founders are strongly advised to consult with qualified legal counsel before executing this Agreement. Laws vary by jurisdiction and individual circumstances.

---

**Document Version:** 1.0  
**Last Updated:** {date_str}  
**Prepared Using:** Founder Match Platform
"""
    
    return document


def generate_docx(document_content: str, founder_a_name: str, founder_b_name: str) -> io.BytesIO:
    """
    Generate a DOCX file from the document content with professional formatting.
    
    Args:
        document_content: The MMD document content
        founder_a_name: Name of founder A
        founder_b_name: Name of founder B
    
    Returns:
        BytesIO object containing the DOCX file
    """
    doc = Document()
    
    # Set document properties
    doc.core_properties.title = "Co-Founder Agreement"
    doc.core_properties.author = f"{founder_a_name} & {founder_b_name}"
    
    # Set default font to Times New Roman (professional legal document font)
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(12)
    
    # Set heading fonts
    for heading_level in range(1, 10):
        heading_style = doc.styles[f'Heading {heading_level}']
        heading_font = heading_style.font
        heading_font.name = 'Times New Roman'
        if heading_level == 1:
            heading_font.size = Pt(18)
            heading_font.bold = True
        elif heading_level == 2:
            heading_font.size = Pt(14)
            heading_font.bold = True
        elif heading_level == 3:
            heading_font.size = Pt(12)
            heading_font.bold = True
    
    # Helper function to clean ALL HTML/Markdown tags from text for tables
    def clean_for_table(text):
        """Remove ALL HTML and markdown formatting, return plain text."""
        import re
        if not text:
            return text
        # Remove ALL HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Remove markdown bold markers
        text = text.replace('**', '')
        return text.strip()
    
    # Helper function to format text with placeholder highlighting
    def add_text_with_placeholders(paragraph, text, is_table_cell=False):
        """Add text to paragraph, highlighting placeholders in yellow."""
        import re
        
        if is_table_cell:
            # For table cells, use completely clean plain text with proper DOCX formatting
            # First remove ALL HTML and markdown
            text = clean_for_table(text)
            # Split by placeholder pattern (matches [text] format)
            parts = re.split(r'(\[[^\]]+\])', text)
            for part in parts:
                if part.startswith('[') and part.endswith(']'):
                    run = paragraph.add_run(part)
                    run.font.highlight_color = 7  # Yellow highlight
                    run.font.color.rgb = RGBColor(128, 0, 0)  # Dark red text
                    run.font.bold = True
                elif part:
                    run = paragraph.add_run(part)
            return
        
        # For regular paragraphs (not table cells)
        # First remove any HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Split by placeholder pattern (matches [text] format)
        parts = re.split(r'(\[[^\]]+\])', text)
        for part in parts:
            if part.startswith('[') and part.endswith(']'):
                # Highlight placeholder
                run = paragraph.add_run(part)
                run.font.highlight_color = 7  # Yellow highlight
                run.font.color.rgb = RGBColor(128, 0, 0)  # Dark red text
                run.font.bold = True
            elif part:
                # Regular text - handle markdown bold markers (**text**)
                bold_parts = part.split('**')
                for i, bold_part in enumerate(bold_parts):
                    if bold_part:
                        run = paragraph.add_run(bold_part)
                        if i % 2 == 1:  # Odd indices are bold (between ** markers)
                            run.bold = True
    
    # Parse markdown and convert to DOCX
    lines = document_content.split('\n')
    current_table_rows = []
    
    for line in lines:
        line = line.strip()
        
        if not line:
            # Empty line - add spacing
            if current_table_rows:
                # Process accumulated table
                if len(current_table_rows) > 1:
                    table = doc.add_table(rows=len(current_table_rows), cols=len(current_table_rows[0]))
                    table.style = 'Light Grid Accent 1'
                    for i, row_data in enumerate(current_table_rows):
                        for j, cell_data in enumerate(row_data):
                            cell = table.rows[i].cells[j]
                            # Clear existing content
                            cell.text = ''
                            # Process cell content as table cell (plain text, no bold markdown)
                            paragraph = cell.paragraphs[0]
                            add_text_with_placeholders(paragraph, cell_data.strip(), is_table_cell=True)
                            # Set font for table cells
                            for para in cell.paragraphs:
                                for run in para.runs:
                                    run.font.name = 'Times New Roman'
                                    run.font.size = Pt(11)
                current_table_rows = []
            doc.add_paragraph()
            continue
        
        if line.startswith('|') and '|' in line[1:]:
            # Table row
            cells = [cell.strip() for cell in line.split('|')[1:-1]]
            if cells:  # Ignore separator rows (all dashes)
                if not all(c.replace('-', '').strip() == '' for c in cells):
                    current_table_rows.append(cells)
            continue
        
        # Process any accumulated table before non-table content
        if current_table_rows:
            if len(current_table_rows) > 1:
                table = doc.add_table(rows=len(current_table_rows), cols=len(current_table_rows[0]))
                table.style = 'Light Grid Accent 1'
                for i, row_data in enumerate(current_table_rows):
                    for j, cell_data in enumerate(row_data):
                        cell = table.rows[i].cells[j]
                        # Clear existing content
                        cell.text = ''
                        # Process cell content as table cell (plain text, no bold markdown)
                        paragraph = cell.paragraphs[0]
                        add_text_with_placeholders(paragraph, cell_data.strip(), is_table_cell=True)
                        # Set font for table cells
                        for para in cell.paragraphs:
                            for run in para.runs:
                                run.font.name = 'Times New Roman'
                                run.font.size = Pt(11)
            current_table_rows = []
        
        if line.startswith('# '):
            # Main title
            p = doc.add_heading(line[2:], level=0)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in p.runs:
                run.font.name = 'Times New Roman'
                run.font.size = Pt(20)
                run.font.bold = True
        elif line.startswith('## '):
            # Section heading
            p = doc.add_heading(line[3:], level=1)
            for run in p.runs:
                run.font.name = 'Times New Roman'
                run.font.size = Pt(14)
                run.font.bold = True
        elif line.startswith('### '):
            # Subsection heading
            p = doc.add_heading(line[4:], level=2)
            for run in p.runs:
                run.font.name = 'Times New Roman'
                run.font.size = Pt(12)
                run.font.bold = True
        elif line.startswith('**') and line.endswith('**'):
            # Bold paragraph
            p = doc.add_paragraph()
            add_text_with_placeholders(p, line[2:-2])
        elif line.startswith('- '):
            # Bullet point
            p = doc.add_paragraph(style='List Bullet')
            add_text_with_placeholders(p, line[2:])
        elif line.startswith('---'):
            # Horizontal rule - add a line break
            p = doc.add_paragraph('─' * 50)
            for run in p.runs:
                run.font.name = 'Times New Roman'
                run.font.size = Pt(10)
                run.font.color.rgb = RGBColor(128, 128, 128)
        elif line.startswith('*') and line.endswith('*') and not line.startswith('**'):
            # Italic text (disclaimer)
            p = doc.add_paragraph()
            add_text_with_placeholders(p, line[1:-1])
            for run in p.runs:
                run.italic = True
                run.font.size = Pt(10)
                run.font.color.rgb = RGBColor(100, 100, 100)
        else:
            # Regular paragraph
            p = doc.add_paragraph()
            add_text_with_placeholders(p, line)
    
    # Process any remaining table
    if current_table_rows:
        if len(current_table_rows) > 1:
            table = doc.add_table(rows=len(current_table_rows), cols=len(current_table_rows[0]))
            table.style = 'Light Grid Accent 1'
            for i, row_data in enumerate(current_table_rows):
                for j, cell_data in enumerate(row_data):
                    cell = table.rows[i].cells[j]
                    # Clear existing content
                    cell.text = ''
                    # Process cell content as table cell (plain text, no bold markdown)
                    paragraph = cell.paragraphs[0]
                    add_text_with_placeholders(paragraph, cell_data.strip(), is_table_cell=True)
                    # Set font for table cells
                    for para in cell.paragraphs:
                        for run in para.runs:
                            run.font.name = 'Times New Roman'
                            run.font.size = Pt(11)
    
    # Save to BytesIO
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    
    return buffer


def generate_pdf(document_content: str, founder_a_name: str, founder_b_name: str) -> io.BytesIO:
    """
    Generate a PDF file from the document content with professional formatting.
    
    Args:
        document_content: The MMD document content
        founder_a_name: Name of founder A
        founder_b_name: Name of founder B
    
    Returns:
        BytesIO object containing the PDF file
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                           rightMargin=72, leftMargin=72,
                           topMargin=72, bottomMargin=72)
    
    # Get styles
    styles = getSampleStyleSheet()
    
    # Professional font: Times-Roman (built-in, professional legal document font)
    # Custom styles with Times-Roman
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontName='Times-Bold',
        fontSize=20,
        spaceAfter=24,
        alignment=1,  # Center
        leading=24,
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontName='Times-Bold',
        fontSize=14,
        spaceBefore=18,
        spaceAfter=10,
        leading=16,
    )
    
    subheading_style = ParagraphStyle(
        'CustomSubheading',
        parent=styles['Heading3'],
        fontName='Times-Bold',
        fontSize=12,
        spaceBefore=12,
        spaceAfter=8,
        leading=14,
    )
    
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontName='Times-Roman',
        fontSize=11,
        spaceAfter=10,
        leading=13,
        leftIndent=0,
        rightIndent=0,
    )
    
    disclaimer_style = ParagraphStyle(
        'Disclaimer',
        parent=styles['Normal'],
        fontName='Times-Italic',
        fontSize=9,
        spaceAfter=8,
        leading=11,
        textColor=colors.HexColor('#666666'),
    )
    
    # Helper function to clean text for table cells (plain text, no HTML or markdown)
    def clean_for_table_pdf(text):
        """Clean text for table cells - remove all HTML and markdown, keep plain text."""
        import re
        if not text:
            return text
        # Remove ALL HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Remove markdown bold markers but keep the text
        text = text.replace('**', '')
        return text.strip()
    
    # Helper function to convert markdown bold to HTML bold properly
    def convert_markdown_bold_to_html(text):
        """Convert **text** to <b>text</b> properly."""
        import re
        # Use regex to find pairs of ** and convert to <b>...</b>
        # Pattern: **anything** (non-greedy)
        text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
        return text
    
    # Helper function to format placeholders with highlighting (for Paragraphs)
    def format_text_with_placeholders(text, for_table=False):
        """Format text, highlighting placeholders. If for_table, return plain text."""
        import re
        
        if for_table:
            # For tables, return completely clean plain text
            return clean_for_table_pdf(text)
        
        # For paragraphs, use HTML formatting for ReportLab
        # First remove any stray HTML tags
        text = re.sub(r'<font[^>]*>', '', text)
        text = re.sub(r'</font>', '', text)
        
        # Replace placeholders with highlighted HTML for PDF
        def replace_placeholder(match):
            placeholder_text = match.group(0)
            # Use dark red text and bold for placeholders
            return f'<font color="#800000"><b>{placeholder_text}</b></font>'
        
        text = re.sub(r'\[[^\]]+\]', replace_placeholder, text)
        
        # Convert markdown bold to HTML bold properly
        text = convert_markdown_bold_to_html(text)
        
        return text
    
    # Build document
    story = []
    current_table_rows = []
    
    lines = document_content.split('\n')
    
    for line in lines:
        line = line.strip()
        
        if not line:
            # Process accumulated table before empty line
            if current_table_rows and len(current_table_rows) > 1:
                # Create table
                table_data = []
                for row in current_table_rows:
                    # Format each cell - use plain text for tables
                    formatted_row = []
                    for cell in row:
                        formatted_cell = format_text_with_placeholders(cell, for_table=True)
                        formatted_row.append(formatted_cell)
                    table_data.append(formatted_row)
                
                table = Table(table_data, colWidths=[inch * 1.5] * len(current_table_rows[0]))
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8E8E8')),
                    ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, -1), 'Times-Roman'),
                    ('FONTSIZE', (0, 0), (-1, -1), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                    ('TOPPADDING', (0, 0), (-1, -1), 8),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ]))
                story.append(table)
                story.append(Spacer(1, 12))
            current_table_rows = []
            story.append(Spacer(1, 12))
            continue
        
        if line.startswith('|') and '|' in line[1:]:
            # Table row
            cells = [cell.strip() for cell in line.split('|')[1:-1]]
            if cells:  # Ignore separator rows (all dashes)
                if not all(c.replace('-', '').strip() == '' for c in cells):
                    current_table_rows.append(cells)
            continue
        
        # Process any accumulated table before non-table content
        if current_table_rows and len(current_table_rows) > 1:
            table_data = []
            for row in current_table_rows:
                formatted_row = []
                for cell in row:
                    formatted_cell = format_text_with_placeholders(cell, for_table=True)
                    formatted_row.append(formatted_cell)
                table_data.append(formatted_row)
            
            table = Table(table_data, colWidths=[inch * 1.5] * len(current_table_rows[0]))
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8E8E8')),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, -1), 'Times-Roman'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            story.append(table)
            story.append(Spacer(1, 12))
            current_table_rows = []
        
        if line.startswith('# '):
            formatted_text = format_text_with_placeholders(line[2:])
            story.append(Paragraph(formatted_text, title_style))
        elif line.startswith('## '):
            formatted_text = format_text_with_placeholders(line[3:])
            story.append(Paragraph(formatted_text, heading_style))
        elif line.startswith('### '):
            formatted_text = format_text_with_placeholders(line[4:])
            story.append(Paragraph(formatted_text, subheading_style))
        elif line.startswith('---'):
            # Horizontal rule
            story.append(Spacer(1, 10))
            t = Table([['─' * 80]], colWidths=[6.5*inch])
            t.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#999999')),
                ('FONTNAME', (0, 0), (-1, -1), 'Times-Roman'),
            ]))
            story.append(t)
            story.append(Spacer(1, 10))
        elif line.startswith('*') and line.endswith('*') and not line.startswith('**'):
            formatted_text = format_text_with_placeholders(line[1:-1])
            story.append(Paragraph(formatted_text, disclaimer_style))
        else:
            formatted_text = format_text_with_placeholders(line)
            story.append(Paragraph(formatted_text, normal_style))
    
    # Process any remaining table
    if current_table_rows and len(current_table_rows) > 1:
        table_data = []
        for row in current_table_rows:
            formatted_row = []
            for cell in row:
                formatted_cell = format_text_with_placeholders(cell, for_table=True)
                formatted_row.append(formatted_cell)
            table_data.append(formatted_row)
        
        table = Table(table_data, colWidths=[inch * 1.5] * len(current_table_rows[0]))
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8E8E8')),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, -1), 'Times-Roman'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(table)
    
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    
    return buffer


def generate_and_save_document(
    clerk_user_id: str,
    workspace_id: str,
    scenario_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate agreement document and save to storage.
    
    Args:
        clerk_user_id: Clerk user ID
        workspace_id: Workspace ID
        scenario_id: Optional specific scenario ID (uses current if not provided)
    
    Returns:
        Dict with document info and download URLs
    """
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    # Use admin client for storage operations to bypass RLS (matching document_service.py pattern)
    supabase_storage = get_supabase_admin()
    if supabase_storage is None:
        log_error("WARNING: SERVICE_ROLE_KEY not set - storage operations may fail due to RLS policies")
        log_error("Please set SUPABASE_SERVICE_ROLE_KEY in your environment variables")
        supabase_storage = supabase  # Fallback to regular client
    else:
        log_info("Using admin client (service role key) for storage upload - RLS bypassed")
    
    # Get workspace title
    workspace = supabase.table('workspaces').select('title').eq('id', workspace_id).execute()
    workspace_title = workspace.data[0].get('title', 'The Company') if workspace.data else 'The Company'
    
    # Get scenario
    if scenario_id:
        scenario_query = supabase.table('equity_scenarios').select(
            '*, founder_a:founders!founder_a_id(id, name), founder_b:founders!founder_b_id(id, name)'
        ).eq('id', scenario_id).execute()
    else:
        # Get current scenario
        scenario_query = supabase.table('equity_scenarios').select(
            '*, founder_a:founders!founder_a_id(id, name), founder_b:founders!founder_b_id(id, name)'
        ).eq('workspace_id', workspace_id).eq('is_current', True).execute()
    
    if not scenario_query.data:
        raise ValueError("No approved equity scenario found. Please complete the equity questionnaire and approve a scenario first.")
    
    scenario = scenario_query.data[0]
    
    if scenario['status'] != 'approved':
        raise ValueError("Only approved scenarios can generate documents")
    
    # Get questionnaire responses
    responses = get_questionnaire_responses(clerk_user_id, workspace_id)
    
    # Extract founder names
    founder_a = scenario.get('founder_a', {})
    founder_b = scenario.get('founder_b', {})
    founder_a_name = founder_a.get('name', 'Founder A')
    founder_b_name = founder_b.get('name', 'Founder B')
    
    # Get advisor info if workspace has an advisor
    advisor = supabase.table('workspace_participants').select(
        'user:founders!user_id(id, name)'
    ).eq('workspace_id', workspace_id).eq('role', 'ADVISOR').execute()
    
    if advisor.data and advisor.data[0].get('user'):
        scenario['advisor_name'] = advisor.data[0]['user'].get('name', 'Project Advisor')
    
    # Generate MMD document
    mmd_content = generate_mmd_document(scenario, responses, workspace_title)
    
    # Generate DOCX
    docx_buffer = generate_docx(mmd_content, founder_a_name, founder_b_name)
    
    # Generate PDF
    pdf_buffer = generate_pdf(mmd_content, founder_a_name, founder_b_name)
    
    # Upload to storage
    doc_id = str(uuid.uuid4())
    docx_path = f"{workspace_id}/agreements/{doc_id}.docx"
    pdf_path = f"{workspace_id}/agreements/{doc_id}.pdf"
    
    # Upload DOCX - fail entire operation if upload fails
    try:
        log_info(f"Uploading DOCX to storage path: {docx_path}")
        storage_response = supabase_storage.storage.from_('workspace-documents').upload(
            docx_path,
            docx_buffer.getvalue(),
            file_options={
                'content-type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'upsert': False
            }
        )
        # Handle response - Supabase Python SDK returns dict or object
        if isinstance(storage_response, dict) and storage_response.get('error'):
            error_msg = storage_response['error']
            if isinstance(error_msg, dict):
                error_msg = error_msg.get('message', str(error_msg))
            raise ValueError(f"Storage upload failed: {error_msg}")
        elif hasattr(storage_response, 'error') and storage_response.error:
            error_msg = storage_response.error
            if isinstance(error_msg, dict):
                error_msg = error_msg.get('message', str(error_msg))
            raise ValueError(f"Storage upload failed: {error_msg}")
    except Exception as e:
        log_error(f"Failed to upload DOCX: {e}")
        raise ValueError(f"Failed to upload document file: {str(e)}")
    
    # Upload PDF - fail entire operation if upload fails
    try:
        log_info(f"Uploading PDF to storage path: {pdf_path}")
        storage_response = supabase_storage.storage.from_('workspace-documents').upload(
            pdf_path,
            pdf_buffer.getvalue(),
            file_options={
                'content-type': 'application/pdf',
                'upsert': False
            }
        )
        # Handle response - Supabase Python SDK returns dict or object
        if isinstance(storage_response, dict) and storage_response.get('error'):
            error_msg = storage_response['error']
            if isinstance(error_msg, dict):
                error_msg = error_msg.get('message', str(error_msg))
            raise ValueError(f"Storage upload failed: {error_msg}")
        elif hasattr(storage_response, 'error') and storage_response.error:
            error_msg = storage_response.error
            if isinstance(error_msg, dict):
                error_msg = error_msg.get('message', str(error_msg))
            raise ValueError(f"Storage upload failed: {error_msg}")
    except Exception as e:
        log_error(f"Failed to upload PDF: {e}")
        # Clean up DOCX if PDF fails
        try:
            supabase_storage.storage.from_('workspace-documents').remove([docx_path])
        except Exception:
            pass
        raise ValueError(f"Failed to upload document file: {str(e)}")
    
    # Generate signed URLs
    docx_url = None
    pdf_url = None
    
    if docx_path:
        try:
            docx_signed = supabase_storage.storage.from_('workspace-documents').create_signed_url(docx_path, 3600)
            docx_url = docx_signed.get('signedUrl') if isinstance(docx_signed, dict) else getattr(docx_signed, 'signedUrl', None)
        except Exception as e:
            log_error(f"Failed to create signed URL for DOCX: {e}")
    
    if pdf_path:
        try:
            pdf_signed = supabase_storage.storage.from_('workspace-documents').create_signed_url(pdf_path, 3600)
            pdf_url = pdf_signed.get('signedUrl') if isinstance(pdf_signed, dict) else getattr(pdf_signed, 'signedUrl', None)
        except Exception as e:
            log_error(f"Failed to create signed URL for PDF: {e}")
    
    # Save document record
    doc_record = {
        'workspace_id': workspace_id,
        'scenario_id': scenario['id'],
        'document_content': mmd_content,
        'pdf_url': pdf_path,
        'docx_url': docx_path,
        'generated_by': founder_id,
    }
    
    # Use admin client for database insert to bypass RLS (matching document_service.py pattern)
    db_client = get_supabase_admin() or supabase
    result = db_client.table('generated_equity_documents').insert(doc_record).execute()
    
    if not result.data:
        raise ValueError("Failed to save document record")
    
    _log_audit(
        workspace_id, founder_id,
        'generate_equity_document',
        'generated_equity_document',
        result.data[0]['id']
    )
    
    return {
        'id': result.data[0]['id'],
        'mmd_content': mmd_content,
        'docx_url': docx_url,
        'pdf_url': pdf_url,
        'generated_at': result.data[0]['generated_at'],
    }


def get_document(
    clerk_user_id: str,
    workspace_id: str,
    document_id: str
) -> Dict[str, Any]:
    """
    Get a previously generated document with fresh signed URLs.
    
    Args:
        clerk_user_id: Clerk user ID
        workspace_id: Workspace ID
        document_id: Document ID
    
    Returns:
        Document info with signed URLs
    """
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    # Use admin client for storage operations to bypass RLS
    supabase_admin = get_supabase_admin()
    
    doc = supabase.table('generated_equity_documents').select('*').eq(
        'id', document_id
    ).eq('workspace_id', workspace_id).execute()
    
    log_info(f"get_document: fetched doc data: {doc.data}")
    
    if not doc.data:
        raise ValueError("Document not found")
    
    doc_data = doc.data[0]
    
    # Generate fresh signed URLs using admin client
    docx_url = None
    pdf_url = None
    
    if doc_data.get('docx_url'):
        try:
            log_info(f"Generating signed URL for DOCX path: {doc_data['docx_url']}")
            docx_signed = supabase_admin.storage.from_('workspace-documents').create_signed_url(
                doc_data['docx_url'], 3600
            )
            log_info(f"DOCX signed URL response: {docx_signed}")
            docx_url = docx_signed.get('signedUrl') if isinstance(docx_signed, dict) else getattr(docx_signed, 'signedUrl', None)
        except Exception as e:
            log_error(f"Failed to create signed URL for DOCX: {e}")
    
    if doc_data.get('pdf_url'):
        try:
            log_info(f"Generating signed URL for PDF path: {doc_data['pdf_url']}")
            pdf_signed = supabase_admin.storage.from_('workspace-documents').create_signed_url(
                doc_data['pdf_url'], 3600
            )
            log_info(f"PDF signed URL response: {pdf_signed}")
            pdf_url = pdf_signed.get('signedUrl') if isinstance(pdf_signed, dict) else getattr(pdf_signed, 'signedUrl', None)
        except Exception as e:
            log_error(f"Failed to create signed URL for PDF: {e}")
    
    return {
        'id': doc_data['id'],
        'scenario_id': doc_data['scenario_id'],
        'mmd_content': doc_data['document_content'],
        'docx_url': docx_url,
        'pdf_url': pdf_url,
        'generated_at': doc_data['generated_at'],
    }


def list_documents(
    clerk_user_id: str,
    workspace_id: str
) -> list:
    """
    List all generated documents for a workspace.
    
    Args:
        clerk_user_id: Clerk user ID
        workspace_id: Workspace ID
    
    Returns:
        List of document records
    """
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    docs = supabase.table('generated_equity_documents').select(
        'id, scenario_id, generated_at, version'
    ).eq('workspace_id', workspace_id).order('generated_at', desc=True).execute()
    
    return docs.data or []


def download_document(
    clerk_user_id: str,
    workspace_id: str,
    document_id: str,
    file_type: str = 'pdf'
) -> Tuple[bytes, str, str]:
    """
    Download document file content directly (proxy download).
    This avoids exposing Supabase signed URLs to the client.
    
    Args:
        clerk_user_id: Clerk user ID
        workspace_id: Workspace ID
        document_id: Document ID
        file_type: 'pdf' or 'docx'
    
    Returns:
        Tuple of (file_content, content_type, filename)
    """
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    supabase_admin = get_supabase_admin()
    
    # Get document record
    doc = supabase.table('generated_equity_documents').select('*').eq(
        'id', document_id
    ).eq('workspace_id', workspace_id).execute()
    
    if not doc.data:
        raise ValueError("Document not found")
    
    doc_data = doc.data[0]
    
    # Determine which file to download
    if file_type == 'pdf':
        file_path = doc_data.get('pdf_url')
        content_type = 'application/pdf'
        filename = f"co-founder-agreement-{document_id[:8]}.pdf"
    elif file_type == 'docx':
        file_path = doc_data.get('docx_url')
        content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        filename = f"co-founder-agreement-{document_id[:8]}.docx"
    else:
        raise ValueError("Invalid file type. Use 'pdf' or 'docx'")
    
    if not file_path:
        raise ValueError(f"No {file_type.upper()} file available for this document")
    
    # Download file from Supabase storage
    try:
        log_info(f"Downloading file from storage: {file_path}")
        file_response = supabase_admin.storage.from_('workspace-documents').download(file_path)
        
        if file_response is None:
            raise ValueError("Failed to download file from storage")
        
        # file_response is bytes
        return file_response, content_type, filename
        
    except Exception as e:
        log_error(f"Failed to download file: {e}")
        raise ValueError(f"Failed to download file: {str(e)}")
