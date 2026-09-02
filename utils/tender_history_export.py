"""
Full Tender History Export Module
Generates combined PDF and JSON exports containing complete chronological audit trail
for a tender (anchor → checks → escalations → corrections → archive).

Preserves SHA-256 hashes from individual events without modification.
"""

import json
import datetime
from typing import List, Dict, Any, Tuple
from fpdf import FPDF
import streamlit as st
import os


# ============================================================================
# HASH LOOKUP UTILITY
# ============================================================================

def load_changelog_hashes(tender_id: str) -> Dict[str, str]:
    """
    Load SHA-256 hashes from the changelog for a specific tender.
    
    Builds a multi-level lookup map to handle microsecond precision differences:
    - Full ISO timestamp (exact match): "2026-08-17T16:21:24.107860"
    - Second-level timestamp (fuzzy match): "2026-08-17T16:21:24"
    
    Args:
        tender_id: Tender identifier to filter by
    
    Returns:
        Dictionary keyed by event timestamp (ISO format or seconds-level) -> hash value (16-char hex)
    """
    changelog_path = os.path.join(
        os.path.dirname(__file__), 
        "../data/scm_run_changelog.jsonl"
    )
    
    hash_map = {}
    hash_map_seconds = {}  # Fallback: keyed by timestamp without microseconds
    
    if not os.path.exists(changelog_path):
        # Changelog file doesn't exist; return empty map (hashes will be None)
        return hash_map
    
    try:
        with open(changelog_path, "r") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    # Only include entries for this tender
                    if entry.get("tender_id") == tender_id:
                        timestamp = entry.get("timestamp", "")
                        hash_value = entry.get("output_hash", "")
                        if timestamp and hash_value:
                            # Add full timestamp mapping
                            hash_map[timestamp] = hash_value
                            
                            # Also add seconds-level mapping (without microseconds)
                            # Split on '.' and take just the base timestamp
                            timestamp_seconds = timestamp.split('.')[0]
                            # Store the first hash for this second
                            # (if multiple entries in same second, first wins)
                            if timestamp_seconds not in hash_map_seconds:
                                hash_map_seconds[timestamp_seconds] = hash_value
                except json.JSONDecodeError:
                    # Skip malformed lines
                    continue
    except Exception as e:
        # If we can't read the file, just return empty map
        print(f"Warning: Could not read changelog: {e}")
        return hash_map
    
    # Merge both maps: seconds-level first, then full timestamps override
    result = {**hash_map_seconds, **hash_map}
    return result


# ============================================================================
# EVENT GATHERING
# ============================================================================

def gather_tender_full_history(tender: dict, tender_id: str) -> List[Dict[str, Any]]:
    """
    Gather all historical events for a tender in strict chronological order.
    
    Args:
        tender: Full tender record dict from tenders.json
        tender_id: Tender identifier string
    
    Returns:
        List of event dicts, each with:
        {
            "event_type": str,
            "sequence_number": int,
            "timestamp": datetime,
            "data": dict,
            "original_output_hash": str or None
        }
    
    Chronological order: ANCHOR → MONTHLY_CHECK → ESCALATION → CORRECTION (nested) → 
                        METADATA_CORRECTION → ARCHIVE
    """
    events = []
    sequence = 0
    
    # Load SHA-256 hashes from changelog for this tender
    hash_map = load_changelog_hashes(tender_id)
    
    # ========================================================================
    # EVENT 1: ANCHOR (Tender Creation)
    # ========================================================================
    sequence += 1
    anchor_event = {
        "event_type": "ANCHOR",
        "sequence_number": sequence,
        "timestamp": datetime.datetime.strptime(
            f"{tender.get('original_anchor_month', '2025-01')}-01", "%Y-%m-%d"
        ),
        "data": {
            "tender_id": tender_id,
            "tender_name": tender.get("tender_name", "N/A"),
            "tender_type": tender.get("tender_type", "N/A"),
            "baseline_type": tender.get("baseline_type", "CUMULATIVE_FROM_ORIGINAL"),
            "cpa_formula_type": tender.get("cpa_formula_type", "CUMULATIVE_FROM_ORIGINAL"),
            "original_anchor_month": tender.get("original_anchor_month"),
            "original_anchor_cpi": tender.get("original_anchor_cpi"),
            "original_base_value": tender.get("original_base_value"),
            "contract_start_date": tender.get("contract_start_date"),
            "contract_end_date": tender.get("contract_end_date"),
            "description": "Tender anchor record - original baseline established"
        },
        "original_output_hash": None  # Anchor itself has no hash (it's the baseline)
    }
    events.append(anchor_event)
    
    # ========================================================================
    # EVENT 2: MONTHLY CHECKS
    # ========================================================================
    check_history = tender.get("check_history", [])
    for check in sorted(check_history, key=lambda x: x.get("checked_at", "")):
        sequence += 1
        checked_at_iso = check.get("checked_at", "2025-01-01T00:00:00")
        # Try exact timestamp first, then seconds-level (without microseconds)
        hash_lookup_key = checked_at_iso
        if hash_lookup_key not in hash_map:
            # Fallback: try without microseconds
            hash_lookup_key_seconds = checked_at_iso.split('.')[0]
            hash_lookup_key = hash_lookup_key_seconds
        
        check_event = {
            "event_type": "MONTHLY_CHECK",
            "sequence_number": sequence,
            "timestamp": datetime.datetime.fromisoformat(checked_at_iso),
            "data": {
                "check_month": check.get("check_month"),
                "checked_at": checked_at_iso,
                "description": f"Monthly invoice verification run for {check.get('check_month')}"
            },
            "original_output_hash": hash_map.get(hash_lookup_key)  # Look up hash from changelog
        }
        events.append(check_event)
    
    # ========================================================================
    # EVENT 3: ESCALATIONS (with nested CORRECTIONS and STALE_INPUT_FLAGS)
    # ========================================================================
    escalation_history = tender.get("escalation_history", [])
    for escalation in sorted(escalation_history, key=lambda x: x.get("escalated_at", "")):
        sequence += 1
        escalated_at_iso = escalation.get("escalated_at", "2025-01-01T00:00:00")
        # Try exact timestamp first, then seconds-level (without microseconds)
        hash_lookup_key = escalated_at_iso
        if hash_lookup_key not in hash_map:
            # Fallback: try without microseconds
            hash_lookup_key_seconds = escalated_at_iso.split('.')[0]
            hash_lookup_key = hash_lookup_key_seconds
        
        escalation_event = {
            "event_type": "ESCALATION",
            "sequence_number": sequence,
            "timestamp": datetime.datetime.fromisoformat(escalated_at_iso),
            "data": {
                "prior_anchor_month": escalation.get("prior_anchor_month"),
                "prior_anchor_cpi": escalation.get("prior_anchor_cpi"),
                "prior_adjusted_price": escalation.get("prior_adjusted_price"),
                "new_anchor_month": escalation.get("new_anchor_month"),
                "new_anchor_cpi": escalation.get("new_anchor_cpi"),
                "new_adjusted_price": escalation.get("new_adjusted_price"),
                "approved_by": escalation.get("approved_by"),
                "escalated_at": escalated_at_iso,
                "description": f"Annual escalation approved - CPI {escalation.get('prior_anchor_cpi')} -> {escalation.get('new_anchor_cpi')}, "
                              f"Price R{escalation.get('prior_adjusted_price'):,.2f} -> R{escalation.get('new_adjusted_price'):,.2f}"
            },
            "original_output_hash": hash_map.get(hash_lookup_key)  # Look up hash from changelog
        }
        events.append(escalation_event)
        
        # ====================================================================
        # EVENT 3A: NESTED CORRECTIONS (within escalation)
        # ====================================================================
        corrections = escalation.get("corrections", [])
        for correction in sorted(corrections, key=lambda x: x.get("corrected_at", "")):
            sequence += 1
            cascade_mode = correction.get("cascade_mode", "ISOLATED")
            correction_event = {
                "event_type": "CORRECTION",
                "sequence_number": sequence,
                "timestamp": datetime.datetime.fromisoformat(
                    correction.get("corrected_at", "2025-01-01T00:00:00")
                ),
                "data": {
                    "parent_escalation_month": escalation.get("new_anchor_month"),
                    "original_figure": correction.get("original_figure"),
                    "corrected_figure": correction.get("corrected_figure"),
                    "corrected_by": correction.get("corrected_by"),
                    "corrected_at": correction.get("corrected_at"),
                    "reason": correction.get("reason"),
                    "cascade_mode": cascade_mode,
                    "description": (
                        f"Correction applied to escalation ({cascade_mode}) - "
                        f"R{correction.get('original_figure'):,.2f} -> R{correction.get('corrected_figure'):,.2f}"
                    )
                },
                "original_output_hash": None
            }
            events.append(correction_event)
        
        # ====================================================================
        # EVENT 3B: NESTED STALE_INPUT_FLAGS (within escalation)
        # ====================================================================
        stale_flags = escalation.get("stale_input_flags", [])
        for flag in sorted(stale_flags, key=lambda x: x.get("flagged_at", "")):
            sequence += 1
            flag_event = {
                "event_type": "STALE_INPUT_FLAG",
                "sequence_number": sequence,
                "timestamp": datetime.datetime.fromisoformat(
                    flag.get("flagged_at", "2025-01-01T00:00:00")
                ),
                "data": {
                    "parent_escalation_month": escalation.get("new_anchor_month"),
                    "corrected_year_month": flag.get("corrected_year_month"),
                    "flagged_at": flag.get("flagged_at"),
                    "note": flag.get("note"),
                    "description": f"Stale input flag raised for {flag.get('corrected_year_month')} - {flag.get('note')}"
                },
                "original_output_hash": None
            }
            events.append(flag_event)
    
    # ========================================================================
    # EVENT 4: METADATA CORRECTIONS (top-level)
    # ========================================================================
    metadata_corrections = tender.get("metadata_corrections", [])
    for meta_corr in sorted(metadata_corrections, key=lambda x: x.get("corrected_at", "")):
        sequence += 1
        meta_corr_event = {
            "event_type": "METADATA_CORRECTION",
            "sequence_number": sequence,
            "timestamp": datetime.datetime.fromisoformat(
                meta_corr.get("corrected_at", "2025-01-01T00:00:00")
            ),
            "data": {
                "field": meta_corr.get("field"),
                "original_value": meta_corr.get("original_value"),
                "corrected_value": meta_corr.get("corrected_value"),
                "reason": meta_corr.get("reason"),
                "corrected_by": meta_corr.get("corrected_by"),
                "corrected_at": meta_corr.get("corrected_at"),
                "retroactive_impact_flag": meta_corr.get("retroactive_impact_flag", False),
                "retroactive_impact_note": meta_corr.get("retroactive_impact_note", ""),
                "description": f"Metadata correction - Field '{meta_corr.get('field')}' corrected by {meta_corr.get('corrected_by')}"
            },
            "original_output_hash": None
        }
        events.append(meta_corr_event)
    
    # ========================================================================
    # EVENT 5: ARCHIVE
    # ========================================================================
    archive_info = tender.get("archive_info")
    if archive_info:
        sequence += 1
        archive_event = {
            "event_type": "ARCHIVE",
            "sequence_number": sequence,
            "timestamp": datetime.datetime.fromisoformat(
                archive_info.get("archived_at", "2025-01-01T00:00:00")
            ),
            "data": {
                "reason": archive_info.get("reason"),
                "archived_by": archive_info.get("archived_by"),
                "archived_at": archive_info.get("archived_at"),
                "description": f"Tender archived - Reason: {archive_info.get('reason')}"
            },
            "original_output_hash": None
        }
        events.append(archive_event)
    
    # Final sort by timestamp to ensure strict chronological order
    events.sort(key=lambda e: e["timestamp"])
    
    # Re-sequence after final sort
    for idx, event in enumerate(events, 1):
        event["sequence_number"] = idx
    
    return events


# ============================================================================
# PDF GENERATION
# ============================================================================

class HistoryPDF(FPDF):
    """Custom FPDF class for history documents with header/footer."""
    
    def __init__(self, tender_id: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tender_id = tender_id
        self.page_num = 0
    
    def header(self):
        """Page header with tender ID and date."""
        self.set_font("Arial", "B", 10)
        self.cell(0, 10, f"TENDER HISTORY: {self.tender_id}", ln=True, align="C")
        self.set_font("Arial", "", 8)
        self.cell(0, 5, f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", 
                 ln=True, align="C")
        self.ln(5)
    
    def footer(self):
        """Page footer with page number."""
        self.set_y(-15)
        self.set_font("Arial", "I", 8)
        self.cell(0, 10, f"Page {self.page_num}", align="C")


def build_combined_audit_pdf(tender_id: str, tender: dict, events: List[Dict]) -> bytes:
    """
    Build complete audit PDF containing all historical events.
    
    Args:
        tender_id: Tender identifier
        tender: Full tender record dict
        events: List of event dicts from gather_tender_full_history()
    
    Returns:
        PDF as bytes
    """
    pdf = HistoryPDF(tender_id)
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # Title
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 15, f"COMPLETE TENDER HISTORY", ln=True, align="C")
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 8, f"Tender ID: {tender_id}", ln=True)
    pdf.cell(0, 8, f"Tender Name: {tender.get('tender_name', 'N/A')}", ln=True)
    pdf.ln(5)
    
    # Summary
    pdf.set_font("Arial", "B", 11)
    pdf.cell(0, 8, f"History Summary: {len(events)} Total Events", ln=True)
    pdf.set_font("Arial", "", 9)
    
    # Count events by type
    event_counts = {}
    for event in events:
        event_type = event["event_type"]
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
    
    for event_type, count in sorted(event_counts.items()):
        pdf.cell(0, 6, f"  - {event_type}: {count}", ln=True)
    pdf.ln(5)
    
    pdf.set_font("Arial", "B", 10)
    pdf.cell(0, 8, "-" * 80, ln=True)
    pdf.ln(3)
    
    # Render each event
    for event in events:
        pdf.page_num = pdf.page
        _render_event_in_pdf(pdf, event)
        pdf.ln(3)
    
    # Final summary page
    pdf.add_page()
    pdf.page_num = pdf.page
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "END OF TENDER HISTORY", ln=True, align="C")
    pdf.ln(5)
    
    pdf.set_font("Arial", "", 10)
    pdf.multi_cell(0, 6, 
        "This document contains the complete, unedited audit trail for the tender listed above. "
        "Every event - including anchor, monthly checks, escalations, corrections, and archive records - "
        "is presented in strict chronological order with full details preserved. No events have been "
        "summarised, omitted, or edited. Each event's SHA-256 hash (where available) is recorded for "
        "integrity verification."
    )
    pdf.ln(5)
    
    pdf.set_font("Arial", "I", 9)
    pdf.cell(0, 6, f"Document Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ln=True)
    pdf.cell(0, 6, f"Lekwankwa SCM Governance Engine v1.0", ln=True)
    
    # fpdf2's FPDF.output() returns a bytearray, not bytes -- st.download_button
    # (and this function's own -> bytes type hint) needs real bytes. Same fix
    # utils/document_gen.py's build_audit_pdf() already uses for this exact
    # fpdf2 quirk.
    return bytes(pdf.output())


def _render_event_in_pdf(pdf: FPDF, event: Dict[str, Any]) -> None:
    """
    Render a single event into the PDF.
    
    Args:
        pdf: FPDF instance
        event: Event dict with event_type, timestamp, data, etc.
    """
    event_type = event["event_type"]
    timestamp = event["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
    sequence = event["sequence_number"]
    data = event["data"]
    
    # Event header (colored background)
    pdf.set_fill_color(41, 128, 185)  # Blue
    if event_type == "ARCHIVE":
        pdf.set_fill_color(192, 57, 43)  # Red
    elif event_type == "CORRECTION":
        pdf.set_fill_color(230, 126, 34)  # Orange
    elif event_type == "METADATA_CORRECTION":
        pdf.set_fill_color(149, 165, 166)  # Gray
    elif event_type in ["MONTHLY_CHECK", "STALE_INPUT_FLAG"]:
        pdf.set_fill_color(52, 152, 219)  # Light Blue
    
    pdf.set_font("Arial", "B", 11)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 8, f"{sequence}. {event_type} - {timestamp}", ln=True, fill=True)
    pdf.set_text_color(0, 0, 0)
    
    # Event details
    pdf.set_font("Arial", "", 9)
    
    for key, value in data.items():
        if key == "description":
            # Description gets special formatting
            pdf.set_font("Arial", "I", 9)
            pdf.multi_cell(0, 5, f"  {value}")
            pdf.set_font("Arial", "", 9)
        elif value is not None and value != "":
            # Format numbers and dates nicely
            if isinstance(value, float) and key.endswith("_price") or key.endswith("_value"):
                formatted_value = f"R{value:,.2f}"
            elif isinstance(value, float):
                formatted_value = f"{value:.4f}"
            else:
                formatted_value = str(value)
            
            pdf.cell(0, 5, f"  {key}: {formatted_value}", ln=True)
    
    # SHA-256 hash if available
    if event.get("original_output_hash"):
        pdf.set_font("Arial", "I", 8)
        pdf.set_text_color(100, 100, 100)
        hash_str = f"SHA-256: {event['original_output_hash']}"
        pdf.cell(0, 4, hash_str, ln=True)
        pdf.set_text_color(0, 0, 0)
    
    pdf.ln(2)


# ============================================================================
# JSON GENERATION
# ============================================================================

def build_combined_export_json(tender_id: str, tender: dict, events: List[Dict]) -> Dict[str, Any]:
    """
    Build complete JSON export containing all historical events.
    
    Args:
        tender_id: Tender identifier
        tender: Full tender record dict
        events: List of event dicts from gather_tender_full_history()
    
    Returns:
        Dictionary ready for JSON serialization
    """
    # Clean events for JSON serialization (convert datetime to ISO string)
    events_for_json = []
    for event in events:
        event_copy = event.copy()
        # Convert datetime to ISO format string
        if isinstance(event_copy["timestamp"], datetime.datetime):
            event_copy["timestamp"] = event_copy["timestamp"].isoformat()
        # Standardize field name: sequence_number -> sequence for JSON
        if "sequence_number" in event_copy:
            event_copy["sequence"] = event_copy.pop("sequence_number")
        events_for_json.append(event_copy)
    
    combined_json = {
        "document_type": "COMPLETE_TENDER_HISTORY",
        "tender_id": tender_id,
        "tender_metadata": {
            "tender_name": tender.get("tender_name"),
            "tender_type": tender.get("tender_type"),
            "baseline_type": tender.get("baseline_type"),
            "cpa_formula_type": tender.get("cpa_formula_type"),
            "original_anchor_month": tender.get("original_anchor_month"),
            "original_anchor_cpi": tender.get("original_anchor_cpi"),
            "original_base_value": tender.get("original_base_value"),
            "current_anchor_month": tender.get("current_anchor_month"),
            "current_anchor_cpi": tender.get("current_anchor_cpi"),
            "current_adjusted_price": tender.get("current_adjusted_price"),
            "contract_start_date": tender.get("contract_start_date"),
            "contract_end_date": tender.get("contract_end_date"),
            "status": tender.get("status", "ACTIVE"),
        },
        "generated_at": datetime.datetime.now().isoformat(),
        "total_events": len(events_for_json),
        "events": events_for_json
    }
    
    return combined_json


# ============================================================================
# STREAMLIT DOWNLOAD INTERFACE
# ============================================================================

def download_tender_history(tender_id: str, tender: dict) -> None:
    """
    Render Streamlit download buttons for tender history PDF and JSON.
    
    Args:
        tender_id: Tender identifier
        tender: Full tender record dict
    """
    # Gather all events
    events = gather_tender_full_history(tender, tender_id)
    
    if not events:
        st.error("No history events found for this tender.")
        return
    
    # Display summary
    st.info(
        f"""
        **Complete Tender History Export**
        
        This export contains **{len(events)} total events** in strict chronological order:
        """
    )
    
    # Event type summary
    event_counts = {}
    for event in events:
        event_type = event["event_type"]
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Event Breakdown:**")
        for event_type, count in sorted(event_counts.items()):
            st.write(f"• {event_type}: {count}")
    
    with col2:
        st.markdown("**Includes:**")
        st.write("✅ Anchor record (baseline)")
        st.write("✅ Monthly checks")
        st.write("✅ Annual escalations")
        st.write("✅ All corrections")
        st.write("✅ Archive records (if applicable)")
    
    st.markdown("---")
    
    # Generate PDF with error handling
    try:
        pdf_bytes = build_combined_audit_pdf(tender_id, tender, events)
        pdf_filename = f"TenderHistory_{tender_id}_Complete.pdf"
    except Exception as e:
        st.error(f"Error generating PDF: {str(e)}")
        return
    
    # Generate JSON with error handling
    try:
        json_data = build_combined_export_json(tender_id, tender, events)
        json_filename = f"TenderHistory_{tender_id}_Complete.json"
    except Exception as e:
        st.error(f"Error generating JSON: {str(e)}")
        return
    
    # Download buttons
    col1, col2 = st.columns(2)
    
    with col1:
        st.download_button(
            label="📥 Download PDF (Complete History)",
            data=pdf_bytes,
            file_name=pdf_filename,
            mime="application/pdf",
            use_container_width=True
        )
    
    with col2:
        st.download_button(
            label="📥 Download JSON (Complete History)",
            data=json.dumps(json_data, indent=2, default=str),
            file_name=json_filename,
            mime="application/json",
            use_container_width=True
        )
    
    st.markdown("---")
    st.caption(
        "✓ Complete, unedited history | ✓ All events included | ✓ SHA-256 hashes preserved "
        "| ✓ Chronological order maintained"
    )
