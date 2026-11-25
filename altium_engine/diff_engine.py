"""Altium Library Diff Engine
Performs component-level and property-level diffing between two Altium libraries.
"""

from enum import Enum
import os
from typing import Dict, List, Optional, Tuple, Any, Union

from .schlib_merger import SchLibMerger, SchComponentInfo
from .pcblib_merger import PcbLibMerger, PcbFootprintInfo


class DiffStatus(str, Enum):
    ADDED_A = "added_a"         # Only in Library A (Mine)
    ADDED_B = "added_b"         # Only in Library B (Theirs)
    IDENTICAL = "identical"     # Present in both, identical hashes
    CONFLICT = "conflict"       # Present in both, content differs


class ComponentDiff:
    """Detailed diff for a single component / footprint."""

    def __init__(
        self,
        name: str,
        status: DiffStatus,
        item_a: Optional[Union[SchComponentInfo, PcbFootprintInfo]] = None,
        item_b: Optional[Union[SchComponentInfo, PcbFootprintInfo]] = None,
    ):
        self.name: str = name
        self.status: DiffStatus = status
        self.item_a = item_a
        self.item_b = item_b
        self.property_diffs: List[Dict[str, Any]] = []
        self._compute_diffs()

    def _compute_diffs(self) -> None:
        if self.status != DiffStatus.CONFLICT or not self.item_a or not self.item_b:
            return

        # Check basic properties
        # Description
        desc_a = getattr(self.item_a, "description", "")
        desc_b = getattr(self.item_b, "description", "")
        if desc_a != desc_b:
            self.property_diffs.append({
                "property": "Description",
                "val_a": desc_a,
                "val_b": desc_b,
                "type": "string",
            })

        # Designator / Height
        des_a = getattr(self.item_a, "designator", None)
        des_b = getattr(self.item_b, "designator", None)
        if des_a is not None and des_b is not None and des_a != des_b:
            self.property_diffs.append({
                "property": "Designator",
                "val_a": des_a,
                "val_b": des_b,
                "type": "string",
            })

        h_a = getattr(self.item_a, "height", None)
        h_b = getattr(self.item_b, "height", None)
        if h_a is not None and h_b is not None and h_a != h_b:
            self.property_diffs.append({
                "property": "Height",
                "val_a": h_a,
                "val_b": h_b,
                "type": "string",
            })

        # Pins or Pads Count
        pin_a = len(getattr(self.item_a, "pins", []))
        pin_b = len(getattr(self.item_b, "pins", []))
        if pin_a or pin_b:
            if pin_a != pin_b:
                self.property_diffs.append({
                    "property": "Pins Count",
                    "val_a": str(pin_a),
                    "val_b": str(pin_b),
                    "type": "count",
                })

        pad_a = getattr(self.item_a, "pad_count", 0)
        pad_b = getattr(self.item_b, "pad_count", 0)
        if pad_a != pad_b:
            self.property_diffs.append({
                "property": "Pads Count",
                "val_a": str(pad_a),
                "val_b": str(pad_b),
                "type": "count",
            })

        # 3D Model presence
        m_a = getattr(self.item_a, "has_3d_model", None)
        m_b = getattr(self.item_b, "has_3d_model", None)
        if m_a is not None and m_b is not None and m_a != m_b:
            self.property_diffs.append({
                "property": "Has 3D Model",
                "val_a": "Yes" if m_a else "No",
                "val_b": "Yes" if m_b else "No",
                "type": "boolean",
            })

        # Parameters diff (for SchLib)
        params_a = getattr(self.item_a, "parameters", {})
        params_b = getattr(self.item_b, "parameters", {})
        all_param_keys = sorted(list(set(params_a.keys()) | set(params_b.keys())))

        for pkey in all_param_keys:
            val_a = params_a.get(pkey)
            val_b = params_b.get(pkey)
            if val_a != val_b:
                self.property_diffs.append({
                    "property": f"Param: {pkey}",
                    "val_a": val_a if val_a is not None else "(Not defined in A)",
                    "val_b": val_b if val_b is not None else "(Not defined in B)",
                    "type": "parameter",
                })

        # Primitives count
        prim_a = getattr(self.item_a, "primitive_count", 0)
        prim_b = getattr(self.item_b, "primitive_count", 0)
        if prim_a != prim_b:
            self.property_diffs.append({
                "property": "Primitives Count",
                "val_a": str(prim_a),
                "val_b": str(prim_b),
                "type": "count",
            })

        # Compare raw streams to identify binary-level differences
        streams_a = getattr(self.item_a, "raw_streams", {})
        streams_b = getattr(self.item_b, "raw_streams", {})
        all_stream_names = sorted(list(set(streams_a.keys()) | set(streams_b.keys())))

        differing_streams = []
        stream_details = []

        for stream_name in all_stream_names:
            data_a = streams_a.get(stream_name)
            data_b = streams_b.get(stream_name)

            if data_a != data_b:
                differing_streams.append(stream_name)
                size_a = len(data_a) if data_a else 0
                size_b = len(data_b) if data_b else 0

                # Analyze what's different in this specific stream
                diff_detail = self._analyze_stream_difference(stream_name, data_a, data_b)
                stream_details.append({
                    "stream": stream_name,
                    "size_a": size_a,
                    "size_b": size_b,
                    "analysis": diff_detail
                })

        # Add detailed information for each differing stream
        if differing_streams:
            for detail in stream_details:
                self.property_diffs.append({
                    "property": f"Stream: {detail['stream']}",
                    "val_a": f"{detail['size_a']} bytes",
                    "val_b": f"{detail['size_b']} bytes",
                    "type": "binary_stream",
                    "details": detail['analysis']
                })

        # If no visible differences found but content hashes differ, add generic marker
        if len(self.property_diffs) == 0 and self.item_a.content_hash != self.item_b.content_hash:
            self.property_diffs.append({
                "property": "Content Hash",
                "val_a": self.item_a.content_hash[:16] + "...",
                "val_b": self.item_b.content_hash[:16] + "...",
                "type": "hash",
                "details": "Binary content differs but no specific property changes detected",
            })

    def _analyze_stream_difference(self, stream_name: str, data_a: Optional[bytes], data_b: Optional[bytes]) -> str:
        """Analyze what's different between two binary streams."""
        from .cfb_handler import parse_altium_params
        import struct

        if data_a is None:
            return "Stream only exists in B"
        if data_b is None:
            return "Stream only exists in A"

        # Stream-specific analysis
        if stream_name == "Parameters":
            return self._analyze_parameters_stream(data_a, data_b)
        elif stream_name == "Header":
            return self._analyze_header_stream(data_a, data_b)
        elif stream_name == "Data":
            return self._analyze_data_stream(data_a, data_b)
        elif stream_name == "UniqueIDPrimitiveInformation/Header":
            return self._analyze_uid_header_stream(data_a, data_b)
        elif stream_name == "UniqueIDPrimitiveInformation/Data":
            return self._analyze_uid_data_stream(data_a, data_b)
        elif stream_name == "WideStrings":
            return self._analyze_widestrings_stream(data_a, data_b)
        else:
            # Generic binary difference
            return self._analyze_binary_difference(data_a, data_b)

    def _analyze_parameters_stream(self, data_a: bytes, data_b: bytes) -> str:
        """Analyze Parameters stream differences."""
        from .cfb_handler import parse_altium_params

        try:
            _, params_a = parse_altium_params(data_a, has_length_prefix=True)
            _, params_b = parse_altium_params(data_b, has_length_prefix=True)

            all_keys = set(params_a.keys()) | set(params_b.keys())
            diffs = []

            for key in sorted(all_keys):
                val_a = params_a.get(key, "(missing)")
                val_b = params_b.get(key, "(missing)")
                if val_a != val_b:
                    diffs.append(f"{key}: '{val_a}' → '{val_b}'")

            if diffs:
                return "Parameter changes: " + "; ".join(diffs[:3]) + (f" +{len(diffs)-3} more" if len(diffs) > 3 else "")
            else:
                return "Parameters parsed but no differences found"
        except Exception as e:
            return f"Parameters differ (parse error: {str(e)[:50]})"

    def _analyze_header_stream(self, data_a: bytes, data_b: bytes) -> str:
        """Analyze Header stream differences."""
        import struct

        if len(data_a) >= 4 and len(data_b) >= 4:
            count_a = struct.unpack('<I', data_a[:4])[0]
            count_b = struct.unpack('<I', data_b[:4])[0]

            if count_a != count_b:
                return f"Primitive count: {count_a} → {count_b}"
            else:
                return f"Header differs but primitive count same ({count_a})"
        else:
            return f"Header size mismatch: {len(data_a)} vs {len(data_b)} bytes"

    def _analyze_data_stream(self, data_a: bytes, data_b: bytes) -> str:
        """Analyze Data stream differences."""
        # This is complex - just provide basic info
        size_diff = len(data_b) - len(data_a)
        percent = abs(size_diff) / max(len(data_a), 1) * 100

        if size_diff > 0:
            return f"Data increased by {size_diff} bytes ({percent:.1f}%)"
        elif size_diff < 0:
            return f"Data decreased by {abs(size_diff)} bytes ({percent:.1f}%)"
        else:
            # Same size but different content
            # Find first difference
            for i in range(len(data_a)):
                if data_a[i] != data_b[i]:
                    return f"Content differs starting at byte {i} (0x{data_a[i]:02X} → 0x{data_b[i]:02X})"
            return "Data differs but couldn't identify location"

    def _analyze_uid_header_stream(self, data_a: bytes, data_b: bytes) -> str:
        """Analyze UniqueIDPrimitiveInformation/Header differences."""
        import struct

        if len(data_a) >= 4 and len(data_b) >= 4:
            count_a = struct.unpack('<I', data_a[:4])[0]
            count_b = struct.unpack('<I', data_b[:4])[0]

            if count_a != count_b:
                return f"UniqueID record count: {count_a} → {count_b}"
            else:
                return f"UID header differs but count same ({count_a})"
        else:
            return f"UID header size mismatch"

    def _analyze_uid_data_stream(self, data_a: bytes, data_b: bytes) -> str:
        """Analyze UniqueIDPrimitiveInformation/Data differences."""
        # Count records
        def count_records(data):
            offset = 0
            count = 0
            while offset < len(data):
                if offset + 4 > len(data):
                    break
                rec_len = struct.unpack('<I', data[offset:offset+4])[0]
                offset += 4 + rec_len
                count += 1
            return count

        try:
            count_a = count_records(data_a)
            count_b = count_records(data_b)

            if count_a != count_b:
                return f"UID records: {count_a} → {count_b}"
            else:
                return f"UID data differs (same record count: {count_a})"
        except Exception:
            return "UID data differs (couldn't parse)"

    def _analyze_widestrings_stream(self, data_a: bytes, data_b: bytes) -> str:
        """Analyze WideStrings stream differences."""
        try:
            # Try to decode as UTF-16
            str_a = data_a.decode('utf-16le', errors='ignore').strip('\x00')
            str_b = data_b.decode('utf-16le', errors='ignore').strip('\x00')

            if str_a != str_b:
                return f"Text: '{str_a[:30]}' → '{str_b[:30]}'"
            else:
                return "WideStrings differ but decoded text is same"
        except Exception:
            return f"WideStrings differ: {len(data_a)} → {len(data_b)} bytes"

    def _analyze_binary_difference(self, data_a: bytes, data_b: bytes) -> str:
        """Generic binary difference analysis."""
        size_a = len(data_a)
        size_b = len(data_b)

        if size_a != size_b:
            diff = size_b - size_a
            return f"Size changed: {size_a} → {size_b} bytes ({diff:+d})"

        # Same size, find first difference
        for i in range(min(size_a, 100)):  # Check first 100 bytes
            if data_a[i] != data_b[i]:
                return f"Content differs at byte {i}: 0x{data_a[i]:02X} → 0x{data_b[i]:02X}"

        return f"Content differs (same size: {size_a} bytes)"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "item_a": self.item_a.to_dict() if self.item_a else None,
            "item_b": self.item_b.to_dict() if self.item_b else None,
            "property_diffs": self.property_diffs,
        }


class LibraryDiffEngine:
    """Analyzes and compares two Altium library files."""

    @staticmethod
    def detect_library_type(filepath: str) -> str:
        """Detect whether file is .SchLib, .PcbLib, or unknown."""
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".schlib":
            return "SchLib"
        elif ext == ".pcblib":
            return "PcbLib"

        # Check content magic bytes
        with open(filepath, "rb") as f:
            header = f.read(100)
            if b"PCB" in header and b"Binary Library File" in header:
                return "PcbLib"
            if b"Schematic Library" in header:
                return "SchLib"

        return "Unknown"

    @classmethod
    def compare(cls, lib_a_path: str, lib_b_path: str) -> Dict[str, Any]:
        """Perform comprehensive comparison between two Altium libraries."""
        type_a = cls.detect_library_type(lib_a_path)
        type_b = cls.detect_library_type(lib_b_path)

        if type_a != type_b:
            raise ValueError(f"Cannot compare different library types: '{lib_a_path}' ({type_a}) vs '{lib_b_path}' ({type_b})")

        lib_type = type_a
        if lib_type == "SchLib":
            _, comps_a, meta_a = SchLibMerger.load_library(lib_a_path)
            _, comps_b, meta_b = SchLibMerger.load_library(lib_b_path)
        elif lib_type == "PcbLib":
            _, comps_a, meta_a = PcbLibMerger.load_library(lib_a_path)
            _, comps_b, meta_b = PcbLibMerger.load_library(lib_b_path)
        else:
            raise ValueError(f"Unsupported Altium library format: {lib_type}")

        map_a: Dict[str, str] = {k.lower(): k for k in comps_a.keys()}
        map_b: Dict[str, str] = {k.lower(): k for k in comps_b.keys()}
        all_keys_lower = sorted(list(set(map_a.keys()) | set(map_b.keys())))

        diff_list: List[ComponentDiff] = []
        counts = {
            "total": len(all_keys_lower),
            "added_a": 0,
            "added_b": 0,
            "identical": 0,
            "conflicts": 0,
        }

        for k in all_keys_lower:
            in_a = k in map_a
            in_b = k in map_b

            if in_a and not in_b:
                orig_name = map_a[k]
                cd = ComponentDiff(orig_name, DiffStatus.ADDED_A, item_a=comps_a[orig_name])
                counts["added_a"] += 1
            elif in_b and not in_a:
                orig_name = map_b[k]
                cd = ComponentDiff(orig_name, DiffStatus.ADDED_B, item_b=comps_b[orig_name])
                counts["added_b"] += 1
            else:
                orig_name_a = map_a[k]
                orig_name_b = map_b[k]
                item_a = comps_a[orig_name_a]
                item_b = comps_b[orig_name_b]
                display_name = orig_name_a

                desc_a = getattr(item_a, "description", "")
                desc_b = getattr(item_b, "description", "")
                if item_a.content_hash == item_b.content_hash and desc_a == desc_b and orig_name_a == orig_name_b:
                    cd = ComponentDiff(display_name, DiffStatus.IDENTICAL, item_a=item_a, item_b=item_b)
                    counts["identical"] += 1
                else:
                    cd = ComponentDiff(display_name, DiffStatus.CONFLICT, item_a=item_a, item_b=item_b)
                    if orig_name_a != orig_name_b:
                        cd.property_diffs.insert(0, {
                            "property": "Name Casing",
                            "val_a": orig_name_a,
                            "val_b": orig_name_b,
                            "type": "string",
                        })
                    counts["conflicts"] += 1

            diff_list.append(cd)

        def clean_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
            cleaned = {}
            for k, v in meta.items():
                if isinstance(v, bytes):
                    continue
                elif k == "layer_kind_mapping" and isinstance(v, dict):
                    # Skip layer_kind_mapping dict containing bytes
                    cleaned[k] = "present" if v.get("data") or v.get("header") else None
                elif k == "models" and isinstance(v, list):
                    cleaned["model_count"] = len(v)
                    cleaned["models"] = [{"id": m.get("id", ""), "name": m.get("name", "")} for m in v]
                elif isinstance(v, dict):
                    # Recursively clean nested dicts
                    cleaned_dict = {}
                    for dk, dv in v.items():
                        if not isinstance(dv, bytes):
                            cleaned_dict[dk] = dv
                    if cleaned_dict:
                        cleaned[k] = cleaned_dict
                elif isinstance(v, list):
                    # Filter any list items containing bytes
                    cleaned_list = []
                    for item in v:
                        if isinstance(item, dict):
                            cleaned_list.append({ik: iv for ik, iv in item.items() if not isinstance(iv, bytes)})
                        elif not isinstance(item, bytes):
                            cleaned_list.append(item)
                    cleaned[k] = cleaned_list
                else:
                    cleaned[k] = v
            return cleaned

        return {
            "library_type": lib_type,
            "file_a": os.path.basename(lib_a_path),
            "file_b": os.path.basename(lib_b_path),
            "path_a": os.path.abspath(lib_a_path),
            "path_b": os.path.abspath(lib_b_path),
            "counts": counts,
            "items": [d.to_dict() for d in diff_list],
            "metadata_a": clean_metadata(meta_a),
            "metadata_b": clean_metadata(meta_b),
        }
