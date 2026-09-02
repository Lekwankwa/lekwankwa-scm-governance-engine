"""
📋 AUTHENTICATION MODULE - MUNICIPAL SCM GOVERNANCE ENGINE
========================================================================

This module implements secure multi-factor authentication, role-based access 
control (RBAC), and MFMA compliance validation for the Lekwankwa SCM system.

QUICK START
========================================================================

1. STANDALONE DEMO (No main app required):
   $ streamlit run auth_demo.py
   
   Opens a clean, isolated authentication demo with login, signup, and 
   role-based tender escalation panel.

2. INTEGRATED WITH MAIN APP:
   $ streamlit run app.py
   
   Runs the full Municipal SCM Governance Engine with authentication gate.
   Users must log in before accessing the tender registry features.

3. IMPORT IN YOUR OWN APP:
   
   from auth_module import render_auth_interface, init_session_state
   
   init_session_state()  # Initialize session on first load
   
   if not st.session_state.authenticated:
       render_auth_interface()
       st.stop()


FILE STRUCTURE
========================================================================

auth_module.py
    ├── init_session_state()
    │   └── Initializes: authenticated, current_user, mock_users
    │
    ├── VALIDATION FUNCTIONS
    │   ├── validate_email() — .gov.za domain check
    │   ├── validate_persal() — 7-8 digit check
    │   ├── validate_password() — 8+ chars, confirmation match
    │   └── validate_name() — Length & character check
    │
    ├── UTILITY FUNCTIONS
    │   ├── hash_password() — SHA256 (production: upgrade to bcrypt)
    │   └── verify_password() — Password hash verification
    │
    ├── UI COMPONENTS
    │   ├── render_signup_form() — Registration form with validation
    │   ├── render_login_form() — Login form with credential check
    │   ├── render_logout_button() — Sidebar logout button
    │   └── render_auth_panel() — Authenticated user dashboard
    │
    └── render_auth_interface()
        └── Main router: Shows login/signup OR auth panel based on state

app.py
    ├── Imports: auth_module.render_auth_interface
    ├── Authentication Gate (early in app.py)
    │   ├── init_session_state()
    │   └── if not authenticated: render_auth_interface() + st.stop()
    └── Main App Logic (only runs if authenticated)

auth_demo.py
    └── Standalone Streamlit demo for testing auth module in isolation


FEATURES & REQUIREMENTS
========================================================================

🔐 AUTHENTICATION
✅ Login form with email/password verification
✅ Sign-up form with comprehensive validation
✅ Password hashing (SHA256 MVP; upgrade to bcrypt in production)
✅ Session-based auth state persistence (browser session only)
✅ User data storage in st.session_state.mock_users dict

📋 VALIDATION RULES

Email:
  • Must end with '.gov.za' (South African government domain)
  • Must contain '@' symbol
  • Local part must be at least 2 characters
  • Example: john.doe@capricorn.gov.za ✅

Persal (Personnel Number):
  • Must be exactly 7 or 8 digits
  • No letters, spaces, or special characters
  • Examples: 1234567 ✅  or  12345678 ✅

Password:
  • Minimum 8 characters
  • Must match confirmation field exactly
  • No complexity rules (can be simple for MVP)
  • Example: SecurePass123 ✅

First Name(s) & Surname:
  • Minimum 2 characters
  • Letters, spaces, and hyphens only
  • No numbers or special characters
  • Examples: Mary-Jane ✅  or  Jean Paul ✅

Role (Selectbox):
  • SCM Clerk
  • Buyer
  • SCM Manager
  • Director: Procurement
  • Chief Financial Officer


🔓 ROLE-BASED ACCESS CONTROL (RBAC)

APPROVAL AUTHORITY (Can Approve Escalations)
───────────────────────────────────────────
✅ SCM Manager
✅ Director: Procurement
✅ Chief Financial Officer

ACTION: "Confirm and Approve Price Escalation" button is ACTIVE
  → Click button → Generates audit trail with timestamp, name, Persal
  → Displays success message with SHA256 document ID
  → Triggers balloons animation 🎈

OPERATIONAL STAFF (Must Submit to Management)
──────────────────────────────────────────────
✅ SCM Clerk
✅ Buyer

ACTION: "Confirm and Approve Price Escalation" button is DISABLED
  → "Submit Calculation to SCM Management for Approval" button is ACTIVE
  → Click button → Shows MFMA compliance warning message
  → Displays audit trail with submission timestamp and user details
  → Explains legal authority limitations under MFMA Section 36(5)


📊 MOCK TENDER ESCALATION PANEL

When authenticated, users see a functional dashboard showing:

Display Elements:
  • Contract Reference: CTR-2024-001-SECURITY
  • Original Base Value: R1,000,000.00
  • Escalation Rate: 5.2%
  • Proposed New Value: R1,052,000.00
  • Category: Security Services (CPI-Linked)
  • Evaluation Period: August 2026 Stats SA Release

MFMA Compliance Status: ✅ Within allowable inflation boundary

Role-Specific Actions:
  • Management: Approve button + audit trail
  • Operational: Submit button + MFMA warning


🔐 SECURITY CONSIDERATIONS

MVP (Current Implementation)
────────────────────────────
✓ Password hashed with SHA256
✓ Session-based auth (cleared on page refresh)
✓ Email domain validation (.gov.za)
✓ Persal format validation (7-8 digits)
✓ Session state stored in browser memory

⚠️ Production Recommendations
──────────────────────────────
• Upgrade password hashing to bcrypt:
  pip install bcrypt
  Then replace hash_password() with:
    import bcrypt
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

• Add database backend for user persistence:
  - Option A: SQLite (file-based, simple)
  - Option B: PostgreSQL (enterprise-grade)
  - Option C: Firebase/Cloud Firestore (fully managed)

• Implement session persistence (optional):
  - Cookie-based tokens
  - Redis session store
  - JWT tokens

• Add multi-factor authentication (MFA):
  - SMS OTP
  - Email verification codes
  - TOTP (Time-based One-Time Password)

• Audit logging to database:
  - Login attempts (success/failure)
  - Approval actions
  - Password changes
  - Account modifications

• Rate limiting:
  - Prevent brute force login attempts
  - Limit sign-up attempts per IP

• HTTPS enforcement:
  - Use Streamlit Cloud or reverse proxy


TESTING THE APPLICATION
========================================================================

MANUAL TEST WORKFLOW

1. Start the demo:
   $ streamlit run auth_demo.py

2. Test Sign-Up:
   ✅ Enter valid .gov.za email
   ✅ Enter 7-digit Persal
   ✅ Choose role: "SCM Clerk"
   ✅ Enter matching 8-char passwords
   ✅ Click Register → See success message

3. Test Invalid Inputs:
   ✗ Email: test@gmail.com → Error: ".gov.za required"
   ✗ Persal: 123 → Error: "7 or 8 digits required"
   ✗ Password: short → Error: "Min 8 characters"
   ✗ Passwords don't match → Error: "Passwords do not match"

4. Test Login:
   ✅ Switch to Login tab
   ✅ Enter email from signup
   ✅ Enter correct password
   ✅ Click Login → Authenticated, shown welcome banner

5. Test RBAC - SCM Clerk (Operational):
   ✅ See welcome banner with name and role
   ✅ "Approve" button is DISABLED (greyed out)
   ✅ "Submit to Management" button is ACTIVE
   ✅ Click Submit → MFMA warning message displays

6. Test RBAC - SCM Manager (Approval Authority):
   ✅ Log out and register new account as "SCM Manager"
   ✅ See welcome banner with manager name and role
   ✅ "Approve" button is ACTIVE
   ✅ Click Approve → Audit trail log displays
   ✅ Log includes timestamp, name, Persal, role

7. Test Logout:
   ✅ Click "🚪 Log Out" in sidebar
   ✅ Redirected to login tab
   ✅ Session state cleared


INTEGRATION INTO MAIN APP
========================================================================

The auth module is already integrated into app.py:

1. Import added (line ~24):
   from auth_module import render_auth_interface, init_session_state

2. Authentication gate (lines ~35-45):
   init_session_state()
   if not st.session_state.authenticated:
       render_auth_interface()
       st.stop()

3. Main app logic (lines ~47+):
   Only executes if user is authenticated

This means:
  • Running 'streamlit run app.py' now requires login first
  • After login, user sees the full tender registry interface
  • Logout clears state and returns to login screen
  • User info is available in st.session_state.current_user


CUSTOMIZATION GUIDE
========================================================================

CHANGE EMAIL DOMAIN REQUIREMENT
────────────────────────────────
In auth_module.py, modify validate_email():

    # Current (lines 35-50):
    if not email.endswith(".gov.za"):
        return False, "Must end with '.gov.za'"
    
    # Change to, e.g., corporate domain:
    if not email.endswith("@mycompany.com"):
        return False, "Must use corporate email"

ADD MORE ROLES
──────────────
In auth_module.py, modify render_signup_form() line ~250:

    role = st.selectbox(
        "Designation / Role",
        options=[
            "SCM Clerk",
            "Buyer",
            "SCM Manager",
            "Director: Procurement",
            "Chief Financial Officer",
            "NEW ROLE HERE"  # Add your role
        ]
    )

Then in render_auth_panel() line ~470+, update RBAC logic:

    approval_roles = ["SCM Manager", "Director: Procurement", ...]
    operational_roles = ["SCM Clerk", "Buyer", ...]

CHANGE PASSWORD REQUIREMENTS
─────────────────────────────
In auth_module.py, modify validate_password() line ~110:

    # Current:
    if len(password) < 8:
        return False, "Min 8 characters"
    
    # Change to, e.g., 12 characters:
    if len(password) < 12:
        return False, "Min 12 characters"
    
    # Or add complexity check:
    import re
    if not re.search(r'[A-Z]', password):
        return False, "Must contain uppercase letter"
    if not re.search(r'[0-9]', password):
        return False, "Must contain a digit"

CHANGE TENDER PANEL VALUES
───────────────────────────
In auth_module.py, modify render_auth_panel() lines ~480-490:

    base_value = 1_000_000.00  # Change this
    escalation_rate = 5.2  # Change this
    new_value = base_value * (1 + escalation_rate / 100)


API REFERENCE
========================================================================

render_auth_interface()
  └─ Main entry point
  ├─ Calls: init_session_state()
  ├─ Calls: render_logout_button()
  ├─ If authenticated: render_auth_panel()
  └─ Else: render_signup_form() + render_login_form()

init_session_state()
  └─ Initializes st.session_state keys:
     ├─ authenticated (bool, default False)
     ├─ current_user (dict, default None)
     ├─ mock_users (dict, default {})
     └─ auth_mode (str, default "login")

validate_email(email: str) → Tuple[bool, str]
  └─ Returns (is_valid, error_message)

validate_persal(persal: str) → Tuple[bool, str]
  └─ Returns (is_valid, error_message)

validate_password(password: str, confirm: str) → Tuple[bool, str]
  └─ Returns (is_valid, error_message)

validate_name(name: str, field_name: str) → Tuple[bool, str]
  └─ Returns (is_valid, error_message)

hash_password(password: str) → str
  └─ Returns SHA256 hash (hex digest)

verify_password(stored_hash: str, password: str) → bool
  └─ Returns True if password matches hash

render_signup_form() → None
  └─ Side effect: Displays Streamlit form UI
  └─ Stores user to st.session_state.mock_users on success

render_login_form() → None
  └─ Side effect: Displays Streamlit login form UI
  └─ Sets st.session_state.authenticated = True on success
  └─ Calls st.rerun() to refresh UI

render_logout_button() → None
  └─ Side effect: Shows logout button in sidebar (if authenticated)
  └─ Clears session state and calls st.rerun()

render_auth_panel() → None
  └─ Side effect: Displays authenticated user dashboard
  └─ Implements RBAC for approval vs. submission actions


KNOWN LIMITATIONS (MVP)
========================================================================

1. Session Persistence:
   • Auth state is cleared on page refresh
   • Use session_state only (no database)
   • Acceptable for MVP; upgrade with database for production

2. Password Storage:
   • Uses SHA256 (fast, simple, not production-grade)
   • No salt or pepper
   • Upgrade to bcrypt for production

3. User Data:
   • Stored in memory only (st.session_state.mock_users)
   • Lost when app restarts
   • Add database for persistence

4. Rate Limiting:
   • No brute-force protection
   • No login attempt throttling
   • Add Streamlit security extensions for production

5. Audit Logging:
   • Audit trail shown to user only (not persisted)
   • No database audit log
   • Add enterprise audit system for compliance


TROUBLESHOOTING
========================================================================

Issue: "ModuleNotFoundError: No module named 'auth_module'"
Fix: Make sure auth_module.py is in the same directory as app.py

Issue: "st.session_state is empty after refresh"
Fix: This is expected. Session state resets on page refresh in Streamlit.
     For persistence, add a database backend.

Issue: Login form not working
Fix: Check that mock_users dict was populated during signup.
     Try registering a new account first, then login.

Issue: "Approve" button doesn't show for SCM Manager
Fix: Check that role matches exactly: "SCM Manager" (with capital S and M)

Issue: Password validation failing
Fix: Ensure password is exactly 8+ characters and matches confirmation.


VERSION & CHANGELOG
========================================================================

Version 1.0 - 2026-09-01
• Initial MVP release
• Sign-up with email, Persal, role, password validation
• Login with credential verification
• Role-based access control (Approval vs. Operational)
• Mock tender escalation panel
• MFMA compliance warnings
• Audit trail logging (display-only, MVP)
• Sidebar logout functionality
• Standalone auth_demo.py for testing
• Integration into app.py


LICENSE & COMPLIANCE
========================================================================

This module implements features required by:
  ✓ Municipal Finance Management Act (MFMA), Section 36(5)
  ✓ Lekwankwa Corporation municipal governance policies
  ✓ Internal Audit Charter

For regulatory compliance questions, contact:
  • Lekwankwa Municipality Procurement Office
  • Chief Financial Officer (CFO)
  • Internal Audit Department


SUPPORT & CONTRIBUTIONS
========================================================================

For questions or improvements:
  1. Check this README thoroughly
  2. Review the inline code comments in auth_module.py
  3. Run auth_demo.py to test features
  4. Check the Streamlit error logs
  5. Consult Streamlit documentation: docs.streamlit.io

"""
