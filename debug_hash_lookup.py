#!/usr/bin/env python3
"""Debug hash lookup to see why timestamps aren't matching."""

import json
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from core.tender_registry import get_tender
from utils.tender_history_export import gather_tender_full_history, load_changelog_hashes

tender_id = "TENDER-LP-2025"

print("=" * 80)
print("DEBUG: Hash Lookup Matching")
print("=" * 80)

# Load changelog hashes
print(f"\n1. Loading changelog hashes for {tender_id}...")
hash_map = load_changelog_hashes(tender_id)
print(f"   Found {len(hash_map)} hash entries")
print(f"   Changelog timestamps:")
for ts, h in sorted(hash_map.items()):
    print(f"      {ts} → {h}")

# Load tender and events
print(f"\n2. Loading tender and gathering events...")
tender = get_tender(tender_id)
events = gather_tender_full_history(tender, tender_id)

print(f"   Found {len(events)} events")
print(f"   Event timestamps:")
for event in events:
    ts = event["timestamp"]
    ts_iso = ts.isoformat() if hasattr(ts, 'isoformat') else str(ts)
    hash_val = event.get("original_output_hash")
    print(f"      {ts_iso} ({event['event_type']}) → hash={hash_val}")

# Try manual matching
print(f"\n3. Attempting manual timestamp matching...")
for event in events:
    ts = event["timestamp"]
    ts_iso = ts.isoformat() if hasattr(ts, 'isoformat') else str(ts)
    
    # Try exact match
    if ts_iso in hash_map:
        print(f"   ✅ MATCH (exact): {ts_iso}")
    else:
        # Try variations
        print(f"   ❌ NO MATCH (exact): {ts_iso}")
        
        # Try with microseconds truncated
        ts_no_micro = ts_iso.split('.')[0]
        for changelog_ts in hash_map.keys():
            changelog_ts_no_micro = changelog_ts.split('.')[0]
            if ts_no_micro == changelog_ts_no_micro:
                print(f"      ✅ MATCH (truncated): {ts_no_micro}")
                break
        else:
            # Try to find close matches
            similar = [cts for cts in hash_map.keys() if ts_no_micro in cts or cts.startswith(ts_no_micro[:10])]
            if similar:
                print(f"      Similar in changelog: {similar[:2]}")

# Check escalation time specifically
print(f"\n4. Checking escalation timestamp specifically...")
escalation = tender.get("escalation_history", [{}])[0]
escalated_at = escalation.get("escalated_at")
print(f"   Escalation escalated_at: {escalated_at}")
print(f"   In hash_map: {escalated_at in hash_map}")
if escalated_at in hash_map:
    print(f"   Hash value: {hash_map[escalated_at]}")

# Check check timestamps
print(f"\n5. Checking check timestamps...")
for check in tender.get("check_history", []):
    checked_at = check.get("checked_at")
    print(f"   Check checked_at: {checked_at}")
    print(f"   In hash_map: {checked_at in hash_map}")
    if checked_at in hash_map:
        print(f"   Hash value: {hash_map[checked_at]}")
