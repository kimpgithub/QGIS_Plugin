// 임시 smoke test 페이지 — /web/* JWT 흐름.
// 외주 OpenLayers 빌드로 교체될 예정. 최소 흐름만 검증한다.
const $ = (s) => document.querySelector(s);
const ls = window.localStorage;

const STATE = {
  token: ls.getItem("jwt") || null,
  admin_cd: ls.getItem("admin_cd") || null,
  role: ls.getItem("role") || null,
  map: null,
  centerSet: false,
};

function authHeaders() {
  return { "Authorization": "Bearer " + STATE.token };
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(opts.headers || {}) },
  });
  if (res.status === 401) {
    logout();
    throw new Error("인증 만료");
  }
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`${res.status} ${t}`);
  }
  return res.json();
}

// ---------------- login
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  const admin_cd = $("#admin_cd").value.trim();
  const password = $("#password").value;
  try {
    const r = await fetch("/web/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ admin_cd, password }),
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    STATE.token = j.access_token;
    STATE.admin_cd = j.admin_cd;
    STATE.role = j.role;
    ls.setItem("jwt", STATE.token);
    ls.setItem("admin_cd", STATE.admin_cd);
    ls.setItem("role", STATE.role);
    bootApp();
  } catch (err) {
    $("#login-error").textContent = err.message;
  }
});

$("#logout").addEventListener("click", logout);
function logout() {
  ls.removeItem("jwt"); ls.removeItem("admin_cd"); ls.removeItem("role");
  STATE.token = STATE.admin_cd = STATE.role = null;
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
}

// ---------------- map
function ensureMap() {
  if (STATE.map) return STATE.map;
  STATE.map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {
        osm: {
          type: "raster",
          tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
          tileSize: 256,
          attribution: "© OpenStreetMap",
        },
      },
      layers: [{ id: "osm", type: "raster", source: "osm" }],
    },
    center: [129.2, 35.2],
    zoom: 10,
  });
  return STATE.map;
}

async function loadBoundary() {
  let adm = STATE.admin_cd;
  if (STATE.role === "master") {
    adm = prompt("조회할 admin_cd (8자):", "21510110");
    if (!adm) return;
  }
  const fc = await api(`/web/boundary?adm_cd=${encodeURIComponent(adm)}`);
  const map = ensureMap();
  if (map.getSource("boundary")) {
    map.getSource("boundary").setData(fc);
  } else {
    map.addSource("boundary", { type: "geojson", data: fc });
    map.addLayer({
      id: "boundary-fill", type: "fill", source: "boundary",
      paint: { "fill-color": "#3388ff", "fill-opacity": 0.10 },
    });
    map.addLayer({
      id: "boundary-line", type: "line", source: "boundary",
      paint: { "line-color": "#1240a0", "line-width": 1.5 },
    });
  }
  $("#boundary-info").textContent = `adm_cd=${adm} · boundary ${fc.features.length}건`;
  STATE.currentAdm = adm;
  // COG 배경 타일 로드 (없으면 무시) + fitBounds 우선순위: COG bbox > boundary
  let fittedFromCog = false;
  try {
    const cog = await api(`/web/cog/${encodeURIComponent(adm)}`);
    if (map.getSource("cog")) map.removeLayer("cog-layer"), map.removeSource("cog");
    map.addSource("cog", {
      type: "raster",
      tiles: [location.origin + cog.tile_url],
      tileSize: 256,
    });
    // 경계선 아래로 들어가도록 boundary-fill 바로 위에 삽입
    map.addLayer({ id: "cog-layer", type: "raster", source: "cog" }, "boundary-fill");
    if (cog.bbox) {
      map.fitBounds([[cog.bbox[0], cog.bbox[1]], [cog.bbox[2], cog.bbox[3]]],
                    { padding: 40, duration: 0 });
      fittedFromCog = true;
    }
    $("#boundary-info").textContent += ` · COG ${cog.width}×${cog.height}`;
  } catch (e) {
    if (!String(e.message).startsWith("404")) console.warn("COG load:", e.message);
    $("#boundary-info").textContent += " · COG 없음";
  }
  if (!fittedFromCog && !STATE.centerSet && fc.features.length) {
    const b = new maplibregl.LngLatBounds();
    fc.features.forEach(f => addBounds(b, f.geometry));
    map.fitBounds(b, { padding: 40, duration: 0 });
  }
  STATE.centerSet = true;
}

function addBounds(b, geom) {
  const walk = (coords) => {
    if (typeof coords[0] === "number") { b.extend(coords); return; }
    coords.forEach(walk);
  };
  walk(geom.coordinates);
}

async function addSampleMarkup() {
  const adm = STATE.currentAdm || STATE.admin_cd;
  if (!adm) { alert("먼저 경계를 로드"); return; }
  const c = STATE.map.getCenter();
  const body = {
    adm_cd: adm,
    kind: "attr",
    geometry: { type: "Point", coordinates: [c.lng, c.lat] },
    attrs: { ri_nm: "샘플리", ri_cd: "9999999999", note: "smoke test" },
  };
  const r = await api("/web/markup", { method: "POST", body: JSON.stringify(body) });
  console.log("created", r);
  await loadMarkupList();
  if (confirm(`방금 등록한 마크업 id=${r.id} 를 applied 처리할까요?`)) {
    await api(`/web/markup/${r.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "applied" }),
    });
    await loadMarkupList();
  }
}

async function loadMarkupList() {
  const adm = STATE.currentAdm || STATE.admin_cd;
  if (!adm) return;
  const r = await api(`/web/markup?adm_cd=${encodeURIComponent(adm)}&size=20`);
  const ul = $("#markup-list");
  ul.innerHTML = "";
  r.items.forEach(it => {
    const li = document.createElement("li");
    li.textContent = `#${it.id} [${it.kind}] ${it.status}` +
      (it.attrs && it.attrs.ri_nm ? ` · ${it.attrs.ri_nm}` : "");
    ul.appendChild(li);
  });
}

$("#reload-boundary").addEventListener("click", () => loadBoundary().catch(showErr));
$("#reload-markup").addEventListener("click", () => loadMarkupList().catch(showErr));
$("#add-markup").addEventListener("click", () => addSampleMarkup().catch(showErr));

function showErr(e) { alert(e.message); }

function bootApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#who").textContent = `${STATE.admin_cd} / ${STATE.role}`;
  ensureMap();
  loadBoundary().catch(showErr).then(() => loadMarkupList().catch(showErr));
}

if (STATE.token && STATE.admin_cd) bootApp();
