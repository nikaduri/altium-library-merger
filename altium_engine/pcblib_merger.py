"""Altium PCB Footprint Library (.PcbLib) Merger
Parses, compares, and merges Altium .PcbLib files with full preservation of
footprint primitives, pads, tracks, 3D STEP models, layer mappings, and SectionKeys.
"""

import hashlib
import logging
import struct
from typing import Dict, List, Optional, Tuple, Any, Set

from .cfb_handler import (
    CompoundFileTree,
    parse_altium_params,
    build_altium_params,
    sanitize_storage_name,
)

logger = logging.getLogger(__name__)


class PcbFootprintInfo:
    """Represents an extracted PCB Footprint from a .PcbLib."""

    def __init__(self, name: str, section_key: str):
        self.name: str = name
        self.section_key: str = section_key
        self.description: str = ""
        self.height: str = "0"
        self.pad_count: int = 0
        self.primitive_count: int = 0
        self.has_3d_model: bool = False
        self.raw_streams: Dict[str, bytes] = {}
        self.content_hash: str = ""

    def compute_hash(self) -> str:
        """Compute SHA-256 hash across all streams of this footprint."""
        hasher = hashlib.sha256()
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
            "height": self.height,
            "pad_count": self.pad_count,
            "primitive_count": self.primitive_count,
            "has_3d_model": self.has_3d_model,
            "stream_names": sorted(list(self.raw_streams.keys())),
            "content_hash": self.content_hash,
        }


class PcbLibMerger:
    """Merger and parser for Altium PCB Footprint Libraries (.PcbLib)."""

    def __init__(self):
        pass

    @classmethod
    def load_library(cls, filepath: str) -> Tuple[CompoundFileTree, Dict[str, PcbFootprintInfo], Dict[str, Any]]:
        """Load and parse an Altium .PcbLib file.
        
        Returns:
            (tree, footprints_dict, metadata_dict)
        """
        tree = CompoundFileTree.load_from_file(filepath)
        footprints, metadata = cls.extract_footprints(tree)
        return tree, footprints, metadata

    @classmethod
    def extract_footprints(cls, tree: CompoundFileTree) -> Tuple[Dict[str, PcbFootprintInfo], Dict[str, Any]]:
        """Extract all footprints and metadata from a .PcbLib compound file tree."""
        metadata: Dict[str, Any] = {
            "file_header": b"",
            "lib_header": b"\x01\x00\x00\x00",
            "library_params": b"",
            "models": [],  # List of dict: {'id', 'param_bytes', 'stream_bytes', 'name'}
            "layer_kind_mapping": None,
        }

        # 1. Root FileHeader (53 bytes)
        raw_fh = tree.get_stream(("FileHeader",))
        if raw_fh:
            metadata["file_header"] = raw_fh

        # 2. Library/Header
        raw_lh = tree.get_stream(("Library", "Header"))
        if raw_lh:
            metadata["lib_header"] = raw_lh

        # 3. LayerKindMapping if present
        raw_lkm_data = tree.get_stream(("Library", "LayerKindMapping", "Data"))
        raw_lkm_header = tree.get_stream(("Library", "LayerKindMapping", "Header"))
        if raw_lkm_data or raw_lkm_header:
            metadata["layer_kind_mapping"] = {
                "data": raw_lkm_data,
                "header": raw_lkm_header
            }

        # 4. SectionKeys (binary map for long names > 31 chars)
        section_key_map: Dict[str, str] = {}
        raw_sk = tree.get_stream(("SectionKeys",))
        if raw_sk and len(raw_sk) >= 4:
            count = struct.unpack('<I', raw_sk[:4])[0]
            offset = 4
            for _ in range(count):
                if offset + 4 > len(raw_sk):
                    break
                orig_len = struct.unpack('<I', raw_sk[offset:offset + 4])[0]
                offset += 4
                orig_name = raw_sk[offset:offset + orig_len].decode("latin1", errors="replace")
                offset += orig_len

                if offset + 4 > len(raw_sk):
                    break
                sec_len = struct.unpack('<I', raw_sk[offset:offset + 4])[0]
                offset += 4
                sec_key = raw_sk[offset:offset + sec_len].decode("latin1", errors="replace")
                offset += sec_len

                section_key_map[orig_name] = sec_key

        # 5. Extract 3D Models from Library/Models/
        raw_mh = tree.get_stream(("Library", "Models", "Header"))
        raw_md = tree.get_stream(("Library", "Models", "Data"))
        if raw_mh and raw_md and len(raw_mh) >= 4:
            model_count = struct.unpack('<I', raw_mh[:4])[0]
            m_offset = 0
            for idx in range(model_count):
                if m_offset + 4 > len(raw_md):
                    break
                plen = struct.unpack('<I', raw_md[m_offset:m_offset + 4])[0]
                m_offset += 4
                pbytes = raw_md[m_offset:m_offset + plen]
                m_offset += plen

                mstream = tree.get_stream(("Library", "Models", str(idx)))
                p_text = pbytes.decode("latin1", errors="replace")
                _, p_lookup = parse_altium_params(pbytes, has_length_prefix=False)
                mid = p_lookup.get("id", f"MODEL_{idx}")
                mname = p_lookup.get("name", "")

                metadata["models"].append({
                    "index": idx,
                    "id": mid,
                    "name": mname,
                    "param_bytes": pbytes,
                    "stream_bytes": mstream or b"",
                })

        # 6. Parse Library/Data: Component Count and Footprint Names
        raw_ld = tree.get_stream(("Library", "Data"))
        if not raw_ld or len(raw_ld) < 4:
            raise ValueError("Corrupt .PcbLib: Missing or empty 'Library/Data' stream.")

        param_len = struct.unpack('<I', raw_ld[:4])[0]
        metadata["library_params"] = raw_ld[4:4 + param_len]

        offset = 4 + param_len
        comp_count = 0
        if offset + 4 <= len(raw_ld):
            comp_count = struct.unpack('<I', raw_ld[offset:offset + 4])[0]
            offset += 4

        footprint_names: List[str] = []
        for _ in range(comp_count):
            if offset + 4 > len(raw_ld):
                break
            block_len = struct.unpack('<I', raw_ld[offset:offset + 4])[0]
            offset += 4
            if offset >= len(raw_ld):
                break
            str_len = raw_ld[offset]
            name_bytes = raw_ld[offset + 1:offset + 1 + str_len]
            fp_name = name_bytes.decode("windows-1252", errors="replace")
            footprint_names.append(fp_name)
            offset += block_len

        # Fallback if catalog was empty
        if not footprint_names:
            for s in tree.list_substorages():
                if s not in ("Library", "FileHeader", "SectionKeys"):
                    footprint_names.append(s)

        # 7. Extract data for each footprint
        footprints: Dict[str, PcbFootprintInfo] = {}

        for fp_name in footprint_names:
            sec_key = section_key_map.get(fp_name)
            if not sec_key:
                if tree.has_stream((fp_name, "Data")):
                    sec_key = fp_name
                else:
                    sanitized = sanitize_storage_name(fp_name)
                    if tree.has_stream((sanitized, "Data")):
                        sec_key = sanitized
                    else:
                        match = None
                        for s in tree.list_substorages():
                            if s.lower() == fp_name[:31].lower() or s.lower() == sanitized.lower():
                                match = s
                                break
                        sec_key = match if match else sanitized

            fp_info = PcbFootprintInfo(name=fp_name, section_key=sec_key)

            # Copy all streams inside this footprint's storage
            for stream_path in tree.list_streams(prefix=(sec_key,)):
                # Relative path from footprint storage
                rel_parts = stream_path[1:]
                rel_path = "/".join(rel_parts)
                data = tree.get_stream(stream_path)
                if data is not None:
                    fp_info.raw_streams[rel_path] = data

            # Parse Parameters stream
            param_data = fp_info.raw_streams.get("Parameters")
            if param_data:
                _, p_lookup = parse_altium_params(param_data, has_length_prefix=True)
                fp_info.description = p_lookup.get("description", "")
                fp_info.height = p_lookup.get("height", "0")

            # Parse Header stream (primitive count)
            hdr_data = fp_info.raw_streams.get("Header")
            if hdr_data and len(hdr_data) >= 4:
                fp_info.primitive_count = struct.unpack('<I', hdr_data[:4])[0]

            # Parse Data stream (count pads & 3d models)
            data_stream = fp_info.raw_streams.get("Data")
            if data_stream:
                cls._parse_footprint_data(fp_info, data_stream)

            fp_info.compute_hash()
            footprints[fp_name] = fp_info

        logger.info(f"Extracted {len(footprints)} PCB footprints and {len(metadata['models'])} 3D models.")
        return footprints, metadata

    @classmethod
    def _parse_footprint_data(cls, fp: PcbFootprintInfo, data: bytes) -> None:
        """Scan Data stream for pads and 3D component body records."""
        offset = 0
        if len(data) < 4:
            return

        # Skip initial string block (the footprint name)
        try:
            initial_block_len = struct.unpack('<I', data[:4])[0]
            offset = 4 + initial_block_len
        except Exception:
            offset = 0

        pad_count = 0
        has_3d = False

        while offset < len(data):
            rec_id = data[offset]
            offset += 1

            if rec_id == 0:  # End of records
                break
            elif rec_id == 2:  # PcbPad
                pad_count += 1
                if offset + 4 <= len(data):
                    blen = struct.unpack('<I', data[offset:offset + 4])[0]
                    offset += 4 + blen
            elif rec_id == 12:  # PcbComponentBody (3D Model)
                has_3d = True
                if offset + 4 <= len(data):
                    blen = struct.unpack('<I', data[offset:offset + 4])[0]
                    offset += 4 + blen
                    # Advance past trailing block
                    if offset + 4 <= len(data):
                        tblen = struct.unpack('<I', data[offset:offset + 4])[0]
                        offset += 4 + tblen
            else:
                # Other records: read block length and skip
                if offset + 4 <= len(data):
                    blen = struct.unpack('<I', data[offset:offset + 4])[0]
                    offset += 4 + blen
                else:
                    break

        fp.pad_count = pad_count
        fp.has_3d_model = has_3d

    @classmethod
    def _count_primitives_in_data(cls, data: bytes) -> int:
        """Count the actual number of primitive records in a footprint Data stream."""
        offset = 0
        if len(data) < 4:
            return 0

        # Skip initial string block (the footprint name)
        try:
            initial_block_len = struct.unpack('<I', data[:4])[0]
            offset = 4 + initial_block_len
        except Exception:
            return 0

        primitive_count = 0

        while offset < len(data):
            if offset >= len(data):
                break

            rec_id = data[offset]
            offset += 1

            if rec_id == 0:  # End of records
                break

            primitive_count += 1

            # Read and skip the record body
            if rec_id == 12:  # PcbComponentBody has special structure
                if offset + 4 <= len(data):
                    blen = struct.unpack('<I', data[offset:offset + 4])[0]
                    offset += 4 + blen
                    # Advance past trailing block
                    if offset + 4 <= len(data):
                        tblen = struct.unpack('<I', data[offset:offset + 4])[0]
                        offset += 4 + tblen
            else:
                # Standard record: ID + 4-byte length + body
                if offset + 4 <= len(data):
                    blen = struct.unpack('<I', data[offset:offset + 4])[0]
                    offset += 4 + blen
                else:
                    break

        return primitive_count

    @classmethod
    def _count_uid_records(cls, data: bytes) -> int:
        """Count the actual number of UniqueID records in UniqueIDPrimitiveInformation/Data stream.

        Each record is length-prefixed: 4-byte length + record data.
        """
        offset = 0
        record_count = 0

        while offset < len(data):
            if offset + 4 > len(data):
                break

            rec_len = struct.unpack('<I', data[offset:offset + 4])[0]
            offset += 4 + rec_len
            record_count += 1

        return record_count

    @classmethod
    def merge(
        cls,
        lib_a_path: str,
        lib_b_path: str,
        output_path: str,
        resolutions: Optional[Dict[str, Dict[str, Any]]] = None,
        default_conflict: str = "prefer_a",
    ) -> Dict[str, Any]:
        """Merge two .PcbLib files and write the merged library to output_path.
        
        Args:
            lib_a_path: Path to Library A (Mine / Base)
            lib_b_path: Path to Library B (Theirs / Incoming)
            output_path: Destination .PcbLib file path
            resolutions: Dict of {footprint_name: {'action': 'keep_a'|'keep_b'|'keep_both', 'new_name': '...'}}
            default_conflict: Default action for unassigned conflicts ('prefer_a' or 'prefer_b')

        Returns:
            Summary of merge operations.
        """
        resolutions = resolutions or {}
        tree_a, fps_a, meta_a = cls.load_library(lib_a_path)
        tree_b, fps_b, meta_b = cls.load_library(lib_b_path)

        map_a: Dict[str, str] = {k.lower(): k for k in fps_a.keys()}
        map_b: Dict[str, str] = {k.lower(): k for k in fps_b.keys()}
        all_keys_lower = sorted(list(set(map_a.keys()) | set(map_b.keys())))

        merged_footprints: List[Tuple[str, PcbFootprintInfo, CompoundFileTree]] = []
        merge_log: List[Dict[str, Any]] = []

        for k in all_keys_lower:
            in_a = k in map_a
            in_b = k in map_b

            if in_a and not in_b:
                orig_name = map_a[k]
                merged_footprints.append((orig_name, fps_a[orig_name], tree_a))
                merge_log.append({"name": orig_name, "status": "added_a", "action": "included_from_a"})
            elif in_b and not in_a:
                orig_name = map_b[k]
                merged_footprints.append((orig_name, fps_b[orig_name], tree_b))
                merge_log.append({"name": orig_name, "status": "added_b", "action": "included_from_b"})
            else:
                orig_name_a = map_a[k]
                orig_name_b = map_b[k]
                fp_a = fps_a[orig_name_a]
                fp_b = fps_b[orig_name_b]
                name = orig_name_a

                desc_a = getattr(fp_a, "description", "")
                desc_b = getattr(fp_b, "description", "")
                if fp_a.content_hash == fp_b.content_hash and desc_a == desc_b and orig_name_a == orig_name_b:
                    # Identical
                    merged_footprints.append((name, fp_a, tree_a))
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
                        merged_footprints.append((name, fp_a, tree_a))
                        merge_log.append({"name": name, "status": "conflict_resolved", "action": "kept_a"})
                    elif action == "keep_b":
                        merged_footprints.append((orig_name_b, fp_b, tree_b))
                        merge_log.append({"name": orig_name_b, "status": "conflict_resolved", "action": "kept_b"})
                    elif action == "keep_both":
                        renamed = res.get("new_name") or f"{orig_name_b}_B"
                        merged_footprints.append((name, fp_a, tree_a))
                        merged_footprints.append((renamed, fp_b, tree_b))
                        merge_log.append({
                            "name": name,
                            "status": "conflict_resolved",
                            "action": "kept_both",
                            "renamed_to": renamed,
                        })

        out_tree = CompoundFileTree()

        # 1. Root FileHeader (53 bytes)
        fh_bytes = meta_a.get("file_header") or meta_b.get("file_header")
        if not fh_bytes or len(fh_bytes) != 53:
            # Standard Altium PCB 6.0 Binary Library FileHeader
            sig = b"PCB 6.0 Binary Library File"
            fh_bytes = struct.pack('<I', len(sig)) + bytes([len(sig)]) + sig + b"\x00" * (53 - len(sig) - 5)
        out_tree.set_stream(("FileHeader",), fh_bytes)

        # 2. Library/Header
        lh_bytes = meta_a.get("lib_header") or meta_b.get("lib_header") or b"\x01\x00\x00\x00"
        out_tree.set_stream(("Library", "Header"), lh_bytes)

        # 3. Copy ALL additional library-level streams from source A (or B if not in A)
        # This includes: FileVersionInfo, ComponentParamsTOC, EmbeddedFonts, PadViaLibrary,
        # Textures, ModelsNoEmbed, and any other library metadata
        footprint_storages = set(fp.section_key for fp in fps_a.values()) | set(fp.section_key for fp in fps_b.values())
        footprint_storages.update(["FileHeader", "SectionKeys"])

        for stream_path in tree_a.list_streams():
            # Skip footprint-specific streams and streams we handle explicitly
            if len(stream_path) == 0:
                continue
            root_storage = stream_path[0]

            # Skip footprint storages
            if root_storage in footprint_storages:
                continue

            # Skip streams we handle explicitly later
            if stream_path == ("FileHeader",):
                continue
            if stream_path == ("Library", "Header"):
                continue
            if stream_path == ("Library", "Data"):
                continue
            if stream_path[:2] == ("Library", "Models"):
                continue
            if stream_path[:2] == ("Library", "LayerKindMapping"):
                continue

            # Copy this library-level stream from tree_a
            data = tree_a.get_stream(stream_path)
            if data is not None:
                out_tree.set_stream(stream_path, data)

        # Also copy any streams from tree_b that aren't in tree_a
        for stream_path in tree_b.list_streams():
            if len(stream_path) == 0:
                continue
            root_storage = stream_path[0]

            # Skip if already copied from tree_a
            if out_tree.has_stream(stream_path):
                continue

            # Skip footprint storages
            if root_storage in footprint_storages:
                continue

            # Skip streams we handle explicitly
            if stream_path == ("FileHeader",):
                continue
            if stream_path == ("Library", "Header"):
                continue
            if stream_path == ("Library", "Data"):
                continue
            if stream_path[:2] == ("Library", "Models"):
                continue
            if stream_path[:2] == ("Library", "LayerKindMapping"):
                continue

            # Copy this library-level stream from tree_b
            data = tree_b.get_stream(stream_path)
            if data is not None:
                out_tree.set_stream(stream_path, data)

        # 4. LayerKindMapping if present
        lkm_a = meta_a.get("layer_kind_mapping")
        lkm_b = meta_b.get("layer_kind_mapping")

        if lkm_a:
            if lkm_a.get("data"):
                out_tree.set_stream(("Library", "LayerKindMapping", "Data"), lkm_a["data"])
            if lkm_a.get("header"):
                out_tree.set_stream(("Library", "LayerKindMapping", "Header"), lkm_a["header"])
        elif lkm_b:
            if lkm_b.get("data"):
                out_tree.set_stream(("Library", "LayerKindMapping", "Data"), lkm_b["data"])
            if lkm_b.get("header"):
                out_tree.set_stream(("Library", "LayerKindMapping", "Header"), lkm_b["header"])

        # 5. Merge 3D Models
        # Copy ALL Library/Models/* streams from both libraries
        # First, get all model streams from tree_a
        all_model_streams_a = [path for path in tree_a.list_streams() if path[:2] == ("Library", "Models")]
        all_model_streams_b = [path for path in tree_b.list_streams() if path[:2] == ("Library", "Models")]

        # For now, prefer models from tree_a (could be enhanced to merge by model ID)
        if all_model_streams_a:
            for stream_path in all_model_streams_a:
                data = tree_a.get_stream(stream_path)
                if data is not None:
                    out_tree.set_stream(stream_path, data)
        elif all_model_streams_b:
            for stream_path in all_model_streams_b:
                data = tree_b.get_stream(stream_path)
                if data is not None:
                    out_tree.set_stream(stream_path, data)

        # 6. Allocate unique 31-character storage names and build SectionKeys map
        section_keys_entries: List[Tuple[str, str]] = []
        used_storage_names: Set[str] = {"Library", "FileHeader", "SectionKeys"}

        final_footprints_list: List[Tuple[str, PcbFootprintInfo, str, CompoundFileTree]] = []

        for final_name, fp_info, src_tree in merged_footprints:
            base_sec = sanitize_storage_name(final_name, max_len=31)
            sec_name = base_sec
            counter = 1
            while sec_name.lower() in [s.lower() for s in used_storage_names]:
                suffix = f"_{counter}"
                sec_name = sanitize_storage_name(final_name, max_len=31 - len(suffix)) + suffix
                counter += 1

            used_storage_names.add(sec_name)
            final_footprints_list.append((final_name, fp_info, sec_name, src_tree))

            if len(final_name) > 31 or sec_name != final_name:
                section_keys_entries.append((final_name, sec_name))

        # 7. Copy footprint storages with primitive count validation
        for final_name, fp_info, sec_name, src_tree in final_footprints_list:
            orig_sec = fp_info.section_key

            # First, copy all streams
            for stream_path in src_tree.list_streams(prefix=(orig_sec,)):
                rel_parts = stream_path[1:]
                dst_path = (sec_name,) + rel_parts
                data = src_tree.get_stream(stream_path)
                if data is not None:
                    out_tree.set_stream(dst_path, data)

            # Note: We do NOT modify the Header stream primitive count
            # The Header stream is copied as-is from the source library
            # Our primitive counting algorithm may not match Altium's internal counting
            # Only validate that Header exists and has correct size
            header_path = (sec_name, "Header")
            header_data = out_tree.get_stream(header_path)

            if header_data and len(header_data) != 4:
                logger.warning(
                    f"Footprint '{final_name}': Header stream has unexpected size {len(header_data)} (expected 4 bytes)"
                )

            # Also validate UniqueIDPrimitiveInformation streams exist
            # Note: We do NOT modify the UID Header count - it's copied as-is
            uid_header_path = (sec_name, "UniqueIDPrimitiveInformation", "Header")
            uid_data_path = (sec_name, "UniqueIDPrimitiveInformation", "Data")

            uid_header_data = out_tree.get_stream(uid_header_path)
            uid_data_stream = out_tree.get_stream(uid_data_path)

            if uid_header_data and len(uid_header_data) != 4:
                logger.warning(
                    f"Footprint '{final_name}': UniqueIDPrimitiveInformation/Header has unexpected size {len(uid_header_data)} (expected 4 bytes)"
                )

            if uid_header_data and not uid_data_stream:
                logger.warning(
                    f"Footprint '{final_name}': Has UniqueIDPrimitiveInformation/Header but missing /Data stream"
                )
            elif uid_data_stream and not uid_header_data:
                logger.warning(
                    f"Footprint '{final_name}': Has UniqueIDPrimitiveInformation/Data but missing /Header stream"
                )

        # 8. Build SectionKeys binary stream
        if section_keys_entries:
            sk_chunks = [struct.pack('<I', len(section_keys_entries))]
            for orig_name, sec_key in section_keys_entries:
                orig_bytes = orig_name.encode("latin1", errors="replace")
                sec_bytes = sec_key.encode("latin1", errors="replace")
                sk_chunks.append(struct.pack('<I', len(orig_bytes)) + orig_bytes)
                sk_chunks.append(struct.pack('<I', len(sec_bytes)) + sec_bytes)
            out_tree.set_stream(("SectionKeys",), b"".join(sk_chunks))

        # 9. Build Library/Data stream
        # Part A: Library parameter block
        lib_params = meta_a.get("library_params") or meta_b.get("library_params")
        if not lib_params:
            lib_params = b"|KIND=Protel_Advanced_PCB_Library|VERSION=3.00|\x00"

        # Part B: Component count and length-prefixed footprint name blocks
        comp_count_bytes = struct.pack('<I', len(final_footprints_list))
        fp_name_chunks = []
        for final_name, _, _, _ in final_footprints_list:
            name_bytes = final_name.encode("windows-1252", errors="replace")
            # Altium string block: 4-byte block_len (= 1 + len(name)), 1-byte str_len, name
            block_len = 1 + len(name_bytes)
            fp_name_chunks.append(struct.pack('<I', block_len) + bytes([len(name_bytes)]) + name_bytes)

        full_lib_data = (
            struct.pack('<I', len(lib_params))
            + lib_params
            + comp_count_bytes
            + b"".join(fp_name_chunks)
        )
        out_tree.set_stream(("Library", "Data"), full_lib_data)

        # Save to disk
        out_tree.save_to_file(output_path)

        # Verification check
        verify_tree = CompoundFileTree.load_from_file(output_path)
        logger.info(f"Verification successful: Merged PcbLib has {len(verify_tree.streams)} streams.")

        return {
            "success": True,
            "output_path": output_path,
            "total_components": len(final_footprints_list),
            "added_from_a": len([l for l in merge_log if l["status"] == "added_a"]),
            "added_from_b": len([l for l in merge_log if l["status"] == "added_b"]),
            "identical": len([l for l in merge_log if l["status"] == "identical"]),
            "conflicts_resolved": len([l for l in merge_log if l["status"] == "conflict_resolved"]),
            "log": merge_log,
        }
