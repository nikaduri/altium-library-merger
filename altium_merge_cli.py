#!/usr/bin/env python3
"""Altium Library Merger CLI & Git Merge Driver
Provides command-line diffing, interactive and batch merging, and seamless Git merge driver integration.
"""

import argparse
import os
import sys
from typing import Dict, Any

from altium_engine.diff_engine import LibraryDiffEngine, DiffStatus
from altium_engine.schlib_merger import SchLibMerger
from altium_engine.pcblib_merger import PcbLibMerger

# ANSI Colors for terminal output
RESET = "\033[0m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
GRAY = "\033[90m"


def print_banner():
    banner = f"""{CYAN}{BOLD}
    ========================================================
                 ALTIUM LIBRARY MERGER & DIFF               
           Clean Collaborative Merging for .SchLib & .PcbLib
    ========================================================{RESET}"""
    print(banner)


def cmd_diff(args: argparse.Namespace) -> int:
    """Print terminal diff of two Altium libraries."""
    lib_a = args.lib_a
    lib_b = args.lib_b

    if not os.path.exists(lib_a):
        print(f"{RED}Error: File not found: {lib_a}{RESET}", file=sys.stderr)
        return 1
    if not os.path.exists(lib_b):
        print(f"{RED}Error: File not found: {lib_b}{RESET}", file=sys.stderr)
        return 1

    try:
        diff_data = LibraryDiffEngine.compare(lib_a, lib_b)
    except Exception as e:
        print(f"{RED}Diff failed: {e}{RESET}", file=sys.stderr)
        return 1

    counts = diff_data["counts"]
    lib_type = diff_data["library_type"]

    print(f"\n{BOLD}Comparing {lib_type} files:{RESET}")
    print(f"  [A] Mine:   {BLUE}{args.lib_a}{RESET}")
    print(f"  [B] Theirs: {YELLOW}{args.lib_b}{RESET}\n")

    print(f"{BOLD}Summary:{RESET}")
    print(f"  Total items:     {counts['total']}")
    print(f"  {GREEN}+ Added in [A]:  {counts['added_a']}{RESET}")
    print(f"  {GREEN}+ Added in [B]:  {counts['added_b']}{RESET}")
    print(f"  {GRAY}= Identical:     {counts['identical']}{RESET}")
    print(f"  {RED}! Conflicts:     {counts['conflicts']}{RESET}\n")

    if args.summary_only:
        return 0

    print(f"{BOLD}{'STATUS':<15} {'NAME':<40} {'DETAILS'}{RESET}")
    print("-" * 80)

    for item in diff_data["items"]:
        status = item["status"]
        name = item["name"]

        if status == DiffStatus.ADDED_A.value:
            badge = f"{GREEN}[ONLY IN A]{RESET}"
            details = "Will be kept in merged library"
            print(f"{badge:<24} {name:<40} {details}")
        elif status == DiffStatus.ADDED_B.value:
            badge = f"{GREEN}[ONLY IN B]{RESET}"
            details = "Will be merged into library"
            print(f"{badge:<24} {name:<40} {details}")
        elif status == DiffStatus.CONFLICT.value:
            badge = f"{RED}[CONFLICT]{RESET}"
            pdiffs = item.get("property_diffs", [])
            summary_parts = []
            for d in pdiffs[:2]:
                if d.get("details") and d.get("type") == "binary_stream":
                    summary_parts.append(f"{d['property']} ({d['details']})")
                else:
                    summary_parts.append(f"{d['property']}: '{d['val_a']}' vs '{d['val_b']}'")
            diff_summary = ", ".join(summary_parts)
            if len(pdiffs) > 2:
                diff_summary += f" (+{len(pdiffs)-2} more)"
            print(f"{badge:<24} {BOLD}{name:<40}{RESET} {diff_summary}")
        elif not args.hide_identical:
            badge = f"{GRAY}[IDENTICAL]{RESET}"
            details = "Matches byte-for-byte"
            print(f"{badge:<24} {GRAY}{name:<40} {details}{RESET}")

    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    """Merge two Altium libraries."""
    lib_a = args.lib_a
    lib_b = args.lib_b
    output = args.output

    if not output:
        print(f"{RED}Error: Output path (-o / --output) is required.{RESET}", file=sys.stderr)
        return 1

    try:
        diff_data = LibraryDiffEngine.compare(lib_a, lib_b)
    except Exception as e:
        print(f"{RED}Failed to compare libraries: {e}{RESET}", file=sys.stderr)
        return 1

    lib_type = diff_data["library_type"]
    counts = diff_data["counts"]
    resolutions: Dict[str, Dict[str, Any]] = {}

    print(f"\n{BOLD}Merging {lib_type} libraries:{RESET}")
    print(f"  [A] Mine:   {BLUE}{lib_a}{RESET}")
    print(f"  [B] Theirs: {YELLOW}{lib_b}{RESET}")
    print(f"  --> Output: {GREEN}{output}{RESET}\n")

    conflicts = [item for item in diff_data["items"] if item["status"] == DiffStatus.CONFLICT.value]

    if conflicts and args.interactive:
        print(f"{YELLOW}{BOLD}Interactive Conflict Resolution ({len(conflicts)} conflicts):{RESET}\n")
        for i, conf in enumerate(conflicts, 1):
            name = conf["name"]
            pdiffs = conf.get("property_diffs", [])
            print(f"--- [{i}/{len(conflicts)}] Conflict: {BOLD}{name}{RESET} ---")
            for d in pdiffs:
                print(f"    {d['property']}: [A] '{d['val_a']}'  <-->  [B] '{d['val_b']}'")
                if d.get("details"):
                    print(f"        -> {d['details']}")

            while True:
                choice = input("  Choice: [A] Keep Mine, [B] Keep Theirs, [K] Keep Both (rename B), [S] Skip: ").strip().lower()
                if choice == "a":
                    resolutions[name] = {"action": "keep_a"}
                    break
                elif choice == "b":
                    resolutions[name] = {"action": "keep_b"}
                    break
                elif choice == "k":
                    new_name = input(f"    Enter name for copy from B [default: {name}_B]: ").strip()
                    resolutions[name] = {"action": "keep_both", "new_name": new_name or f"{name}_B"}
                    break
                elif choice == "s":
                    # Use default
                    break
                else:
                    print("  Invalid choice. Enter A, B, K, or S.")
            print()
    elif conflicts:
        default_act = "keep_a" if args.prefer_mine else ("keep_b" if args.prefer_theirs else ("keep_both" if args.keep_both else "keep_a"))
        print(f"Resolving {len(conflicts)} conflict(s) using policy: {BOLD}{default_act}{RESET}")
        for conf in conflicts:
            name = conf["name"]
            if default_act == "keep_both":
                resolutions[name] = {"action": "keep_both", "new_name": f"{name}_B"}
            else:
                resolutions[name] = {"action": default_act}

    # Perform actual merge
    print(f"Building merged {lib_type} file...")
    if lib_type == "SchLib":
        res = SchLibMerger.merge(
            lib_a_path=lib_a,
            lib_b_path=lib_b,
            output_path=output,
            resolutions=resolutions,
            default_conflict="prefer_b" if args.prefer_theirs else "prefer_a",
        )
    elif lib_type == "PcbLib":
        res = PcbLibMerger.merge(
            lib_a_path=lib_a,
            lib_b_path=lib_b,
            output_path=output,
            resolutions=resolutions,
            default_conflict="prefer_b" if args.prefer_theirs else "prefer_a",
        )
    else:
        print(f"{RED}Unknown library type: {lib_type}{RESET}", file=sys.stderr)
        return 1

    print(f"\n{GREEN}{BOLD}Merge completed successfully!{RESET}")
    print(f"  Merged Library: {output}")
    print(f"  Total items:    {res['total_components']}")
    print(f"  Added from [A]: {res['added_from_a']}")
    print(f"  Added from [B]: {res['added_from_b']}")
    print(f"  Identical:      {res['identical']}")
    print(f"  Conflicts:      {res['conflicts_resolved']}")
    return 0


def cmd_git_merge(args: argparse.Namespace) -> int:
    """Git 3-way merge driver handler.
    Arguments provided by Git: %O %A %B %P
    %O = Ancestor / Base
    %A = Ours / Mine (merge target, to be updated in-place)
    %B = Theirs / Other (incoming)
    %P = Relative path
    """
    base_file = args.base
    ours_file = args.ours
    theirs_file = args.theirs
    path_name = args.path_name or "Altium Library"

    print(f"[Altium-Git-Merge] Resolving library merge for '{path_name}'...")
    print(f"  Ours:   {ours_file}")
    print(f"  Theirs: {theirs_file}")

    # Temporary output file
    temp_output = ours_file + ".merged.tmp"

    # Merge ours and theirs, writing to temp_output
    try:
        diff_data = LibraryDiffEngine.compare(ours_file, theirs_file)
        lib_type = diff_data["library_type"]
        resolutions = {}

        # Default policy for automated Git merge:
        # Keep all additions from both sides.
        # For direct conflicts: keep ours or keep both depending on policy.
        conflicts = [item for item in diff_data["items"] if item["status"] == DiffStatus.CONFLICT.value]
        if conflicts:
            print(f"[Altium-Git-Merge] {len(conflicts)} conflict(s) detected. Resolving with keep-both to prevent data loss.")
            for c in conflicts:
                resolutions[c["name"]] = {"action": "keep_both", "new_name": f"{c['name']}_Incoming"}

        if lib_type == "SchLib":
            SchLibMerger.merge(ours_file, theirs_file, temp_output, resolutions=resolutions, default_conflict="prefer_a")
        elif lib_type == "PcbLib":
            PcbLibMerger.merge(ours_file, theirs_file, temp_output, resolutions=resolutions, default_conflict="prefer_a")
        else:
            print(f"[Altium-Git-Merge] Unknown type {lib_type}", file=sys.stderr)
            return 1

        # Replace ours with merged output
        if os.path.exists(ours_file):
            os.remove(ours_file)
        os.rename(temp_output, ours_file)

        print(f"[Altium-Git-Merge] Successfully merged '{path_name}' cleanly!")
        return 0
    except Exception as e:
        print(f"[Altium-Git-Merge] Merge failed: {e}", file=sys.stderr)
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return 1


def cmd_git_install(args: argparse.Namespace) -> int:
    """Configure Git repository to use this Altium merge driver."""
    script_path = os.path.abspath(__file__)
    python_exe = sys.executable

    # Command line that Git will execute
    # Git passes: %O (ancestor), %A (ours), %B (theirs), %P (path)
    driver_cmd = f'"{python_exe}" "{script_path}" git-merge %O %A %B %P'

    print(f"{BOLD}Setting up Altium Git Merge Driver...{RESET}")
    os.system(f'git config merge.altiumlib.name "Altium Library Merge Driver"')
    os.system(f'git config merge.altiumlib.driver "{driver_cmd}"')

    # Update or create .gitattributes
    gitattributes_file = os.path.join(os.getcwd(), ".gitattributes")
    rules = [
        "*.SchLib merge=altiumlib\n",
        "*.schlib merge=altiumlib\n",
        "*.PcbLib merge=altiumlib\n",
        "*.pcblib merge=altiumlib\n",
    ]

    existing_lines = []
    if os.path.exists(gitattributes_file):
        with open(gitattributes_file, "r") as f:
            existing_lines = f.readlines()

    with open(gitattributes_file, "a") as f:
        for r in rules:
            if r not in existing_lines:
                f.write(r)

    print(f"{GREEN}{BOLD}Altium Git Merge Driver successfully registered!{RESET}")
    print(f"  Driver:         {driver_cmd}")
    print(f"  .gitattributes: Updated with *.SchLib and *.PcbLib rules")
    print(f"\nNow whenever coworkers run 'git merge' or 'git pull', Altium libraries will be merged cleanly without binary conflicts!")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Altium Library Merger & Diff Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: diff
    p_diff = subparsers.add_parser("diff", help="Compare two Altium libraries")
    p_diff.add_argument("lib_a", help="Path to Library A (Mine / Base)")
    p_diff.add_argument("lib_b", help="Path to Library B (Theirs / Incoming)")
    p_diff.add_argument("--hide-identical", action="store_true", help="Hide components that match identically")
    p_diff.add_argument("--summary-only", action="store_true", help="Print only high-level summary counts")

    # Subcommand: merge
    p_merge = subparsers.add_parser("merge", help="Merge two Altium libraries")
    p_merge.add_argument("lib_a", help="Path to Library A (Mine)")
    p_merge.add_argument("lib_b", help="Path to Library B (Theirs)")
    p_merge.add_argument("-o", "--output", required=True, help="Path for merged output library")
    p_merge.add_argument("-i", "--interactive", action="store_true", help="Interactively resolve each conflict")
    p_merge.add_argument("--prefer-mine", action="store_true", help="On conflict, keep version from Library A")
    p_merge.add_argument("--prefer-theirs", action="store_true", help="On conflict, keep version from Library B")
    p_merge.add_argument("--keep-both", action="store_true", help="On conflict, keep both (rename B with suffix)")

    # Subcommand: git-merge
    p_git = subparsers.add_parser("git-merge", help="Git 3-way merge driver command (base ours theirs path)")
    p_git.add_argument("base", help="Ancestor base file (%%O)")
    p_git.add_argument("ours", help="Ours / current branch file (%%A)")
    p_git.add_argument("theirs", help="Theirs / incoming branch file (%%B)")
    p_git.add_argument("path_name", nargs="?", default="", help="File path in repository (%%P)")

    # Subcommand: git-install
    subparsers.add_parser("git-install", help="Configure Git to use Altium merge driver in this repository")

    args = parser.parse_args()

    if args.command == "diff":
        sys.exit(cmd_diff(args))
    elif args.command == "merge":
        sys.exit(cmd_merge(args))
    elif args.command == "git-merge":
        sys.exit(cmd_git_merge(args))
    elif args.command == "git-install":
        sys.exit(cmd_git_install(args))


if __name__ == "__main__":
    main()
