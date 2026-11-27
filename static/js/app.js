/**
 * Altium Library Merger - Frontend Application Logic
 */

document.addEventListener("DOMContentLoaded", () => {
    // State
    const state = {
        fileA: null,
        fileB: null,
        sessionId: null,
        diffData: null,
        activeFilter: "all",
        searchQuery: "",
        resolutions: {}, // name -> { action: 'keep_a'|'keep_b'|'keep_both', new_name: '...' }
        activeModalItem: null,
    };

    // DOM Elements
    const dropzoneA = document.getElementById("dropzoneA");
    const dropzoneB = document.getElementById("dropzoneB");
    const fileInputA = document.getElementById("fileInputA");
    const fileInputB = document.getElementById("fileInputB");
    const chipA = document.getElementById("chipA");
    const chipB = document.getElementById("chipB");
    const titleA = document.getElementById("titleA");
    const titleB = document.getElementById("titleB");
    const hintA = document.getElementById("hintA");
    const hintB = document.getElementById("hintB");
    const btnCompare = document.getElementById("btnCompare");
    const btnSwap = document.getElementById("btnSwap");

    const diffSection = document.getElementById("diffSection");
    const mergeSection = document.getElementById("mergeSection");
    const diffSubtitle = document.getElementById("diffSubtitle");
    const libTypeTag = document.getElementById("libTypeTag");

    // Metrics
    const valTotal = document.getElementById("valTotal");
    const valAddedA = document.getElementById("valAddedA");
    const valAddedB = document.getElementById("valAddedB");
    const valConflicts = document.getElementById("valConflicts");
    const valIdentical = document.getElementById("valIdentical");

    // Tabs
    const filterTabs = document.querySelectorAll(".filter-tab");
    const searchInput = document.getElementById("searchInput");
    const componentsTbody = document.getElementById("componentsTbody");
    const emptyState = document.getElementById("emptyState");

    // Batch buttons
    const btnBatchKeepA = document.getElementById("btnBatchKeepA");
    const btnBatchKeepB = document.getElementById("btnBatchKeepB");
    const btnBatchKeepBoth = document.getElementById("btnBatchKeepBoth");
    const btnBatchReset = document.getElementById("btnBatchReset");

    // Merge & Download
    const outputFilename = document.getElementById("outputFilename");
    const btnExecuteMerge = document.getElementById("btnExecuteMerge");
    const downloadBanner = document.getElementById("downloadBanner");
    const downloadMetrics = document.getElementById("downloadMetrics");
    const btnDownload = document.getElementById("btnDownload");

    // Modals
    const diffModal = document.getElementById("diffModal");
    const btnModalClose = document.getElementById("btnModalClose");
    const modalTitle = document.getElementById("modalTitle");
    const modalBadge = document.getElementById("modalBadge");
    const modalFileA = document.getElementById("modalFileA");
    const modalFileB = document.getElementById("modalFileB");
    const propsColA = document.getElementById("propsColA");
    const propsColB = document.getElementById("propsColB");
    const modalParamsTbody = document.getElementById("modalParamsTbody");
    const modalBtnKeepA = document.getElementById("modalBtnKeepA");
    const modalBtnKeepB = document.getElementById("modalBtnKeepB");
    const modalBtnKeepBoth = document.getElementById("modalBtnKeepBoth");

    const gitModal = document.getElementById("gitModal");
    const btnGitGuide = document.getElementById("btnGitGuide");
    const btnGitModalClose = document.getElementById("btnGitModalClose");
    const btnGitModalDone = document.getElementById("btnGitModalDone");

    // Demo pills
    const pillDemoSch = document.getElementById("pillDemoSch");
    const pillDemoPcb = document.getElementById("pillDemoPcb");
    const btnSampleDemo = document.getElementById("btnSampleDemo");

    let currentDemoType = "SchLib";

    // Setup Dropzone Drag-and-Drop
    function setupDropzone(zone, input, chip, titleEl, hintEl, targetKey) {
        zone.addEventListener("click", () => input.click());

        zone.addEventListener("dragover", (e) => {
            e.preventDefault();
            zone.classList.add("dragover");
        });

        zone.addEventListener("dragleave", () => {
            zone.classList.remove("dragover");
        });

        zone.addEventListener("drop", (e) => {
            e.preventDefault();
            zone.classList.remove("dragover");
            if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                handleFileSelect(e.dataTransfer.files[0], targetKey, chip, titleEl, hintEl);
            }
        });

        input.addEventListener("change", (e) => {
            if (e.target.files && e.target.files.length > 0) {
                handleFileSelect(e.target.files[0], targetKey, chip, titleEl, hintEl);
            }
        });
    }

    function formatFileSize(bytes) {
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
        return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    }

    function handleFileSelect(file, targetKey, chip, titleEl, hintEl) {
        state[targetKey] = file;
        chip.querySelector(".chip-name").textContent = file.name;
        chip.querySelector(".chip-size").textContent = formatFileSize(file.size);
        chip.classList.remove("hidden");
        titleEl.textContent = file.name;
        hintEl.textContent = `Ready (${formatFileSize(file.size)})`;

        if (state.fileA && state.fileB) {
            btnCompare.disabled = false;
        }
    }

    setupDropzone(dropzoneA, fileInputA, chipA, titleA, hintA, "fileA");
    setupDropzone(dropzoneB, fileInputB, chipB, titleB, hintB, "fileB");

    function swapFiles() {
        if (!state.fileA && !state.fileB) return;

        const tempFile = state.fileA;
        state.fileA = state.fileB;
        state.fileB = tempFile;

        // Update Dropzone A UI
        if (state.fileA) {
            chipA.querySelector(".chip-name").textContent = state.fileA.name;
            chipA.querySelector(".chip-size").textContent = formatFileSize(state.fileA.size);
            chipA.classList.remove("hidden");
            titleA.textContent = state.fileA.name;
            hintA.textContent = `Ready (${formatFileSize(state.fileA.size)})`;
        } else {
            chipA.classList.add("hidden");
            titleA.textContent = "Drag & Drop Library A";
            hintA.textContent = "or click to browse (.SchLib / .PcbLib)";
        }

        // Update Dropzone B UI
        if (state.fileB) {
            chipB.querySelector(".chip-name").textContent = state.fileB.name;
            chipB.querySelector(".chip-size").textContent = formatFileSize(state.fileB.size);
            chipB.classList.remove("hidden");
            titleB.textContent = state.fileB.name;
            hintB.textContent = `Ready (${formatFileSize(state.fileB.size)})`;
        } else {
            chipB.classList.add("hidden");
            titleB.textContent = "Drag & Drop Library B";
            hintB.textContent = "or click to browse (.SchLib / .PcbLib)";
        }

        btnCompare.disabled = !(state.fileA && state.fileB);

        // If both files are loaded, automatically trigger comparison
        if (state.fileA && state.fileB) {
            btnCompare.click();
        }
    }

    if (btnSwap) {
        btnSwap.addEventListener("click", swapFiles);
    }

    // Compare Uploaded Files
    btnCompare.addEventListener("click", async () => {
        if (!state.fileA || !state.fileB) return;

        btnCompare.disabled = true;
        btnCompare.innerHTML = `<span class="spinner"></span> Analyzing Libraries...`;

        const formData = new FormData();
        formData.append("file_a", state.fileA);
        formData.append("file_b", state.fileB);

        try {
            const resp = await fetch("/api/compare", {
                method: "POST",
                body: formData,
            });
            const data = await resp.json();
            if (!resp.ok) {
                alert(`Error: ${data.error || "Failed to compare libraries."}`);
                return;
            }
            renderDiffResults(data.session_id, data.diff);
        } catch (err) {
            alert(`Network error: ${err.message}`);
        } finally {
            btnCompare.disabled = false;
            btnCompare.innerHTML = `Compare & Inspect Libraries`;
        }
    });

    // Sample Demo Loader
    async function loadSampleDemo(type) {
        const btn = btnSampleDemo;
        btn.disabled = true;
        btn.textContent = "Loading Demo...";

        try {
            const resp = await fetch("/api/compare-samples", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ type: type }),
            });
            const data = await resp.json();
            if (!resp.ok) {
                alert(`Error: ${data.error || "Failed to load sample demo."}`);
                return;
            }
            renderDiffResults(data.session_id, data.diff);
        } catch (err) {
            alert(`Failed to load demo: ${err.message}`);
        } finally {
            btn.disabled = false;
            btn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Load Sample Demo`;
        }
    }

    pillDemoSch.addEventListener("click", () => {
        pillDemoSch.classList.add("active");
        pillDemoPcb.classList.remove("active");
        currentDemoType = "SchLib";
        loadSampleDemo("SchLib");
    });

    pillDemoPcb.addEventListener("click", () => {
        pillDemoPcb.classList.add("active");
        pillDemoSch.classList.remove("active");
        currentDemoType = "PcbLib";
        loadSampleDemo("PcbLib");
    });

    btnSampleDemo.addEventListener("click", () => {
        loadSampleDemo(currentDemoType);
    });

    // Render Diff Results Dashboard
    function renderDiffResults(sessionId, diff) {
        state.sessionId = sessionId;
        state.diffData = diff;
        state.resolutions = {};

        // Default resolutions for all conflicts: keep_a
        diff.items.forEach((item) => {
            if (item.status === "conflict") {
                state.resolutions[item.name] = { action: "keep_a" };
            }
        });

        // Unhide sections
        diffSection.classList.remove("hidden");
        mergeSection.classList.remove("hidden");
        downloadBanner.classList.add("hidden");

        // Set subtitles & type
        diffSubtitle.textContent = `Comparing '${diff.file_a}' (Mine) vs '${diff.file_b}' (Theirs)`;
        libTypeTag.textContent = diff.library_type === "SchLib" ? "Schematic Library (.SchLib)" : "PCB Footprint Library (.PcbLib)";

        // Set default output filename
        const baseName = diff.file_a.replace(/\.(schlib|pcblib)$/i, "");
        const ext = diff.library_type === "SchLib" ? ".SchLib" : ".PcbLib";
        outputFilename.value = `${baseName}_Merged${ext}`;

        // Metrics
        valTotal.textContent = diff.counts.total;
        valAddedA.textContent = diff.counts.added_a;
        valAddedB.textContent = diff.counts.added_b;
        valConflicts.textContent = diff.counts.conflicts;
        valIdentical.textContent = diff.counts.identical;

        // Tab counts
        document.getElementById("tabCountAll").textContent = diff.counts.total;
        document.getElementById("tabCountConflict").textContent = diff.counts.conflicts;
        document.getElementById("tabCountAddedA").textContent = diff.counts.added_a;
        document.getElementById("tabCountAddedB").textContent = diff.counts.added_b;
        document.getElementById("tabCountIdentical").textContent = diff.counts.identical;

        renderComponentRows();

        // Smooth scroll to diff section
        diffSection.scrollIntoView({ behavior: "smooth" });
    }

    // Filter Tabs
    filterTabs.forEach((tab) => {
        tab.addEventListener("click", () => {
            filterTabs.forEach((t) => t.classList.remove("active"));
            tab.classList.add("active");
            state.activeFilter = tab.dataset.filter;
            renderComponentRows();
        });
    });

    // Search Input
    searchInput.addEventListener("input", (e) => {
        state.searchQuery = e.target.value.toLowerCase().trim();
        renderComponentRows();
    });

    // Batch Actions
    btnBatchKeepA.addEventListener("click", () => {
        if (!state.diffData) return;
        state.diffData.items.forEach((item) => {
            if (item.status === "conflict") {
                state.resolutions[item.name] = { action: "keep_a" };
            }
        });
        renderComponentRows();
    });

    btnBatchKeepB.addEventListener("click", () => {
        if (!state.diffData) return;
        state.diffData.items.forEach((item) => {
            if (item.status === "conflict") {
                state.resolutions[item.name] = { action: "keep_b" };
            }
        });
        renderComponentRows();
    });

    btnBatchKeepBoth.addEventListener("click", () => {
        if (!state.diffData) return;
        state.diffData.items.forEach((item) => {
            if (item.status === "conflict") {
                state.resolutions[item.name] = {
                    action: "keep_both",
                    new_name: `${item.name}_B`,
                };
            }
        });
        renderComponentRows();
    });

    btnBatchReset.addEventListener("click", () => {
        if (!state.diffData) return;
        state.diffData.items.forEach((item) => {
            if (item.status === "conflict") {
                state.resolutions[item.name] = { action: "keep_a" };
            }
        });
        renderComponentRows();
    });

    // Render Table Rows
    function renderComponentRows() {
        if (!state.diffData) return;
        componentsTbody.innerHTML = "";

        const items = state.diffData.items.filter((item) => {
            // Tab filter
            if (state.activeFilter !== "all" && item.status !== state.activeFilter) {
                return false;
            }
            // Search query
            if (state.searchQuery) {
                const name = item.name.toLowerCase();
                const descA = (item.item_a && item.item_a.description ? item.item_a.description : "").toLowerCase();
                const descB = (item.item_b && item.item_b.description ? item.item_b.description : "").toLowerCase();
                if (!name.includes(state.searchQuery) && !descA.includes(state.searchQuery) && !descB.includes(state.searchQuery)) {
                    return false;
                }
            }
            return true;
        });

        if (items.length === 0) {
            emptyState.classList.remove("hidden");
            return;
        } else {
            emptyState.classList.add("hidden");
        }

        items.forEach((item) => {
            const tr = document.createElement("tr");

            // Status Badge
            const tdStatus = document.createElement("td");
            let badgeClass = "badge-identical";
            let badgeText = "Identical";
            if (item.status === "conflict") {
                badgeClass = "badge-conflict";
                badgeText = "Conflict";
            } else if (item.status === "added_a") {
                badgeClass = "badge-added-a";
                badgeText = "Only in A";
            } else if (item.status === "added_b") {
                badgeClass = "badge-added-b";
                badgeText = "Only in B";
            }
            tdStatus.innerHTML = `<span class="badge ${badgeClass}">${badgeText}</span>`;
            tr.appendChild(tdStatus);

            // Component Name
            const tdName = document.createElement("td");
            const activeItem = item.item_a || item.item_b;
            const desc = activeItem ? activeItem.description || "" : "";
            tdName.innerHTML = `
                <div class="comp-name">${escapeHtml(item.name)}</div>
                <div class="comp-desc">${escapeHtml(desc)}</div>
            `;
            tr.appendChild(tdName);

            // Details & Properties
            const tdDetails = document.createElement("td");
            const metaTags = [];
            if (activeItem) {
                if (activeItem.pin_count !== undefined) metaTags.push(`${activeItem.pin_count} pins`);
                if (activeItem.pad_count !== undefined) metaTags.push(`${activeItem.pad_count} pads`);
                if (activeItem.designator) metaTags.push(`Des: ${activeItem.designator}`);
                if (activeItem.has_3d_model) metaTags.push(`3D Model`);
                if (activeItem.height && activeItem.height !== "0") metaTags.push(`H: ${activeItem.height}`);
            }

            if (item.status === "conflict" && item.property_diffs && item.property_diffs.length > 0) {
                const diffSummary = item.property_diffs.slice(0, 2).map((d) => `${d.property}`).join(", ");
                const moreCount = item.property_diffs.length > 2 ? ` +${item.property_diffs.length - 2} more` : "";
                tdDetails.innerHTML = `
                    <div style="color: var(--accent-red); font-size: 0.8rem; font-weight: 600;">Differs: ${escapeHtml(diffSummary)}${moreCount}</div>
                    <div class="comp-meta-tags">${metaTags.map((t) => `<span class="meta-pill">${t}</span>`).join("")}</div>
                `;
            } else if (item.status === "conflict") {
                tdDetails.innerHTML = `
                    <div style="color: var(--accent-orange); font-size: 0.8rem; font-weight: 600;">⚠ Conflict detected (click Compare to see details)</div>
                    <div class="comp-meta-tags">${metaTags.map((t) => `<span class="meta-pill">${t}</span>`).join("")}</div>
                `;
            } else {
                tdDetails.innerHTML = `
                    <div class="comp-meta-tags">${metaTags.map((t) => `<span class="meta-pill">${t}</span>`).join("")}</div>
                `;
            }
            tr.appendChild(tdDetails);

            // Merge Resolution Column
            const tdRes = document.createElement("td");
            if (item.status === "conflict") {
                const res = state.resolutions[item.name] || { action: "keep_a" };
                const isBoth = res.action === "keep_both";
                const newName = res.new_name || `${item.name}_B`;

                tdRes.innerHTML = `
                    <div class="res-control-group">
                        <div class="res-btn-group">
                            <button class="res-btn act-a ${res.action === 'keep_a' ? 'active' : ''}" data-act="keep_a" data-name="${escapeHtml(item.name)}">Keep A</button>
                            <button class="res-btn act-b ${res.action === 'keep_b' ? 'active' : ''}" data-act="keep_b" data-name="${escapeHtml(item.name)}">Keep B</button>
                            <button class="res-btn act-both ${res.action === 'keep_both' ? 'active' : ''}" data-act="keep_both" data-name="${escapeHtml(item.name)}">Keep Both</button>
                        </div>
                        ${
                            isBoth
                                ? `<div class="rename-input-wrap">
                                      <span class="text-xs text-muted">Rename B:</span>
                                      <input type="text" class="rename-input" data-name="${escapeHtml(item.name)}" value="${escapeHtml(newName)}">
                                   </div>`
                                : ""
                        }
                    </div>
                `;
            } else if (item.status === "added_a") {
                tdRes.innerHTML = `<span class="text-xs text-muted">Include from [A] (Mine)</span>`;
            } else if (item.status === "added_b") {
                tdRes.innerHTML = `<span class="text-xs text-muted">Merge in from [B] (Theirs)</span>`;
            } else {
                tdRes.innerHTML = `<span class="text-xs text-muted">Retained (Identical)</span>`;
            }
            tr.appendChild(tdRes);

            // Action / Inspect Column
            const tdAction = document.createElement("td");
            tdAction.style.textAlign = "right";
            if (item.item_a && item.item_b) {
                const btnInspect = document.createElement("button");
                btnInspect.className = "btn btn-outline btn-xs";
                btnInspect.textContent = "Compare";
                btnInspect.addEventListener("click", () => openDiffModal(item));
                tdAction.appendChild(btnInspect);
            }
            tr.appendChild(tdAction);

            componentsTbody.appendChild(tr);
        });

        // Attach event listeners for resolution toggle buttons
        componentsTbody.querySelectorAll(".res-btn").forEach((btn) => {
            btn.addEventListener("click", (e) => {
                const name = e.target.dataset.name;
                const act = e.target.dataset.act;
                if (!state.resolutions[name]) state.resolutions[name] = {};
                state.resolutions[name].action = act;
                if (act === "keep_both" && !state.resolutions[name].new_name) {
                    state.resolutions[name].new_name = `${name}_B`;
                }
                renderComponentRows();
            });
        });

        // Attach event listeners for rename inputs
        componentsTbody.querySelectorAll(".rename-input").forEach((inp) => {
            inp.addEventListener("input", (e) => {
                const name = e.target.dataset.name;
                if (!state.resolutions[name]) state.resolutions[name] = {};
                state.resolutions[name].new_name = e.target.value.trim();
            });
        });
    }

    // Modal Side-by-Side Comparison
    function openDiffModal(item) {
        state.activeModalItem = item;
        modalTitle.textContent = item.name;

        if (item.status === "conflict") {
            modalBadge.className = "badge badge-conflict";
            modalBadge.textContent = "Conflict";
        } else {
            modalBadge.className = "badge badge-identical";
            modalBadge.textContent = "Identical";
        }

        modalFileA.textContent = state.diffData.file_a;
        modalFileB.textContent = state.diffData.file_b;

        // Render Column A properties
        renderPropsCol(propsColA, item.item_a);
        // Render Column B properties
        renderPropsCol(propsColB, item.item_b);

        // Parameters Table Diff
        modalParamsTbody.innerHTML = "";
        const paramsA = (item.item_a && item.item_a.parameters) || {};
        const paramsB = (item.item_b && item.item_b.parameters) || {};
        const allKeys = Array.from(new Set([...Object.keys(paramsA), ...Object.keys(paramsB)])).sort();

        if (allKeys.length === 0) {
            modalParamsTbody.innerHTML = `<tr><td colspan="3" class="text-muted" style="text-align: center;">No custom parameters recorded.</td></tr>`;
        } else {
            allKeys.forEach((key) => {
                const valA = paramsA[key];
                const valB = paramsB[key];
                const isDiff = valA !== valB;
                const tr = document.createElement("tr");
                if (isDiff) tr.className = "diff-highlight";
                tr.innerHTML = `
                    <td><strong>${escapeHtml(key)}</strong></td>
                    <td>${valA !== undefined ? escapeHtml(valA) : `<span class="text-muted text-xs">(Not in A)</span>`}</td>
                    <td>${valB !== undefined ? escapeHtml(valB) : `<span class="text-muted text-xs">(Not in B)</span>`}</td>
                `;
                modalParamsTbody.appendChild(tr);
            });
        }

        // Add property diffs to the parameters table for visibility
        if (item.property_diffs && item.property_diffs.length > 0) {
            const headerRow = document.createElement("tr");
            headerRow.innerHTML = `<td colspan="3" style="padding-top: 1rem;"><strong>Detected Differences & Extracted Stream Details:</strong></td>`;
            modalParamsTbody.appendChild(headerRow);

            item.property_diffs.forEach((diff) => {
                const tr = document.createElement("tr");
                tr.className = diff.type === "stream_detail" ? "diff-highlight diff-stream-item" : "diff-highlight";

                let detailsHtml = "";
                if (diff.details) {
                    const parts = String(diff.details).split(" | ").filter(Boolean);
                    if (parts.length > 1) {
                        detailsHtml = `<ul class="diff-details-list">${parts.map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul>`;
                    } else {
                        detailsHtml = `<div class="text-xs text-muted diff-detail-single">${escapeHtml(diff.details)}</div>`;
                    }
                }

                const badgeHtml = diff.type === "stream_detail" ? `<span class="diff-sub-tag">Decoded</span> ` : "";
                tr.innerHTML = `
                    <td>${badgeHtml}<strong>${escapeHtml(diff.property)}</strong>${detailsHtml}</td>
                    <td>${escapeHtml(String(diff.val_a))}</td>
                    <td>${escapeHtml(String(diff.val_b))}</td>
                `;
                modalParamsTbody.appendChild(tr);
            });
        }

        diffModal.showModal();
    }

    function renderPropsCol(container, item) {
        if (!item) {
            container.innerHTML = `<div class="text-muted text-xs" style="padding: 1rem 0;">Component does not exist in this library.</div>`;
            return;
        }

        const rows = [
            { label: "Description", val: item.description || "(None)" },
            { label: "Designator", val: item.designator || item.height || "(None)" },
            { label: "Pins Count", val: item.pin_count !== undefined ? item.pin_count : "-" },
            { label: "Pads Count", val: item.pad_count !== undefined ? item.pad_count : "-" },
            { label: "Primitives", val: item.primitive_count || "-" },
            { label: "3D Model", val: item.has_3d_model ? "Yes (Embedded)" : "No" },
            { label: "Streams", val: (item.stream_names || []).join(", ") },
        ];

        container.innerHTML = rows
            .map(
                (r) => `
            <div class="prop-row">
                <span class="prop-key">${escapeHtml(r.label)}</span>
                <span class="prop-val" title="${escapeHtml(String(r.val))}">${escapeHtml(String(r.val))}</span>
            </div>
        `
            )
            .join("");
    }

    btnModalClose.addEventListener("click", () => diffModal.close());
    modalBtnKeepA.addEventListener("click", () => {
        if (state.activeModalItem) {
            state.resolutions[state.activeModalItem.name] = { action: "keep_a" };
            renderComponentRows();
            diffModal.close();
        }
    });
    modalBtnKeepB.addEventListener("click", () => {
        if (state.activeModalItem) {
            state.resolutions[state.activeModalItem.name] = { action: "keep_b" };
            renderComponentRows();
            diffModal.close();
        }
    });
    modalBtnKeepBoth.addEventListener("click", () => {
        if (state.activeModalItem) {
            state.resolutions[state.activeModalItem.name] = {
                action: "keep_both",
                new_name: `${state.activeModalItem.name}_B`,
            };
            renderComponentRows();
            diffModal.close();
        }
    });

    // Git Modal
    btnGitGuide.addEventListener("click", () => gitModal.showModal());
    btnGitModalClose.addEventListener("click", () => gitModal.close());
    btnGitModalDone.addEventListener("click", () => gitModal.close());

    // Execute Merge
    btnExecuteMerge.addEventListener("click", async () => {
        if (!state.sessionId) return;

        btnExecuteMerge.disabled = true;
        btnExecuteMerge.innerHTML = `<span class="spinner"></span> Merging & Validating Altium Library...`;

        const payload = {
            session_id: state.sessionId,
            resolutions: state.resolutions,
            output_filename: outputFilename.value.trim() || undefined,
            default_action: "prefer_a",
        };

        try {
            const resp = await fetch("/api/merge", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const data = await resp.json();
            if (!resp.ok) {
                alert(`Merge Failed: ${data.error || "Unknown server error."}`);
                return;
            }

            // Success!
            downloadBanner.classList.remove("hidden");
            downloadMetrics.textContent = `Total components: ${data.total_components} | Added from A: ${data.added_from_a} | Added from B: ${data.added_from_b} | Conflicts resolved: ${data.conflicts_resolved}`;
            btnDownload.href = data.download_url;
            btnDownload.download = data.filename;

            // Trigger download automatically
            btnDownload.click();

            downloadBanner.scrollIntoView({ behavior: "smooth" });
        } catch (err) {
            alert(`Network error during merge: ${err.message}`);
        } finally {
            btnExecuteMerge.disabled = false;
            btnExecuteMerge.innerHTML = `Generate Merged Library`;
        }
    });

    function escapeHtml(text) {
        if (!text) return "";
        return String(text)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }
});
