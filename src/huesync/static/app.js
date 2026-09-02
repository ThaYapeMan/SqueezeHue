// Populate the Entertainment Area dropdown whenever the selected bridge changes.
const bridgeSelect = document.getElementById("bridge-select");
const areaSelect = document.getElementById("area-select");
const areaNameHidden = document.getElementById("area-name-hidden");

async function loadAreas() {
  if (!bridgeSelect || !areaSelect) return;
  const bridgeId = bridgeSelect.value;
  areaSelect.innerHTML = "<option>Loading...</option>";
  try {
    const res = await fetch(`/bridges/${bridgeId}/areas`);
    const areas = await res.json();
    areaSelect.innerHTML = "";
    if (areas.length === 0) {
      areaSelect.innerHTML = "<option value=''>No Entertainment Areas found - create one in the Hue app first</option>";
      return;
    }
    for (const a of areas) {
      const opt = document.createElement("option");
      opt.value = a.id;
      opt.textContent = `${a.name} (${a.light_count} lights)`;
      opt.dataset.name = a.name;
      areaSelect.appendChild(opt);
    }
    if (areaNameHidden) areaNameHidden.value = areaSelect.selectedOptions[0]?.dataset.name || "";
  } catch (err) {
    areaSelect.innerHTML = "<option value=''>Failed to load areas</option>";
    console.error(err);
  }
}

if (bridgeSelect) {
  bridgeSelect.addEventListener("change", loadAreas);
  areaSelect.addEventListener("change", () => {
    if (areaNameHidden) areaNameHidden.value = areaSelect.selectedOptions[0]?.dataset.name || "";
  });
  loadAreas();
}

// Live colour preview over a websocket.
const swatch = document.getElementById("preview-swatch");
if (swatch) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws/preview`);
  ws.onmessage = (event) => {
    const { r, g, b } = JSON.parse(event.data);
    // Values arrive as 16-bit (0-65535); scale down for CSS.
    const to8 = (v) => Math.round((v / 65535) * 255);
    swatch.style.backgroundColor = `rgb(${to8(r)}, ${to8(g)}, ${to8(b)})`;
  };
}
