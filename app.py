"""Flask Web Application for Altium Library Merger
Provides a modern visual interface for comparing, resolving conflicts, and merging
Altium Schematic (.SchLib) and PCB Footprint (.PcbLib) libraries.
"""

import os
import sys
import uuid
import logging
from typing import Dict, Any
from flask import Flask, render_template, request, jsonify, send_file

from altium_engine.diff_engine import LibraryDiffEngine
from altium_engine.schlib_merger import SchLibMerger
from altium_engine.pcblib_merger import PcbLibMerger

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Temporary upload and output folders
UPLOAD_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), "temp_work", "uploads"))
OUTPUT_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), "temp_work", "merged"))
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# In-memory session store: session_id -> {file_a, file_b, type, diff_data}
SESSIONS: Dict[str, Dict[str, Any]] = {}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "app": "Altium Library Merger", "version": "1.0.0"})


@app.route("/api/compare", methods=["POST"])
def api_compare():
    """Upload two library files and return side-by-side diff."""
    if "file_a" not in request.files or "file_b" not in request.files:
        return jsonify({"error": "Both 'file_a' (Mine) and 'file_b' (Theirs) must be uploaded."}), 400

    file_a = request.files["file_a"]
    file_b = request.files["file_b"]

    if not file_a.filename or not file_b.filename:
        return jsonify({"error": "Empty filename provided."}), 400

    session_id = str(uuid.uuid4())
    session_dir = os.path.join(UPLOAD_FOLDER, session_id)
    dir_a = os.path.join(session_dir, "lib_a")
    dir_b = os.path.join(session_dir, "lib_b")
    os.makedirs(dir_a, exist_ok=True)
    os.makedirs(dir_b, exist_ok=True)

    path_a = os.path.join(dir_a, file_a.filename)
    path_b = os.path.join(dir_b, file_b.filename)

    file_a.save(path_a)
    file_b.save(path_b)

    try:
        diff_data = LibraryDiffEngine.compare(path_a, path_b)
        SESSIONS[session_id] = {
            "session_id": session_id,
            "path_a": path_a,
            "path_b": path_b,
            "filename_a": file_a.filename,
            "filename_b": file_b.filename,
            "library_type": diff_data["library_type"],
            "diff_data": diff_data,
        }
        return jsonify({
            "session_id": session_id,
            "diff": diff_data,
        })
    except Exception as e:
        logger.exception("Comparison error")
        return jsonify({"error": str(e)}), 400


@app.route("/api/compare-samples", methods=["POST"])
def api_compare_samples():
    """Load and compare bundled demo sample files for immediate evaluation."""
    sample_type = request.json.get("type", "SchLib") if request.is_json else "SchLib"
    test_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_data"))

    if sample_type == "PcbLib":
        base_sample = os.path.join(test_dir, "sample.PcbLib")
        ext = ".PcbLib"
    else:
        base_sample = os.path.join(test_dir, "sample.SchLib")
        ext = ".SchLib"

    if not os.path.exists(base_sample):
        return jsonify({"error": f"Sample file '{base_sample}' not found."}), 404

    session_id = str(uuid.uuid4())
    session_dir = os.path.join(UPLOAD_FOLDER, session_id)
    os.makedirs(session_dir, exist_ok=True)

    path_a = os.path.join(session_dir, f"Coworker_A_Library{ext}")
    path_b = os.path.join(session_dir, f"Coworker_B_Library{ext}")

    # Synthesize two realistic coworker branches from base sample
    if sample_type == "SchLib":
        from altium_engine.cfb_handler import CompoundFileTree, parse_altium_params, build_altium_params

        tree_orig = CompoundFileTree.load_from_file(base_sample)
        # Create Branch A: remove component #5
        tree_a = CompoundFileTree.load_from_file(base_sample)
        # Create Branch B: remove component #10
        tree_b = CompoundFileTree.load_from_file(base_sample)

        # In Branch B, also slightly modify component #0's description to produce a realistic conflict!
        raw_fh = tree_b.get_stream(("FileHeader",))
        if raw_fh:
            pairs, _ = parse_altium_params(raw_fh, has_length_prefix=True)
            modified_pairs = []
            for k, v in pairs:
                if k.lower() == "compdescr0":
                    modified_pairs.append((k, (v or "Generic Part") + " [Updated in Branch B: 1% Tolerance]"))
                else:
                    modified_pairs.append((k, v))
            tree_b.set_stream(("FileHeader",), build_altium_params(modified_pairs, has_length_prefix=True))

        tree_a.save_to_file(path_a)
        tree_b.save_to_file(path_b)
    else:
        # PcbLib demo branches
        import shutil
        from altium_engine.cfb_handler import CompoundFileTree, parse_altium_params, build_altium_params
        from altium_engine.pcblib_merger import PcbLibMerger

        shutil.copyfile(base_sample, path_a)

        # In Branch B, update first footprint's description to simulate a realistic conflict
        tree_b = CompoundFileTree.load_from_file(base_sample)
        fps_b, _ = PcbLibMerger.extract_footprints(tree_b)
        if fps_b:
            first_fp = list(fps_b.keys())[0]
            sec_key = fps_b[first_fp].section_key
            raw_p = tree_b.get_stream((sec_key, "Parameters"))
            if raw_p:
                pairs, _ = parse_altium_params(raw_p, has_length_prefix=True)
                new_pairs = [(k, v) for k, v in pairs if k.lower() != "description"]
                new_pairs.append(("DESCRIPTION", "High-Density Relay Footprint [Branch B Revision]"))
                tree_b.set_stream((sec_key, "Parameters"), build_altium_params(new_pairs, has_length_prefix=True))
            tree_b.save_to_file(path_b)
        else:
            shutil.copyfile(base_sample, path_b)

    try:
        diff_data = LibraryDiffEngine.compare(path_a, path_b)
        SESSIONS[session_id] = {
            "session_id": session_id,
            "path_a": path_a,
            "path_b": path_b,
            "filename_a": os.path.basename(path_a),
            "filename_b": os.path.basename(path_b),
            "library_type": diff_data["library_type"],
            "diff_data": diff_data,
        }
        return jsonify({
            "session_id": session_id,
            "diff": diff_data,
        })
    except Exception as e:
        logger.exception("Sample comparison error")
        return jsonify({"error": str(e)}), 400


@app.route("/api/merge", methods=["POST"])
def api_merge():
    """Execute merge based on user conflict resolutions and produce merged file."""
    data = request.get_json() or {}
    session_id = data.get("session_id")
    resolutions = data.get("resolutions", {})
    default_action = data.get("default_action", "prefer_a")
    output_filename = data.get("output_filename")

    if not session_id or session_id not in SESSIONS:
        return jsonify({"error": "Invalid or expired session. Please re-upload libraries."}), 400

    session = SESSIONS[session_id]
    path_a = session["path_a"]
    path_b = session["path_b"]
    lib_type = session["library_type"]

    if not output_filename:
        base_name = os.path.splitext(session["filename_a"])[0]
        ext = ".SchLib" if lib_type == "SchLib" else ".PcbLib"
        output_filename = f"{base_name}_Merged{ext}"

    output_path = os.path.join(OUTPUT_FOLDER, f"{session_id}_{output_filename}")

    try:
        if lib_type == "SchLib":
            res = SchLibMerger.merge(
                lib_a_path=path_a,
                lib_b_path=path_b,
                output_path=output_path,
                resolutions=resolutions,
                default_conflict=default_action,
            )
        elif lib_type == "PcbLib":
            res = PcbLibMerger.merge(
                lib_a_path=path_a,
                lib_b_path=path_b,
                output_path=output_path,
                resolutions=resolutions,
                default_conflict=default_action,
            )
        else:
            return jsonify({"error": f"Unsupported library type {lib_type}"}), 400

        res["download_url"] = f"/api/download/{session_id}_{output_filename}?name={output_filename}"
        res["filename"] = output_filename
        return jsonify(res)
    except Exception as e:
        logger.exception("Merge error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/download/<file_id>")
def api_download(file_id):
    """Download the generated merged library file."""
    target_path = os.path.join(OUTPUT_FOLDER, file_id)
    if not os.path.exists(target_path):
        return jsonify({"error": "File not found or expired."}), 404

    display_name = request.args.get("name") or file_id.split("_", 1)[-1]
    return send_file(
        target_path,
        as_attachment=True,
        download_name=display_name,
        mimetype="application/octet-stream",
    )


def run_server(host="127.0.0.1", port=5000, debug=False):
    print(f"Altium Library Merger Web App starting at http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    run_server(host="127.0.0.1", port=port, debug=True)
