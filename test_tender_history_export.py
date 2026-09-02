"""
Test script for Full Tender History Export feature.
Verifies that the export correctly gathers all events in chronological order,
generates valid PDF and JSON outputs, and preserves all details without summarization.

Run with: python test_tender_history_export.py
"""

import json
import datetime
import sys
from utils.tender_history_export import (
    gather_tender_full_history,
    build_combined_audit_pdf,
    build_combined_export_json
)


def create_test_tender() -> tuple[str, dict]:
    """
    Create a realistic test tender with:
    - Anchor record
    - Multiple monthly checks
    - One escalation with nested corrections
    - Metadata corrections
    - Optional: archive
    
    Returns:
        (tender_id, tender_dict)
    """
    tender_id = "TEST-TENDER-001"
    
    tender = {
        "tender_id": tender_id,
        "tender_name": "Test Security Services Contract",
        "tender_type": "Services",
        "baseline_type": "MONTHLY",
        "cpa_formula_type": "CUMULATIVE_FROM_ORIGINAL",
        "contract_start_date": "2025-01-01",
        "contract_end_date": "2027-12-31",
        "description": "Test tender for history export validation",
        "status": "ACTIVE",
        
        # Anchor
        "original_anchor_month": "2025-01",
        "original_anchor_cpi": 100.3,
        "original_base_value": 1000000.00,
        
        # Current (after escalation)
        "current_anchor_month": "2026-01",
        "current_anchor_cpi": 103.7,
        "current_adjusted_price": 1033898.00,
        
        # Monthly checks
        "check_history": [
            {
                "check_month": "2025-02",
                "checked_at": "2025-02-03T10:30:00"
            },
            {
                "check_month": "2025-06",
                "checked_at": "2025-06-15T14:22:00"
            },
            {
                "check_month": "2025-12",
                "checked_at": "2025-12-20T11:45:00"
            }
        ],
        
        # Annual escalation with nested corrections
        "escalation_history": [
            {
                "prior_anchor_month": "2025-01",
                "prior_anchor_cpi": 100.3,
                "prior_adjusted_price": 1000000.00,
                "new_anchor_month": "2026-01",
                "new_anchor_cpi": 103.7,
                "new_adjusted_price": 1033898.00,
                "approved_by": "john_smith",
                "escalated_at": "2026-01-15T09:30:00",
                
                # Corrections within escalation
                "corrections": [
                    {
                        "corrected_at": "2026-01-18T15:22:00",
                        "corrected_by": "jane_doe",
                        "reason": "Adjustment for Rand/Dollar volatility",
                        "original_figure": 1033898.00,
                        "corrected_figure": 1033500.00,
                        "cascade_mode": "ISOLATED"
                    }
                ],
                
                # Stale input flags
                "stale_input_flags": [
                    {
                        "flagged_at": "2026-01-20T11:00:00",
                        "corrected_year_month": "2025-06",
                        "note": "Earlier check needs review due to data quality issue"
                    }
                ]
            }
        ],
        
        # Metadata corrections
        "metadata_corrections": [
            {
                "field": "tender_name",
                "original_value": "Test Security Services Contract",
                "corrected_value": "Test Security Services Contract - Revised Scope",
                "reason": "Contract scope updated per amendment",
                "corrected_by": "admin_user",
                "corrected_at": "2026-02-10T08:15:00",
                "retroactive_impact_flag": False,
                "retroactive_impact_note": ""
            }
        ],
        
        # Archive info (optional - commented out for active tender)
        # "archive_info": {
        #     "reason": "Contract concluded",
        #     "archived_by": "chief_officer",
        #     "archived_at": "2026-09-01T16:00:00"
        # }
    }
    
    return tender_id, tender


def test_gather_history():
    """Test: gather_tender_full_history() produces correct chronological order."""
    print("\n" + "="*80)
    print("TEST 1: gather_tender_full_history()")
    print("="*80)
    
    tender_id, tender = create_test_tender()
    events = gather_tender_full_history(tender, tender_id)
    
    print(f"\n✓ Gathered {len(events)} events:")
    
    expected_order = [
        "ANCHOR",
        "MONTHLY_CHECK",
        "MONTHLY_CHECK",
        "MONTHLY_CHECK",
        "ESCALATION",
        "CORRECTION",
        "STALE_INPUT_FLAG",
        "METADATA_CORRECTION"
    ]
    
    actual_order = [e["event_type"] for e in events]
    
    print(f"  Expected order: {' → '.join(expected_order)}")
    print(f"  Actual order:   {' → '.join(actual_order)}")
    
    if actual_order == expected_order:
        print("\n✅ PASS: Event order is correct!")
    else:
        print("\n❌ FAIL: Event order mismatch!")
        return False
    
    # Verify timestamps are strictly ascending
    timestamps = [e["timestamp"] for e in events]
    sorted_timestamps = sorted(timestamps)
    
    if timestamps == sorted_timestamps:
        print("✅ PASS: Timestamps are strictly ascending!")
    else:
        print("❌ FAIL: Timestamps are not in order!")
        print(f"  Timestamps: {[t.isoformat() for t in timestamps]}")
        return False
    
    # Verify all events have required fields
    for i, event in enumerate(events, 1):
        required_fields = ["event_type", "sequence_number", "timestamp", "data"]
        missing = [f for f in required_fields if f not in event]
        if missing:
            print(f"❌ FAIL: Event {i} missing fields: {missing}")
            return False
    
    print("✅ PASS: All events have required fields!")
    
    return True


def test_pdf_generation():
    """Test: build_combined_audit_pdf() generates valid PDF."""
    print("\n" + "="*80)
    print("TEST 2: build_combined_audit_pdf()")
    print("="*80)
    
    tender_id, tender = create_test_tender()
    events = gather_tender_full_history(tender, tender_id)
    
    try:
        pdf_bytes = build_combined_audit_pdf(tender_id, tender, events)
        
        if not pdf_bytes or len(pdf_bytes) == 0:
            print("❌ FAIL: PDF is empty!")
            return False
        
        print(f"\n✓ Generated PDF of {len(pdf_bytes):,} bytes")
        
        # Check PDF header (should start with %PDF)
        if pdf_bytes.startswith(b"%PDF"):
            print("✅ PASS: PDF has valid header!")
        else:
            print("❌ FAIL: PDF header invalid!")
            return False
        
        # Save to file for manual inspection
        output_file = f"test_output_{tender_id}_Complete.pdf"
        with open(output_file, "wb") as f:
            f.write(pdf_bytes)
        print(f"✅ Saved test PDF: {output_file}")
        
        return True
    except Exception as e:
        print(f"❌ FAIL: PDF generation failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_json_generation():
    """Test: build_combined_export_json() generates correct structure."""
    print("\n" + "="*80)
    print("TEST 3: build_combined_export_json()")
    print("="*80)
    
    tender_id, tender = create_test_tender()
    events = gather_tender_full_history(tender, tender_id)
    
    try:
        json_data = build_combined_export_json(tender_id, tender, events)
        
        # Check required top-level fields
        required_fields = [
            "document_type",
            "tender_id",
            "tender_metadata",
            "generated_at",
            "total_events",
            "events"
        ]
        
        missing = [f for f in required_fields if f not in json_data]
        if missing:
            print(f"❌ FAIL: Missing top-level fields: {missing}")
            return False
        
        print(f"\n✓ JSON has all required top-level fields")
        
        # Verify document_type
        if json_data["document_type"] != "COMPLETE_TENDER_HISTORY":
            print(f"❌ FAIL: Unexpected document_type: {json_data['document_type']}")
            return False
        
        print("✅ PASS: document_type is correct!")
        
        # Verify tender_id
        if json_data["tender_id"] != tender_id:
            print(f"❌ FAIL: Tender ID mismatch!")
            return False
        
        print("✅ PASS: tender_id is correct!")
        
        # Verify event count
        if json_data["total_events"] != len(events):
            print(f"❌ FAIL: Event count mismatch! Expected {len(events)}, got {json_data['total_events']}")
            return False
        
        print(f"✅ PASS: Event count matches ({len(events)} events)!")
        
        # Verify each event has required fields
        for i, event in enumerate(json_data["events"], 1):
            required_event_fields = ["event_type", "sequence", "timestamp", "data"]
            missing_event = [f for f in required_event_fields if f not in event]
            if missing_event:
                print(f"❌ FAIL: Event {i} missing fields: {missing_event}")
                return False
        
        print("✅ PASS: All events have required fields!")
        
        # Save to file for inspection
        output_file = f"test_output_{tender_id}_Complete.json"
        with open(output_file, "w") as f:
            json.dump(json_data, f, indent=2)
        print(f"✅ Saved test JSON: {output_file}")
        
        return True
    except Exception as e:
        print(f"❌ FAIL: JSON generation failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_no_summarization():
    """Test: Verify no events are summarized or omitted."""
    print("\n" + "="*80)
    print("TEST 4: No Summarization or Omission")
    print("="*80)
    
    tender_id, tender = create_test_tender()
    events = gather_tender_full_history(tender, tender_id)
    
    # Count each event type from source data
    expected_counts = {
        "ANCHOR": 1,
        "MONTHLY_CHECK": len(tender["check_history"]),
        "ESCALATION": len(tender["escalation_history"]),
        "CORRECTION": sum(len(e.get("corrections", [])) for e in tender["escalation_history"]),
        "STALE_INPUT_FLAG": sum(len(e.get("stale_input_flags", [])) for e in tender["escalation_history"]),
        "METADATA_CORRECTION": len(tender.get("metadata_corrections", []))
    }
    
    # Count in gathered events
    actual_counts = {}
    for event in events:
        event_type = event["event_type"]
        actual_counts[event_type] = actual_counts.get(event_type, 0) + 1
    
    print("\n✓ Event count verification:")
    all_match = True
    for event_type in sorted(expected_counts.keys()):
        expected = expected_counts[event_type]
        actual = actual_counts.get(event_type, 0)
        match = "✅" if expected == actual else "❌"
        print(f"  {match} {event_type}: Expected {expected}, Got {actual}")
        if expected != actual:
            all_match = False
    
    if all_match:
        print("\n✅ PASS: All event types present with correct counts!")
        return True
    else:
        print("\n❌ FAIL: Event count mismatch!")
        return False


def main():
    """Run all tests."""
    print("\n" + "█"*80)
    print("█  Full Tender History Export - Test Suite")
    print("█"*80)
    
    tests = [
        ("Event Gathering & Ordering", test_gather_history),
        ("PDF Generation", test_pdf_generation),
        ("JSON Generation", test_json_generation),
        ("No Summarization", test_no_summarization),
    ]
    
    results = {}
    for test_name, test_func in tests:
        try:
            results[test_name] = test_func()
        except Exception as e:
            print(f"\n❌ EXCEPTION in {test_name}: {e}")
            import traceback
            traceback.print_exc()
            results[test_name] = False
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, passed_flag in results.items():
        status = "✅ PASS" if passed_flag else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! Export feature is ready.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. See details above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
