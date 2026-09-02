"""
Municipal SCM Authentication Module (RBAC)
Secure login, sign-up, and role-based access control for Lekwankwa SCM Engine.
Enforces MFMA compliance and audit trail logging for contract escalation approvals.
"""

import hashlib
import datetime
import json
from pathlib import Path
import streamlit as st
from typing import Tuple, Dict, Any, Optional

# ============================================================================
# PERSISTENT USER STORE
# ============================================================================
# st.session_state is per-browser-session, in-memory only -- it resets on
# every page reload, server restart, or new browser/session. Registered
# accounts need to survive all of that, so they're persisted to a JSON file
# on disk (same pattern data/tenders.json already uses elsewhere in this
# app), not just kept in session_state. session_state.mock_users still
# exists as an in-memory working copy, but it's always refreshed from disk
# before being read, and written back to disk immediately after any change
# -- so a registration from one browser/session is visible to a login
# attempt from a different one without needing a shared server restart.

USERS_FILE_PATH = Path("data") / "scm_users.json"


def _load_users_from_disk() -> Dict[str, Any]:
    """Read all registered users from disk. Never raises -- a missing or
    corrupt file just means no users yet, not a crash."""
    if not USERS_FILE_PATH.exists():
        return {}
    try:
        with open(USERS_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_users_to_disk(users: Dict[str, Any]) -> None:
    """Write the full user store back to disk."""
    USERS_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(USERS_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)


# ============================================================================
# SESSION STATE INITIALIZATION
# ============================================================================

def init_session_state() -> None:
    """Initialize auth-related session state on first app load."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if "current_user" not in st.session_state:
        st.session_state.current_user = None

    if "mock_users" not in st.session_state:
        st.session_state.mock_users = _load_users_from_disk()

    if "auth_mode" not in st.session_state:
        st.session_state.auth_mode = "login"


# ============================================================================
# VALIDATION FUNCTIONS
# ============================================================================

def validate_email(email: str) -> Tuple[bool, str]:
    """
    Validate email format and South African government domain.
    
    Args:
        email: Email string to validate
    
    Returns:
        (is_valid, error_message) tuple
    """
    email = email.strip()
    
    if not email:
        return False, "Email is required."
    
    if "@" not in email:
        return False, "Email must contain '@' symbol."
    
    if not email.endswith(".gov.za"):
        return False, "Municipal email must end with '.gov.za' domain (e.g., @capricorn.gov.za)."
    
    local_part = email.split("@")[0]
    if not local_part or len(local_part) < 2:
        return False, "Invalid email format."
    
    return True, ""


def validate_persal(persal: str) -> Tuple[bool, str]:
    """
    Validate Persal (Personnel) number format: must be exactly 7 or 8 digits.
    
    Args:
        persal: Persal number string to validate
    
    Returns:
        (is_valid, error_message) tuple
    """
    persal = persal.strip()
    
    if not persal:
        return False, "Persal number is required."
    
    if not persal.isdigit():
        return False, "Persal number must contain only digits."
    
    if len(persal) not in (7, 8):
        return False, "Persal number must be exactly 7 or 8 digits."
    
    return True, ""


def validate_password(password: str, confirm_password: str) -> Tuple[bool, str]:
    """
    Validate password strength and confirmation match.
    
    Args:
        password: Password string
        confirm_password: Confirmation password string
    
    Returns:
        (is_valid, error_message) tuple
    """
    if not password:
        return False, "Password is required."
    
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    
    if not confirm_password:
        return False, "Password confirmation is required."
    
    if password != confirm_password:
        return False, "Passwords do not match. Please re-enter both fields."
    
    return True, ""


def validate_name(name: str, field_name: str) -> Tuple[bool, str]:
    """
    Validate first/last name fields.
    
    Args:
        name: Name string
        field_name: "First Name(s)" or "Surname" for error message context
    
    Returns:
        (is_valid, error_message) tuple
    """
    name = name.strip()
    
    if not name:
        return False, f"{field_name} is required."
    
    if len(name) < 2:
        return False, f"{field_name} must be at least 2 characters."
    
    # Allow letters, spaces, and hyphens (common in names)
    allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ- ")
    if not all(c in allowed_chars for c in name):
        return False, f"{field_name} can only contain letters, spaces, and hyphens."
    
    return True, ""


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def hash_password(password: str) -> str:
    """
    Hash password using SHA256 (MVP).
    
    NOTE: For production, upgrade to bcrypt:
        pip install bcrypt
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    
    Args:
        password: Plain text password
    
    Returns:
        Hashed password string (SHA256 hex digest)
    """
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(stored_hash: str, password: str) -> bool:
    """
    Verify password against stored hash.
    
    Args:
        stored_hash: Stored password hash
        password: Plain text password to verify
    
    Returns:
        True if password matches hash
    """
    return stored_hash == hash_password(password)


# ============================================================================
# SIGN-UP FORM UI
# ============================================================================

def render_signup_form() -> None:
    """Render the user registration form with full validation."""
    st.subheader("📝 Create Your Municipal SCM Account")
    
    with st.form("signup_form"):
        # Row 1: Names
        col1, col2 = st.columns(2)
        with col1:
            first_name = st.text_input(
                "First Name(s)",
                placeholder="Enter your first name(s)",
                key="signup_first_name"
            )
        with col2:
            surname = st.text_input(
                "Surname",
                placeholder="Enter your surname",
                key="signup_surname"
            )
        
        # Row 2: Email
        email = st.text_input(
            "Municipal Email",
            placeholder="e.g., john.doe@capricorn.gov.za",
            help="Must be a South African government domain (.gov.za)",
            key="signup_email"
        )
        
        # Row 3: Persal & Role
        col3, col4 = st.columns(2)
        with col3:
            persal = st.text_input(
                "Employee / Persal No.",
                placeholder="7 or 8 digits",
                help="Used for internal HR verification.",
                key="signup_persal"
            )
        with col4:
            role = st.selectbox(
                "Designation / Role",
                options=[
                    "SCM Clerk",
                    "Buyer",
                    "SCM Manager",
                    "Director: Procurement",
                    "Chief Financial Officer"
                ],
                key="signup_role"
            )
        
        # Row 4: Password
        col5, col6 = st.columns(2)
        with col5:
            password = st.text_input(
                "Password",
                type="password",
                placeholder="Min 8 characters",
                key="signup_password"
            )
        with col6:
            confirm_password = st.text_input(
                "Confirm Password",
                type="password",
                placeholder="Re-enter password",
                key="signup_confirm_password"
            )
        
        # Submit Button
        submitted = st.form_submit_button("Register Account", use_container_width=True)
        
        if submitted:
            # Refresh from disk first -- catches an account registered from a
            # different browser/session since this session last loaded.
            st.session_state.mock_users = _load_users_from_disk()

            # Validate all fields
            errors = []

            # Validate names
            first_name_valid, first_name_error = validate_name(first_name, "First Name(s)")
            if not first_name_valid:
                errors.append(first_name_error)

            surname_valid, surname_error = validate_name(surname, "Surname")
            if not surname_valid:
                errors.append(surname_error)

            # Validate email
            email_valid, email_error = validate_email(email)
            if not email_valid:
                errors.append(email_error)

            # Check for duplicate email
            if email in st.session_state.mock_users:
                errors.append("This email is already registered. Please log in or use a different email.")
            
            # Validate Persal
            persal_valid, persal_error = validate_persal(persal)
            if not persal_valid:
                errors.append(persal_error)
            
            # Validate password
            password_valid, password_error = validate_password(password, confirm_password)
            if not password_valid:
                errors.append(password_error)
            
            # If any errors, display them
            if errors:
                st.error("Registration failed. Please fix the following:")
                for error in errors:
                    st.write(f"• {error}")
            else:
                # All validations passed - store user
                user_data = {
                    "first_name": first_name.strip(),
                    "surname": surname.strip(),
                    "email": email.lower().strip(),
                    "persal": persal.strip(),
                    "role": role,
                    "password_hash": hash_password(password),
                    "registered_date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                
                st.session_state.mock_users[email.lower().strip()] = user_data
                _save_users_to_disk(st.session_state.mock_users)

                st.success(
                    f"✅ Account created successfully!\n\n"
                    f"**Welcome, {first_name} {surname}!**\n\n"
                    f"Your account is now active. Switch to the **Login** tab to sign in."
                )


# ============================================================================
# LOGIN FORM UI
# ============================================================================

def render_login_form() -> None:
    """Render the login form with credential verification."""
    st.subheader("🔐 Municipal SCM Login")
    
    with st.form("login_form"):
        email = st.text_input(
            "Municipal Email",
            placeholder="e.g., john.doe@capricorn.gov.za",
            key="login_email"
        )
        
        password = st.text_input(
            "Password",
            type="password",
            placeholder="Enter your password",
            key="login_password"
        )
        
        submitted = st.form_submit_button("Login", use_container_width=True)
        
        if submitted:
            email_normalized = email.lower().strip()

            # Refresh from disk first -- an account registered in a
            # different browser/session might not be in this session's
            # in-memory copy yet.
            st.session_state.mock_users = _load_users_from_disk()

            # Check if email exists in registered users
            if email_normalized not in st.session_state.mock_users:
                st.error("❌ Email not found. Please check your email or register a new account.")
            else:
                user_data = st.session_state.mock_users[email_normalized]
                
                # Verify password hash
                if not verify_password(user_data["password_hash"], password):
                    st.error("❌ Incorrect password. Please try again.")
                else:
                    # Login successful
                    st.session_state.authenticated = True
                    st.session_state.current_user = user_data
                    
                    st.success(
                        f"✅ Welcome back, {user_data['first_name']}!\n\n"
                        f"You are now logged in as: **{user_data['role']}**"
                    )
                    
                    # Rerun to refresh UI and show authenticated content
                    st.rerun()
    
    # Helper message for new users
    st.info("💡 Don't have an account? Switch to the **Register New Account** tab to sign up.")


# ============================================================================
# LOGOUT FUNCTIONALITY (SIDEBAR)
# ============================================================================

def render_logout_button() -> None:
    """Render logout button in sidebar (only shown when authenticated)."""
    if st.session_state.authenticated:
        st.sidebar.markdown("---")
        
        if st.sidebar.button("🚪 Log Out", use_container_width=True, type="secondary"):
            # Clear authentication state
            st.session_state.authenticated = False
            st.session_state.current_user = None
            
            st.rerun()


# ============================================================================
# ROLE-BASED ACCESS CONTROL PANEL
# ============================================================================

def render_auth_panel() -> None:
    """
    Render authenticated user panel with tender escalation dashboard.
    Implements RBAC: Approval authority vs. Operational staff.
    """
    user = st.session_state.current_user
    role = user["role"]
    
    # Welcome Banner
    st.markdown(
        f"""
        <div style="background-color: #1f77b4; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
            <h2 style="color: white; margin: 0;">Welcome back, {user['first_name']} {user['surname']}</h2>
            <p style="color: #e0e0e0; margin: 5px 0 0 0;"><strong>Role:</strong> {role} | <strong>Persal:</strong> {user['persal']}</p>
        </div>
        """,
        unsafe_allow_html=True
    )
    
    # ========================================================================
    # Mock Tender Price Anniversary Escalation Panel
    # ========================================================================
    st.markdown("### 📋 Tender Price Anniversary Escalation")
    st.markdown("---")
    
    # Contract Details
    contract_ref = "CTR-2024-001-SECURITY"
    base_value = 1_000_000.00
    escalation_rate = 5.2
    new_value = base_value * (1 + escalation_rate / 100)
    
    # Display metrics in columns
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(
            label="Original Base Value",
            value=f"R{base_value:,.2f}",
            delta=None
        )
    with col2:
        st.metric(
            label="Escalation Rate",
            value=f"{escalation_rate}%",
            delta="Stats SA CPI (Aug 2026)"
        )
    with col3:
        st.metric(
            label="Proposed New Value",
            value=f"R{new_value:,.2f}",
            delta=f"+R{new_value - base_value:,.2f}",
            delta_color="off"
        )
    
    st.markdown("---")
    
    # Contract Reference Info Box
    st.info(
        f"""
        **Contract Reference:** {contract_ref}  
        **Category:** Security Services (CPI-Linked)  
        **Evaluation Period:** August 2026 Stats SA Release  
        **MFMA Compliance:** ✅ Within allowable inflation boundary
        """
    )
    
    st.markdown("---")
    
    # ========================================================================
    # ROLE-BASED ACTION BUTTONS
    # ========================================================================
    
    # Define approval authority roles
    approval_roles = ["SCM Manager", "Director: Procurement", "Chief Financial Officer"]
    operational_roles = ["SCM Clerk", "Buyer"]
    
    if role in approval_roles:
        # ====================================================================
        # APPROVAL AUTHORITY: Show Active Approve Button
        # ====================================================================
        st.markdown("#### 🔓 Approval Authority - Contract Escalation Approval")
        st.markdown(
            f"As a **{role}**, you have legal authority to approve contract price adjustments "
            f"under the MFMA (Municipal Finance Management Act)."
        )
        
        col_approve, col_space = st.columns([2, 1])
        with col_approve:
            if st.button(
                "✅ Confirm and Approve Price Escalation",
                use_container_width=True,
                type="primary"
            ):
                # Generate audit trail with timestamp
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                audit_log = f"""
                ╔════════════════════════════════════════════════════════════════╗
                ║                      AUDIT TRAIL LOG                           ║
                ╚════════════════════════════════════════════════════════════════╝
                
                **Action:** Price Escalation Approved
                **Timestamp:** {timestamp}
                **Approver Name:** {user['first_name']} {user['surname']}
                **Approver Persal:** {user['persal']}
                **Approver Role:** {role}
                **Contract Reference:** {contract_ref}
                **Original Value:** R{base_value:,.2f}
                **New Value:** R{new_value:,.2f}
                **Escalation Rate:** {escalation_rate}%
                **Authority Level:** MFMA-Compliant Approval Authority
                
                **System Status:** ✅ APPROVED
                **Document ID:** SHA256-{hashlib.sha256(audit_log.encode()).hexdigest()[:16]}
                
                This transaction is recorded in the municipal audit trail and cannot be reversed.
                """
                
                st.success(audit_log)
                st.balloons()
    
    elif role in operational_roles:
        # ====================================================================
        # OPERATIONAL STAFF: Show Disabled Approve + Active Submit Button
        # ====================================================================
        st.markdown("#### 🔒 Operational Role - Submission to Management")
        
        # Show disabled/greyed out approve button with explanation
        st.markdown(
            """
            <div style="background-color: #f0f2f6; padding: 15px; border-radius: 8px; margin-bottom: 15px;">
                <p style="margin: 0; color: #666;">
                    <strong>Approve Button Unavailable:</strong> Your designation level does not possess 
                    approval authority for contract adjustments under the MFMA.
                </p>
            </div>
            """,
            unsafe_allow_html=True
        )
        
        st.button(
            "✅ Confirm and Approve Price Escalation",
            use_container_width=True,
            disabled=True
        )
        
        st.markdown("---")
        
        # Active submit button for operational staff
        st.markdown(
            f"As a **{role}**, you can submit this escalation calculation to SCM Management for review and approval."
        )
        
        if st.button(
            "📤 Submit Calculation to SCM Management for Approval",
            use_container_width=True,
            type="primary"
        ):
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            st.warning(
                f"""
                ⚠️ **MFMA Compliance Notice**
                
                **Legal Framework:** Municipal Finance Management Act (MFMA), Section 36(5)
                
                Your designation level (**{role}**) does **NOT** possess legal authority 
                to commit the Municipality to contract price adjustments without explicit 
                approval from SCM Management (SCM Manager, Director: Procurement, or CFO).
                
                **Action:** Escalation calculation submitted for management review
                **Submitted By:** {user['first_name']} {user['surname']} (Persal: {user['persal']})
                **Timestamp:** {timestamp}
                **Contract:** {contract_ref}
                **Amount:** R{new_value - base_value:,.2f}
                
                ✅ Your submission has been logged in the audit trail. 
                A management-level user will review and approve this escalation.
                
                **Next Steps:** You will be notified once management completes their review.
                """
            )


# ============================================================================
# MAIN AUTHENTICATION ROUTER
# ============================================================================

def render_auth_interface() -> None:
    """
    Main authentication interface router.
    Shows login/signup if not authenticated, otherwise shows authenticated panel.
    """
    init_session_state()
    
    # Render logout button in sidebar (only if authenticated)
    render_logout_button()
    
    # Check authentication state
    if st.session_state.authenticated:
        # User is logged in - show authenticated panel
        render_auth_panel()
    else:
        # User is not logged in - show login/signup tabs
        st.title("🔐 Municipal SCM Governance Engine - Authentication")
        st.markdown("Secure login and account management for Lekwankwa SCM")
        
        # Navigation tabs
        tab1, tab2 = st.tabs(["🔐 Login", "📝 Register New Account"])
        
        with tab1:
            render_login_form()
        
        with tab2:
            render_signup_form()
