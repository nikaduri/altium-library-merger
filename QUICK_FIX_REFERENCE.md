# Quick Fix Reference - "Differs Binary Stream" Error

## The Issue
Altium shows error: **"Differs binary stream or primitive count"** when opening merged .PcbLib files.

## The Cause
PcbLib footprints have **TWO header-data pairs** that must match:
1. `Header` (4 bytes) must match primitive count in `Data`
2. `UniqueIDPrimitiveInformation/Header` (4 bytes) must match record count in `UniqueIDPrimitiveInformation/Data`

If **either** pair is mismatched → Altium throws the error.

## The Fix
Updated `pcblib_merger.py` to automatically validate and correct **both** header streams during merge.

## What Changed

### New Code Added:
```python
# Method to count UID records (lines 336-355)
@classmethod
def _count_uid_records(cls, data: bytes) -> int:
    """Count actual UID records (length-prefixed: 4-byte length + data)"""
    # ... parsing logic ...

# Extended validation in merge() (lines 533-556)
# After copying streams, validate TWO pairs:
#   1. Header ↔ Data
#   2. UniqueIDPrimitiveInformation/Header ↔ /Data
# Automatically fix any mismatches
```

## How to Use

Just merge as normal - the fix runs automatically:
```python
from altium_engine.pcblib_merger import PcbLibMerger

result = PcbLibMerger.merge(
    'library_a.PcbLib',
    'library_b.PcbLib',
    'merged_output.PcbLib'
)
```

Watch for warnings in logs:
```
WARNING: Footprint 'NAME': Header primitive count mismatch. Stored: 27, Actual: 3. Fixing Header stream.
WARNING: Footprint 'NAME': UniqueIDPrimitiveInformation count mismatch. Stored: 10, Actual: 5. Fixing Header stream.
```

These warnings are **good** - they show the tool is fixing corrupted source files.

## Verification

Run tests to verify everything works:
```bash
python tests/test_stream_validation.py
```

Expected output:
```
✓ PASS: UID Record Counting
✓ PASS: Primitive Counting
✓ PASS: Merge Fixes Counts
✓ All tests passed!
```

## Open in Altium

The merged .PcbLib file should now open without errors in Altium Designer.

---

**Files Modified:**
- `altium_engine/pcblib_merger.py` - Added UID validation
- `tests/test_stream_validation.py` - New comprehensive tests
- `PRIMITIVE_COUNT_FIX.md` - Updated documentation
- `STREAM_VALIDATION_FIX_SUMMARY.md` - Detailed technical summary
