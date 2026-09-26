// disaster-map.js
// 消防栓分布地圖 — 雙北，資料來源：臺北自來水事業處《大臺北地區消防栓清冊》
// 靜態讀取 data/fire_hydrants.json（build-time 轉出的檔案，執行期不打任何外部API）
// 依賴：Leaflet + Leaflet.markercluster（需在 HTML 裡引入，見下方整合說明）

(function () {
  const DEFAULT_CENTER = [25.04, 121.5]; // 雙北大致中心
  const DEFAULT_ZOOM = 11;

  let map = null;
  let clusterUp = null;   // 地上式消防栓 cluster 群組
  let clusterDown = null; // 地下式消防栓 cluster 群組
  let hydrantData = null; // 快取，避免切分頁時重抓

  function initMap() {
    if (map) return; // 已經初始化過就不要重來一次（Leaflet 容器只能綁定一次）

    map = L.map("hydrant-map", {
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      preferCanvas: true // 三萬筆點位用 canvas 渲染，效能比 SVG 好很多
    });

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    clusterUp = L.markerClusterGroup({ disableClusteringAtZoom: 18 });
    clusterDown = L.markerClusterGroup({ disableClusteringAtZoom: 18 });

    loadHydrants();
  }

  async function loadHydrants() {
    if (!hydrantData) {
      const res = await fetch("data/fire_hydrants.json");
      hydrantData = await res.json();
    }

    hydrantData.forEach(h => {
      const isUp = h.type === "地上";
      const marker = L.circleMarker([h.lat, h.lng], {
        radius: 5,
        weight: 1,
        color: isUp ? "#D2691E" : "#3B7DD8",
        fillColor: isUp ? "#D2691E" : "#3B7DD8",
        fillOpacity: 0.85
      }).bindPopup(`<b>${h.type}式消防栓</b><br>${h.area}`);

      (isUp ? clusterUp : clusterDown).addLayer(marker);
    });

    // 預設都顯示；圖層開關見 wireLayerToggles()
    clusterUp.addTo(map);
    clusterDown.addTo(map);
  }

  function wireLayerToggles() {
    const cbUp = document.getElementById("hydrant-toggle-up");
    const cbDown = document.getElementById("hydrant-toggle-down");

    cbUp.addEventListener("change", () => {
      if (cbUp.checked) map.addLayer(clusterUp);
      else map.removeLayer(clusterUp);
    });
    cbDown.addEventListener("change", () => {
      if (cbDown.checked) map.addLayer(clusterDown);
      else map.removeLayer(clusterDown);
    });
  }

  // 供外部（switchView 之類的分頁切換邏輯）呼叫：分頁被打開時才初始化地圖。
  // Leaflet 在容器還是 display:none 時初始化會抓錯容器尺寸、畫面會是空白或錯位，
  // 所以不要在頁面載入當下直接 initMap()，要等分頁真的顯示出來那一刻再呼叫。
  window.SafePathHydrantMap = {
    show() {
      const firstTime = !map;
      initMap();
      wireLayerToggles();
      if (!firstTime) {
        // 分頁之前被隱藏過，容器尺寸可能變了，重新計算一次
        setTimeout(() => map.invalidateSize(), 0);
      }
    }
  };
})();
