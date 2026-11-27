"""Altium Binary & Sidecar Stream Inspector
Decodes Altium .SchLib and .PcbLib binary records and compressed sidecar streams
into human-readable semantic diffs without relying on raw byte offsets alone.
"""

from collections import Counter
import struct
import zlib
from typing import Any, Dict, List, Optional, Tuple

from .cfb_handler import parse_altium_params

PIN_ELECTRICAL_TYPES: Dict[int, str] = {
    0: "Input",
    1: "I/O",
    2: "Output",
    3: "OpenCollector",
    4: "Passive",
    5: "HiZ",
    6: "OpenEmitter",
    7: "Power",
}

SCH_RECORD_NAMES: Dict[int, str] = {
    1: "Component Header",
    2: "Pin",
    3: "IEEE Symbol",
    4: "Text Label",
    5: "Bezier",
    6: "Polyline",
    7: "Polygon",
    8: "Ellipse",
    10: "Round Rectangle",
    11: "Elliptical Arc",
    12: "Arc",
    13: "Line",
    14: "Rectangle",
    15: "Sheet Symbol",
    17: "Power Object",
    25: "Net Label",
    27: "Wire",
    28: "Text Frame",
    30: "Image",
    34: "Designator",
    41: "Parameter",
    44: "Implementation List",
    45: "Model",
    46: "Model Datafile List",
    47: "Model Pin Map",
    48: "Model Parameters",
}

PCB_PAD_SHAPES: Dict[int, str] = {
    0: "Unknown",
    1: "Round",
    2: "Rectangular",
    3: "Octagonal",
    9: "RoundedRect",
}

PCB_HOLE_SHAPES: Dict[int, str] = {
    -1: "None",
    0: "Round",
    1: "Square",
    2: "Slot",
}

PCB_RECORD_NAMES: Dict[int, str] = {
    1: "Arc",
    2: "Pad",
    3: "Via",
    4: "Track",
    5: "String",
    6: "Fill",
    11: "Region",
    12: "3D Body",
}

PCB_LAYER_NAMES: Dict[int, str] = {
    1: "Top Layer",
    32: "Bottom Layer",
    33: "Top Overlay",
    34: "Bottom Overlay",
    35: "Top Paste",
    36: "Bottom Paste",
    37: "Top Solder",
    38: "Bottom Solder",
    55: "Drill Guide",
    56: "Keep-Out",
    **{56 + i: f"Mechanical {i}" for i in range(1, 17)},
    73: "Drill Drawing",
    74: "Multi-Layer",
    **{66 + i: f"Mechanical {i}" for i in range(17, 33)},
}

# Transient bookkeeping keys in SchLib text records that shift automatically when items are added/removed
_SCH_IGNORED_KEYS = {"indexinsheet", "ownerindex", "ownerpartdisplaymode"}


def _fmt_num(val: float, precision: int = 2) -> str:
    """Format a floating point number cleanly without trailing zeros."""
    rounded = round(val, precision)
    if rounded == 0:
        return "0"
    if abs(rounded - round(rounded)) < 1e-6:
        return str(int(round(rounded)))
    return f"{rounded:.{precision}f}".rstrip("0").rstrip(".")


def _fmt_mil(raw_val: int) -> str:
    """Format a raw Altium PCB coordinate (10,000 units = 1 mil)."""
    return f"{_fmt_num(raw_val / 10000.0)}mil"


def _fmt_pt_mil(x_raw: int, y_raw: int) -> str:
    """Format a 2D Altium PCB point in mils."""
    return f"({_fmt_num(x_raw / 10000.0)}, {_fmt_num(y_raw / 10000.0)})mil"


def _read_pascal_str(buf: bytes, offset: int) -> Tuple[str, int]:
    """Read a 1-byte length-prefixed Windows-1252 string from buf."""
    if offset >= len(buf):
        return "", offset
    slen = buf[offset]
    offset += 1
    end = min(offset + slen, len(buf))
    text = buf[offset:end].decode("windows-1252", errors="replace")
    return text, end


def _resolve_font_label(font_id: Optional[int], meta: Optional[Dict[str, Any]]) -> str:
    """Resolve a 1-based Altium FontID using FileHeader font metadata."""
    if font_id is None:
        return "(Default)"
    fonts = (meta or {}).get("fonts") or []
    for f in fonts:
        if f.get("id") == font_id:
            styles = []
            if f.get("bold") == "T":
                styles.append("Bold")
            if f.get("italic") == "T":
                styles.append("Italic")
            if f.get("underline") == "T":
                styles.append("Underline")
            if f.get("rotation") and f.get("rotation") != "0":
                styles.append(f"Rot {f['rotation']}")
            suffix = f" {' '.join(styles)}" if styles else ""
            return f"Font #{font_id} ({f.get('name', '?')} {f.get('size', '?')}pt{suffix})"
    return f"Font #{font_id}"


# =============================================================================
# Schematic Library (.SchLib) Binary & Sidecar Parsers
# =============================================================================

def parse_sch_binary_pin(rec_body: bytes) -> Dict[str, Any]:
    """Decode an Altium SchLib RecordType=1 binary pin record accurately."""
    if len(rec_body) < 26:
        return {"record": 2, "designator": "", "name": "", "electrical": "Passive", "raw_hex": rec_body.hex()}

    rec_id = struct.unpack_from("<I", rec_body, 0)[0]
    owner_part_id = struct.unpack_from("<h", rec_body, 5)[0]
    owner_display_mode = rec_body[7]
    inner_edge, outer_edge, inside, outside = rec_body[8], rec_body[9], rec_body[10], rec_body[11]

    desc, off = _read_pascal_str(rec_body, 12)
    formal_type = rec_body[off] if off < len(rec_body) else 0
    elec_code = rec_body[off + 1] if off + 1 < len(rec_body) else 4
    flags = rec_body[off + 2] if off + 2 < len(rec_body) else 0
    pin_len = struct.unpack_from("<H", rec_body, off + 3)[0] if off + 5 <= len(rec_body) else 0
    loc_x = struct.unpack_from("<h", rec_body, off + 5)[0] if off + 7 <= len(rec_body) else 0
    loc_y = struct.unpack_from("<h", rec_body, off + 7)[0] if off + 9 <= len(rec_body) else 0
    color = struct.unpack_from("<I", rec_body, off + 9)[0] if off + 13 <= len(rec_body) else 0

    str_off = off + 13
    name, str_off = _read_pascal_str(rec_body, str_off)
    designator, str_off = _read_pascal_str(rec_body, str_off)
    swap_group, str_off = _read_pascal_str(rec_body, str_off)
    swap_part, str_off = _read_pascal_str(rec_body, str_off)
    default_val, str_off = _read_pascal_str(rec_body, str_off)

    return {
        "record": rec_id,
        "owner_part_id": owner_part_id,
        "owner_display_mode": owner_display_mode,
        "formal_type": formal_type,
        "symbols": (inner_edge, outer_edge, inside, outside),
        "description": desc,
        "electrical": PIN_ELECTRICAL_TYPES.get(elec_code, f"Type({elec_code})"),
        "flags": flags,
        "rotated": bool(flags & 0x01),
        "flipped": bool(flags & 0x02),
        "hidden": bool(flags & 0x04),
        "show_name": bool(flags & 0x08),
        "show_designator": bool(flags & 0x10),
        "locked": bool(flags & 0x40),
        "length": pin_len,
        "x": loc_x,
        "y": loc_y,
        "color": color,
        "name": name,
        "designator": designator,
        "swap_group": swap_group,
        "swap_part": swap_part,
        "default_value": default_val,
    }


def _describe_sch_graphic(rec_id: int, lookup: Dict[str, str], font_meta: Optional[Dict[str, Any]]) -> str:
    """Create a concise human-readable description of a schematic graphic/primitive."""
    rname = SCH_RECORD_NAMES.get(rec_id, f"Record {rec_id}")
    if rec_id == 6 or rec_id == 7:  # Polyline / Polygon
        cnt = int(lookup.get("locationcount", 0))
        pts = [f"({lookup.get(f'x{i}', '0')},{lookup.get(f'y{i}', '0')})" for i in range(1, min(cnt, 4) + 1)]
        if cnt > 4:
            pts.append(f"...+{cnt - 4}")
        return f"{rname} [{cnt} pts: {' '.join(pts)}]"
    if rec_id in (13, 14, 10):  # Line, Rectangle, RoundRect
        p1 = f"({lookup.get('location.x', '0')},{lookup.get('location.y', '0')})"
        p2 = f"({lookup.get('corner.x', '0')},{lookup.get('corner.y', '0')})"
        return f"{rname} {p1}..{p2}"
    if rec_id in (8, 12, 11):  # Ellipse, Arc, EllipticalArc
        p = f"({lookup.get('location.x', '0')},{lookup.get('location.y', '0')})"
        rad = lookup.get("radius", "0")
        return f"{rname} @{p} r={rad}"
    if rec_id == 4:  # Label
        txt = lookup.get("text", "")
        fid = lookup.get("fontid")
        fstr = f" [{_resolve_font_label(int(fid), font_meta)}]" if fid and fid.isdigit() else ""
        return f"{rname} '{txt}'{fstr}"
    if rec_id == 30:  # Image
        fname = lookup.get("filename", "")
        return f"Image '{fname}'"
    if rec_id == 3:  # IEEE Symbol
        return f"IEEE Symbol #{lookup.get('symbol', '?')} @({lookup.get('location.x', '0')},{lookup.get('location.y', '0')})"
    return f"{rname} ({len(lookup)} attrs)"


def parse_sch_data_records(data: bytes, font_meta: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Parse every record inside a SchLib component 'Data' stream."""
    records: List[Dict[str, Any]] = []
    offset = 0
    idx = 0

    while offset + 4 <= len(data):
        rec_len = int.from_bytes(data[offset:offset + 2], "little")
        rec_type = int.from_bytes(data[offset + 2:offset + 4], "big")
        offset += 4

        if rec_len == 0:
            break
        rec_body = data[offset:offset + rec_len]
        offset += rec_len

        if rec_type == 1:
            pin = parse_sch_binary_pin(rec_body)
            records.append({
                "index": idx,
                "rec_type": 1,
                "rec_id": 2,
                "category": "pin",
                "raw_size": rec_len,
                "data": pin,
            })
        elif rec_type == 0:
            pairs, lookup = parse_altium_params(rec_body, has_length_prefix=False)
            rec_id = int(lookup.get("record", "0") or 0)
            if rec_id == 2:
                category = "pin"
                parsed = {
                    "record": 2,
                    "owner_part_id": int(lookup.get("ownerpartid", "1") or 1),
                    "description": lookup.get("description", ""),
                    "electrical": PIN_ELECTRICAL_TYPES.get(int(lookup.get("electrical", "4") or 4), lookup.get("electrical", "4")),
                    "hidden": lookup.get("ishidden") == "T",
                    "show_name": lookup.get("showname", "T") != "F",
                    "show_designator": lookup.get("showdesignator", "T") != "F",
                    "length": int(lookup.get("pinlength", lookup.get("length", "0")) or 0),
                    "x": int(lookup.get("location.x", "0") or 0),
                    "y": int(lookup.get("location.y", "0") or 0),
                    "name": lookup.get("name", ""),
                    "designator": lookup.get("designator", ""),
                    "rotated": False,
                    "flipped": False,
                }
            elif rec_id == 1:
                category = "component"
                parsed = lookup
            elif rec_id == 34:
                category = "designator"
                parsed = lookup
            elif rec_id == 41:
                category = "parameter"
                parsed = lookup
            elif rec_id == 45:
                category = "model"
                parsed = lookup
            elif rec_id in (44, 46, 47, 48):
                category = "model_aux"
                parsed = lookup
            else:
                category = "graphic"
                parsed = {
                    "rec_id": rec_id,
                    "summary": _describe_sch_graphic(rec_id, lookup, font_meta),
                    "attrs": {k: v for k, v in lookup.items() if k not in _SCH_IGNORED_KEYS and k != "uniqueid"},
                    "full": lookup,
                }

            records.append({
                "index": idx,
                "rec_type": 0,
                "rec_id": rec_id,
                "category": category,
                "raw_size": rec_len,
                "data": parsed,
            })
        else:
            records.append({
                "index": idx,
                "rec_type": rec_type,
                "rec_id": -1,
                "category": "unknown",
                "raw_size": rec_len,
                "data": {"hex": rec_body.hex()},
            })
        idx += 1

    return records


def _sch_pin_signature(pin: Dict[str, Any]) -> Tuple[Any, ...]:
    """Normalized comparable tuple of meaningful SchPin attributes."""
    orient = []
    if pin.get("rotated"):
        orient.append("Rotated")
    if pin.get("flipped"):
        orient.append("Flipped")
    return (
        pin.get("name", ""),
        pin.get("electrical", ""),
        pin.get("x", 0),
        pin.get("y", 0),
        pin.get("length", 0),
        ",".join(orient) if orient else "Normal",
        pin.get("hidden", False),
        pin.get("show_name", True),
        pin.get("show_designator", True),
        pin.get("description", ""),
        pin.get("owner_part_id", 1),
    )


def analyze_sch_data_diff(
    data_a: bytes,
    data_b: bytes,
    meta_a: Optional[Dict[str, Any]] = None,
    meta_b: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Decode and semantically compare two SchLib component 'Data' streams.

    Returns:
        (stream_summary_details, additional_itemized_diffs)
    """
    recs_a = parse_sch_data_records(data_a, meta_a)
    recs_b = parse_sch_data_records(data_b, meta_b)

    bullets: List[str] = []
    extra_diffs: List[Dict[str, Any]] = []

    # 1. Compare Parameters (styles, positions, visibility, fonts; name/text diffs already shown)
    params_a = {r["data"].get("name", ""): r["data"] for r in recs_a if r["category"] == "parameter" and r["data"].get("name")}
    params_b = {r["data"].get("name", ""): r["data"] for r in recs_b if r["category"] == "parameter" and r["data"].get("name")}
    for pname in sorted(set(params_a.keys()) | set(params_b.keys())):
        if pname in params_a and pname not in params_b:
            txt = params_a[pname].get("text", "")
            bullets.append(f"Removed Parameter '{pname}' (was '{txt}')")
        elif pname in params_b and pname not in params_a:
            txt = params_b[pname].get("text", "")
            bullets.append(f"Added Parameter '{pname}' ('{txt}')")
        else:
            pa, pb = params_a[pname], params_b[pname]
            style_changes = []
            fa_id = int(pa["fontid"]) if str(pa.get("fontid", "")).isdigit() else None
            fb_id = int(pb["fontid"]) if str(pb.get("fontid", "")).isdigit() else None
            if fa_id != fb_id:
                f_str = f"{_resolve_font_label(fa_id, meta_a)} → {_resolve_font_label(fb_id, meta_b)}"
                style_changes.append(f_str)
                extra_diffs.append({
                    "property": f"Data → Param Style ({pname})",
                    "val_a": _resolve_font_label(fa_id, meta_a),
                    "val_b": _resolve_font_label(fb_id, meta_b),
                    "type": "stream_detail",
                    "details": "Font ID changed inside component Data stream",
                })
            if (pa.get("location.x"), pa.get("location.y")) != (pb.get("location.x"), pb.get("location.y")):
                xy_a = f"({pa.get('location.x', '0')},{pa.get('location.y', '0')})"
                xy_b = f"({pb.get('location.x', '0')},{pb.get('location.y', '0')})"
                style_changes.append(f"Pos {xy_a} → {xy_b}")
                extra_diffs.append({
                    "property": f"Data → Param Pos ({pname})",
                    "val_a": xy_a,
                    "val_b": xy_b,
                    "type": "stream_detail",
                    "details": "Parameter label coordinates moved",
                })
            if pa.get("ishidden", "F") != pb.get("ishidden", "F"):
                hid_a = "Hidden" if pa.get("ishidden") == "T" else "Visible"
                hid_b = "Hidden" if pb.get("ishidden") == "T" else "Visible"
                style_changes.append(f"Visibility {hid_a} → {hid_b}")
                extra_diffs.append({
                    "property": f"Data → Param Visibility ({pname})",
                    "val_a": hid_a,
                    "val_b": hid_b,
                    "type": "stream_detail",
                })
            if style_changes:
                bullets.append(f"Parameter '{pname}': {', '.join(style_changes)}")

    # 2. Compare Pins
    pins_a = [r["data"] for r in recs_a if r["category"] == "pin"]
    pins_b = [r["data"] for r in recs_b if r["category"] == "pin"]
    pins_a_map = {(p.get("owner_part_id", 1), p.get("designator", str(i))): p for i, p in enumerate(pins_a)}
    pins_b_map = {(p.get("owner_part_id", 1), p.get("designator", str(i))): p for i, p in enumerate(pins_b)}

    pin_field_labels = ("Name", "Electrical", "X", "Y", "Length", "Orientation", "Hidden", "ShowName", "ShowDes", "Description", "Part")
    pin_diffs_count = 0
    for pk in sorted(set(pins_a_map.keys()) | set(pins_b_map.keys()), key=lambda x: (x[0], x[1])):
        part_id, des = pk
        if pk in pins_a_map and pk not in pins_b_map:
            p = pins_a_map[pk]
            pin_diffs_count += 1
            msg = f"Removed Pin '{des}' ('{p.get('name', '')}', {p.get('electrical')}, @{p.get('x')},{p.get('y')})"
            bullets.append(msg)
            extra_diffs.append({
                "property": f"Data → Pin {des}",
                "val_a": f"Name='{p.get('name', '')}', {p.get('electrical')}, len={p.get('length')}, @({p.get('x')},{p.get('y')})",
                "val_b": "(Removed in B)",
                "type": "stream_detail",
            })
        elif pk in pins_b_map and pk not in pins_a_map:
            p = pins_b_map[pk]
            pin_diffs_count += 1
            msg = f"Added Pin '{des}' ('{p.get('name', '')}', {p.get('electrical')}, @{p.get('x')},{p.get('y')})"
            bullets.append(msg)
            extra_diffs.append({
                "property": f"Data → Pin {des}",
                "val_a": "(Not in A)",
                "val_b": f"Name='{p.get('name', '')}', {p.get('electrical')}, len={p.get('length')}, @({p.get('x')},{p.get('y')})",
                "type": "stream_detail",
            })
        else:
            sa = _sch_pin_signature(pins_a_map[pk])
            sb = _sch_pin_signature(pins_b_map[pk])
            if sa != sb:
                pin_diffs_count += 1
                deltas_a, deltas_b = [], []
                for lbl, va, vb in zip(pin_field_labels, sa, sb):
                    if va != vb:
                        deltas_a.append(f"{lbl}={va}")
                        deltas_b.append(f"{lbl}={vb}")
                bullets.append(f"Modified Pin '{des}': {'; '.join(deltas_a)} → {'; '.join(deltas_b)}")
                extra_diffs.append({
                    "property": f"Data → Pin {des}",
                    "val_a": ", ".join(deltas_a),
                    "val_b": ", ".join(deltas_b),
                    "type": "stream_detail",
                })

    # 3. Compare Graphics Primitives (order & IndexInSheet normalized)
    gfx_a = [r["data"] for r in recs_a if r["category"] == "graphic"]
    gfx_b = [r["data"] for r in recs_b if r["category"] == "graphic"]

    def gfx_key(g: Dict[str, Any]) -> Tuple[Any, ...]:
        return (g["rec_id"], tuple(sorted(g["attrs"].items())))

    cnt_gfx_a = Counter(gfx_key(g) for g in gfx_a)
    cnt_gfx_b = Counter(gfx_key(g) for g in gfx_b)
    sum_by_key_a = {gfx_key(g): g["summary"] for g in gfx_a}
    sum_by_key_b = {gfx_key(g): g["summary"] for g in gfx_b}

    removed_gfx = list((cnt_gfx_a - cnt_gfx_b).elements())
    added_gfx = list((cnt_gfx_b - cnt_gfx_a).elements())
    if removed_gfx or added_gfx:
        rem_strs = [sum_by_key_a[k] for k in removed_gfx[:5]]
        add_strs = [sum_by_key_b[k] for k in added_gfx[:5]]
        if removed_gfx:
            bullets.append(f"Removed {len(removed_gfx)} graphic(s): {', '.join(rem_strs)}")
        if added_gfx:
            bullets.append(f"Added {len(added_gfx)} graphic(s): {', '.join(add_strs)}")
        extra_diffs.append({
            "property": "Data → Symbol Graphics",
            "val_a": f"-{len(removed_gfx)}: {'; '.join(rem_strs)}" if removed_gfx else "(Unchanged)",
            "val_b": f"+{len(added_gfx)}: {'; '.join(add_strs)}" if added_gfx else "(Removed)",
            "type": "stream_detail",
            "details": "Geometric primitives compared independent of sheet index",
        })

    # 4. Compare Models / Footprint Links (RECORD=45)
    models_a = {(m["data"].get("modeltype", ""), m["data"].get("modelname", "")): m["data"] for m in recs_a if m["category"] == "model"}
    models_b = {(m["data"].get("modeltype", ""), m["data"].get("modelname", "")): m["data"] for m in recs_b if m["category"] == "model"}
    for mk in sorted(set(models_a.keys()) | set(models_b.keys())):
        mtype, mname = mk
        if mk in models_a and mk not in models_b:
            bullets.append(f"Removed {mtype} Model '{mname}'")
            extra_diffs.append({
                "property": f"Data → Model ({mtype})",
                "val_a": mname,
                "val_b": "(Removed in B)",
                "type": "stream_detail",
                "details": models_a[mk].get("description", ""),
            })
        elif mk in models_b and mk not in models_a:
            bullets.append(f"Added {mtype} Model '{mname}'")
            extra_diffs.append({
                "property": f"Data → Model ({mtype})",
                "val_a": "(Not in A)",
                "val_b": mname,
                "type": "stream_detail",
                "details": models_b[mk].get("description", ""),
            })
        else:
            ma, mb = models_a[mk], models_b[mk]
            norm_ma = {k: v for k, v in ma.items() if k not in _SCH_IGNORED_KEYS and k != "uniqueid"}
            norm_mb = {k: v for k, v in mb.items() if k not in _SCH_IGNORED_KEYS and k != "uniqueid"}
            if norm_ma != norm_mb:
                changed_keys = [k for k in sorted(set(norm_ma) | set(norm_mb)) if norm_ma.get(k) != norm_mb.get(k)]
                bullets.append(f"Modified {mtype} Model '{mname}': {', '.join(changed_keys)}")
                extra_diffs.append({
                    "property": f"Data → Model ({mname})",
                    "val_a": "; ".join(f"{k}={norm_ma.get(k, '')}" for k in changed_keys[:3]),
                    "val_b": "; ".join(f"{k}={norm_mb.get(k, '')}" for k in changed_keys[:3]),
                    "type": "stream_detail",
                })

    # 5. Compare Component Header (RECORD=1) & Designator (RECORD=34) extras
    comp_a = next((r["data"] for r in recs_a if r["category"] == "component"), {})
    comp_b = next((r["data"] for r in recs_b if r["category"] == "component"), {})
    comp_ignore = _SCH_IGNORED_KEYS | {"uniqueid", "libreference", "componentdescription", "%utf8%componentdescription"}
    comp_diffs = [
        k for k in sorted(set(comp_a.keys()) | set(comp_b.keys()))
        if k not in comp_ignore and comp_a.get(k) != comp_b.get(k)
    ]
    if comp_diffs:
        bullets.append(f"Component Header attrs changed: {', '.join(comp_diffs)}")
        extra_diffs.append({
            "property": "Data → Component Header",
            "val_a": "; ".join(f"{k}={comp_a.get(k, '')}" for k in comp_diffs[:4]),
            "val_b": "; ".join(f"{k}={comp_b.get(k, '')}" for k in comp_diffs[:4]),
            "type": "stream_detail",
        })

    des_a = next((r["data"] for r in recs_a if r["category"] == "designator"), {})
    des_b = next((r["data"] for r in recs_b if r["category"] == "designator"), {})
    if des_a and des_b:
        des_diffs = [
            k for k in sorted(set(des_a.keys()) | set(des_b.keys()))
            if k not in (_SCH_IGNORED_KEYS | {"uniqueid", "text"}) and des_a.get(k) != des_b.get(k)
        ]
        if des_diffs:
            bullets.append(f"Designator formatting changed: {', '.join(des_diffs)}")

    if not bullets:
        size_delta = len(data_b) - len(data_a)
        bullets.append(
            f"All {len(pins_a)} pins, {len(gfx_a)} graphic shapes, and {len(models_a)} model link(s) are identical; "
            f"only metadata/UniqueIDs or record order changed ({size_delta:+d} bytes)"
        )
    elif pin_diffs_count == 0 and not removed_gfx and not added_gfx:
        bullets.append(
            f"Verified identical: {len(pins_a)} pins & {len(gfx_a)} graphic shapes (only sheet indices shifted)"
        )

    return " | ".join(bullets), extra_diffs


# =============================================================================
# Schematic Pin Sidecar Streams (PinTextData, PinFrac, PinWideText, ...)
# =============================================================================

def _decode_pintext_subblock(buf: bytes, offset: int, font_meta: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], int]:
    """Decode one 1..11 byte Name or Designator block from a decompressed PinTextData payload."""
    if offset >= len(buf):
        return {"custom_pos": False, "custom_font": False, "text": "Default"}, offset

    flags = buf[offset]
    offset += 1
    result: Dict[str, Any] = {
        "flags": flags,
        "custom_pos": bool(flags & 0x01),
        "custom_font": bool(flags & 0x10),
    }

    if flags & 0x01:
        if offset + 4 <= len(buf):
            pos_val = struct.unpack_from("<i", buf, offset)[0]
            result["pos_data"] = pos_val
            offset += 4
        else:
            result["trailing_hex"] = buf[offset:].hex()
            offset = len(buf)

    if flags & 0x10:
        if offset + 6 <= len(buf):
            font_id, color = struct.unpack_from("<HI", buf, offset)
            result["font_id"] = font_id
            result["color"] = color
            result["font_label"] = _resolve_font_label(font_id, font_meta)
            offset += 6
        else:
            result["trailing_hex"] = buf[offset:].hex()
            offset = len(buf)

    if flags & ~(0x01 | 0x10):
        # Preserve any unhandled custom bits explicitly
        result["extra_flags"] = f"0x{flags:02X}"
        if offset < len(buf) and not result.get("custom_font"):
            result["raw_hex"] = buf[offset:].hex()

    parts = []
    if result.get("custom_font"):
        parts.append(result.get("font_label", f"Font #{result.get('font_id')}"))
        if result.get("color"):
            parts.append(f"color=0x{result['color']:06X}")
    if result.get("custom_pos"):
        parts.append(f"customPos/margin={result.get('pos_data')}")
    if not parts:
        parts.append("Default" if flags == 0 else f"flags=0x{flags:02X}")

    result["summary"] = ", ".join(parts)
    return result, offset


def parse_sch_pin_sidecar(
    stream_name: str,
    data: bytes,
    font_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Parse compressed 0xD0 sidecar stream (PinTextData, PinFrac, PinWideText, etc.)."""
    if len(data) < 4:
        return {"header": "", "entries": {}}

    hlen = struct.unpack_from("<I", data, 0)[0]
    header_raw = data[4:4 + hlen]
    _, header_lookup = parse_altium_params(header_raw, has_length_prefix=False)
    off = 4 + hlen
    entries: Dict[str, Dict[str, Any]] = {}

    while off + 4 <= len(data):
        rec_len = int.from_bytes(data[off:off + 2], "little")
        off += 4
        if rec_len == 0 or off + rec_len > len(data):
            break
        body = data[off:off + rec_len]
        off += rec_len

        if len(body) >= 6 and body[0] == 0xD0:
            slen = body[1]
            pin_key = body[2:2 + slen].decode("ascii", errors="replace")
            if 6 + slen > len(body):
                entries[pin_key] = {"summary": f"raw({body.hex()})"}
                continue
            zlen = struct.unpack_from("<I", body, 2 + slen)[0]
            zdata = body[6 + slen:6 + slen + zlen]
            try:
                dec = zlib.decompress(zdata)
            except Exception:
                entries[pin_key] = {"summary": f"zlib_err({zdata.hex()[:24]})"}
                continue

            if stream_name == "PinTextData":
                name_cfg, ptr = _decode_pintext_subblock(dec, 0, font_meta)
                desig_cfg, ptr = _decode_pintext_subblock(dec, ptr, font_meta)
                entries[pin_key] = {
                    "name": name_cfg,
                    "designator": desig_cfg,
                    "raw_hex": dec.hex(),
                    "summary": f"Name=[{name_cfg['summary']}], Des=[{desig_cfg['summary']}]",
                }
            elif stream_name == "PinFrac" and len(dec) >= 12:
                xf, yf, lf = struct.unpack_from("<iii", dec, 0)
                entries[pin_key] = {
                    "x_frac": xf,
                    "y_frac": yf,
                    "len_frac": lf,
                    "summary": f"frac(dx={xf/100000:+.5f}, dy={yf/100000:+.5f}, dlen={lf/100000:+.5f})",
                }
            else:
                # PinPackageLength, PinSymbolLineWidth, PinWideText, PinFunctionData
                if len(dec) >= 4:
                    plen = struct.unpack_from("<I", dec, 0)[0]
                    payload = dec[4:4 + plen]
                    try:
                        text = payload.decode("utf-16le").rstrip("\x00")
                    except Exception:
                        text = payload.decode("windows-1252", errors="replace").rstrip("\x00")
                    _, parsed_kv = parse_altium_params(text.encode("utf-8"), has_length_prefix=False)
                    entries[pin_key] = {"params": parsed_kv, "raw_text": text, "summary": text or "(empty)"}
                else:
                    entries[pin_key] = {"summary": dec.hex()}
        else:
            entries[f"raw_{off}"] = {"summary": body.hex()}

    return {
        "header": header_lookup,
        "weight": header_lookup.get("weight", str(len(entries))),
        "entries": entries,
    }


def analyze_sch_pin_sidecar_diff(
    stream_name: str,
    data_a: bytes,
    data_b: bytes,
    meta_a: Optional[Dict[str, Any]] = None,
    meta_b: Optional[Dict[str, Any]] = None,
    pins_a: Optional[List[Dict[str, Any]]] = None,
    pins_b: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Compare decoded zlib sidecar streams (PinTextData, PinFrac, etc.) per pin."""
    pa = parse_sch_pin_sidecar(stream_name, data_a, meta_a)
    pb = parse_sch_pin_sidecar(stream_name, data_b, meta_b)
    ea, eb = pa["entries"], pb["entries"]

    def _pin_label(key: str) -> str:
        if key.isdigit():
            idx = int(key)
            for p_list in (pins_a or [], pins_b or []):
                if 0 <= idx < len(p_list):
                    des = p_list[idx].get("designator", "")
                    if des:
                        return f"#{idx} (Pin {des})"
            return f"#{idx}"
        return key

    all_keys = sorted(set(ea.keys()) | set(eb.keys()), key=lambda x: (0, int(x)) if x.isdigit() else (1, x))

    # Group pins that share identical A -> B transitions to keep UI concise on 100-pin ICs
    grouped: Dict[Tuple[str, str], List[str]] = {}
    for pk in all_keys:
        va = ea.get(pk, {}).get("summary", "(Not present)")
        vb = eb.get(pk, {}).get("summary", "(Not present)")
        if va != vb:
            grouped.setdefault((va, vb), []).append(_pin_label(pk))

    if not grouped:
        return f"Weight {pa['weight']} → {pb['weight']} (decoded payloads match; compression headers differ)", []

    summary_parts: List[str] = []
    extra_diffs: List[Dict[str, Any]] = []
    for (va, vb), pin_labels in grouped.items():
        if len(pin_labels) > 3:
            scope = f"{len(pin_labels)} pins ({pin_labels[0]}..{pin_labels[-1]})"
        else:
            scope = f"Pin(s) {', '.join(pin_labels)}"
        summary_parts.append(f"{scope}: {va} → {vb}")
        extra_diffs.append({
            "property": f"{stream_name} → {scope}",
            "val_a": va,
            "val_b": vb,
            "type": "stream_detail",
        })

    return " | ".join(summary_parts), extra_diffs[:10]


# =============================================================================
# PCB Footprint Library (.PcbLib) Binary & Sidecar Parsers
# =============================================================================

def extract_pcb_layer_names(meta: Optional[Dict[str, Any]]) -> Dict[int, str]:
    """Merge default Altium PCB layer IDs with any custom layer names from metadata."""
    layers = dict(PCB_LAYER_NAMES)
    raw_params = (meta or {}).get("library_params")
    if isinstance(raw_params, bytes) and raw_params:
        _, lookup = parse_altium_params(raw_params, has_length_prefix=False)
        for i in range(1, 100):
            name = lookup.get(f"layer{i}name")
            lid = lookup.get(f"layer{i}id")
            if name and lid and lid.isdigit():
                layers[int(lid)] = name
    return layers


def _read_sized_block(data: bytes, offset: int) -> Tuple[bytes, int]:
    """Read a uint32-LE length-prefixed block."""
    if offset + 4 > len(data):
        return b"", len(data)
    blen = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    end = min(offset + blen, len(data))
    return data[offset:end], end


def _resolve_extended_layer(base_layer: int, trailing: bytes) -> int:
    """Resolve Mechanical 17-32 encoded after \\x00\\x02\\x01 when base_layer == 72."""
    if base_layer != 72:
        return base_layer
    idx = trailing.find(b"\x00\x02\x01")
    if idx > 0:
        n = trailing[idx - 1]
        if 17 <= n <= 32:
            return 66 + n
    return base_layer


def parse_pcb_data_records(
    data: bytes,
    meta: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Decode every geometric primitive in a PcbLib footprint 'Data' stream."""
    if len(data) < 4:
        return []

    layer_names = extract_pcb_layer_names(meta)
    model_names = {m.get("id", ""): m.get("name", "") for m in (meta or {}).get("models", [])}

    def layer_label(lid: int) -> str:
        return layer_names.get(lid, f"Layer {lid}")

    # Skip initial footprint name string block
    _, offset = _read_sized_block(data, 0)
    records: List[Dict[str, Any]] = []

    while offset < len(data):
        rid = data[offset]
        offset += 1
        if rid == 0:
            break

        if rid == 2:  # PcbPad: 6 sized blocks
            b0, offset = _read_sized_block(data, offset)  # designator
            b1, offset = _read_sized_block(data, offset)
            b2, offset = _read_sized_block(data, offset)  # |&|0
            b3, offset = _read_sized_block(data, offset)
            b4, offset = _read_sized_block(data, offset)  # core pad geometry
            b5, offset = _read_sized_block(data, offset)  # stack / hole / rounded rect

            des, _ = _read_pascal_str(b0, 0)
            attrs: Dict[str, Any] = {"designator": des}
            if len(b4) >= 62:
                lid = b4[0]
                loc_x, loc_y = struct.unpack_from("<ii", b4, 13)
                sx_top, sy_top = struct.unpack_from("<ii", b4, 21)
                hole_sz = struct.unpack_from("<i", b4, 45)[0]
                sh_top, sh_mid, sh_bot = b4[49], b4[50], b4[51]
                rot = struct.unpack_from("<d", b4, 52)[0]
                plated = bool(b4[60])
                stack_mode = b4[62] if len(b4) > 62 else 0
                attrs.update({
                    "layer": layer_label(lid),
                    "location": _fmt_pt_mil(loc_x, loc_y),
                    "size": f"{_fmt_mil(sx_top)} x {_fmt_mil(sy_top)}",
                    "hole_size": _fmt_mil(hole_sz),
                    "shape": PCB_PAD_SHAPES.get(sh_top, str(sh_top)),
                    "rotation": _fmt_num(rot),
                    "plated": plated,
                    "stack_mode": stack_mode,
                })
                if len(b4) >= 103:
                    paste_exp, solder_exp = struct.unpack_from("<ii", b4, 86)
                    attrs["paste_mask_exp"] = _fmt_mil(paste_exp)
                    attrs["solder_mask_exp"] = _fmt_mil(solder_exp)
                    attrs["manual_paste"] = b4[101]
                    attrs["manual_solder"] = b4[102]
            if len(b5) >= 263:
                hole_shape = b5[262]
                attrs["hole_shape"] = PCB_HOLE_SHAPES.get(hole_shape, str(hole_shape))
            if len(b5) >= 596 and b5[531]:
                attrs["shape"] = "RoundedRect"
                attrs["corner_radius_pct"] = f"{b5[564]}%"

            records.append({
                "rid": 2,
                "type": "Pad",
                "key": des,
                "attrs": attrs,
                "summary": f"Pad {des} on {attrs.get('layer', '?')} @{attrs.get('location', '?')} ({attrs.get('size', '?')}, {attrs.get('shape', '?')})",
            })

        elif rid == 4:  # PcbTrack
            blk, offset = _read_sized_block(data, offset)
            attrs = {}
            if len(blk) >= 33:
                lid = _resolve_extended_layer(blk[0], blk[33:])
                x1, y1, x2, y2, w = struct.unpack_from("<iiiii", blk, 13)
                attrs = {
                    "layer": layer_label(lid),
                    "start": _fmt_pt_mil(x1, y1),
                    "end": _fmt_pt_mil(x2, y2),
                    "width": _fmt_mil(w),
                }
            records.append({
                "rid": 4,
                "type": "Track",
                "attrs": attrs,
                "summary": f"Track [{attrs.get('layer', '?')}] {attrs.get('start', '?')} → {attrs.get('end', '?')} (w={attrs.get('width', '?')})",
            })

        elif rid == 1:  # PcbArc
            blk, offset = _read_sized_block(data, offset)
            attrs = {}
            if len(blk) >= 45:
                lid = _resolve_extended_layer(blk[0], blk[45:])
                cx, cy, rad = struct.unpack_from("<iii", blk, 13)
                ang_s, ang_e = struct.unpack_from("<dd", blk, 25)
                w = struct.unpack_from("<i", blk, 41)[0]
                attrs = {
                    "layer": layer_label(lid),
                    "center": _fmt_pt_mil(cx, cy),
                    "radius": _fmt_mil(rad),
                    "angles": f"{_fmt_num(ang_s)}°..{_fmt_num(ang_e)}°",
                    "width": _fmt_mil(w),
                }
            records.append({
                "rid": 1,
                "type": "Arc",
                "attrs": attrs,
                "summary": f"Arc [{attrs.get('layer', '?')}] @{attrs.get('center', '?')} r={attrs.get('radius', '?')} ({attrs.get('angles', '')})",
            })

        elif rid == 3:  # PcbVia
            blk, offset = _read_sized_block(data, offset)
            attrs = {}
            if len(blk) >= 31:
                cx, cy, diam, hole = struct.unpack_from("<iiii", blk, 13)
                l_from, l_to = blk[29], blk[30]
                attrs = {
                    "layers": f"{layer_label(l_from)}..{layer_label(l_to)}",
                    "location": _fmt_pt_mil(cx, cy),
                    "diameter": _fmt_mil(diam),
                    "hole_size": _fmt_mil(hole),
                }
                if len(blk) >= 52:
                    solder_exp = struct.unpack_from("<i", blk, 47)[0]
                    attrs["solder_mask_exp"] = _fmt_mil(solder_exp)
            records.append({
                "rid": 3,
                "type": "Via",
                "attrs": attrs,
                "summary": f"Via @{attrs.get('location', '?')} d={attrs.get('diameter', '?')} hole={attrs.get('hole_size', '?')}",
            })

        elif rid == 5:  # PcbString (2 sized blocks)
            blk, offset = _read_sized_block(data, offset)
            sblk, offset = _read_sized_block(data, offset)
            text, _ = _read_pascal_str(sblk, 0)
            attrs = {"text": text}
            if len(blk) >= 38:
                lid = _resolve_extended_layer(blk[0], blk[38:])
                x, y, h = struct.unpack_from("<iii", blk, 13)
                rot = struct.unpack_from("<d", blk, 27)[0]
                attrs.update({
                    "layer": layer_label(lid),
                    "location": _fmt_pt_mil(x, y),
                    "height": _fmt_mil(h),
                    "rotation": _fmt_num(rot),
                })
            records.append({
                "rid": 5,
                "type": "String",
                "attrs": attrs,
                "summary": f"Text '{text}' [{attrs.get('layer', '?')}] @{attrs.get('location', '?')}",
            })

        elif rid == 6:  # PcbFill
            blk, offset = _read_sized_block(data, offset)
            attrs = {}
            if len(blk) >= 37:
                lid = _resolve_extended_layer(blk[0], blk[37:])
                x1, y1, x2, y2 = struct.unpack_from("<iiii", blk, 13)
                rot = struct.unpack_from("<d", blk, 29)[0]
                attrs = {
                    "layer": layer_label(lid),
                    "bounds": f"{_fmt_pt_mil(x1, y1)}..{_fmt_pt_mil(x2, y2)}",
                    "rotation": _fmt_num(rot),
                }
            records.append({
                "rid": 6,
                "type": "Fill",
                "attrs": attrs,
                "summary": f"Fill [{attrs.get('layer', '?')}] {attrs.get('bounds', '?')}",
            })

        elif rid == 11:  # PcbRegion
            blk, offset = _read_sized_block(data, offset)
            attrs = {}
            if len(blk) >= 22:
                lid = blk[0]
                plen = struct.unpack_from("<I", blk, 18)[0]
                p_off = 22
                p_bytes = blk[p_off:p_off + plen]
                _, plookup = parse_altium_params(p_bytes, has_length_prefix=False)
                v_off = p_off + plen
                v_count = struct.unpack_from("<I", blk, v_off)[0] if v_off + 4 <= len(blk) else 0
                pts = []
                for vi in range(min(v_count, 200)):
                    vo = v_off + 4 + vi * 16
                    if vo + 16 <= len(blk):
                        vx, vy = struct.unpack_from("<dd", blk, vo)
                        pts.append((round(vx / 10000.0, 2), round(vy / 10000.0, 2)))
                bbox = ""
                if pts:
                    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                    bbox = f"({min(xs)},{min(ys)})..({max(xs)},{max(ys)})mil"
                attrs = {
                    "layer": layer_label(lid),
                    "kind": plookup.get("kind", "0"),
                    "cutout": plookup.get("isboardcutout", "FALSE"),
                    "vertices": v_count,
                    "bbox": bbox,
                }
            records.append({
                "rid": 11,
                "type": "Region",
                "attrs": attrs,
                "summary": f"Region [{attrs.get('layer', '?')}] {attrs.get('vertices', 0)}v {attrs.get('bbox', '')}",
            })

        elif rid == 12:  # PcbComponentBody (3D Model: single sized block)
            blk, offset = _read_sized_block(data, offset)
            idx = blk.find(b"|")
            plookup = {}
            if idx >= 0:
                # Trim trailing binary float vertex block after last parameter
                pipe_chunk = blk[idx:]
                _, plookup = parse_altium_params(pipe_chunk, has_length_prefix=False)
            mid = plookup.get("modelid", "")
            mname = plookup.get("model.name") or model_names.get(mid) or plookup.get("name", "").strip()
            attrs = {
                "layer": plookup.get("v7_layer", "Mechanical"),
                "model_name": mname or "(Extruded/Unnamed)",
                "standoff": plookup.get("standoffheight", ""),
                "height": plookup.get("overallheight", ""),
                "model_id": mid,
                "checksum": plookup.get("model.checksum", ""),
            }
            records.append({
                "rid": 12,
                "type": "3D Body",
                "attrs": attrs,
                "summary": f"3D Body '{attrs['model_name']}' [{attrs['layer']}] h={attrs['height']}",
            })

        else:
            blk, offset = _read_sized_block(data, offset)
            records.append({
                "rid": rid,
                "type": PCB_RECORD_NAMES.get(rid, f"Record({rid})"),
                "attrs": {"size": len(blk)},
                "summary": f"Record({rid}) ({len(blk)} B)",
            })

    return records


def analyze_pcb_data_diff(
    data_a: bytes,
    data_b: bytes,
    meta_a: Optional[Dict[str, Any]] = None,
    meta_b: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Semantically compare PcbLib footprint 'Data' primitives across versions."""
    recs_a = parse_pcb_data_records(data_a, meta_a)
    recs_b = parse_pcb_data_records(data_b, meta_b)

    bullets: List[str] = []
    extra_diffs: List[Dict[str, Any]] = []

    # 1. Compare Pads semantically (match by designator, or fallback by location)
    pads_a = [r for r in recs_a if r["rid"] == 2]
    pads_b = [r for r in recs_b if r["rid"] == 2]

    def pad_match_key(p: Dict[str, Any], idx: int) -> Tuple[str, str]:
        des = p["attrs"].get("designator", "")
        return (des, "") if des else ("", p["attrs"].get("location", str(idx)))

    # Handle duplicate designators cleanly via indexed occurrences
    occ_a: Counter = Counter()
    map_a: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for i, p in enumerate(pads_a):
        base = pad_match_key(p, i)
        occ_a[base] += 1
        map_a[(base[0], base[1], occ_a[base])] = p

    occ_b: Counter = Counter()
    map_b: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for i, p in enumerate(pads_b):
        base = pad_match_key(p, i)
        occ_b[base] += 1
        map_b[(base[0], base[1], occ_b[base])] = p

    # Group identical pad attribute changes (e.g. 8 pads all changed solder_mask_exp 4mil -> 0mil)
    pad_change_groups: Dict[Tuple[str, str], List[str]] = {}
    added_pads, removed_pads = [], []

    for pk in sorted(set(map_a.keys()) | set(map_b.keys())):
        if pk in map_a and pk not in map_b:
            removed_pads.append(map_a[pk]["summary"])
        elif pk in map_b and pk not in map_a:
            added_pads.append(map_b[pk]["summary"])
        else:
            aa, ab = map_a[pk]["attrs"], map_b[pk]["attrs"]
            diffs_a, diffs_b = [], []
            for k in sorted(set(aa.keys()) | set(ab.keys())):
                if k != "designator" and aa.get(k) != ab.get(k):
                    diffs_a.append(f"{k}={aa.get(k)}")
                    diffs_b.append(f"{k}={ab.get(k)}")
            if diffs_a:
                pad_name = aa.get("designator") or aa.get("location", "?")
                pad_change_groups.setdefault((", ".join(diffs_a), ", ".join(diffs_b)), []).append(pad_name)

    if removed_pads:
        bullets.append(f"Removed {len(removed_pads)} Pad(s): {', '.join(removed_pads[:3])}")
        extra_diffs.append({
            "property": "Data → Removed Pads",
            "val_a": f"{len(removed_pads)} pad(s)",
            "val_b": "(Removed)",
            "type": "stream_detail",
            "details": "; ".join(removed_pads[:4]),
        })
    if added_pads:
        bullets.append(f"Added {len(added_pads)} Pad(s): {', '.join(added_pads[:3])}")
        extra_diffs.append({
            "property": "Data → Added Pads",
            "val_a": "(Not in A)",
            "val_b": f"{len(added_pads)} pad(s)",
            "type": "stream_detail",
            "details": "; ".join(added_pads[:4]),
        })
    for (da_str, db_str), pnames in pad_change_groups.items():
        scope = f"{len(pnames)} Pads ({', '.join(pnames[:4])}{'...' if len(pnames) > 4 else ''})" if len(pnames) > 1 else f"Pad {pnames[0]}"
        bullets.append(f"{scope}: {da_str} → {db_str}")
        extra_diffs.append({
            "property": f"Data → {scope}",
            "val_a": da_str,
            "val_b": db_str,
            "type": "stream_detail",
        })

    # 2. Compare 3D Bodies separately from 2D primitives so ModelID/checksum updates show cleanly
    bodies_a = [r for r in recs_a if r["rid"] == 12]
    bodies_b = [r for r in recs_b if r["rid"] == 12]
    non_pads_a = [r for r in recs_a if r["rid"] not in (2, 12)]
    non_pads_b = [r for r in recs_b if r["rid"] not in (2, 12)]

    def body_geom_key(r: Dict[str, Any]) -> Tuple[Any, ...]:
        a = r["attrs"]
        return (a.get("layer"), a.get("model_name"), a.get("standoff"), a.get("height"))

    if len(bodies_a) == len(bodies_b):
        matched_b = sorted(bodies_b, key=body_geom_key)
        for ba, bb in zip(sorted(bodies_a, key=body_geom_key), matched_b):
            aa, ab = ba["attrs"], bb["attrs"]
            if body_geom_key(ba) == body_geom_key(bb):
                if aa.get("model_id") != ab.get("model_id") or aa.get("checksum") != ab.get("checksum"):
                    bullets.append(
                        f"3D Body '{aa.get('model_name')}' [{aa.get('layer')}] h={aa.get('height')}: "
                        f"ModelID/checksum refreshed (geometry identical)"
                    )
            else:
                changed = [k for k in ("model_name", "layer", "standoff", "height", "model_id") if aa.get(k) != ab.get(k)]
                bullets.append(f"Modified 3D Body '{aa.get('model_name')}': {', '.join(changed)}")
                extra_diffs.append({
                    "property": f"Data → 3D Body ({aa.get('model_name')})",
                    "val_a": ", ".join(f"{k}={aa.get(k)}" for k in changed),
                    "val_b": ", ".join(f"{k}={ab.get(k)}" for k in changed),
                    "type": "stream_detail",
                })
    else:
        non_pads_a = [r for r in recs_a if r["rid"] != 2]
        non_pads_b = [r for r in recs_b if r["rid"] != 2]

    def prim_sig(r: Dict[str, Any]) -> Tuple[Any, ...]:
        attrs = dict(r["attrs"])
        return (r["rid"], tuple(sorted(attrs.items())))

    cnt_a = Counter(prim_sig(r) for r in non_pads_a)
    cnt_b = Counter(prim_sig(r) for r in non_pads_b)
    sum_a = {prim_sig(r): r for r in non_pads_a}
    sum_b = {prim_sig(r): r for r in non_pads_b}

    rem_non = list((cnt_a - cnt_b).elements())
    add_non = list((cnt_b - cnt_a).elements())

    if rem_non or add_non:
        rem_by_type = Counter(sum_a[k]["type"] for k in rem_non)
        add_by_type = Counter(sum_b[k]["type"] for k in add_non)
        rem_examples = [sum_a[k]["summary"] for k in rem_non[:4]]
        add_examples = [sum_b[k]["summary"] for k in add_non[:4]]
        if rem_non:
            t_str = ", ".join(f"{v} {k}" for k, v in rem_by_type.items())
            bullets.append(f"Removed {t_str}: {rem_examples[0]}")
        if add_non:
            t_str = ", ".join(f"{v} {k}" for k, v in add_by_type.items())
            bullets.append(f"Added {t_str}: {add_examples[0]}")
        extra_diffs.append({
            "property": "Data → Tracks / Graphics / 3D",
            "val_a": f"-{len(rem_non)}: {'; '.join(rem_examples)}" if rem_non else "(Unchanged)",
            "val_b": f"+{len(add_non)}: {'; '.join(add_examples)}" if add_non else "(Unchanged)",
            "type": "stream_detail",
            "details": "Compared geometrically across all layers",
        })

    if not bullets:
        type_counts = Counter(r["type"] for r in recs_a)
        summary_cnt = ", ".join(f"{v} {k}(s)" for k, v in type_counts.items())
        bullets.append(
            f"All {len(recs_a)} primitives ({summary_cnt}) match geometrically; "
            f"only storage order or internal Altium flags/GUIDs differ"
        )

    return " | ".join(bullets), extra_diffs


def analyze_pcb_widestrings_diff(data_a: bytes, data_b: bytes) -> str:
    """Decode Altium PcbLib WideStrings (comma-separated Unicode codepoints) and compare."""
    def decode_ws(raw: bytes) -> Dict[str, str]:
        if len(raw) <= 5:
            return {}
        _, lookup = parse_altium_params(raw, has_length_prefix=True)
        if not lookup:
            _, lookup = parse_altium_params(raw, has_length_prefix=False)
        out = {}
        for k, v in lookup.items():
            if k.startswith("encodedtext") and v:
                try:
                    out[k] = "".join(chr(int(c)) for c in v.split(",") if c.strip().isdigit())
                except Exception:
                    out[k] = v
            else:
                out[k] = v
        return out

    wa, wb = decode_ws(data_a), decode_ws(data_b)
    all_k = sorted(set(wa.keys()) | set(wb.keys()))
    diffs = [f"{k}: '{wa.get(k, '')}' → '{wb.get(k, '')}'" for k in all_k if wa.get(k) != wb.get(k)]
    if diffs:
        return "WideStrings changed: " + "; ".join(diffs[:3]) + (f" (+{len(diffs) - 3} more)" if len(diffs) > 3 else "")
    return f"No decoded WideString text entries differ ({len(data_a)} vs {len(data_b)} bytes)"


def analyze_uid_data_diff(data_a: bytes, data_b: bytes) -> str:
    """Parse UniqueIDPrimitiveInformation/Data pipe records safely and summarize changes."""
    def parse_uids(raw: bytes) -> List[Tuple[str, str, str]]:
        offset = 0
        entries = []
        while offset + 4 <= len(raw):
            rec_len = struct.unpack_from("<I", raw, offset)[0]
            offset += 4
            chunk = raw[offset:offset + rec_len]
            offset += rec_len
            _, lookup = parse_altium_params(chunk, has_length_prefix=False)
            entries.append((
                lookup.get("primitiveobjectid", "?"),
                lookup.get("primitiveindex", "?"),
                lookup.get("uniqueid", ""),
            ))
        return entries

    try:
        ua = parse_uids(data_a)
        ub = parse_uids(data_b)
    except Exception as exc:
        return f"UID records differ ({len(data_a)} vs {len(data_b)} bytes; {exc})"

    if len(ua) != len(ub):
        return f"UID records: {len(ua)} → {len(ub)}"

    ids_a = {u[2] for u in ua}
    ids_b = {u[2] for u in ub}
    changed = len(ids_a - ids_b)
    return f"{len(ua)} primitive UIDs: {changed} regenerated/re-indexed" if changed else f"Same {len(ua)} UIDs (order differs)"


def analyze_primitive_guids_diff(data_a: bytes, data_b: bytes) -> str:
    """Parse 24-byte PrimitiveGuids/Data entries and report count/GUID differences."""
    rec_size = 24
    cnt_a = len(data_a) // rec_size
    cnt_b = len(data_b) // rec_size
    if cnt_a != cnt_b:
        return f"GUID entries: {cnt_a} → {cnt_b} primitives"

    guids_a = {data_a[i + 8:i + 24] for i in range(0, cnt_a * rec_size, rec_size)}
    guids_b = {data_b[i + 8:i + 24] for i in range(0, cnt_b * rec_size, rec_size)}
    if guids_a == guids_b:
        return f"Same {cnt_a} primitive GUIDs (reordered)"
    diff_cnt = len(guids_a - guids_b)
    return f"{cnt_a} primitive GUIDs ({diff_cnt} regenerated)"
