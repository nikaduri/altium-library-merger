"""Altium Library Merger Engine
Handles reading, comparing, and merging Altium Schematic and PCB libraries.
"""

from .cfb_handler import CompoundFileTree, parse_altium_params, build_altium_params
from .schlib_merger import SchLibMerger, SchComponentInfo
from .pcblib_merger import PcbLibMerger, PcbFootprintInfo
from .diff_engine import LibraryDiffEngine, ComponentDiff, DiffStatus

__all__ = [
    "CompoundFileTree",
    "parse_altium_params",
    "build_altium_params",
    "SchLibMerger",
    "SchComponentInfo",
    "PcbLibMerger",
    "PcbFootprintInfo",
    "LibraryDiffEngine",
    "ComponentDiff",
    "DiffStatus",
]
