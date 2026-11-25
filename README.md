# Altium Library Merger

A specialized application and Git merge driver designed to cleanly compare, diff, resolve conflicts, and merge **Altium Designer Schematic Libraries (`.SchLib`)** and **PCB Footprint Libraries (`.PcbLib`)**.

Altium Designer treats library files as proprietary OLE Compound Binary files. When multiple electrical engineers and coworkers work on shared libraries in Git, SVN, or network shares, Altium provides no native merge utility, resulting in corrupted binary conflict markers or lost components. **Altium Library Merger** solves this cleanly.

---

## Features

- **Auto-Detects Library Format**: Automatically inspects and handles both `.SchLib` (Schematic symbols) and `.PcbLib` (PCB footprints).
- **Component-Level & Property-Level Diffing**:
  - Detects **Added in Mine (A)**, **Added in Theirs (B)**, **Identical** parts, and **Conflicting** parts.
  - Deep inspection of differing parameters (Value, Manufacturer Part Number, Description, Designators, Heights, Pins, Pads, and 3D models).
- **Flexible Conflict Resolution**:
  - **Keep Mine (A)**: Preserve your version of the component.
  - **Keep Theirs (B)**: Accept coworker's updated version.
  - **Keep Both**: Retain your part and import coworker's part under an auto-renamed or custom identifier (e.g. `RES_0402_10K_Coworker`).
  - **1-Click Batch Actions**: Resolve all conflicts in bulk with one click.
- **Full Binary & Model Fidelity**:
  - Preserves all graphical primitives (pins, lines, rectangles, pads, tracks, vias).
  - Preserves Altium 3D STEP models (`Library/Models/`), deduplicating and maintaining model GUID associations.
  - Correctly rebuilds OLE directory trees, `FileHeader`, and `SectionKeys` lookup tables for component names longer than 31 characters.
- **Multiple Interfaces**:
  - **Interactive Web App**: Modern, responsive dark-themed GUI with drag-and-drop dual upload zones and side-by-side comparison modal dialogs.
  - **CLI Tool**: Scriptable command-line interface for terminal workflows, automated scripts, and CI/CD pipelines.
  - **Seamless Git Merge Driver**: Integrates with `git merge` and `git pull` so coworkers can merge branches without binary conflict errors.

---

## Quick Start

### 1. Requirements & Installation

- Python 3.10+ on Windows (or Linux/macOS with standard Python).
- Install dependencies:

```bash
pip install -r requirements.txt
```

### 2. Launching the Web App

Double-click **`run_app.bat`** on Windows, or run:

```bash
python run_app.py
```

This will automatically launch the local web server at `http://127.0.0.1:5000` and open your default browser:

1. **Drop Library A** (Mine / Current Branch) into the left dropzone.
2. **Drop Library B** (Theirs / Coworker Branch) into the right dropzone.
3. Click **"Compare & Inspect Libraries"** (or click *"Load Sample Demo"* to test immediately).
4. Review the diff dashboard, filter by conflicts, and choose resolution decisions per item or via batch buttons.
5. Click **"Generate Merged Library"** to download the unified `.SchLib` or `.PcbLib`!

---

## Command-Line Interface (CLI)

The CLI tool `altium_merge_cli.py` provides terminal commands for diffing and merging.

### Compare two libraries:
```bash
python altium_merge_cli.py diff CompanyLib_v1.SchLib CompanyLib_v2.SchLib
```

Options:
- `--summary-only`: Print only high-level summary counts.
- `--hide-identical`: Show only additions and conflicts.

### Merge two libraries:
```bash
# Prefer your version on any conflicts:
python altium_merge_cli.py merge LibA.SchLib LibB.SchLib -o Lib_Merged.SchLib --prefer-mine

# Or prefer coworker's version:
python altium_merge_cli.py merge LibA.SchLib LibB.SchLib -o Lib_Merged.SchLib --prefer-theirs

# Or keep both copies of conflicting parts (appends _B suffix):
python altium_merge_cli.py merge LibA.SchLib LibB.SchLib -o Lib_Merged.SchLib --keep-both

# Or interactive resolution in terminal:
python altium_merge_cli.py merge LibA.SchLib LibB.SchLib -o Lib_Merged.SchLib -i
```

---

## Git Merge Driver Integration

To eliminate binary Git conflicts forever across your entire engineering team:

1. Run this command inside your Git repository:

```bash
python altium_merge_cli.py git-install
```

### What this does:
1. Registers the merge driver in your Git config (`merge.altiumlib.driver`).
2. Adds rules to your repository's `.gitattributes`:
   ```gitattributes
   *.SchLib merge=altiumlib
   *.schlib merge=altiumlib
   *.PcbLib merge=altiumlib
   *.pcblib merge=altiumlib
   ```
3. From then on, when coworkers run `git merge` or `git pull`, Git will run the merger automatically:
   - Additions from both branches are cleanly unified.
   - Identical parts are untouched.
   - Any conflicting parts are safely preserved without binary file corruption!

---

## Automated Tests

Run the test suite to verify binary integrity:

```bash
python -m unittest tests/test_merger.py
```

All 5 test suites verify parsing, diffing, divergent branch synthesis, conflict resolution, 3D model preservation, and Flask API endpoints.
