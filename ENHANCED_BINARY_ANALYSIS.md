# Enhanced Binary Stream Analysis

## Overview

The diff engine now provides detailed analysis of binary stream differences instead of just showing "binary streams differ". Each stream type is analyzed intelligently to extract meaningful information about what changed.

## Stream-Specific Analysis

### 1. Parameters Stream
**What it contains**: Component metadata (description, height, etc.)

**Analysis provided**:
- Parses Altium parameter format
- Shows which parameters changed
- Displays old → new values
- Example: `description: '' → 'Updated Relay Description'`

### 2. Header Stream
**What it contains**: Primitive count (4-byte integer)

**Analysis provided**:
- Extracts primitive count from both versions
- Shows count changes
- Example: `Primitive count: 27 → 32`

### 3. Data Stream
**What it contains**: Binary primitive data (pads, tracks, arcs, etc.)

**Analysis provided**:
- Size change in bytes and percentage
- First difference location for same-size changes
- Example: `Data increased by 8 bytes (0.3%)`

### 4. UniqueIDPrimitiveInformation/Header
**What it contains**: UID record count (4-byte integer)

**Analysis provided**:
- Extracts UID record count
- Shows count changes
- Example: `UniqueID record count: 5 → 7`

### 5. UniqueIDPrimitiveInformation/Data
**What it contains**: Length-prefixed UID records

**Analysis provided**:
- Counts actual UID records by parsing structure
- Shows record count changes
- Example: `UID records: 5 → 7`

### 6. WideStrings Stream
**What it contains**: UTF-16 encoded strings

**Analysis provided**:
- Decodes UTF-16 text
- Shows text changes
- Example: `Text: 'Original' → 'Modified'`

### 7. Generic Binary Streams
**What it contains**: Unknown binary data

**Analysis provided**:
- Size changes
- First byte difference location
- Example: `Content differs at byte 42: 0x1A → 0x2B`

## Example Output

When comparing two footprints with differences:

```
Component: G5LE
Status: conflict

Property Differences:

  • Description
    A: (empty)
    B: Updated Relay Description
    
  • Stream: Parameters
    A: 67 bytes
    B: 88 bytes
    Details: Parameter changes: description: '' → 'Updated Relay Description'; height: '0mil' → '15'
    
  • Stream: Header
    A: 4 bytes
    B: 4 bytes
    Details: Primitive count: 27 → 32
    
  • Stream: Data
    A: 3071 bytes
    B: 3079 bytes
    Details: Data increased by 8 bytes (0.3%)
```

## Benefits

### Before Enhancement
```
Binary Streams: 6 streams
Differing: Parameters (67B vs 88B), Header (content), Data (3071B vs 3079B)
```
❌ No insight into what actually changed

### After Enhancement
```
Stream: Parameters
  67 bytes → 88 bytes
  Analysis: Parameter changes: description: '' → 'Updated Relay Description'; height: '0mil' → '15'

Stream: Header
  4 bytes → 4 bytes
  Analysis: Primitive count: 27 → 32

Stream: Data
  3071 bytes → 3079 bytes
  Analysis: Data increased by 8 bytes (0.3%)
```
✅ Clear understanding of what changed

## Implementation Details

### File Modified
`altium_engine/diff_engine.py`

### New Methods Added

1. **`_analyze_stream_difference(stream_name, data_a, data_b)`**
   - Main dispatcher that routes to specific analyzers based on stream name
   
2. **`_analyze_parameters_stream(data_a, data_b)`**
   - Parses Altium parameter format
   - Compares key-value pairs
   
3. **`_analyze_header_stream(data_a, data_b)`**
   - Extracts 4-byte primitive count
   - Compares counts
   
4. **`_analyze_data_stream(data_a, data_b)`**
   - Calculates size differences
   - Finds first byte difference
   
5. **`_analyze_uid_header_stream(data_a, data_b)`**
   - Extracts UID record count
   
6. **`_analyze_uid_data_stream(data_a, data_b)`**
   - Parses length-prefixed records
   - Counts actual records
   
7. **`_analyze_widestrings_stream(data_a, data_b)`**
   - Decodes UTF-16 text
   
8. **`_analyze_binary_difference(data_a, data_b)`**
   - Generic analysis for unknown streams

### Changes to Output Format

Each differing stream now generates a separate property diff entry:
```python
{
    "property": "Stream: Parameters",
    "val_a": "67 bytes",
    "val_b": "88 bytes",
    "type": "binary_stream",
    "details": "Parameter changes: description: '' → 'Updated Relay Description'"
}
```

## Usage in Web App

The web app automatically displays the enhanced analysis in the comparison view:

1. User uploads two library files
2. Diff engine analyzes all components
3. For conflicts, each differing stream is analyzed
4. Detailed analysis shown in the UI
5. User can see exactly what changed before choosing resolution

## Testing

Run the test:
```bash
python -c "
from altium_engine.diff_engine import LibraryDiffEngine

diff_data = LibraryDiffEngine.compare('lib_a.PcbLib', 'lib_b.PcbLib')

for item in diff_data['items']:
    if item['status'] == 'conflict':
        print(f\"Component: {item['name']}\")
        for diff in item['property_diffs']:
            print(f\"  {diff['property']}: {diff.get('details', 'N/A')}\")
"
```

## Future Enhancements

Possible improvements:
- Visual diff for geometric primitives (pad positions, track routing)
- 3D model comparison
- Layer stack analysis
- Pin mapping validation
- Electrical rule differences
