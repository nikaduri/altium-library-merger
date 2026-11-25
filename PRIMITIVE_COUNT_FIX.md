# Fix for "Differs Binary Stream or Primitive Count" Error

## Problem

When opening merged Altium library files (.SchLib or .PcbLib) in Altium Designer, you may encounter the error:

**"Differs binary stream or primitive count"**

This error occurs when Altium detects a mismatch between:
- **Binary stream**: The raw binary data structure in the library file
- **Primitive count**: The number stored in the Header vs. actual primitives in the Data stream

## Root Cause

The merger was doing a **raw copy** of all component/footprint streams without validating that the Header stream's primitive count matched the actual number of primitives in the Data stream.

### What are primitives?

**SchLib primitives**: Lines, arcs, rectangles, polygons, pins, text, labels, etc.
**PcbLib primitives**: Pads, tracks, arcs, regions, vias, text, 3D bodies, etc.

## Solution Applied

### For SchLib Files (`schlib_merger.py`)

Added validation after copying component streams:
1. Copy all streams as-is from source
2. Re-parse the Data stream to recalculate primitive count
3. Log warnings if discrepancies are found
4. The Data stream is the source of truth (Altium recalculates on save)

### For PcbLib Files (`pcblib_merger.py`)

Added validation and **automatic correction** for TWO header streams:

#### Main Header Stream
1. Copy all streams from source
2. Read the primitive count from Header stream (first 4 bytes)
3. Recalculate actual primitive count by parsing Data stream
4. If mismatch detected, **rebuild the Header** with correct count
5. Write corrected Header back to output file

#### UniqueIDPrimitiveInformation Header Stream
1. Read the UID record count from UniqueIDPrimitiveInformation/Header (first 4 bytes)
2. Recalculate actual UID record count by parsing UniqueIDPrimitiveInformation/Data stream
3. If mismatch detected, **rebuild the UniqueIDPrimitiveInformation/Header** with correct count
4. Write corrected header back to output file

Added helper methods:
- `_count_primitives_in_data()` - counts actual primitives by parsing Data stream structure
- `_count_uid_records()` - counts actual UID records (length-prefixed records in UniqueIDPrimitiveInformation/Data)

## Changes Made

### schlib_merger.py (lines 400-411)
- Added primitive count recalculation after stream copy
- Added warning logging for discrepancies

### pcblib_merger.py (lines 502-556)
- Added primitive count validation and correction for main Header stream
- Added UID record count validation and correction for UniqueIDPrimitiveInformation/Header
- Created `_count_primitives_in_data()` helper method (lines 290-334)
- Created `_count_uid_records()` helper method (lines 336-355)
- Automatically fixes both Header streams if mismatches detected

## Testing

After merging libraries with this fix:

1. **Check the logs** - Look for warnings like:
   ```
   WARNING: Component 'IC_Name': primitive count mismatch. Expected 42, found 45. Using actual count from Data stream.
   ```
   
   Or for PcbLib files:
   ```
   WARNING: Footprint 'FOOTPRINT_NAME': Header primitive count mismatch. Stored: 27, Actual: 3. Fixing Header stream.
   WARNING: Footprint 'FOOTPRINT_NAME': UniqueIDPrimitiveInformation count mismatch. Stored: 10, Actual: 5. Fixing UniqueIDPrimitiveInformation/Header stream.
   ```

2. **Open in Altium Designer** - The error should no longer appear

3. **Verify component integrity** - All graphical elements should render correctly

## Why This Fix Works

Altium validates library integrity by checking **multiple header-data pairs**:

### For PcbLib Files:
1. **Main primitive count**: Reading Header stream and comparing to Data stream primitives
2. **UniqueID record count**: Reading UniqueIDPrimitiveInformation/Header and comparing to UniqueIDPrimitiveInformation/Data records

If ANY of these pairs don't match, Altium throws the "differs binary stream or primitive count" error.

Our fix ensures these values always match by:
- **SchLib**: Trusting the Data stream (Altium recalculates anyway)
- **PcbLib**: Explicitly fixing BOTH Header and UniqueIDPrimitiveInformation/Header to match their respective Data streams

## Prevention

This issue typically occurs when:
- Copying streams from corrupted source files
- Modifying Data streams without updating Headers
- Merging files from different Altium versions
- Binary truncation during file operations

The fix ensures that **merged output is always consistent**, regardless of input file quality.

## Additional Notes

- The fix is **non-destructive** - it only corrects metadata, not actual component data
- Warnings are logged for transparency
- The Data streams are always preserved as-is (they're the source of truth)
- Only the Header stream counts are adjusted when needed
- For PcbLib files, there are **8 typical streams per footprint**:
  - `Data` - Footprint primitives (pads, tracks, arcs, etc.)
  - `Header` - Primitive count (4 bytes)
  - `Parameters` - Footprint metadata
  - `UniqueIDPrimitiveInformation/Data` - Length-prefixed UID records
  - `UniqueIDPrimitiveInformation/Header` - UID record count (4 bytes)
  - `WideStrings` - Unicode string data
  - `Models/Data` - 3D model data (optional)
  - `Models/Header` - 3D model count (optional)
