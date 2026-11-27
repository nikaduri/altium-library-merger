"""Altium Library Diff Engine
Performs component-level and property-level diffing between two Altium libraries.
"""

from enum import Enum
import os
import struct
from typing import Dict, List, Optional, Tuple, Any, Union

from .cfb_handler import parse_altium_params
from .schlib_merger import SchLibMerger, SchComponentInfo
from .pcblib_merger import PcbLibMerger, PcbFootprintInfo
from .binary_inspector import (
    analyze_sch_data_diff,
    analyze_sch_pin_sidecar_diff,
    analyze_pcb_data_diff,
    analyze_pcb_widestrings_diff,
    analyze_uid_data_diff,
    analyze_primitive_guids_diff,
)


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
        lib_type: str = "",
        meta_a: Optional[Dict[str, Any]] = None,
        meta_b: Optional[Dict[str, Any]] = None,
    ):
        self.name: str = name
        self.status: DiffStatus = status
        self.item_a = item_a
        self.item_b = item_b
        self.lib_type: str = lib_type
        self.meta_a: Optional[Dict[str, Any]] = meta_a
        self.meta_b: Optional[Dict[str, Any]] = meta_b
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

        # Compare raw streams to identify semantic and binary differences
        streams_a = getattr(self.item_a, "raw_streams", {})
        streams_b = getattr(self.item_b, "raw_streams", {})
        all_stream_names = sorted(list(set(streams_a.keys()) | set(streams_b.keys())))

        stream_details = []
        stream_extra_diffs: List[Dict[str, Any]] = []

        for stream_name in all_stream_names:
            data_a = streams_a.get(stream_name)
            data_b = streams_b.get(stream_name)

            if data_a != data_b:
                size_a = len(data_a) if data_a else 0
                size_b = len(data_b) if data_b else 0

                # Analyze what's different in this specific stream
                diff_detail, extra_diffs = self._analyze_stream_difference(stream_name, data_a, data_b)
                stream_details.append({
                    "stream": stream_name,
                    "size_a": size_a,
                    "size_b": size_b,
                    "analysis": diff_detail,
                })
                stream_extra_diffs.extend(extra_diffs)

        # Add detailed information for each differing stream plus decoded sub-items
        for detail in stream_details:
            self.property_diffs.append({
                "property": f"Stream: {detail['stream']}",
                "val_a": f"{detail['size_a']} bytes",
                "val_b": f"{detail['size_b']} bytes",
                "type": "binary_stream",
                "details": detail["analysis"],
            })

        if stream_extra_diffs:
            max_items = 25
            self.property_diffs.extend(stream_extra_diffs[:max_items])
            if len(stream_extra_diffs) > max_items:
                self.property_diffs.append({
                    "property": "Stream Details (truncated)",
                    "val_a": f"+{len(stream_extra_diffs) - max_items} more",
                    "val_b": "See stream summary",
                    "type": "stream_detail",
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

    def _is_schlib_item(self) -> bool:
        """Infer whether compared items belong to a Schematic Library."""
        if self.lib_type == "SchLib":
            return True
        if self.lib_type == "PcbLib":
            return False
        return hasattr(self.item_a, "pins") or hasattr(self.item_b, "pins")

    def _analyze_stream_difference(
        self, stream_name: str, data_a: Optional[bytes], data_b: Optional[bytes]
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Analyze semantic and binary differences between two component streams."""
        if data_a is None:
            return "Stream only exists in B", []
        if data_b is None:
            return "Stream only exists in A", []

        sidecar_streams = (
            "PinTextData",
            "PinFrac",
            "PinWideText",
            "PinPackageLength",
            "PinSymbolLineWidth",
            "PinFunctionData",
        )

        if stream_name == "Parameters":
            return self._analyze_parameters_stream(data_a, data_b), []
        if stream_name == "Header":
            return self._analyze_header_stream(data_a, data_b), []
        if stream_name == "Data":
            return self._analyze_data_stream(data_a, data_b)
        if stream_name in sidecar_streams:
            return analyze_sch_pin_sidecar_diff(
                stream_name,
                data_a,
                data_b,
                self.meta_a,
                self.meta_b,
                getattr(self.item_a, "pins", None),
                getattr(self.item_b, "pins", None),
            )
        if stream_name == "UniqueIDPrimitiveInformation/Header":
            return self._analyze_count_header_stream(data_a, data_b, "UniqueID record count"), []
        if stream_name == "PrimitiveGuids/Header":
            return self._analyze_count_header_stream(data_a, data_b, "Primitive GUID count"), []
        if stream_name == "UniqueIDPrimitiveInformation/Data":
            return analyze_uid_data_diff(data_a, data_b), []
        if stream_name == "PrimitiveGuids/Data":
            return analyze_primitive_guids_diff(data_a, data_b), []
        if stream_name == "WideStrings":
            return analyze_pcb_widestrings_diff(data_a, data_b), []
        return self._analyze_binary_difference(data_a, data_b), []

    def _analyze_parameters_stream(self, data_a: bytes, data_b: bytes) -> str:
        """Analyze Parameters stream differences."""
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
            return "Parameters parsed but no differences found"
        except Exception as e:
            return f"Parameters differ (parse error: {str(e)[:50]})"

    def _analyze_header_stream(self, data_a: bytes, data_b: bytes) -> str:
        """Analyze Header stream differences."""
        if len(data_a) >= 4 and len(data_b) >= 4:
            count_a = struct.unpack("<I", data_a[:4])[0]
            count_b = struct.unpack("<I", data_b[:4])[0]
            if count_a != count_b:
                return f"Primitive count: {count_a} → {count_b}"
            return f"Header differs but primitive count same ({count_a})"
        return f"Header size mismatch: {len(data_a)} vs {len(data_b)} bytes"

    def _analyze_data_stream(self, data_a: bytes, data_b: bytes) -> Tuple[str, List[Dict[str, Any]]]:
        """Decode and analyze SchLib or PcbLib 'Data' stream differences."""
        try:
            if self._is_schlib_item():
                return analyze_sch_data_diff(data_a, data_b, self.meta_a, self.meta_b)
            return analyze_pcb_data_diff(data_a, data_b, self.meta_a, self.meta_b)
        except Exception as exc:
            size_diff = len(data_b) - len(data_a)
            return f"Data differs ({size_diff:+d} bytes; decode note: {str(exc)[:60]})", []

    def _analyze_count_header_stream(self, data_a: bytes, data_b: bytes, label: str = "Record count") -> str:
        """Analyze 4-byte uint32 count header streams (UID/GUID headers)."""
        if len(data_a) >= 4 and len(data_b) >= 4:
            count_a = struct.unpack("<I", data_a[:4])[0]
            count_b = struct.unpack("<I", data_b[:4])[0]
            if count_a != count_b:
                return f"{label}: {count_a} → {count_b}"
            return f"Header differs but {label.lower()} same ({count_a})"
        return f"Header size mismatch: {len(data_a)} vs {len(data_b)} bytes"

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
                cd = ComponentDiff(
                    orig_name, DiffStatus.ADDED_A, item_a=comps_a[orig_name], lib_type=lib_type, meta_a=meta_a, meta_b=meta_b
                )
                counts["added_a"] += 1
            elif in_b and not in_a:
                orig_name = map_b[k]
                cd = ComponentDiff(
                    orig_name, DiffStatus.ADDED_B, item_b=comps_b[orig_name], lib_type=lib_type, meta_a=meta_a, meta_b=meta_b
                )
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
                    cd = ComponentDiff(
                        display_name, DiffStatus.IDENTICAL, item_a=item_a, item_b=item_b, lib_type=lib_type, meta_a=meta_a, meta_b=meta_b
                    )
                    counts["identical"] += 1
                else:
                    cd = ComponentDiff(
                        display_name, DiffStatus.CONFLICT, item_a=item_a, item_b=item_b, lib_type=lib_type, meta_a=meta_a, meta_b=meta_b
                    )
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

    @classmethod
    def plan_three_way_merge(
        cls,
        base_path: Optional[str],
        ours_path: str,
        theirs_path: str,
    ) -> Dict[str, Any]:
        """Compute a Git-style 3-way merge plan using a common ancestor (base).

        Returns:
            {
                "library_type": "SchLib" | "PcbLib",
                "resolutions": dict ready for SchLibMerger.merge / PcbLibMerger.merge,
                "conflicts": list of true conflict dicts (name, reason, property_diffs),
                "summary": dict of counts (added_ours, added_theirs, updated_ours,
                           updated_theirs, deleted_ours, deleted_theirs, identical, conflicts),
            }
        """
        type_ours = cls.detect_library_type(ours_path)
        type_theirs = cls.detect_library_type(theirs_path)
        if type_ours != type_theirs:
            raise ValueError(f"Cannot merge different library types: {type_ours} vs {type_theirs}")

        lib_type = type_ours
        if lib_type == "SchLib":
            loader = SchLibMerger.load_library
        elif lib_type == "PcbLib":
            loader = PcbLibMerger.load_library
        else:
            raise ValueError(f"Unsupported Altium library format: {lib_type}")

        _, items_ours, meta_ours = loader(ours_path)
        _, items_theirs, meta_theirs = loader(theirs_path)

        items_base: Dict[str, Any] = {}
        meta_base: Dict[str, Any] = {}
        if base_path and os.path.exists(base_path) and os.path.getsize(base_path) > 0:
            type_base = cls.detect_library_type(base_path)
            if type_base == lib_type:
                _, items_base, meta_base = loader(base_path)

        def _sig(item: Any) -> Tuple[str, str, str]:
            return (
                item.name,
                getattr(item, "description", ""),
                item.content_hash,
            )

        map_base = {k.lower(): k for k in items_base.keys()}
        map_ours = {k.lower(): k for k in items_ours.keys()}
        map_theirs = {k.lower(): k for k in items_theirs.keys()}
        all_keys = sorted(set(map_base.keys()) | set(map_ours.keys()) | set(map_theirs.keys()))

        resolutions: Dict[str, Dict[str, Any]] = {}
        conflicts: List[Dict[str, Any]] = []
        summary = {
            "added_ours": 0,
            "added_theirs": 0,
            "updated_ours": 0,
            "updated_theirs": 0,
            "deleted_ours": 0,
            "deleted_theirs": 0,
            "identical": 0,
            "conflicts": 0,
        }

        for k in all_keys:
            in_base = k in map_base
            in_ours = k in map_ours
            in_theirs = k in map_theirs

            b_item = items_base[map_base[k]] if in_base else None
            o_item = items_ours[map_ours[k]] if in_ours else None
            t_item = items_theirs[map_theirs[k]] if in_theirs else None

            if in_ours and in_theirs:
                if _sig(o_item) == _sig(t_item):
                    summary["identical"] += 1
                    continue
                if in_base:
                    o_same = _sig(o_item) == _sig(b_item)
                    t_same = _sig(t_item) == _sig(b_item)
                    if o_same and not t_same:
                        resolutions[o_item.name] = {"action": "keep_b"}
                        summary["updated_theirs"] += 1
                        continue
                    if t_same and not o_same:
                        resolutions[o_item.name] = {"action": "keep_a"}
                        summary["updated_ours"] += 1
                        continue
                    reason = "Both branches modified component differently"
                else:
                    reason = "Both branches added component with different content"

                # True conflict: keep ours in partial output, report conflict
                resolutions[o_item.name] = {"action": "keep_a"}
                cd = ComponentDiff(
                    o_item.name, DiffStatus.CONFLICT, item_a=o_item, item_b=t_item,
                    lib_type=lib_type, meta_a=meta_ours, meta_b=meta_theirs,
                )
                conflicts.append({
                    "name": o_item.name,
                    "reason": reason,
                    "property_diffs": cd.property_diffs,
                })
                summary["conflicts"] += 1

            elif in_ours and not in_theirs:
                if in_base:
                    if _sig(o_item) == _sig(b_item):
                        # Cleanly deleted in theirs
                        resolutions[o_item.name] = {"action": "skip"}
                        summary["deleted_theirs"] += 1
                    else:
                        # Modified in ours, deleted in theirs -> true conflict
                        resolutions[o_item.name] = {"action": "keep_a"}
                        conflicts.append({
                            "name": o_item.name,
                            "reason": "Modified in ours, deleted in theirs",
                            "property_diffs": [],
                        })
                        summary["conflicts"] += 1
                else:
                    summary["added_ours"] += 1

            elif in_theirs and not in_ours:
                if in_base:
                    if _sig(t_item) == _sig(b_item):
                        # Cleanly deleted in ours
                        resolutions[t_item.name] = {"action": "skip"}
                        summary["deleted_ours"] += 1
                    else:
                        # Deleted in ours, modified in theirs -> true conflict
                        resolutions[t_item.name] = {"action": "keep_b"}
                        conflicts.append({
                            "name": t_item.name,
                            "reason": "Deleted in ours, modified in theirs",
                            "property_diffs": [],
                        })
                        summary["conflicts"] += 1
                else:
                    summary["added_theirs"] += 1

        return {
            "library_type": lib_type,
            "resolutions": resolutions,
            "conflicts": conflicts,
            "summary": summary,
        }
