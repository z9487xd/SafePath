// ---------------------------------------------------------------
    // 災後應變與職安資源 — 由後端 /api/resources 提供
    // ---------------------------------------------------------------
    // 這三筆勞動部開放資料存放於 data/opendata/*.json，由 backend/opendata_client.py
    // 讀取並負責更新；此處只負責顯示。資料不寫在前端有三個理由：
    //   一、前端寫死等於沒有任何程式碼取用過開放資料，「串接」不成立
    //   二、與後端的法規門檻比對形成兩套來源，違反單一資料來源原則
    //   三、文字留在瀏覽器裡就送不到 Gateway，法條燈板需要伺服器端持有
    // 後端讀的是磁碟快取而非即時查詢，所以斷網時這一頁照樣可用 —— 對一套以
    // 「火災時斷網是常態」為前提的系統，這是刻意的，不是將就。
    // 本區塊不讀 nodeState 也不寫回，純資訊呈現。

    // 特殊備註：位於加工出口區／科學園區內的廠址另有專屬管理局受理通報
    // （例如高雄楠梓/前鎮、臺中/中港、屏東、新竹、中科、南科等分處），
    // 不在此清單內，實際部署前需先確認廠址是否落在園區範圍。
    let INSPECTION_HOTLINES = {};
    let CASE_SERVICE_CONTACTS = {};
    let OSH_COURSES = [];
    let DATASET_META = {};



    // ---------------------------------------------------------------
    // 災後應變與職安資源 — view wiring
    // ---------------------------------------------------------------

    function populateCountySelects() {
      const counties = Object.keys(INSPECTION_HOTLINES);
      [el("res-county"), el("res-county-case")].forEach(select => {
        select.innerHTML = counties
          .map(c => `<option value="${c}">${c}</option>`)
          .join("");
      });
    }

    function renderNotifyResult() {
      const county = el("res-county").value;
      const deaths = Math.max(0, parseInt(el("res-deaths").value, 10) || 0);
      const hospitalized = Math.max(0, parseInt(el("res-hospitalized").value, 10) || 0);
      // 罹災人數至少等於死亡與住院人數，避免只填了其中一欄就漏判
      const injured = Math.max(
        Math.max(0, parseInt(el("res-injured").value, 10) || 0), deaths, hospitalized);
      const box = el("res-notify-result");

      // 第37條三款各自獨立成立，任一款達標即須通報。
      // 第二款看的是罹災人數，與是否住院無關 —— 三人受傷而無人住院仍須通報，
      // 因此「罹災人數」必須是獨立欄位，不能用住院人數頂替。
      const reasons = [];
      if (deaths >= 1) reasons.push("發生死亡災害");
      if (injured >= 3) reasons.push("罹災人數三人以上");
      if (injured >= 1 && hospitalized >= 1) reasons.push("罹災人數一人以上且需住院治療");
      const triggered = reasons.length > 0;
      const info = INSPECTION_HOTLINES[county];

      if (!triggered) {
        box.innerHTML = `<div class="result-box is-quiet">尚未達第37條通報門檻，僅供現場記錄使用。</div>`;
        return;
      }
      if (!info) {
        box.innerHTML = `<div class="result-box is-quiet">請先選擇事故發生地縣市。</div>`;
        return;
      }

      box.innerHTML = `
        <div class="result-box is-trigger">
          <div class="result-title">已達法定通報門檻（${reasons.join("、")}）— 應於8小時內通報</div>
          <div class="result-line">受理機構　<b>${info.agency}</b></div>
          <div class="result-line">通報專線　<b>${info.phone}</b></div>
          <div class="result-line">地址　${info.address}</div>
          <a class="call-btn" href="tel:${info.phone.replace(/[^\d+#]/g, "")}">撥打通報專線</a>
        </div>`;
    }

    function renderCaseResult() {
      const county = el("res-county-case").value;
      const info = CASE_SERVICE_CONTACTS[county];
      const box = el("res-case-result");
      if (!info) { box.innerHTML = ""; return; }

      box.innerHTML = `
        <div class="result-box is-info">
          <div class="result-line">服務窗口　<b>${info.agency}</b></div>
          <div class="result-line">電話　<b>${info.phone}</b></div>
          <div class="result-line">地址　${info.address}</div>
        </div>`;
    }

    function renderCourseList() {
      el("res-course-list").innerHTML = OSH_COURSES.map(c => `
        <a class="course-item" href="${c.url}" target="_blank" rel="noopener">
          <div class="course-title">${c.title}</div>
          <div class="course-desc">${c.desc}</div>
        </a>`).join("");
    }

    function switchView(view) {
      const idMap = { monitor: "app", resources: "resources-view", hydrants: "hydrants-view" };
      Object.values(idMap).forEach(id =>
      el(id).classList.toggle("hidden", id !== idMap[view])
      );
      el("tab-monitor").classList.toggle("active", view === "monitor");
      el("tab-resources").classList.toggle("active", view === "resources");
      el("tab-hydrants").classList.toggle("active", view === "hydrants");

      if (view === "hydrants") window.SafePathHydrantMap.show();
    }
    
    document.getElementById("tab-hydrants").addEventListener("click", () => switchView("hydrants"));

    const SUBVIEWS = ["notify", "case", "courses"];
    function switchSubview(name) {
      SUBVIEWS.forEach(v => {
        el(`subview-${v}`).classList.toggle("hidden", v !== name);
        el(`subtab-${v}`).classList.toggle("active", v === name);
      });
    }

    async function loadResources() {
      const prefix = window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "";
      const res = await fetch(`${prefix}/api/resources`);
      if (!res.ok) throw new Error("HTTP error " + res.status);
      return await res.json();
    }

    function renderResources() {
      populateCountySelects();
      renderNotifyResult();
      renderCaseResult();
      renderCourseList();
    }

    // A failed fetch must not leave three silently blank panels looking like
    // there is simply no help available.
    function showResourcesError() {
      const msg = '<div class="result-box is-quiet">職安資源載入失敗，無法取得 /api/resources。'
                + '監控與導引功能不受影響。</div>';
      ["res-notify-result", "res-case-result", "res-course-list"].forEach(id => {
        const box = el(id);
        if (box) box.innerHTML = msg;
      });
    }

    async function initResourcesView() {
      // Listeners are bound before the fetch, so tabs and inputs respond while it
      // is still in flight and keep working even if it never arrives.
      el("res-county").addEventListener("change", renderNotifyResult);
      el("res-deaths").addEventListener("input", renderNotifyResult);
      el("res-injured").addEventListener("input", renderNotifyResult);
      el("res-hospitalized").addEventListener("input", renderNotifyResult);
      el("res-county-case").addEventListener("change", renderCaseResult);

      el("tab-monitor").addEventListener("click", () => switchView("monitor"));
      el("tab-resources").addEventListener("click", () => switchView("resources"));
      SUBVIEWS.forEach(v => el(`subtab-${v}`).addEventListener("click", () => switchSubview(v)));

      try {
        const data = await loadResources();
        INSPECTION_HOTLINES = data.inspection_hotlines || {};
        CASE_SERVICE_CONTACTS = data.case_service_contacts || {};
        OSH_COURSES = data.elearning_courses || [];
        DATASET_META = data.datasets || {};
        renderResources();
      } catch (err) {
        console.error("Failed to load open data resources:", err);
        showResourcesError();
      }
    }

    initResourcesView();