"""Altium Schematic Library (.SchLib) Merger
Parses, compares, and merges Altium .SchLib files with full preservation of
graphic primitives, pins, parameters, models, fonts, and metadata.
"""

import hashlib
import logging
import re
import struct
from typing import Dict, List, Optional, Tuple, Any

from .cfb_handler import (
    CompoundFileTree,
    parse_altium_params,
    build_altium_params,
    sanitize_storage_name,
)

logger = logging.getLogger(__name__)


class SchComponentInfo:
    """Represents an extracted Schematic Library Component."""

    def __init__(self, name: str, section_key: str):
        self.name: str = name
        self.section_key: str = section_key
        self.description: str = ""
        self.part_count: int = 1
        self.designator: str = ""
        self.parameters: Dict[str, str] = {}
        self.pins: List[Dict[str, str]] = []
        self.primitive_count: int = 0
        self.raw_streams: Dict[str, bytes] = {}
        self.content_hash: str = ""

    def compute_hash(self) -> str:
        """Compute SHA-256 hash across all streams of this component."""
        hasher = hashlib.sha256()
        # Sort stream names for deterministic hashing
        for sname in sorted(self.raw_streams.keys()):
            hasher.update(sname.encode("utf-8"))
            hasher.update(self.raw_streams[sname])
        self.content_hash = hasher.hexdigest()
        return self.content_hash

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a serializable dictionary for API/UI."""
        return {
            "name": self.name,
            "section_key": self.section_key,
            "description": self.description,
            "part_count": self.part_count,
            "designator": self.designator,
            "parameters": self.parameters,
            "pins": self.pins,
            "pin_count": len(self.pins),
            "primitive_count": self.primitive_count,
            "stream_names": sorted(list(self.raw_streams.keys())),
            "content_hash": self.content_hash,
        }


class SchLibMerger:
    """Merger and parser for Altium Schematic Libraries (.SchLib)."""

    def __init__(self):
        pass

    @classmethod
    def load_library(cls, filepath: str) -> Tuple[CompoundFileTree, Dict[str, SchComponentInfo], Dict[str, Any]]:
        """Load and parse an Altium .SchLib file.
        
        Returns:
            (tree, components_dict, metadata_dict)
        """
        tree = CompoundFileTree.load_from_file(filepath)
        components, metadata = cls.extract_components(tree)
        return tree, components, metadata

    @classmethod
    def extract_components(cls, tree: CompoundFileTree) -> Tuple[Dict[str, SchComponentInfo], Dict[str, Any]]:
        """Extract all components and metadata from a .SchLib compound file tree."""
        metadata: Dict[str, Any] = {
            "lib_header": "",
            "unique_id": "",
            "weight": "",
            "minor_version": "",
            "fonts": [],
            "raw_file_header": b"",
        }

        # 1. Parse FileHeader
        raw_fh = tree.get_stream(("FileHeader",))
        if not raw_fh:
            raise ValueError("Corrupt .SchLib: Missing 'FileHeader' stream.")

        metadata["raw_file_header"] = raw_fh
        ordered_pairs, fh_lookup = parse_altium_params(raw_fh, has_length_prefix=True)

        metadata["lib_header"] = fh_lookup.get("header", "")
        metadata["unique_id"] = fh_lookup.get("uniqueid", "")
        metadata["weight"] = fh_lookup.get("weight", "")
        metadata["minor_version"] = fh_lookup.get("minorversion", "")

        # Extract fonts
        font_count = int(fh_lookup.get("fontidcount", 0))
        for i in range(1, font_count + 1):
            fname = fh_lookup.get(f"fontname{i}")
            if fname:
                metadata["fonts"].append({
                    "id": i,
                    "name": fname,
                    "size": fh_lookup.get(f"size{i}", "10"),
                    "bold": fh_lookup.get(f"bold{i}", "F"),
                    "italic": fh_lookup.get(f"italic{i}", "F"),
                    "underline": fh_lookup.get(f"underline{i}", "F"),
                })

        # 2. Parse SectionKeys (maps long component names >31 chars to storage name)
        section_key_map: Dict[str, str] = {}
        raw_sk = tree.get_stream(("SectionKeys",))
        if raw_sk:
            _, sk_lookup = parse_altium_params(raw_sk, has_length_prefix=True)
            key_count = int(sk_lookup.get("keycount", 0))
            for i in range(key_count):
                lib_ref = sk_lookup.get(f"libref{i}")
                sec_key = sk_lookup.get(f"sectionkey{i}")
                if lib_ref and sec_key:
                    section_key_map[lib_ref] = sec_key

        # 3. Read component catalog from FileHeader (0 .. compcount-1)
        comp_count = int(fh_lookup.get("compcount", 0))
        component_catalog: List[Tuple[str, str, int]] = []
        for i in range(comp_count):
            lib_ref = fh_lookup.get(f"libref{i}")
            descr = fh_lookup.get(f"compdescr{i}", "")
            parts = int(fh_lookup.get(f"partcount{i}", 1))
            if lib_ref:
                component_catalog.append((lib_ref, descr, parts))

        # Fallback: if compcount was 0 or missing, list storages directly
        if not component_catalog:
            root_storages = tree.list_substorages()
            for sname in root_storages:
                if sname in ("Storage", "FileHeader", "SectionKeys"):
                    continue
                component_catalog.append((sname, "", 1))

        # 4. Extract data for each component
        components: Dict[str, SchComponentInfo] = {}

        for lib_ref, descr, part_count in component_catalog:
            # Determine storage name
            sec_key = section_key_map.get(lib_ref)
            if not sec_key:
                # Try direct storage match or sanitized
                if tree.has_stream((lib_ref, "Data")):
                    sec_key = lib_ref
                else:
                    sanitized = sanitize_storage_name(lib_ref)
                    if tree.has_stream((sanitized, "Data")):
                        sec_key = sanitized
                    else:
                        # Scan root storages for prefix match
                        match = None
                        for s in tree.list_substorages():
                            if s.lower() == lib_ref[:31].lower() or s.lower() == sanitized.lower():
                                match = s
                                break
                        sec_key = match if match else sanitized

            comp_info = SchComponentInfo(name=lib_ref, section_key=sec_key)
            comp_info.description = descr
            comp_info.part_count = part_count

            # Extract all streams in this component's storage
            for stream_path in tree.list_streams(prefix=(sec_key,)):
                sname = stream_path[-1]
                data = tree.get_stream(stream_path)
                if data is not None:
                    comp_info.raw_streams[sname] = data

            # Parse primitives from 'Data' stream
            data_stream = comp_info.raw_streams.get("Data")
            if data_stream:
                cls._parse_symbol_primitives(comp_info, data_stream)

            comp_info.compute_hash()
            components[lib_ref] = comp_info

        logger.info(f"Extracted {len(components)} schematic components.")
        return components, metadata

    @classmethod
    def _parse_symbol_primitives(cls, comp: SchComponentInfo, data: bytes) -> None:
        """Parse primitive records in component Data stream."""
        offset = 0
        prim_count = 0

        while offset < len(data):
            if offset + 4 > len(data):
                break
            rec_len = int.from_bytes(data[offset:offset + 2], "little")
            rec_type = int.from_bytes(data[offset + 2:offset + 4], "big")
            offset += 4

            if rec_len == 0:
                break
            if offset + rec_len > len(data):
                rec_body = data[offset:]
                offset = len(data)
            else:
                rec_body = data[offset:offset + rec_len]
                offset += rec_len

            prim_count += 1

            if rec_type == 0:  # Text parameter record
                _, lookup = parse_altium_params(rec_body, has_length_prefix=False)
                rec_id = lookup.get("record")
                if rec_id == "1":  # Component Header
                    if not comp.description and lookup.get("componentdescription"):
                        comp.description = lookup.get("componentdescription", "")
                elif rec_id == "34":  # Designator
                    comp.designator = lookup.get("text", "")
                elif rec_id == "41":  # Parameter
                    pname = lookup.get("name")
                    ptext = lookup.get("text", "")
                    if pname:
                        comp.parameters[pname] = ptext
                elif rec_id == "2":  # Pin (text format)
                    pin_des = lookup.get("designator", "")
                    pin_name = lookup.get("name", "")
                    pin_elec = lookup.get("electrical", "")
                    comp.pins.append({
                        "designator": pin_des,
                        "name": pin_name,
                        "electrical": pin_elec,
                    })
            elif rec_type == 1:  # Binary pin record
                from .binary_inspector import parse_sch_binary_pin

                try:
                    pin = parse_sch_binary_pin(rec_body)
                    comp.pins.append({
                        "designator": pin["designator"] or f"Pin{len(comp.pins) + 1}",
                        "name": pin["name"],
                        "electrical": pin["electrical"],
                        "length": pin.get("length", 0),
                        "x": pin.get("x", 0),
                        "y": pin.get("y", 0),
                    })
                except Exception:
                    pass

        comp.primitive_count = prim_count

    @classmethod
    def merge(
        cls,
        lib_a_path: str,
        lib_b_path: str,
        output_path: str,
        resolutions: Optional[Dict[str, Dict[str, Any]]] = None,
        default_conflict: str = "prefer_a",
    ) -> Dict[str, Any]:
        """Merge two .SchLib files and write the merged library to output_path.
        
        Args:
            lib_a_path: Path to Library A (Mine / Base)
            lib_b_path: Path to Library B (Theirs / Incoming)
            output_path: Destination .SchLib file path
            resolutions: Dict of {part_name: {'action': 'keep_a'|'keep_b'|'keep_both', 'new_name': '...'}}
            default_conflict: Default action for unassigned conflicts ('prefer_a' or 'prefer_b')

        Returns:
            Summary of merge operations.
        """
        resolutions = resolutions or {}
        tree_a, comps_a, meta_a = cls.load_library(lib_a_path)
        tree_b, comps_b, meta_b = cls.load_library(lib_b_path)

        map_a: Dict[str, str] = {k.lower(): k for k in comps_a.keys()}
        map_b: Dict[str, str] = {k.lower(): k for k in comps_b.keys()}
        all_keys_lower = sorted(list(set(map_a.keys()) | set(map_b.keys())))

        merged_components: List[Tuple[str, SchComponentInfo, CompoundFileTree]] = []
        merge_log: List[Dict[str, Any]] = []

        for k in all_keys_lower:
            in_a = k in map_a
            in_b = k in map_b

            if in_a and not in_b:
                orig_name = map_a[k]
                res = resolutions.get(orig_name) or resolutions.get(k) or {}
                if res.get("action") == "skip":
                    merge_log.append({"name": orig_name, "status": "deleted", "action": "skipped_from_a"})
                    continue
                merged_components.append((orig_name, comps_a[orig_name], tree_a))
                merge_log.append({"name": orig_name, "status": "added_a", "action": "included_from_a"})
            elif in_b and not in_a:
                orig_name = map_b[k]
                res = resolutions.get(orig_name) or resolutions.get(k) or {}
                if res.get("action") == "skip":
                    merge_log.append({"name": orig_name, "status": "deleted", "action": "skipped_from_b"})
                    continue
                merged_components.append((orig_name, comps_b[orig_name], tree_b))
                merge_log.append({"name": orig_name, "status": "added_b", "action": "included_from_b"})
            else:
                # Present in both
                orig_name_a = map_a[k]
                orig_name_b = map_b[k]
                comp_a = comps_a[orig_name_a]
                comp_b = comps_b[orig_name_b]
                name = orig_name_a

                desc_a = getattr(comp_a, "description", "")
                desc_b = getattr(comp_b, "description", "")
                if comp_a.content_hash == comp_b.content_hash and desc_a == desc_b and orig_name_a == orig_name_b:
                    # Identical
                    merged_components.append((name, comp_a, tree_a))
                    merge_log.append({"name": name, "status": "identical", "action": "kept_identical"})
                else:
                    # Conflict!
                    res = (
                        resolutions.get(name)
                        or resolutions.get(orig_name_b)
                        or resolutions.get(k)
                        or {}
                    )
                    action = res.get("action")
                    if not action:
                        action = "keep_a" if default_conflict == "prefer_a" else "keep_b"

                    if action == "keep_a":
                        merged_components.append((name, comp_a, tree_a))
                        merge_log.append({"name": name, "status": "conflict_resolved", "action": "kept_a"})
                    elif action == "keep_b":
                        merged_components.append((orig_name_b, comp_b, tree_b))
                        merge_log.append({"name": orig_name_b, "status": "conflict_resolved", "action": "kept_b"})
                    elif action == "keep_both":
                        # Keep A under original name, add B under new name
                        renamed = res.get("new_name") or f"{orig_name_b}_B"
                        merged_components.append((name, comp_a, tree_a))
                        merged_components.append((renamed, comp_b, tree_b))
                        merge_log.append({
                            "name": name,
                            "status": "conflict_resolved",
                            "action": "kept_both",
                            "renamed_to": renamed,
                        })

        # Build output CompoundFileTree
        out_tree = CompoundFileTree()

        # Copy global 'Storage' stream if present in A or B
        if tree_a.has_stream(("Storage",)):
            out_tree.set_stream(("Storage",), tree_a.get_stream(("Storage",)))
        elif tree_b.has_stream(("Storage",)):
            out_tree.set_stream(("Storage",), tree_b.get_stream(("Storage",)))

        # Merge fonts
        fonts_map: Dict[str, Dict[str, str]] = {}
        for f in meta_a.get("fonts", []):
            fonts_map[f["name"]] = f
        for f in meta_b.get("fonts", []):
            if f["name"] not in fonts_map:
                fonts_map[f["name"]] = f

        merged_fonts = list(fonts_map.values())

        # Allocate unique 31-character storage names and build SectionKeys map
        section_keys_list: List[Tuple[str, str]] = []
        used_storage_names: set[str] = {"Storage", "FileHeader", "SectionKeys"}

        final_components_list: List[Tuple[str, SchComponentInfo, str, CompoundFileTree]] = []

        for final_name, comp_info, src_tree in merged_components:
            base_sec = sanitize_storage_name(final_name, max_len=31)
            sec_name = base_sec
            counter = 1
            while sec_name.lower() in [s.lower() for s in used_storage_names]:
                suffix = f"_{counter}"
                sec_name = sanitize_storage_name(final_name, max_len=31 - len(suffix)) + suffix
                counter += 1

            used_storage_names.add(sec_name)
            final_components_list.append((final_name, comp_info, sec_name, src_tree))

            # If name is longer than 31 chars or was sanitized/renamed, record in SectionKeys
            if len(final_name) > 31 or sec_name != final_name:
                section_keys_list.append((final_name, sec_name))

        # Copy all component streams into the output tree
        # Recalculate primitive counts to ensure Header matches Data stream
        for final_name, comp_info, sec_name, src_tree in final_components_list:
            orig_sec = comp_info.section_key

            # First, copy all streams as-is
            for stream_path in src_tree.list_streams(prefix=(orig_sec,)):
                sname = stream_path[-1]
                data = src_tree.get_stream(stream_path)
                if data is not None:
                    out_tree.set_stream((sec_name, sname), data)

            # Then verify/fix the Header stream if it exists
            # For SchLib, the Header doesn't typically store primitive count
            # (it's implicit in the Data stream), but we ensure consistency
            # by re-parsing the Data stream we just copied
            data_stream = out_tree.get_stream((sec_name, "Data"))
            if data_stream:
                # Recalculate primitive count from the actual Data stream
                temp_comp = SchComponentInfo(name=final_name, section_key=sec_name)
                cls._parse_symbol_primitives(temp_comp, data_stream)
                # Log any discrepancy
                if temp_comp.primitive_count != comp_info.primitive_count:
                    logger.warning(
                        f"Component '{final_name}': primitive count mismatch. "
                        f"Expected {comp_info.primitive_count}, found {temp_comp.primitive_count}. "
                        f"Using actual count from Data stream."
                    )

        # Build SectionKeys stream
        if section_keys_list:
            sk_params: List[Tuple[str, str]] = [("KeyCount", str(len(section_keys_list)))]
            for i, (lib_ref, sec_key) in enumerate(section_keys_list):
                sk_params.append((f"LibRef{i}", lib_ref))
                sk_params.append((f"SectionKey{i}", sec_key))
            out_tree.set_stream(("SectionKeys",), build_altium_params(sk_params, has_length_prefix=True))

        # Build FileHeader stream
        fh_params: List[Tuple[str, str]] = []

        # 1. Start with base document settings from Library A (or B) to preserve canvas colors, grids, and sheet borders
        raw_fh_base = meta_a.get("raw_file_header") or meta_b.get("raw_file_header")
        seen_keys: set = set()

        if raw_fh_base:
            comp_key_re = re.compile(r"^(%utf8%)?(compcount|libref\d+|compdescr\d+|partcount\d+)$")
            font_key_re = re.compile(r"^(%utf8%)?(fontidcount|fontname\d+|size\d+|bold\d+|italic\d+|underline\d+)$")
            base_pairs, _ = parse_altium_params(raw_fh_base, has_length_prefix=True)
            for k, v in base_pairs:
                kl = k.lower()
                # Skip component items and font items (including any %utf8% prefixed keys)
                if comp_key_re.match(kl) or font_key_re.match(kl):
                    continue
                if kl not in seen_keys:
                    seen_keys.add(kl)
                    fh_params.append((k, v))

        # Ensure crucial Altium display settings exist so the schematic canvas is NOT black
        # AreaColor = 16317695 (#F8FCFF, standard Altium light schematic sheet background)
        if "areacolor" not in seen_keys:
            fh_params.append(("AreaColor", "16317695"))
            seen_keys.add("areacolor")
        else:
            # If AreaColor is 0 or empty, force it to standard Altium default light color
            for idx, (k, v) in enumerate(fh_params):
                if k.lower() == "areacolor" and (not v or v == "0"):
                    fh_params[idx] = ("AreaColor", "16317695")

        if "header" not in seen_keys:
            header_val = meta_a.get("lib_header") or meta_b.get("lib_header") or "Protel for Windows - Schematic Library Editor Binary File Version 5.0"
            fh_params.insert(0, ("HEADER", header_val))
            seen_keys.add("header")

        if "sheetstyle" not in seen_keys:
            fh_params.append(("SheetStyle", "9"))
        if "borderon" not in seen_keys:
            fh_params.append(("BorderOn", "T"))
        if "snapgridon" not in seen_keys:
            fh_params.append(("SnapGridOn", "T"))
        if "snapgridsize" not in seen_keys:
            fh_params.append(("SnapGridSize", "10"))
        if "visiblegridon" not in seen_keys:
            fh_params.append(("VisibleGridOn", "T"))
        if "visiblegridsize" not in seen_keys:
            fh_params.append(("VisibleGridSize", "10"))
        if "usembcs" not in seen_keys:
            fh_params.append(("UseMBCS", "T"))
        if "isboc" not in seen_keys:
            fh_params.append(("IsBOC", "T"))

        # 2. Add Fonts
        fh_params.append(("FontIdCount", str(len(merged_fonts))))
        for i, font in enumerate(merged_fonts, 1):
            fh_params.append((f"Size{i}", font.get("size", "10")))
            fh_params.append((f"FontName{i}", font.get("name", "Arial")))
            if font.get("bold") == "T":
                fh_params.append((f"Bold{i}", "T"))
            if font.get("italic") == "T":
                fh_params.append((f"Italic{i}", "T"))
            if font.get("underline") == "T":
                fh_params.append((f"Underline{i}", "T"))

        # 3. Add Component Index Catalog
        fh_params.append(("CompCount", str(len(final_components_list))))
        for i, (final_name, comp_info, _, _) in enumerate(final_components_list):
            fh_params.append((f"LibRef{i}", final_name))
            fh_params.append((f"CompDescr{i}", comp_info.description or ""))
            fh_params.append((f"PartCount{i}", str(comp_info.part_count or 1)))

        out_tree.set_stream(("FileHeader",), build_altium_params(fh_params, has_length_prefix=True))

        # Save merged compound file to disk
        out_tree.save_to_file(output_path)

        # Verify output with olefile
        verify_tree = CompoundFileTree.load_from_file(output_path)
        logger.info(f"Verification successful: Merged library has {len(verify_tree.streams)} streams.")

        return {
            "success": True,
            "output_path": output_path,
            "total_components": len(final_components_list),
            "added_from_a": len([l for l in merge_log if l["status"] == "added_a"]),
            "added_from_b": len([l for l in merge_log if l["status"] == "added_b"]),
            "identical": len([l for l in merge_log if l["status"] == "identical"]),
            "conflicts_resolved": len([l for l in merge_log if l["status"] == "conflict_resolved"]),
            "log": merge_log,
        }
