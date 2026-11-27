"""Automated Comprehensive Test Suite for Altium Library Merger
Verifies end-to-end functionality for both .SchLib and .PcbLib file formats.
"""

import os
import sys
import shutil
import unittest
import pyaltiumlib
import olefile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from altium_engine.cfb_handler import CompoundFileTree, parse_altium_params, build_altium_params
from altium_engine.schlib_merger import SchLibMerger
from altium_engine.pcblib_merger import PcbLibMerger
from altium_engine.diff_engine import LibraryDiffEngine, DiffStatus
from app import app


class TestAltiumMerger(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.test_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "test_data"))
        cls.sample_sch = os.path.join(cls.test_dir, "sample.SchLib")
        cls.sample_pcb = os.path.join(cls.test_dir, "sample.PcbLib")
        cls.out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "out_test"))
        os.makedirs(cls.out_dir, exist_ok=True)

        assert os.path.exists(cls.sample_sch), f"Missing test file: {cls.sample_sch}"
        assert os.path.exists(cls.sample_pcb), f"Missing test file: {cls.sample_pcb}"

    def test_01_schlib_parse(self):
        """Verify SchLib loads and extracts all components properly."""
        tree, comps, meta = SchLibMerger.load_library(self.sample_sch)
        self.assertGreater(len(comps), 0)
        self.assertIn("lib_header", meta)
        self.assertTrue(len(meta["fonts"]) > 0)
        # Verify first component
        first_comp = list(comps.values())[0]
        self.assertTrue(len(first_comp.name) > 0)
        self.assertTrue("Data" in first_comp.raw_streams)
        self.assertTrue(len(first_comp.content_hash) == 64)

    def test_02_pcblib_parse(self):
        """Verify PcbLib loads and extracts all footprints and 3D models."""
        tree, fps, meta = PcbLibMerger.load_library(self.sample_pcb)
        self.assertGreater(len(fps), 0)
        self.assertGreater(len(meta["models"]), 0)
        first_fp = list(fps.values())[0]
        self.assertTrue(len(first_fp.name) > 0)
        self.assertTrue("Data" in first_fp.raw_streams)
        self.assertTrue(len(first_fp.content_hash) == 64)

    def test_03_schlib_divergent_merge(self):
        """Simulate two coworkers modifying a .SchLib:
        Coworker A adds Part_Alpha
        Coworker B adds Part_Beta
        Coworker B also updates Part #0's description (conflict)
        Merge them and verify resolution.
        """
        branch_a_path = os.path.join(self.out_dir, "Sch_Branch_A.SchLib")
        branch_b_path = os.path.join(self.out_dir, "Sch_Branch_B.SchLib")
        merged_path = os.path.join(self.out_dir, "Sch_Merged.SchLib")

        tree_a, comps_a, meta_a = SchLibMerger.load_library(self.sample_sch)
        tree_b, comps_b, meta_b = SchLibMerger.load_library(self.sample_sch)

        # In Branch A: remove component #5 (so it's only in B)
        comp_names = list(comps_a.keys())
        part_only_in_b = comp_names[5]
        # In Branch B: remove component #10 (so it's only in A)
        part_only_in_a = comp_names[10]
        # Conflict part: component #0
        conflict_part = comp_names[0]

        # Save Branch A without part_only_in_b
        tree_a.remove_substorage(comps_a[part_only_in_b].section_key)
        # Update FileHeader for Branch A
        rfh_a = tree_a.get_stream(("FileHeader",))
        pairs_a, _ = parse_altium_params(rfh_a, has_length_prefix=True)
        # Filter out part_only_in_b from catalog
        new_pairs_a = []
        cur_idx = 0
        for k, v in pairs_a:
            if k.lower().startswith("compcount"):
                continue
            elif k.lower().startswith("libref") or k.lower().startswith("compdescr") or k.lower().startswith("partcount"):
                continue
            else:
                new_pairs_a.append((k, v))
        remaining_a = [n for n in comp_names if n != part_only_in_b]
        new_pairs_a.append(("CompCount", str(len(remaining_a))))
        for i, n in enumerate(remaining_a):
            new_pairs_a.append((f"LibRef{i}", n))
            new_pairs_a.append((f"CompDescr{i}", comps_a[n].description))
            new_pairs_a.append((f"PartCount{i}", str(comps_a[n].part_count)))
        tree_a.set_stream(("FileHeader",), build_altium_params(new_pairs_a, has_length_prefix=True))
        tree_a.save_to_file(branch_a_path)

        # Save Branch B without part_only_in_a, and with modified description on conflict_part
        tree_b.remove_substorage(comps_b[part_only_in_a].section_key)
        rfh_b = tree_b.get_stream(("FileHeader",))
        pairs_b, _ = parse_altium_params(rfh_b, has_length_prefix=True)
        new_pairs_b = []
        for k, v in pairs_b:
            if k.lower().startswith("compcount") or k.lower().startswith("libref") or k.lower().startswith("compdescr") or k.lower().startswith("partcount"):
                continue
            new_pairs_b.append((k, v))
        remaining_b = [n for n in comp_names if n != part_only_in_a]
        new_pairs_b.append(("CompCount", str(len(remaining_b))))
        for i, n in enumerate(remaining_b):
            new_pairs_b.append((f"LibRef{i}", n))
            desc = comps_b[n].description
            if n == conflict_part:
                desc = (desc or "") + " [Branch B Modified Description]"
            new_pairs_b.append((f"CompDescr{i}", desc))
            new_pairs_b.append((f"PartCount{i}", str(comps_b[n].part_count)))
        tree_b.set_stream(("FileHeader",), build_altium_params(new_pairs_b, has_length_prefix=True))
        tree_b.save_to_file(branch_b_path)

        # Diff Branch A vs Branch B
        diff = LibraryDiffEngine.compare(branch_a_path, branch_b_path)
        self.assertIn(diff["counts"]["added_a"], (1, 2))
        self.assertIn(diff["counts"]["added_b"], (1, 2))
        self.assertGreaterEqual(diff["counts"]["conflicts"], 1)

        # Merge with resolutions:
        # Keep both for conflict part
        resolutions = {
            conflict_part: {"action": "keep_both", "new_name": f"{conflict_part}_BranchB"}
        }

        res = SchLibMerger.merge(
            lib_a_path=branch_a_path,
            lib_b_path=branch_b_path,
            output_path=merged_path,
            resolutions=resolutions,
            default_conflict="prefer_a",
        )

        self.assertTrue(res["success"])
        self.assertTrue(os.path.exists(merged_path))

        # Verify output with pyaltiumlib
        merged_lib = pyaltiumlib.read(merged_path)
        merged_parts = merged_lib.list_parts()
        self.assertIn(part_only_in_a, merged_parts)
        self.assertIn(part_only_in_b, merged_parts)
        self.assertIn(conflict_part, merged_parts)
        self.assertIn(f"{conflict_part}_BranchB", merged_parts)
        print(f"Verified merged SchLib: {len(merged_parts)} parts successfully unified!")

    def test_04_pcblib_merge(self):
        """Simulate merging PCB library and verify 3D models and SectionKeys."""
        branch_a_path = os.path.join(self.out_dir, "Pcb_Branch_A.PcbLib")
        branch_b_path = os.path.join(self.out_dir, "Pcb_Branch_B.PcbLib")
        merged_path = os.path.join(self.out_dir, "Pcb_Merged.PcbLib")

        shutil.copyfile(self.sample_pcb, branch_a_path)
        shutil.copyfile(self.sample_pcb, branch_b_path)

        res = PcbLibMerger.merge(
            lib_a_path=branch_a_path,
            lib_b_path=branch_b_path,
            output_path=merged_path,
            default_conflict="prefer_a",
        )

        self.assertTrue(res["success"])
        self.assertTrue(os.path.exists(merged_path))

        # Verify output with pyaltiumlib
        merged_lib = pyaltiumlib.read(merged_path)
        fps = merged_lib.list_parts()
        self.assertGreater(len(fps), 100)

        # Verify OLE streams
        ole = olefile.OleFileIO(merged_path)
        self.assertTrue(ole.exists("FileHeader"))
        self.assertTrue(ole.exists("Library/Header"))
        self.assertTrue(ole.exists("Library/Data"))
        self.assertTrue(ole.exists("Library/Models/Header"))
        self.assertTrue(ole.exists("SectionKeys"))
        ole.close()
        print(f"Verified merged PcbLib: {len(fps)} footprints and 3D models intact!")

    def test_05_flask_api(self):
        """Test Flask web app REST endpoints."""
        client = app.test_client()

        # 1. Health check
        resp = client.get("/api/health")
        self.assertEqual(resp.status_code, 200)

        # 2. Demo sample compare
        resp = client.post("/api/compare-samples", json={"type": "SchLib"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("session_id", data)
        self.assertIn("diff", data)
        session_id = data["session_id"]

        # 3. Merge API
        resp = client.post("/api/merge", json={
            "session_id": session_id,
            "resolutions": {},
            "default_action": "prefer_a",
            "output_filename": "ApiTest_Merged.SchLib",
        })
        self.assertEqual(resp.status_code, 200)
        m_data = resp.get_json()
        self.assertTrue(m_data.get("success"))
        self.assertIn("download_url", m_data)

    def test_06_reversing_order_symmetry(self):
        """Verify that reversing upload order yields strictly symmetric and identical total results,
        even when both files share the identical filename (e.g. Components.SchLib).
        """
        import io

        branch_a_path = os.path.join(self.out_dir, "Sch_Branch_A.SchLib")
        branch_b_path = os.path.join(self.out_dir, "Sch_Branch_B.SchLib")
        self.assertTrue(os.path.exists(branch_a_path))
        self.assertTrue(os.path.exists(branch_b_path))

        with open(branch_a_path, "rb") as f:
            bytes_a = f.read()
        with open(branch_b_path, "rb") as f:
            bytes_b = f.read()

        client = app.test_client()

        # 1. Compare A as file_a and B as file_b, both named 'Components.SchLib'
        data_ab = {
            "file_a": (io.BytesIO(bytes_a), "Components.SchLib"),
            "file_b": (io.BytesIO(bytes_b), "Components.SchLib"),
        }
        resp_ab = client.post("/api/compare", data=data_ab, content_type="multipart/form-data")
        self.assertEqual(resp_ab.status_code, 200)
        diff_ab = resp_ab.get_json()["diff"]
        counts_ab = diff_ab["counts"]

        # 2. Reverse order: B as file_a and A as file_b, both named 'Components.SchLib'
        data_ba = {
            "file_a": (io.BytesIO(bytes_b), "Components.SchLib"),
            "file_b": (io.BytesIO(bytes_a), "Components.SchLib"),
        }
        resp_ba = client.post("/api/compare", data=data_ba, content_type="multipart/form-data")
        self.assertEqual(resp_ba.status_code, 200)
        diff_ba = resp_ba.get_json()["diff"]
        counts_ba = diff_ba["counts"]

        # Verify mathematical symmetry
        self.assertEqual(counts_ab["total"], counts_ba["total"], "Total count must be identical regardless of order!")
        self.assertEqual(counts_ab["identical"], counts_ba["identical"], "Identical count must match regardless of order!")
        self.assertEqual(counts_ab["conflicts"], counts_ba["conflicts"], "Conflict count must match regardless of order!")
        self.assertEqual(counts_ab["added_a"], counts_ba["added_b"], "Added in A must equal Added in B when reversed!")
        self.assertEqual(counts_ab["added_b"], counts_ba["added_a"], "Added in B must equal Added in A when reversed!")
        self.assertEqual(counts_ab["total"], 81)
        self.assertEqual(counts_ab["conflicts"], 1)

        print(f"Symmetry test passed: Total={counts_ab['total']}, Identical={counts_ab['identical']}, Conflicts={counts_ab['conflicts']}")

    def test_07_schlib_stream_extraction(self):
        """Verify deep semantic extraction of SchLib Data, binary pins, PinTextData, and PinFrac streams."""
        import struct
        import zlib
        from altium_engine.binary_inspector import (
            parse_sch_binary_pin,
            parse_sch_pin_sidecar,
            analyze_sch_data_diff,
            analyze_sch_pin_sidecar_diff,
        )

        # 1. Verify binary pin decoder handles non-empty descriptions accurately
        desc = b"PWR"
        pin_body = (
            struct.pack("<I", 2)
            + b"\x00"
            + struct.pack("<h", 1)
            + b"\x00\x00\x00\x00\x00"
            + bytes([len(desc)]) + desc
            + bytes([1, 4, 0x33])
            + struct.pack("<HhhI", 20, -15, -5, 0)
            + b"\x03VDD\x011\x00\x00\x00"
        )
        parsed_pin = parse_sch_binary_pin(pin_body)
        self.assertEqual(parsed_pin["description"], "PWR")
        self.assertEqual(parsed_pin["electrical"], "Passive")
        self.assertEqual(parsed_pin["name"], "VDD")
        self.assertEqual(parsed_pin["designator"], "1")
        self.assertEqual((parsed_pin["x"], parsed_pin["y"], parsed_pin["length"]), (-15, -5, 20))

        # 2. Verify Data stream diff ignores IndexInSheet shifts and reports real changes
        def pack_text_rec(text: str) -> bytes:
            raw = text.encode("windows-1252") + b"\x00"
            return struct.pack("<HH", len(raw), 0) + raw

        data_a = (
            pack_text_rec("|RECORD=1|LibReference=TEST_PART|PartCount=2")
            + pack_text_rec("|RECORD=41|IndexInSheet=0|Name=sample link|FontID=3|Text=https://example.com")
            + pack_text_rec("|RECORD=41|IndexInSheet=1|Name=unit price|FontID=1|Text=$ 0.9375")
            + pack_text_rec("|RECORD=6|IndexInSheet=2|LocationCount=2|X1=0|Y1=0|X2=10|Y2=10")
            + pack_text_rec("|RECORD=44")
            + pack_text_rec("|RECORD=45|OwnerIndex=4|ModelName=FP_ALPHA|ModelType=PCBLIB")
        )
        data_b = (
            pack_text_rec("|RECORD=1|LibReference=TEST_PART|PartCount=2")
            + pack_text_rec("|RECORD=41|IndexInSheet=0|Name=sample link|FontID=2|Text=https://example.com")
            + pack_text_rec("|RECORD=6|IndexInSheet=1|LocationCount=2|X1=0|Y1=0|X2=10|Y2=10")
            + pack_text_rec("|RECORD=44")
            + pack_text_rec("|RECORD=45|OwnerIndex=3|ModelName=FP_BETA|ModelType=PCBLIB")
        )
        meta_a = {"fonts": [{"id": 3, "name": "Times New Roman", "size": "10", "underline": "T"}, {"id": 4, "name": "Times New Roman", "size": "5"}]}
        meta_b = {"fonts": [{"id": 2, "name": "Times New Roman", "size": "10", "underline": "T"}, {"id": 3, "name": "Times New Roman", "size": "5"}]}

        summary, extra = analyze_sch_data_diff(data_a, data_b, meta_a, meta_b)
        self.assertIn("Removed Parameter 'unit price'", summary)
        self.assertIn("Font #3 (Times New Roman 10pt Underline)", summary)
        self.assertIn("Font #2 (Times New Roman 10pt Underline)", summary)
        self.assertIn("FP_ALPHA", summary)
        self.assertIn("FP_BETA", summary)
        self.assertGreaterEqual(len(extra), 2)

        # 3. Verify PinTextData zlib decoding resolves FontIDs cleanly
        def make_ptd(font_id: int) -> bytes:
            hdr = b"|HEADER=PinTextData|Weight=1\x00"
            payload = struct.pack("<BHI", 0x10, font_id, 0) * 2
            comp_z = zlib.compress(payload)
            entry_body = b"\xD0\x010" + struct.pack("<I", len(comp_z)) + comp_z
            return struct.pack("<I", len(hdr)) + hdr + struct.pack("<HH", len(entry_body), 0x0100) + entry_body

        ptd_summary, ptd_extra = analyze_sch_pin_sidecar_diff(
            "PinTextData",
            make_ptd(4),
            make_ptd(3),
            meta_a,
            meta_b,
            [{"designator": "1"}],
            [{"designator": "1"}],
        )
        self.assertIn("Font #4 (Times New Roman 5pt)", ptd_summary)
        self.assertIn("Font #3 (Times New Roman 5pt)", ptd_summary)
        self.assertEqual(len(ptd_extra), 1)

        # 4. Verify PinFrac sidecar parsing
        pf_hdr = b"|HEADER=PinFrac|Weight=1\x00"
        pf_z = zlib.compress(struct.pack("<iii", -31496, 31496, 68504))
        pf_body = b"\xD0\x010" + struct.pack("<I", len(pf_z)) + pf_z
        pf_stream = struct.pack("<I", len(pf_hdr)) + pf_hdr + struct.pack("<HH", len(pf_body), 0x0100) + pf_body
        parsed_pf = parse_sch_pin_sidecar("PinFrac", pf_stream)
        self.assertEqual(parsed_pf["entries"]["0"]["x_frac"], -31496)

    def test_08_pcblib_stream_extraction(self):
        """Verify PcbLib Data, WideStrings, UniqueID, and PrimitiveGuids stream extraction."""
        import struct
        from altium_engine.binary_inspector import (
            parse_pcb_data_records,
            analyze_pcb_data_diff,
            analyze_pcb_widestrings_diff,
            analyze_uid_data_diff,
            analyze_primitive_guids_diff,
        )

        # 1. Parse all footprints in sample.PcbLib cleanly
        _, fps, meta = PcbLibMerger.load_library(self.sample_pcb)
        unknowns = 0
        for fp in fps.values():
            recs = parse_pcb_data_records(fp.raw_streams.get("Data", b""), meta)
            unknowns += sum(1 for r in recs if r["type"].startswith("Record("))
        self.assertEqual(unknowns, 0)

        # 2. Verify PcbLib Data track diff + pad diff detection
        first_fp = fps["RPI2"]
        d_orig = first_fp.raw_streams["Data"]
        recs_orig = parse_pcb_data_records(d_orig, meta)
        # Synthesize a modified Data stream by appending a new Track (rid=4) before EOF
        track_common = bytes([33, 0, 0]) + b"\xff" * 10  # Layer 33 (Top Overlay)
        track_body = track_common + struct.pack("<iiiii", 0, 0, 1000000, 1000000, 100000)
        d_mod = d_orig + bytes([4]) + struct.pack("<I", len(track_body)) + track_body
        pcb_sum, pcb_extra = analyze_pcb_data_diff(d_orig, d_mod, meta, meta)
        self.assertIn("Added 1 Track", pcb_sum)
        self.assertIn("Top Overlay", pcb_sum)
        self.assertTrue(any("Tracks / Graphics" in e["property"] for e in pcb_extra))
        self.assertGreater(len(recs_orig), 10)

        # 3. Verify WideStrings decoding
        ws_a = b"|ENCODEDTEXT0=82,80,73\x00"
        ws_b = b"|ENCODEDTEXT0=71,80,73,79\x00"
        ws_a_blk = struct.pack("<I", len(ws_a)) + ws_a
        ws_b_blk = struct.pack("<I", len(ws_b)) + ws_b
        ws_diff = analyze_pcb_widestrings_diff(ws_a_blk, ws_b_blk)
        self.assertIn("'RPI' → 'GPIO'", ws_diff)

        # 4. Verify UID & PrimitiveGuids analyzers
        u1 = b"|PRIMITIVEINDEX=0|PRIMITIVEOBJECTID=Pad|UNIQUEID=AAAAAAAA\x00"
        u2 = b"|PRIMITIVEINDEX=0|PRIMITIVEOBJECTID=Pad|UNIQUEID=BBBBBBBB\x00"
        uid_diff = analyze_uid_data_diff(struct.pack("<I", len(u1)) + u1, struct.pack("<I", len(u2)) + u2)
        self.assertIn("1 primitive UIDs: 1 regenerated", uid_diff)

        g1 = struct.pack("<II", 2, 0) + b"\x01" * 16
        g2 = struct.pack("<II", 2, 0) + b"\x02" * 16
        guid_diff = analyze_primitive_guids_diff(g1, g2)
        self.assertIn("1 primitive GUIDs (1 regenerated)", guid_diff)

    def test_09_git_three_way_merge(self):
        """Verify Git 3-way merge driver treats single-branch updates/deletions as clean (exit 0) and concurrent edits as conflicts (exit 1)."""
        import subprocess
        from altium_engine.cfb_handler import CompoundFileTree, parse_altium_params, build_altium_params

        base_path = os.path.join(self.out_dir, "git3_base.SchLib")
        ours_path = os.path.join(self.out_dir, "git3_ours.SchLib")
        theirs_path = os.path.join(self.out_dir, "git3_theirs.SchLib")

        def _edit_descr(tree, idx, new_descr):
            raw_fh = tree.get_stream(("FileHeader",))
            pairs, fh_lookup = parse_altium_params(raw_fh, has_length_prefix=True)
            comp_count = int(fh_lookup.get("compcount", 0))
            entries = [
                {
                    "libref": fh_lookup.get(f"libref{i}", ""),
                    "descr": fh_lookup.get(f"compdescr{i}", ""),
                    "partcount": fh_lookup.get(f"partcount{i}", "1"),
                }
                for i in range(comp_count)
            ]
            entries[idx]["descr"] = new_descr
            new_pairs = [(k, v) for k, v in pairs if not any(k.lower().startswith(p) for p in ("compcount", "libref", "compdescr", "partcount"))]
            new_pairs.append(("CompCount", str(len(entries))))
            for i, e in enumerate(entries):
                new_pairs.append((f"LibRef{i}", e["libref"]))
                if e["descr"]:
                    new_pairs.append((f"CompDescr{i}", e["descr"]))
                new_pairs.append((f"PartCount{i}", e["partcount"]))
            tree.set_stream(("FileHeader",), build_altium_params(new_pairs, has_length_prefix=True))
            return entries[idx]["libref"]

        def _delete_comp(tree, idx):
            raw_fh = tree.get_stream(("FileHeader",))
            pairs, fh_lookup = parse_altium_params(raw_fh, has_length_prefix=True)
            comp_count = int(fh_lookup.get("compcount", 0))
            entries = [
                {
                    "libref": fh_lookup.get(f"libref{i}", ""),
                    "descr": fh_lookup.get(f"compdescr{i}", ""),
                    "partcount": fh_lookup.get(f"partcount{i}", "1"),
                }
                for i in range(comp_count)
            ]
            removed = entries.pop(idx)
            for sp in list(tree.list_streams(prefix=(removed["libref"],))):
                tree.delete_stream(sp)
            new_pairs = [(k, v) for k, v in pairs if not any(k.lower().startswith(p) for p in ("compcount", "libref", "compdescr", "partcount"))]
            new_pairs.append(("CompCount", str(len(entries))))
            for i, e in enumerate(entries):
                new_pairs.append((f"LibRef{i}", e["libref"]))
                if e["descr"]:
                    new_pairs.append((f"CompDescr{i}", e["descr"]))
                new_pairs.append((f"PartCount{i}", e["partcount"]))
            tree.set_stream(("FileHeader",), build_altium_params(new_pairs, has_length_prefix=True))
            return removed["libref"]

        # 1. Clean 3-way merge: ours updates #0, theirs updates #1 and deletes #2
        tree_base = CompoundFileTree.load_from_file(self.sample_sch)
        tree_base.save_to_file(base_path)

        tree_ours = CompoundFileTree.load_from_file(self.sample_sch)
        name_0 = _edit_descr(tree_ours, 0, "Updated in Ours")
        tree_ours.save_to_file(ours_path)

        tree_theirs = CompoundFileTree.load_from_file(self.sample_sch)
        name_1 = _edit_descr(tree_theirs, 1, "Updated in Theirs")
        name_2_del = _delete_comp(tree_theirs, 2)
        tree_theirs.save_to_file(theirs_path)

        cli_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "altium_merge_cli.py"))
        proc = subprocess.run(
            [sys.executable, cli_path, "git-merge", base_path, ours_path, theirs_path, "sample.SchLib"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"Expected clean 3-way merge (exit 0), got {proc.returncode}:\n{proc.stdout}\n{proc.stderr}")

        _, merged_comps, _ = SchLibMerger.load_library(ours_path)
        self.assertEqual(merged_comps[name_0].description, "Updated in Ours")
        self.assertEqual(merged_comps[name_1].description, "Updated in Theirs")
        self.assertNotIn(name_2_del, merged_comps)

        # 2. True concurrent conflict: both ours and theirs modify #0 differently -> exit 1
        tree_ours2 = CompoundFileTree.load_from_file(self.sample_sch)
        _edit_descr(tree_ours2, 0, "Concurrent Edit A")
        tree_ours2.save_to_file(ours_path)

        tree_theirs2 = CompoundFileTree.load_from_file(self.sample_sch)
        _edit_descr(tree_theirs2, 0, "Concurrent Edit B")
        tree_theirs2.save_to_file(theirs_path)

        proc2 = subprocess.run(
            [sys.executable, cli_path, "git-merge", base_path, ours_path, theirs_path, "sample.SchLib"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc2.returncode, 1, f"Expected conflict exit code 1, got {proc2.returncode}")
        self.assertIn("CONFLICT", proc2.stderr)


if __name__ == "__main__":
    unittest.main()
