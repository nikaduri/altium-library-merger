"""Compound File Binary (CFB / OLE) Handler
Provides reading, modification, and writing of Altium OLE compound files.
Uses pythoncom (Win32 Structured Storage) for 100% native Altium binary compatibility.
"""

import os
import io
import struct
import logging
from typing import Dict, List, Tuple, Optional, Any, Union

import olefile
try:
    import pythoncom
    from win32com.storagecon import STGM_CREATE, STGM_READWRITE, STGM_SHARE_EXCLUSIVE
    HAS_PYTHONCOM = True
except ImportError:
    HAS_PYTHONCOM = False

logger = logging.getLogger(__name__)

# Characters disallowed in OLE storage and stream names
ILLEGAL_OLE_CHARS = '<>:"/\\|?*\x00'


def sanitize_storage_name(name: str, max_len: int = 31) -> str:
    """Sanitize a name to comply with OLE storage naming rules:
    Max 31 characters, illegal characters replaced with underscore.
    """
    sanitized = "".join("_" if c in ILLEGAL_OLE_CHARS else c for c in name)
    return sanitized[:max_len] if len(sanitized) > max_len else sanitized


def normalize_path(path: Union[str, Tuple[str, ...], List[str]]) -> Tuple[str, ...]:
    """Normalize a stream/storage path into a tuple of string segments."""
    if isinstance(path, str):
        # Handle forward and backward slashes
        clean = path.replace("\\", "/").strip("/")
        return tuple(p for p in clean.split("/") if p) if clean else ()
    elif isinstance(path, (tuple, list)):
        return tuple(str(p).strip("/") for p in path if str(p).strip("/"))
    return ()


def parse_altium_params(raw_data: bytes, has_length_prefix: bool = True) -> Tuple[List[Tuple[str, str]], Dict[str, str]]:
    """Parse Altium pipe-delimited parameter collection block.
    
    Returns:
        ordered_pairs: List of (key, value) pairs in their original order.
        lookup: Case-insensitive dictionary {key.lower(): value}.
    """
    if has_length_prefix and len(raw_data) >= 4:
        block_len = struct.unpack('<I', raw_data[:4])[0]
        # In case raw_data has trailing bytes after block_len
        body = raw_data[4:4 + block_len]
    else:
        body = raw_data

    # Strip trailing null byte if present
    if body.endswith(b'\x00'):
        body = body[:-1]

    # Split by pipe '|'
    tokens = body.split(b'|')
    ordered_pairs: List[Tuple[str, str]] = []
    lookup: Dict[str, str] = {}

    for token in tokens:
        if not token:
            continue
        try:
            text = token.decode('utf-8')
        except UnicodeDecodeError:
            try:
                text = token.decode('windows-1252', errors='replace')
            except Exception:
                text = token.decode('latin1', errors='replace')

        if '=' in text:
            k, v = text.split('=', 1)
            ordered_pairs.append((k, v))
            lookup[k.lower()] = v
        else:
            ordered_pairs.append((text, ""))
            lookup[text.lower()] = ""

    return ordered_pairs, lookup


def build_altium_params(pairs: Union[List[Tuple[str, str]], Dict[str, Any]], has_length_prefix: bool = True) -> bytes:
    """Build Altium pipe-delimited parameter block with 4-byte little endian length prefix
    and null terminator, matching Altium Designer serialization.
    """
    parts = []
    if isinstance(pairs, dict):
        iterable = pairs.items()
    else:
        iterable = pairs

    for k, v in iterable:
        if v is None:
            continue
        if str(v) == "":
            parts.append(str(k))
        else:
            parts.append(f"{k}={v}")

    body_str = "|" + "|".join(parts) + "\x00"
    try:
        body_bytes = body_str.encode('windows-1252')
    except UnicodeEncodeError:
        body_bytes = body_str.encode('utf-8', errors='replace')

    if has_length_prefix:
        prefix = struct.pack('<I', len(body_bytes))
        return prefix + body_bytes
    return body_bytes


class CompoundFileTree:
    """Represents the in-memory hierarchical directory and stream storage
    of an Altium OLE compound file (.SchLib or .PcbLib).
    """

    def __init__(self):
        # Maps normalized path tuple -> bytes
        self.streams: Dict[Tuple[str, ...], bytes] = {}
        # Set of explicit storage directory tuples
        self.storages: set[Tuple[str, ...]] = set()

    @classmethod
    def load_from_file(cls, filepath: str) -> "CompoundFileTree":
        """Load an OLE Compound Document from disk into memory."""
        tree = cls()
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        ole = olefile.OleFileIO(filepath)
        try:
            # Extract all storages and streams
            for entry in ole.listdir(streams=True, storages=True):
                norm = tuple(entry)
                if ole.get_type(entry) == olefile.STGTY_STORAGE:
                    tree.storages.add(norm)
                elif ole.get_type(entry) == olefile.STGTY_STREAM:
                    stg_dir = norm[:-1]
                    if stg_dir:
                        tree.storages.add(stg_dir)
                    stream_obj = ole.openstream(entry)
                    tree.streams[norm] = stream_obj.read()
        finally:
            ole.close()

        logger.info(f"Loaded OLE tree from '{filepath}': {len(tree.streams)} streams, {len(tree.storages)} storages.")
        return tree

    @classmethod
    def load_from_bytes(cls, data: bytes) -> "CompoundFileTree":
        """Load an OLE Compound Document from bytes buffer."""
        bio = io.BytesIO(data)
        tree = cls()
        ole = olefile.OleFileIO(bio)
        try:
            for entry in ole.listdir(streams=True, storages=True):
                norm = tuple(entry)
                if ole.get_type(entry) == olefile.STGTY_STORAGE:
                    tree.storages.add(norm)
                elif ole.get_type(entry) == olefile.STGTY_STREAM:
                    stg_dir = norm[:-1]
                    if stg_dir:
                        tree.storages.add(stg_dir)
                    stream_obj = ole.openstream(entry)
                    tree.streams[norm] = stream_obj.read()
        finally:
            ole.close()
        return tree

    def has_stream(self, path: Union[str, Tuple[str, ...]]) -> bool:
        return normalize_path(path) in self.streams

    def get_stream(self, path: Union[str, Tuple[str, ...]], default: Optional[bytes] = None) -> Optional[bytes]:
        return self.streams.get(normalize_path(path), default)

    def set_stream(self, path: Union[str, Tuple[str, ...]], data: bytes) -> None:
        norm = normalize_path(path)
        self.streams[norm] = data
        # Ensure parent storage paths are recorded
        for i in range(1, len(norm)):
            self.storages.add(norm[:i])

    def delete_stream(self, path: Union[str, Tuple[str, ...]]) -> bool:
        norm = normalize_path(path)
        if norm in self.streams:
            del self.streams[norm]
            return True
        return False

    def list_streams(self, prefix: Optional[Union[str, Tuple[str, ...]]] = None) -> List[Tuple[str, ...]]:
        if not prefix:
            return sorted(list(self.streams.keys()))
        norm_prefix = normalize_path(prefix)
        return sorted([k for k in self.streams.keys() if len(k) >= len(norm_prefix) and k[:len(norm_prefix)] == norm_prefix])

    def list_substorages(self, prefix: Optional[Union[str, Tuple[str, ...]]] = None) -> List[str]:
        """List immediate child storage names under a given prefix."""
        norm_prefix = normalize_path(prefix) if prefix else ()
        prefix_len = len(norm_prefix)
        children = set()

        for s in self.storages:
            if len(s) == prefix_len + 1 and s[:prefix_len] == norm_prefix:
                children.add(s[-1])
        for st in self.streams:
            if len(st) > prefix_len + 1 and st[:prefix_len] == norm_prefix:
                children.add(st[prefix_len])

        return sorted(list(children))

    def copy_substorage(self, source_tree: "CompoundFileTree", src_prefix: Union[str, Tuple[str, ...]], dst_prefix: Union[str, Tuple[str, ...]]) -> int:
        """Copy all streams belonging to src_prefix from source_tree to dst_prefix in this tree."""
        src_norm = normalize_path(src_prefix)
        dst_norm = normalize_path(dst_prefix)
        copied = 0

        for path, data in source_tree.streams.items():
            if len(path) >= len(src_norm) and path[:len(src_norm)] == src_norm:
                relative = path[len(src_norm):]
                new_path = dst_norm + relative
                self.set_stream(new_path, data)
                copied += 1

        return copied

    def remove_substorage(self, prefix: Union[str, Tuple[str, ...]]) -> int:
        """Remove all streams and storages with the given prefix."""
        norm_prefix = normalize_path(prefix)
        to_del = [k for k in self.streams if len(k) >= len(norm_prefix) and k[:len(norm_prefix)] == norm_prefix]
        for k in to_del:
            del self.streams[k]

        stg_del = [s for s in self.storages if len(s) >= len(norm_prefix) and s[:len(norm_prefix)] == norm_prefix]
        for s in stg_del:
            self.storages.remove(s)

        return len(to_del)

    def save_to_file(self, output_path: str) -> None:
        """Serialize and write the compound document to disk using pythoncom."""
        if not HAS_PYTHONCOM:
            raise RuntimeError("pythoncom is required to write Altium compound files on Windows.")

        # Ensure directory exists
        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError as e:
                logger.error(f"Cannot overwrite existing file {output_path}: {e}")
                raise

        mode = STGM_CREATE | STGM_READWRITE | STGM_SHARE_EXCLUSIVE
        root_stg = pythoncom.StgCreateDocfile(output_path, mode)

        # Cache of opened PyIStorage objects keyed by path tuple
        storage_cache: Dict[Tuple[str, ...], Any] = {(): root_stg}

        def get_or_create_storage(stg_path: Tuple[str, ...]):
            if stg_path in storage_cache:
                return storage_cache[stg_path]
            # Recursively ensure parent
            parent_path = stg_path[:-1]
            parent_stg = get_or_create_storage(parent_path)
            child_name = stg_path[-1]
            child_stg = parent_stg.CreateStorage(child_name, mode, 0, 0)
            storage_cache[stg_path] = child_stg
            return child_stg

        try:
            # 1. Create all storages
            all_storages = sorted(list(self.storages), key=lambda x: (len(x), x))
            for stg_path in all_storages:
                get_or_create_storage(stg_path)

            # 2. Write all streams
            for stream_path, data in self.streams.items():
                stg_path = stream_path[:-1]
                stream_name = stream_path[-1]
                parent_stg = get_or_create_storage(stg_path)
                stm = parent_stg.CreateStream(stream_name, mode, 0, 0)
                if data:
                    stm.Write(data)
                del stm

            # Commit root storage
            root_stg.Commit(0)
        finally:
            # Release all COM pointers
            storage_cache.clear()
            del root_stg

        logger.info(f"Successfully saved {len(self.streams)} streams to '{output_path}'.")
