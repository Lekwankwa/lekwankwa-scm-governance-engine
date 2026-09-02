"""
Streamlit Authentication & RBAC Demo
Standalone demonstration of the Municipal SCM authentication module.

Run with: streamlit run auth_demo.py

This simplified demo shows the login/signup and role-based access control features
without requiring the full app.py dependencies.
"""

import streamlit as st
from auth_module import render_auth_interface

# ============================================================================
# Page Configuration
# ============================================================================
st.set_page_config(
    page_title="Municipal SCM - Authentication Demo",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================================
# Main Application
# ============================================================================

# Add a header with instructions
col1, col2 = st.columns([3, 1])
with col1:
    st.markdown(
        """
        # 🏛️ Municipal SCM Authentication Demo
        **Lekwankwa Governance Engine - Secure Access Control**
        """
    )
with col2:
    st.info(
        """
        **Quick Start:**
        1. Register a test account
        2. Login with credentials
        3. Test role-based access
        """
    )

st.markdown("---")

# Render the authentication interface (login/signup/RBAC panel)
render_auth_interface()

# ============================================================================
# Sidebar Information
# ============================================================================

st.sidebar.markdown("---")
st.sidebar.markdown("### ℹ️ Demo Information")

with st.sidebar.expander("Test Accounts (Pre-Registered)", expanded=False):
    st.markdown(
        """
        You can create your own accounts by registering with any valid 
        `.gov.za` email address.
        
        **Requirements:**
        - Email: Must end with `.gov.za`
        - Persal: Exactly 7 or 8 digits
        - Password: Min 8 characters
        - Role: Select from dropdown (5 options)
        
        **Example credentials:**
        ```
        Email: john.doe@capricorn.gov.za
        Persal: 1234567
        Role: SCM Clerk
        Password: SecurePass123
        ```
        """
    )

with st.sidebar.expander("Role-Based Access Control", expanded=False):
    st.markdown(
        """
        ### Approval Authority (Can Approve)
        - **SCM Manager**
        - **Director: Procurement**
        - **Chief Financial Officer**
        
        **Action:** Can click "Confirm and Approve Price Escalation" 
        button with audit trail logging.
        
        ---
        
        ### Operational Staff (Must Submit)
        - **SCM Clerk**
        - **Buyer**
        
        **Action:** Cannot approve directly; must submit calculation 
        to management with MFMA compliance explanation.
        """
    )

with st.sidebar.expander("Features Implemented", expanded=False):
    st.markdown(
        """
        ✅ **Authentication**
        - Login form with credential verification
        - Registration form with validation
        - Session-based state management
        
        ✅ **Validation Rules**
        - Email: Must be `.gov.za` domain
        - Persal: Exactly 7 or 8 digits
        - Password: Min 8 chars, must match confirmation
        - Names: Min 2 chars, letters/hyphens/spaces only
        
        ✅ **Role-Based Access Control**
        - Approval authority for management roles
        - Submission-only for operational roles
        - MFMA compliance warnings
        
        ✅ **Mock Tender Panel**
        - Contract escalation dashboard
        - Metrics display (base value, escalation rate, new value)
        - Audit trail logging with timestamp & Persal
        - Balloons animation on approval
        
        ✅ **User Experience**
        - Welcome banner with user info
        - Logout button in sidebar
        - Clean tab-based navigation
        - Error messages for validation failures
        """
    )

st.sidebar.markdown("---")
st.sidebar.markdown(
    """
    **Lekwankwa SCM Governance Engine**  
    Municipal Finance Management Act (MFMA) Compliance  
    Version 1.0 - Authentication Module
    """
)
