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


if __name__ == "__main__":
    unittest.main()
