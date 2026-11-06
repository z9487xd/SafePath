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

    // 三個縣市選單共用同一個選擇：選一次，通報、窗口、就醫三頁一起切換。
    const COUNTY_SELECTS = () => [el("res-county"), el("res-county-case"), el("res-county-med")];
    function setCounty(value) {
      COUNTY_SELECTS().forEach(select => { select.value = value; });
      renderNotifyResult();
      renderCaseResult();
      renderMedical();
    }

    let medicalRequest = 0;
    async function renderMedical() {
      const county = el("res-county-med").value;
      const box = el("res-medical-result");
      if (!county) { box.replaceChildren(); return; }
      const ticket = ++medicalRequest;
      const prefix = window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "";
      let data;
      try {
        const res = await fetch(`${prefix}/api/medical_facilities?county=${encodeURIComponent(county)}`);
        if (!res.ok) throw new Error("HTTP " + res.status);
        data = await res.json();
      } catch (err) {
        console.error("[Medical] 載入失敗", err);
        if (ticket === medicalRequest) box.replaceChildren(Object.assign(document.createElement("div"),
          {className: "result-box is-quiet", textContent: "就醫名單載入失敗，監控與導引功能不受影響。"}));
        return;
      }
      if (ticket !== medicalRequest) return;   // 使用者已經切到別的縣市
      box.replaceChildren(...data.groups.map(medicalGroup));

      const sets = Object.values(data.datasets || {});
      el("res-medical-source").textContent = sets.length
        ? "資料來源：勞動部開放資料 " + sets.map(d => `${d.dataset_name}（${d.dataset_id}）`).join("、") +
          "；名單為離線快取，就醫前請先電話確認門診時段。"
        : "";
    }

    // 初查不合格項目排行：行業選單留空 = 全行業合計。長條只比同一次查詢內的相對多寡。
    let inspectionRequest = 0;
    let industriesFilled = false;
    async function renderInspection() {
      const select = el("res-industry");
      const box = el("res-inspection-result");
      const ticket = ++inspectionRequest;
      const prefix = window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "";
      let data;
      try {
        const res = await fetch(`${prefix}/api/inspection_failures?industry=${encodeURIComponent(select.value)}`);
        if (!res.ok) throw new Error("HTTP " + res.status);
        data = await res.json();
      } catch (err) {
        console.error("[Inspection] 載入失敗", err);
        if (ticket === inspectionRequest) box.replaceChildren(Object.assign(document.createElement("div"),
          {className: "result-box is-quiet", textContent: "檢查統計載入失敗，監控與導引功能不受影響。"}));
        return;
      }
      if (ticket !== inspectionRequest) return;
      if (!industriesFilled && data.industries.length) {
        select.replaceChildren(Object.assign(document.createElement("option"), {value: "", textContent: "全行業合計"}),
          ...data.industries.map(name => Object.assign(document.createElement("option"), {value: name, textContent: name})));
        industriesFilled = true;
      }
      const label = data.industry || "全行業合計";
      const top = data.items.length ? data.items[0].failed_sites : 0;
      const head = Object.assign(document.createElement("div"), {className: "bar-head",
        textContent: `${data.year} ${label}：初查不合格 ${data.failed_sites.toLocaleString()} 場次，最常見的項目`});
      const list = document.createElement("ol");
      list.className = "bar-list";
      data.items.forEach(item => {
        const li = document.createElement("li");
        const name = Object.assign(document.createElement("div"), {className: "bar-name", textContent: item.item});
        const law = Object.assign(document.createElement("div"), {className: "bar-law", textContent: item.law});
        const track = document.createElement("div");
        track.className = "bar-track";
        const fill = document.createElement("div");
        fill.className = "bar-fill";
        fill.style.width = top ? `${Math.max(2, 100 * item.failed_sites / top)}%` : "0";
        track.appendChild(fill);
        const value = Object.assign(document.createElement("div"), {className: "bar-value",
          textContent: `${item.failed_sites.toLocaleString()} 場（${item.share}%）`});
        li.append(name, law, track, value);
        list.appendChild(li);
      });
      box.replaceChildren(head, list);
      const d = data.dataset || {};
      el("res-inspection-source").textContent = d.dataset_id
        ? `資料來源：勞動部職安署《${d.dataset_name}》（${d.dataset_id}，${data.year}）。百分比 = 該項不合格場次 ÷ 該行業初查不合格場次；`
          + "一個場次可同時不合格多項，所以加總會超過 100%。"
        : "";
    }

    const FACILITY_PREVIEW = 5;
    function facilityItem(f) {
      const row = document.createElement("div");
      row.className = "facility";
      const name = f.url
        ? Object.assign(document.createElement("a"), {href: f.url, target: "_blank", rel: "noopener", textContent: f.name})
        : Object.assign(document.createElement("span"), {textContent: f.name});
      name.className = "facility-name";
      const lines = [f.address, f.phone ? "電話 " + f.phone : ""].filter(Boolean);
      const meta = Object.assign(document.createElement("div"), {className: "facility-meta", textContent: lines.join("　")});
      row.append(name, meta);
      return row;
    }

    function medicalGroup(group) {
      const wrap = document.createElement("div");
      wrap.className = "facility-group";
      const head = Object.assign(document.createElement("div"), {className: "facility-head"});
      head.append(Object.assign(document.createElement("span"), {className: "facility-tag", textContent: group.category}),
        Object.assign(document.createElement("span"), {className: "facility-count", textContent: `${group.facilities.length} 家`}));
      wrap.appendChild(head);
      if (!group.facilities.length) {
        wrap.appendChild(Object.assign(document.createElement("div"),
          {className: "result-box is-quiet", textContent: `本縣市沒有列在這份名單（全國共 ${group.total} 家），可查詢鄰近縣市。`}));
        return wrap;
      }
      const shown = group.facilities.slice(0, FACILITY_PREVIEW);
      const rest = group.facilities.slice(FACILITY_PREVIEW);
      shown.forEach(f => wrap.appendChild(facilityItem(f)));
      if (rest.length) {
        const more = document.createElement("details");
        more.className = "facility-more";
        more.appendChild(Object.assign(document.createElement("summary"), {textContent: `顯示其餘 ${rest.length} 家`}));
        rest.forEach(f => more.appendChild(facilityItem(f)));
        wrap.appendChild(more);
      }
      return wrap;
    }

    // ---------------------------------------------------------------
    // 通報資料整理：用職災統計填報系統的四張代碼表組出通報草稿
    // ---------------------------------------------------------------
    let REPORT_CODES = {disaster_types: [], injury_parts: [], agents: [], disability: []};

    function fillSelect(select, items, label, text) {
      select.replaceChildren(Object.assign(document.createElement("option"), {value: "", textContent: label}),
        ...items.map(item => Object.assign(document.createElement("option"), {value: item.code, textContent: text(item)})));
    }

    function populateReportForm() {
      fillSelect(el("rep-type"), REPORT_CODES.disaster_types, "未選擇", t => `${t.code} ${t.name}`);
      fillSelect(el("rep-part"), REPORT_CODES.injury_parts, "未選擇", p => `${p.code} ${p.name}`);
      fillSelect(el("rep-disability"), REPORT_CODES.disability, "未選擇", d => `${d.code} ${d.name}`);
      const groups = [...new Set(REPORT_CODES.agents.map(a => a.group))];
      el("rep-agent-group").replaceChildren(
        Object.assign(document.createElement("option"), {value: "", textContent: "未選擇"}),
        ...groups.map(g => Object.assign(document.createElement("option"), {value: g, textContent: g})));
      populateAgents();
    }

    function populateAgents() {
      const group = el("rep-agent-group").value;
      const agents = REPORT_CODES.agents.filter(a => a.group === group);
      fillSelect(el("rep-agent"), agents, group ? "未選擇" : "請先選分類",
        a => a.subgroup && a.subgroup !== a.name ? `${a.code} ${a.subgroup}／${a.name}` : `${a.code} ${a.name}`);
    }

    const pick = (items, code) => items.find(i => i.code === code);

    function renderReportSummary() {
      if (!el("rep-output")) return;
      const a = assessNotify();
      const type = pick(REPORT_CODES.disaster_types, el("rep-type").value);
      const part = pick(REPORT_CODES.injury_parts, el("rep-part").value);
      const agent = pick(REPORT_CODES.agents, el("rep-agent").value);
      const disability = pick(REPORT_CODES.disability, el("rep-disability").value);
      el("rep-type-desc").textContent = type ? type.desc : "";

      const when = el("rep-time").value ? el("rep-time").value.replace("T", " ") : "（未填）";
      const place = el("rep-place").value.trim();
      const lines = ["【職業災害通報資料整理（草稿，送出前請人工確認）】"];
      lines.push(`發生時間：${when}`);
      lines.push(`發生地點：${a.county || "（未選縣市）"}${place ? "　" + place : ""}`);
      lines.push(`罹災情形：罹災 ${a.injured} 人，其中死亡 ${a.deaths} 人、需住院 ${a.hospitalized} 人`);
      lines.push(a.reasons.length
        ? `通報判斷：已達職業安全衛生法第37條門檻（${a.reasons.join("、")}），應於8小時內通報`
        : "通報判斷：依填寫人數尚未達第37條門檻，僅供現場記錄");
      if (type) lines.push(`災害類型：${type.code} ${type.name}`);
      if (part) lines.push(`受傷部位：${part.code} ${part.name}`);
      if (agent) lines.push(`媒介物：${agent.code} ${agent.name}（${agent.group}）`);
      if (disability) lines.push(`失能傷害種類：${disability.code} ${disability.name}`);
      if (a.info) lines.push(`受理機構：${a.info.agency}　專線 ${a.info.phone}`);
      lines.push("代碼依勞動部職業災害統計網路填報系統代碼表（資料集 41471、41469、41470、41467）");
      el("rep-output").value = lines.join("\n");
    }

    // 目前警示只能推測災害類型，不能當作事實；帶入後仍由人確認。
    function suggestFromAlerts() {
      const alerts = (window.SafePathLive && window.SafePathLive.alerts) || [];
      const names = [];
      if (alerts.some(r => String(r.hazard_type).startsWith("SMOKE"))) names.push("火災");
      if (alerts.some(r => r.hazard_type === "THERMAL_OVERLOAD")) names.push("與高溫、低溫之接觸");
      const msg = el("rep-suggest-msg");
      if (!names.length) { msg.textContent = "目前沒有警示，請手動選擇。"; return; }
      const type = REPORT_CODES.disaster_types.find(t => t.name === names[0]);
      if (type) el("rep-type").value = type.code;
      msg.textContent = `依目前警示（${names.join("、")}）帶入「${names[0]}」。感測器只能推測類型，請依實際情況確認或修改。`;
      renderReportSummary();
    }

    async function copyReport() {
      const msg = el("rep-copy-msg");
      try {
        await navigator.clipboard.writeText(el("rep-output").value);
        msg.textContent = "已複製。";
      } catch (err) {
        el("rep-output").select();
        msg.textContent = "無法自動複製，已選取全文，請按 Ctrl+C。";
      }
    }

    function populateCountySelects() {
      const counties = Object.keys(INSPECTION_HOTLINES);
      COUNTY_SELECTS().forEach(select => {
        select.innerHTML = counties
          .map(c => `<option value="${c}">${c}</option>`)
          .join("");
      });
    }

    function renderNotifyResult() {
      renderNotifyBox();
      renderReportSummary();
    }

    // 第37條三款各自獨立成立，任一款達標即須通報。通報比對與通報摘要共用同一份判斷。
    function assessNotify() {
      const county = el("res-county").value;
      const deaths = Math.max(0, parseInt(el("res-deaths").value, 10) || 0);
      const hospitalized = Math.max(0, parseInt(el("res-hospitalized").value, 10) || 0);
      const injured = Math.max(
        Math.max(0, parseInt(el("res-injured").value, 10) || 0), deaths, hospitalized);
      const reasons = [];
      if (deaths >= 1) reasons.push("發生死亡災害");
      if (injured >= 3) reasons.push("罹災人數三人以上");
      if (injured >= 1 && hospitalized >= 1) reasons.push("罹災人數一人以上且需住院治療");
      return {county, deaths, hospitalized, injured, reasons, info: INSPECTION_HOTLINES[county]};
    }

    function renderNotifyBox() {
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
      const meta = DATASET_META.elearning_courses;
      el("res-course-source").textContent = meta && meta.dataset_id
        ? `資料來源：勞動部開放資料《${meta.dataset_name}》（${meta.dataset_id}），本頁只列出其中與火災、職災預防相關的 ${OSH_COURSES.length} 門課。`
        : "";
    }

    function switchView(view) {
      el("kpi-row").classList.toggle("hidden", view !== "monitor");
      const idMap = { monitor: "app", resources: "resources-view", hydrants: "hydrants-view" };
      Object.values(idMap).forEach(id =>
      el(id).classList.toggle("hidden", id !== idMap[view])
      );
      el("tab-monitor").classList.toggle("active", view === "monitor");
      el("tab-resources").classList.toggle("active", view === "resources");
      el("tab-hydrants").classList.toggle("active", view === "hydrants");
      ["monitor", "resources", "hydrants"].forEach(v => el(`tab-${v}`).setAttribute("aria-selected", String(v === view)));

      if (view === "hydrants") window.SafePathHydrantMap.show();
    }
    
    document.getElementById("tab-hydrants").addEventListener("click", () => switchView("hydrants"));

    const SUBVIEWS = ["notify", "case", "medical", "inspection", "courses"];
    function switchSubview(name) {
      SUBVIEWS.forEach(v => {
        el(`subview-${v}`).classList.toggle("hidden", v !== name);
        el(`subtab-${v}`).classList.toggle("active", v === name);
        el(`subtab-${v}`).setAttribute("aria-selected", String(v === name));
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
      renderMedical();
      renderInspection();
      renderCourseList();
      populateReportForm();
      renderReportSummary();
    }

    // A failed fetch must not leave three silently blank panels looking like
    // there is simply no help available.
    function showResourcesError() {
      const msg = '<div class="result-box is-quiet">職安資源載入失敗，無法取得 /api/resources。'
                + '監控與導引功能不受影響。</div>';
      ["res-notify-result", "res-case-result", "res-medical-result", "res-inspection-result", "res-course-list"].forEach(id => {
        const box = el(id);
        if (box) box.innerHTML = msg;
      });
    }

    async function initResourcesView() {
      // Listeners are bound before the fetch, so tabs and inputs respond while it
      // is still in flight and keep working even if it never arrives.
      COUNTY_SELECTS().forEach(select => select.addEventListener("change", () => setCounty(select.value)));
      el("res-industry").addEventListener("change", renderInspection);
      el("res-deaths").addEventListener("input", renderNotifyResult);
      el("res-injured").addEventListener("input", renderNotifyResult);
      el("res-hospitalized").addEventListener("input", renderNotifyResult);

      ["rep-time", "rep-place", "rep-type", "rep-part", "rep-agent", "rep-disability"]
        .forEach(id => el(id).addEventListener("input", renderReportSummary));
      el("rep-agent-group").addEventListener("change", () => { populateAgents(); renderReportSummary(); });
      el("rep-suggest").addEventListener("click", suggestFromAlerts);
      el("rep-copy").addEventListener("click", copyReport);

      el("tab-monitor").addEventListener("click", () => switchView("monitor"));
      el("tab-resources").addEventListener("click", () => switchView("resources"));
      SUBVIEWS.forEach(v => el(`subtab-${v}`).addEventListener("click", () => switchSubview(v)));

      try {
        const data = await loadResources();
        INSPECTION_HOTLINES = data.inspection_hotlines || {};
        CASE_SERVICE_CONTACTS = data.case_service_contacts || {};
        OSH_COURSES = data.elearning_courses || [];
        DATASET_META = data.datasets || {};
        REPORT_CODES = data.report_codes || REPORT_CODES;
        renderResources();
      } catch (err) {
        console.error("Failed to load open data resources:", err);
        showResourcesError();
      }
    }

    initResourcesView();