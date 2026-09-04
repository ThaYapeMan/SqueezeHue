// ---------------------------------------------------------------------------
// Entertainment Area dropdown
// ---------------------------------------------------------------------------

const bridgeSelect = document.getElementById("bridge-select");
const areaSelect = document.getElementById("area-select");
const areaNameHidden = document.getElementById("area-name-hidden");

// Load areas for the currently selected bridge.  Pass selectAreaId /
// selectAreaName to pre-select a specific area (used when editing a profile).
async function loadAreas(selectAreaId = null, selectAreaName = null) {
  if (!bridgeSelect || !areaSelect) return;
  const bridgeId = bridgeSelect.value;
  areaSelect.innerHTML = "<option>Loading…</option>";
  try {
    const res = await fetch(`/bridges/${bridgeId}/areas`);
    const areas = await res.json();
    areaSelect.innerHTML = "";
    if (areas.length === 0) {
      areaSelect.innerHTML =
        "<option value=''>No Entertainment Areas found — create one in the Hue app first</option>";
      return;
    }
    for (const a of areas) {
      const opt = document.createElement("option");
      opt.value = a.id;
      opt.textContent = `${a.name} (${a.light_count} lights)`;
      opt.dataset.name = a.name;
      if (a.id === selectAreaId) opt.selected = true;
      areaSelect.appendChild(opt);
    }
    // If the profile's area has been removed from the bridge, add it as a
    // fallback option so the form can still be saved without picking a new one.
    if (selectAreaId && !areas.find((a) => a.id === selectAreaId)) {
      const opt = document.createElement("option");
      opt.value = selectAreaId;
      opt.textContent = selectAreaName || selectAreaId;
      opt.dataset.name = selectAreaName || "";
      opt.selected = true;
      areaSelect.appendChild(opt);
    }
    if (areaNameHidden)
      areaNameHidden.value = areaSelect.selectedOptions[0]?.dataset.name || "";
  } catch (err) {
    areaSelect.innerHTML = "<option value=''>Failed to load areas</option>";
    console.error(err);
  }
}

if (bridgeSelect) {
  bridgeSelect.addEventListener("change", () => loadAreas());
  areaSelect.addEventListener("change", () => {
    if (areaNameHidden)
      areaNameHidden.value = areaSelect.selectedOptions[0]?.dataset.name || "";
  });
  loadAreas();
}

// ---------------------------------------------------------------------------
// Profile editing
// ---------------------------------------------------------------------------

function editProfile(btn) {
  const profile = JSON.parse(btn.dataset.profile);
  const form = document.getElementById("profile-form");
  if (!form) return;

  // Mark the form as an edit by setting the hidden profile_id field.
  document.getElementById("profile-id").value = profile.id;

  // Populate all editable fields.
  form.elements["name"].value = profile.name || "";
  form.elements["lms_host"].value = profile.lms_host || "";
  form.elements["lms_port"].value = profile.lms_port ?? 3483;
  form.elements["player_name"].value = profile.player_name || "";
  form.elements["color_mode"].value = profile.color_mode || "";
  if (form.elements["exertion_clip"])
    form.elements["exertion_clip"].value = profile.exertion_clip ?? 3.0;
  form.elements["sensitivity"].value = profile.sensitivity ?? 1.0;
  form.elements["brightness_floor"].value = profile.brightness_floor ?? 0.15;
  form.elements["bars"].value = profile.bars ?? 30;
  form.elements["lower_cutoff_freq"].value = profile.lower_cutoff_freq ?? 50;
  form.elements["higher_cutoff_freq"].value = profile.higher_cutoff_freq ?? 12000;
  if (form.elements["onset_delta"])
    form.elements["onset_delta"].value = profile.onset_delta ?? 0.1;
  if (form.elements["onset_alpha"])
    form.elements["onset_alpha"].value = profile.onset_alpha ?? 0.9;

  // Set the bridge, then load areas with the profile's area pre-selected.
  if (bridgeSelect) {
    bridgeSelect.value = profile.bridge_id || "";
    loadAreas(profile.entertainment_area_id, profile.entertainment_area_name);
  }

  // Update the form heading and submit button.
  const heading = document.getElementById("form-heading");
  if (heading) heading.textContent = `Edit: ${profile.name}`;
  const submitBtn = form.querySelector("[type=submit]");
  if (submitBtn) submitBtn.textContent = "Save changes";
  const cancelBtn = document.getElementById("cancel-edit");
  if (cancelBtn) cancelBtn.style.display = "";

  form.scrollIntoView({ behavior: "smooth" });
}

function cancelEdit() {
  const form = document.getElementById("profile-form");
  if (!form) return;

  document.getElementById("profile-id").value = "";

  const heading = document.getElementById("form-heading");
  if (heading) heading.textContent = "New profile";
  const submitBtn = form.querySelector("[type=submit]");
  if (submitBtn) submitBtn.textContent = "Save profile";
  const cancelBtn = document.getElementById("cancel-edit");
  if (cancelBtn) cancelBtn.style.display = "none";

  form.reset();
  loadAreas();
}

// ---------------------------------------------------------------------------
// LMS discovery
// ---------------------------------------------------------------------------

async function discoverLms() {
  const btn = document.getElementById("discover-lms-btn");
  const hostInput = document.querySelector("input[name=lms_host]");
  if (!btn || !hostInput) return;

  btn.disabled = true;
  btn.textContent = "Discovering…";
  try {
    const res = await fetch("/lms/discover");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const servers = await res.json();
    if (servers.length === 0) {
      alert("No LMS server found on the local network.");
    } else {
      hostInput.value = servers[0].host;
      if (servers.length > 1) {
        // More than one server: note it in the console; the first one wins.
        console.info("Multiple LMS servers found:", servers);
      }
    }
  } catch (e) {
    alert("Discovery failed: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Discover";
  }
}

// ---------------------------------------------------------------------------
// Player latency — strategy dropdown
// ---------------------------------------------------------------------------

function toggleFixedDelay() {
  const sel = document.getElementById("latency-strategy");
  const lbl = document.getElementById("fixed-delay-label");
  if (sel && lbl) lbl.style.display = sel.value === "fixed" ? "" : "none";
}

// Set initial state on page load.
toggleFixedDelay();

// ---------------------------------------------------------------------------
// Live colour preview over WebSocket, with onset flash and latency status
// ---------------------------------------------------------------------------

const swatch = document.getElementById("preview-swatch");
if (swatch) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws/preview`);
  let onsetTimer = null;

  ws.onmessage = (event) => {
    const { r, g, b, onset, latency_warning, sync_master } = JSON.parse(event.data);
    // Values arrive as 16-bit (0-65535); scale down for CSS.
    const to8 = (v) => Math.round((v / 65535) * 255);
    swatch.style.backgroundColor = `rgb(${to8(r)}, ${to8(g)}, ${to8(b)})`;

    // Show a brief white outline when an onset is detected so the user can
    // judge detection timing against what they hear.
    if (onset) {
      swatch.classList.add("onset");
      clearTimeout(onsetTimer);
      onsetTimer = setTimeout(() => swatch.classList.remove("onset"), 80);
    }

    const masterEl = document.getElementById("sync-master-info");
    if (masterEl) {
      if (sync_master) {
        masterEl.textContent = `Detected sync master: ${sync_master}`;
        masterEl.style.display = "";
      } else {
        masterEl.style.display = "none";
      }
    }

    const warnEl = document.getElementById("latency-warning");
    if (warnEl) {
      if (latency_warning) {
        warnEl.textContent = latency_warning;
        warnEl.style.display = "";
      } else {
        warnEl.style.display = "none";
      }
    }
  };
}
