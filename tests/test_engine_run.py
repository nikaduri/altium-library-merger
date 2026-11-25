import sys
import os

sys.path.insert(0, os.path.abspath("."))
from altium_engine import SchLibMerger, PcbLibMerger, LibraryDiffEngine

print("Testing SchLib load...")
tree_s, comps_s, meta_s = SchLibMerger.load_library("test_data/sample.SchLib")
print(f"SchLib loaded: {len(comps_s)} components, LibHeader: {meta_s.get('lib_header')}")

print("Testing PcbLib load...")
tree_p, fps_p, meta_p = PcbLibMerger.load_library("test_data/sample.PcbLib")
print(f"PcbLib loaded: {len(fps_p)} footprints, {len(meta_p.get('models', []))} 3D models")

print("Testing DiffEngine on sample.SchLib vs sample.SchLib (identical self-diff)...")
diff = LibraryDiffEngine.compare("test_data/sample.SchLib", "test_data/sample.SchLib")
print(f"Diff counts: {diff.get('counts')}")
print("ALL TESTS PASSED SUCCESSFULLY!")
