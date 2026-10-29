    // Dashboard: draws what the ESP32 mesh reports. It decides nothing itself.
    //
    // Topology comes from GET /api/topology once at load; live state arrives as
    // a full snapshot over WebSocket every update and every 2s. Each node's
    // arrow points at the next_hop its own ESP32 chose, so an arrow here and an
    // LED arrow on the model always agree.

    const SVG_NS = "http://www.w3.org/2000/svg";
    const NODE_R = 40;
    const EXIT_SIZE = 80;
    const SPRINKLER_STROKE_WIDTH = 5; // Active sprinkler border, in screen pixels.

    // Status is encoded three ways at once: colour, glyph, and a word. Red and
    // green are indistinguishable to many people, so colour can never be alone.
    const STATUS = {
      NORMAL:  { color:"var(--node)",     glyph:"·", label:"正常" },
      WARNING: { color:"var(--warning)",  ink:"#1a1300", glyph:"!", label:"警示" },
      HAZARD:  { color:"var(--critical)", glyph:"✕", label:"危險阻斷" },
      OFFLINE: { color:"var(--offline)",  glyph:"?", label:"斷線" },
      EXIT:    { color:"var(--good)",     ink:"var(--exit-ink)", glyph:"✓", label:"安全出口" },
      UNKNOWN: { color:"var(--line-strong)", glyph:"·", label:"等待資料" }
    };

    const el = id => document.getElementById(id);
    const heatLayer = el("layer-heat");
    const edgesLayer = el("layer-edges");
    const pathLayer = el("layer-path");
    const nodesLayer = el("layer-nodes");
    const tooltip = el("tooltip");
    const mapContainer = el("map-container");

    let topology = {};
    let edges = [];
    let limits = {};
    let nodeState = {};
    let frameTime = 0;
    let selectedNodeId = null;
    const pendingControls = new Map();
    const confirmedManual = new Set();
    let commandMessage = "選擇節點後即可手動啟動。";
    let commandError = false;
        const isExit = id => !!topology[id] && topology[id].type === "exit";
    const roleLabel = id => ({arrow:"箭頭", sprinkler:"撒水", sensor:"僅感測", exit:"出口"}[topology[id].type] || "節點");
    const isSprinkler = id => topology[id]?.type === "sprinkler";
    const sensorIds = () => Object.keys(topology).filter(id => !isExit(id));

    function statusOf(id) {
      const state = nodeState[id];
      if (!state) return isExit(id) ? STATUS.EXIT : STATUS.UNKNOWN;
      if (isExit(id)) return STATUS.EXIT;
      return STATUS[state.status] || STATUS.UNKNOWN;
    }

    function hitRadius(id) {
      return isExit(id) ? EXIT_SIZE / 2 : NODE_R;
    }

    // Always the value the ESP32 reported. Nothing here recomputes a route.
    function nextHopOf(id) {
      const state = nodeState[id];
      if (!state || state.status === "OFFLINE") return null;
      return state.next_hop || null;
    }

    function hopLabel(id) {
      const hop = nextHopOf(id);
      if (!hop) return "—";
      if (hop === "SAFE") return "已在出口";
      if (hop === "TRAPPED") return "無路徑";
      return hop;
    }

    // Walk next_hop to next_hop to trace a full route out. Guarded against a
    // cycle, which stale readings on different nodes can briefly produce.
    // A dead DHT22 reports nothing rather than a plausible-looking number, so
    // the dash here means "no reading", never "normal".
    function tempLabel(state) {
      if (!state || state.temp == null) return "—";
      return `${state.temp.toFixed(1)}°`;
    }

    function hopChainFrom(startId) {
      const chain = [startId];
      const limit = Object.keys(topology).length + 1;
      let current = startId;
      while (chain.length < limit) {
        const hop = nextHopOf(current);
        if (!hop || hop === "SAFE" || hop === "TRAPPED" || chain.includes(hop)) break;
        chain.push(hop);
        current = hop;
      }
      return chain;
    }

    // Smoke halo, sized by reading. Shows where a hazard is spreading, not just
    // which single node crossed a threshold.
    function renderHeat() {
      heatLayer.innerHTML = "";
      sensorIds().forEach(id => {
        const state = nodeState[id];
        if (!state || state.status === "OFFLINE") return;
        const intensity = Math.min(1, (state.smoke || 0) / (limits.smoke_hazard || 1));
        if (intensity < 0.2) return;

        const blob = document.createElementNS(SVG_NS, "circle");
        blob.setAttribute("cx", topology[id].x);
        blob.setAttribute("cy", topology[id].y);
        blob.setAttribute("r", 26 + intensity * 52);
        blob.setAttribute("fill", "url(#heat)");
        blob.setAttribute("class", "heat-blob");
        blob.setAttribute("opacity", (0.35 + intensity * 0.65).toFixed(2));
        heatLayer.appendChild(blob);
      });
    }

    function renderPaths() {
      pathLayer.innerHTML = "";
      const chain = selectedNodeId ? hopChainFrom(selectedNodeId) : [];
      const chainEdges = new Set();
      for (let i = 0; i < chain.length - 1; i++) chainEdges.add(`${chain[i]}>${chain[i + 1]}`);

      Object.keys(topology).forEach(id => {
        const hop = nextHopOf(id);
        if (!hop || hop === "SAFE" || hop === "TRAPPED" || !topology[hop]) return;

        const from = topology[id];
        const to = topology[hop];
        const dx = to.x - from.x, dy = to.y - from.y;
        const len = Math.hypot(dx, dy) || 1;
        const ux = dx / len, uy = dy / len;
        // Start at the centre: the path layer sits under the nodes, so the
        // overhang is hidden. Insetting both ends instead would collapse the
        // line to zero length for close pairs, leaving the arrow unoriented.
        const free = len - hitRadius(id) - hitRadius(hop);
        const x1 = from.x, y1 = from.y;
        const x2 = to.x - ux * (hitRadius(hop) + 3);
        const y2 = to.y - uy * (hitRadius(hop) + 3);

        // Bow the line so a long corridor does not pass straight through a node
        // that happens to sit on the line. No room to bow means no bow.
        const bow = Math.max(0, Math.min(16, free * 0.18));
        const cx = (x1 + x2) / 2 - uy * bow;
        const cy = (y1 + y2) / 2 + ux * bow;

        const path = document.createElementNS(SVG_NS, "path");
        path.setAttribute("d", `M${x1} ${y1} Q${cx} ${cy} ${x2} ${y2}`);
        path.setAttribute("fill", "none");
        path.setAttribute("marker-end", "url(#arrow)");
        path.setAttribute("class",
          selectedNodeId && !chainEdges.has(`${id}>${hop}`) ? "path-flow path-muted" : "path-flow");
        pathLayer.appendChild(path);
      });
    }

    function applyNodeVisual(id) {
      const shape = el(`shape-${id}`);
      if (!shape) return;

      const state = nodeState[id];
      const meta = statusOf(id);

      shape.setAttribute("fill", meta.color);
      shape.style.filter = "";
      const nodeLabel = el(`label-${id}`);
      if (nodeLabel) nodeLabel.style.fill = meta.ink || "";
      if (isSprinkler(id) && state?.status !== "OFFLINE" && state?.sprinkler_active) {
        shape.setAttribute("stroke", "var(--ring)");
        shape.style.strokeWidth = `${SPRINKLER_STROKE_WIDTH}px`;
        shape.style.filter = "drop-shadow(0 0 5px var(--ring))";
      }
      if (id === selectedNodeId && !(isSprinkler(id) && state?.status !== "OFFLINE" && state?.sprinkler_active)) {
        shape.setAttribute("stroke", "var(--select-stroke)");
        shape.style.strokeWidth = "3px";
      } else if (!(isSprinkler(id) && state?.status !== "OFFLINE" && state?.sprinkler_active)) {
        shape.setAttribute("stroke", "var(--node-outline)");
        shape.style.strokeWidth = "2px";
      }
      if (state && state.status === "OFFLINE") {
        shape.setAttribute("stroke-dasharray", "4 3");
      } else {
        shape.removeAttribute("stroke-dasharray");
      }

      const showBadge = meta !== STATUS.NORMAL && meta !== STATUS.UNKNOWN;
      const badge = el(`badge-${id}`);
      const glyph = el(`glyph-${id}`);
      if (badge) {
        badge.setAttribute("fill", meta.color);
        badge.setAttribute("opacity", showBadge ? "1" : "0");
      }
      if (glyph) glyph.textContent = showBadge ? meta.glyph : "";

      const ring = el(`ring-${id}`);
      // A node's last report outlives it: one unplugged right after losing its
      // neighbours stays TRAPPED in the snapshot. Offline has to win, or a dead
      // board is shown as someone waiting for rescue.
      const trapped = !!state && state.status !== "OFFLINE" && state.next_hop === "TRAPPED";
      if (ring) ring.classList.toggle("active", trapped);

    }

    function apiPrefix() {
      return window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "";
    }
    async function sendSprinkler(id, action) {
      if (pendingControls.has(id)) return;
      selectedNodeId = id;
      el("sprinkler-target").value = id;
      const command = {action, baseline:nodeState[id]?.last_seen ?? 0, deadline:Date.now()+10000};
      pendingControls.set(id, command);
      commandMessage = `${id} 號：正在傳送指令…`;
      commandError = false;
      renderSelection();
      try {
        const response = await fetch(`${apiPrefix()}/api/sprinklers/${encodeURIComponent(id)}/${action}`, {
          method:"POST", signal:AbortSignal.timeout(5000), cache:"no-store"
        });
        let result;
        try { result = await response.json(); } catch { throw new Error("服務回應無法辨識，請重新啟動後端。"); }
        if (!response.ok) throw new Error(response.status === 503 ? "主控 ESP32 未連線，請確認 USB 與後端序列埠。" : result.detail || "指令傳送失敗");
        commandMessage = `${id} 號：指令已傳送，等待節點確認…`;
      } catch (error) {
        pendingControls.delete(id);
        commandMessage = `${id} 號：${error.name === "TimeoutError" ? "連線逾時，請確認後端服務。" : error.message}`;
        commandError = true;
      }
      renderSelection();
    }
    function appendSprinklerButtons(container, id) {
      // Delegated events keep buttons usable while telemetry updates the table.
      for (const [action, label] of [["on", "啟動 30 秒"], ["auto", "自動"]]) {
        const button = document.createElement("button");
        button.type = "button"; button.textContent = label;
        button.className = "sprinkler-control";
        button.dataset.node = id; button.dataset.action = action;
        container.appendChild(button);
      }
    }
    function renderSelection() {
      const target = el("sprinkler-target").value;
      for (const [id, command] of pendingControls) {
        const state = nodeState[id];
        if (state && state.status !== "OFFLINE" && state.last_seen > command.baseline &&
            state.sprinkler_manual === (command.action === "on")) {
          pendingControls.delete(id);
          if (command.action === "on") confirmedManual.add(id);
          else confirmedManual.delete(id);
          commandMessage = `${id} 號：${command.action === "on" ? "節點已確認手動撒水啟動，持續 30 秒。" : "節點已確認恢復自動。"}`;
          commandError = false;
        } else if (Date.now() > command.deadline) {
          pendingControls.delete(id);
          commandMessage = `${id} 號：未收到節點確認，請確認節點電源、韌體與無線連線後重試。`;
          commandError = true;
        }
      }
      for (const id of confirmedManual) {
        const report = nodeState[id];
        if (report && report.status !== "OFFLINE" && !report.sprinkler_manual && !pendingControls.has(id)) {
          confirmedManual.delete(id);
          if (!pendingControls.size) commandMessage = `${id} 號：手動撒水已結束，已恢復自動${report.sprinkler_auto ? "撒水。" : "監測。"}`;
        }
      }
      const state = nodeState[target];
      const online = state && state.status !== "OFFLINE";
      el("sprinkler-output").textContent = !online ? "節點未連線" : state.sprinkler_active ? "撒水已啟動" : "待命中";
      el("sprinkler-mode").textContent = !online ? "等待節點回報" : state.sprinkler_manual ? "手動模式 · 30 秒後自動結束" : state.sprinkler_auto ? "危險觸發 · 自動模式" : "自動監測中";
      el("sprinkler-indicator").classList.toggle("active", !!online && state.sprinkler_active);
      el("sprinkler-on").disabled = pendingControls.has(target);
      el("sprinkler-auto").disabled = pendingControls.has(target);
      el("control-message").textContent = commandMessage;
      el("control-message").dataset.state = commandError ? "error" : "info";
      renderTable();
      if (!selectedNodeId) return;
      const id = selectedNodeId, selected = nodeState[id];
      el("inspector-role").textContent = roleLabel(id);
      el("inspector-title").textContent = isExit(id) ? id.replace("EX", "出口 ") : `${id} 號節點`;
      el("inspector-detail").textContent = isExit(id) ? "安全出口" :
        `${statusOf(id).label}　煙霧 ${selected?.smoke ?? "—"} ADC　溫度 ${tempLabel(selected)}　下一跳 ${hopLabel(id)}`;
    }
    el("sprinkler-on").addEventListener("click", () => sendSprinkler(el("sprinkler-target").value,"on"));
    el("sprinkler-auto").addEventListener("click", () => sendSprinkler(el("sprinkler-target").value,"auto"));
    el("sprinkler-target").addEventListener("change", () => {
      selectedNodeId = el("sprinkler-target").value; refresh();
    });
    el("node-tbody").addEventListener("click", event => {
      const button = event.target.closest("button[data-action]");
      if (button) { event.stopPropagation(); sendSprinkler(button.dataset.node,button.dataset.action); }
    });

    // Also the accessible fallback: every value on the map is readable as text.
    function renderTable() {
      const tbody = el("node-tbody");
      sensorIds().forEach(id => {
        const state = nodeState[id], meta = statusOf(id);
        let row = el(`table-row-${id}`);
        if (!row) {
          row = document.createElement("tr"); row.id = `table-row-${id}`;
          row.tabIndex = 0;
          row.addEventListener("click", event => { if (!event.target.closest("button")) selectNode(id); });
          row.addEventListener("keydown", event => {
            if (event.target === row && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); selectNode(id); }
          });
          for (const cls of ["id", "num mono", "num mono", "num mono", "mono", "", "table-controls"]) {
            const cell = document.createElement("td");cell.className = cls;row.appendChild(cell);
          }
          if (isSprinkler(id)) {
            row.cells[6].appendChild(document.createElement("div"));
            appendSprinklerButtons(row.cells[6], id);
          }
          tbody.appendChild(row);
        }
        row.classList.toggle("selected", id === selectedNodeId);
        const risk = state && state.risk_factor != null ? `×${state.risk_factor.toFixed(2)}` : "∞";
        const values = [`${id} · ${roleLabel(id)}`, state ? `${state.smoke}` : "—", tempLabel(state),
          state && state.status !== "OFFLINE" ? risk : "—", hopLabel(id)];
        values.forEach((value, index) => {row.cells[index].textContent = value;});
        row.cells[5].textContent = state && state.status !== "OFFLINE" && state.next_hop === "TRAPPED" ? "孤立待援" : meta.label;
        row.cells[5].style.color = meta.color;
        if (isSprinkler(id)) {
          const busy = pendingControls.has(id);
          row.cells[6].querySelector("div").textContent = busy ? "等待節點確認…" :
            !state || state.status === "OFFLINE" ? "撒水狀態未知" : state.sprinkler_manual ? "撒水（手動）" :
            state.sprinkler_auto ? "撒水（自動）" : "自動待命";
          row.cells[6].querySelectorAll("button").forEach(button => {button.disabled = busy;});
        } else row.cells[6].textContent = "—";
      });
    }

    function updateKpis() {
      const ids = sensorIds();
      let connected = 0, warning = 0, hazard = 0, trapped = 0;

      ids.forEach(id => {
        const state = nodeState[id];
        if (!state || state.status === "OFFLINE") return;
        connected++;
        if (state.status === "WARNING") warning++;
        if (state.status === "HAZARD") hazard++;
        if (state.next_hop === "TRAPPED") trapped++;
      });

      el("kpi-connected").firstChild.textContent = connected;
      el("kpi-total").textContent = `/${ids.length}`;
      el("kpi-warning").textContent = warning;
      el("kpi-hazard").textContent = hazard;
      el("kpi-trapped").textContent = trapped;
    }

    function refresh() {
      Object.keys(topology).forEach(applyNodeVisual);
      renderHeat();
      renderPaths();
      renderSelection();
      updateKpis();
    }

    function selectNode(id) {
      selectedNodeId = (selectedNodeId === id) ? null : id;
      if (selectedNodeId && isSprinkler(selectedNodeId)) el("sprinkler-target").value = selectedNodeId;
      refresh();
    }

    function showTooltip(id, evt) {
      const state = nodeState[id];
      const meta = statusOf(id);
      const rect = mapContainer.getBoundingClientRect();

      const rows = [`<div class="tt-title">${id} · ${roleLabel(id)} · ${meta.label}</div>`];
      if (state) {
        rows.push(`<div class="tt-row">煙霧 <b>${state.smoke} ADC</b></div>`);
        rows.push(`<div class="tt-row">溫度 <b>${tempLabel(state)}${state.temp == null ? "" : "C"}</b></div>`);
        rows.push(`<div class="tt-row">濕度 <b>${state.humidity == null ? "—" : state.humidity + " %"}</b></div>`);
        rows.push(`<div class="tt-row">風險加權 <b>${state.risk_factor != null ? "×" + state.risk_factor.toFixed(2) : "不可通行"}</b></div>`);
        rows.push(`<div class="tt-row">下一跳 <b>${hopLabel(id)}</b></div>`);
        if (state.hops != null) rows.push(`<div class="tt-row">轉傳 <b>${state.hops} 跳</b></div>`);
      } else {
        rows.push(`<div class="tt-row">此節點無感測硬體</div>`);
      }

      tooltip.innerHTML = rows.join("");
      tooltip.style.left = `${evt.clientX - rect.left}px`;
      tooltip.style.top = `${evt.clientY - rect.top}px`;
      tooltip.classList.add("visible");
    }

    function hideTooltip() {
      tooltip.classList.remove("visible");
    }

    // Fit the view to wherever the nodes actually are, so moving one in
    // place.py cannot push it off canvas or shrink the map into the middle.
    function fitViewBox() {
      const pts = Object.values(topology).filter(n => n && n.x !== undefined);
      if (!pts.length) return;
      const pad = 95;
      const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
      const x = Math.min(...xs) - pad, y = Math.min(...ys) - pad;
      const w = Math.max(...xs) - Math.min(...xs) + pad * 2;
      const h = Math.max(...ys) - Math.min(...ys) + pad * 2;
      const side = Math.max(w, h);
      mapBounds = {w, h};
      homeCamera = {x,y,w,h};

      el("factory-svg").setAttribute("viewBox",
        `${x} ${y} ${w} ${h}`);
      document.querySelectorAll("#factory-svg > rect").forEach(r => {
        r.setAttribute("x", x - side); r.setAttribute("y", y - side);
        r.setAttribute("width", side * 3); r.setAttribute("height", side * 3);
      });
    }

    function initMap() {
      fitViewBox();
      resizeMap();
      edgesLayer.innerHTML = "";
      heatLayer.innerHTML = "";
      pathLayer.innerHTML = "";
      nodesLayer.innerHTML = "";

      (Array.isArray(edges) ? edges : []).forEach(edge => {
        const p1 = topology[edge.source];
        const p2 = topology[edge.target];
        if (!p1 || !p2) return;
        const line = document.createElementNS(SVG_NS, "line");
        line.setAttribute("x1", p1.x); line.setAttribute("y1", p1.y);
        line.setAttribute("x2", p2.x); line.setAttribute("y2", p2.y);
        line.setAttribute("class", "edge-line");
        edgesLayer.appendChild(line);
      });

      Object.keys(topology).forEach(id => {
        const node = topology[id];
        if (!node || node.x === undefined || node.y === undefined) return;

        const g = document.createElementNS(SVG_NS, "g");

        const ring = document.createElementNS(SVG_NS, "circle");
        ring.setAttribute("cx", node.x);
        ring.setAttribute("cy", node.y);
        ring.setAttribute("r", hitRadius(id) + 7);
        ring.setAttribute("id", `ring-${id}`);
        ring.setAttribute("class", "isolation-ring");
        g.appendChild(ring);

        let shape;
        if (isExit(id) || node.type === "arrow") {
          shape = document.createElementNS(SVG_NS, "rect");
          shape.setAttribute("x", node.x - EXIT_SIZE / 2);
          shape.setAttribute("y", node.y - EXIT_SIZE / 2);
          shape.setAttribute("width", EXIT_SIZE);
          shape.setAttribute("height", EXIT_SIZE);
          shape.setAttribute("rx", 8);
        } else if (node.type === "sensor") {
          shape = document.createElementNS(SVG_NS, "polygon");
          shape.setAttribute("points", `${node.x},${node.y-NODE_R} ${node.x+NODE_R},${node.y} ${node.x},${node.y+NODE_R} ${node.x-NODE_R},${node.y}`);
        } else {
          shape = document.createElementNS(SVG_NS, "circle");
          shape.setAttribute("cx", node.x);
          shape.setAttribute("cy", node.y);
          shape.setAttribute("r", NODE_R);
        }
        shape.setAttribute("vector-effect", "non-scaling-stroke");
        shape.setAttribute("id", `shape-${id}`);
        shape.setAttribute("class", `node-shape role-${node.type}`);
        shape.setAttribute("fill", "var(--line-strong)");
        g.appendChild(shape);

        const label = document.createElementNS(SVG_NS, "text");
        label.setAttribute("x", node.x);
        label.setAttribute("y", node.y + 4);
        label.setAttribute("class", "node-label");
        label.setAttribute("id", `label-${id}`);
        label.textContent = id;
        g.appendChild(label);

        const bx = node.x + hitRadius(id) - 3;
        const by = node.y - hitRadius(id) + 3;

        const badge = document.createElementNS(SVG_NS, "circle");
        badge.setAttribute("cx", bx);
        badge.setAttribute("cy", by);
        badge.setAttribute("r", 7);
        badge.setAttribute("id", `badge-${id}`);
        badge.setAttribute("class", "badge-ring");
        g.appendChild(badge);

        const glyph = document.createElementNS(SVG_NS, "text");
        glyph.setAttribute("x", bx);
        glyph.setAttribute("y", by + 3.2);
        glyph.setAttribute("id", `glyph-${id}`);
        glyph.setAttribute("class", "badge-glyph");
        g.appendChild(glyph);

        shape.setAttribute("tabindex", "0");
        shape.setAttribute("role", "button");
        shape.setAttribute("aria-label", `${id} 號節點，查看詳細資料`);
        shape.addEventListener("keydown", evt => {
          if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); selectNode(id); }
        });
        shape.addEventListener("click", () => selectNode(id));
        shape.addEventListener("mousemove", evt => showTooltip(id, evt));
        shape.addEventListener("mouseleave", hideTooltip);

        nodesLayer.appendChild(g);
      });
    }

    function renderAlerts(alerts) {
      const list = el("alerts-list");
      const count = el("alert-count");

      if (!alerts || alerts.length === 0) {
        count.textContent = "";
        list.innerHTML = '<div class="empty-state">系統正常，持續監控中</div>';
        return;
      }

      count.textContent = `${alerts.length} 則`;
      list.innerHTML = "";
      alerts.forEach(rule => {
        const card = document.createElement("div");
        card.className = "alert-card" + (rule.hazard_type === "THERMAL_OVERLOAD" ? " is-warning" : "");

        const tag = document.createElement("div");
        tag.className = "alert-tag mono";
        tag.textContent = rule.rule_id || rule.hazard_type || "ALERT";

        const title = document.createElement("div");
        title.className = "alert-title";
        title.textContent = rule.title || "感測值超過法規門檻";

        card.appendChild(tag);
        card.appendChild(title);

        // 只有填了法源才顯示。空字串代表尚未查證，寧可不標也不要標一個查不到的條號。
        if (rule.legal_basis) {
          const basis = document.createElement("div");
          basis.className = "alert-basis";
          basis.textContent = rule.legal_basis;
          card.appendChild(basis);
        }

        if (Array.isArray(rule.checklist) && rule.checklist.length > 0) {
          const ul = document.createElement("ul");
          ul.className = "alert-checklist";
          rule.checklist.forEach(item => {
            const li = document.createElement("li");
            li.textContent = item;
            ul.appendChild(li);
          });
          card.appendChild(ul);
        }
        list.appendChild(card);
      });
    }

    function applySource(source) {
      const pill = el("source-pill");
      if (source === "mock") {
        pill.textContent = "模擬資料（虛擬節點群）";
        pill.className = "pill is-mock";
      } else if (source === "serial") {
        pill.textContent = "實機連線（ESP-NOW → Gateway）";
        pill.className = "pill is-serial";
      }
    }

    function applyFrame(payload) {
      nodeState = payload.nodes || {};
      frameTime = payload.timestamp || (Date.now() / 1000);
      applySource(payload.source);
      refresh();
      renderAlerts(payload.compliance_alerts);
    }

    // Normally served by the backend itself. The file:// case is only for
    // opening this page straight off disk during development.
    function backendOrigin() {
      return window.location.protocol === "file:" ? "127.0.0.1:8000" : window.location.host;
    }

    async function loadTopology() {
      const prefix = window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "";
      const res = await fetch(`${prefix}/api/topology`);
      if (!res.ok) throw new Error("HTTP error " + res.status);
      return await res.json();
    }

    function connectWebSocket() {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(`${protocol}//${backendOrigin()}/ws`);
      const pill = el("conn-pill");

      socket.onopen = () => {
        pill.classList.add("live");
        el("conn-text").textContent = "已連線";
      };

      socket.onmessage = event => {
        try {
          applyFrame(JSON.parse(event.data));
        } catch (err) {
          console.error("Payload parse error:", err);
        }
      };

      socket.onclose = () => {
        pill.classList.remove("live");
        el("conn-text").textContent = "已斷線，重連中";
        setTimeout(connectWebSocket, 2000);
      };

      socket.onerror = err => {
        console.error("WebSocket error:", err);
        socket.close();
      };
    }

    let mapBounds = {w:1100,h:1500};
    let mapZoom = 1;
    let originalProportions = window.matchMedia("(max-width:760px)").matches;
    let camera = {x:0,y:0,w:1100,h:1500};
    let homeCamera = null;
    function resizeMap() {
      if (!homeCamera) return;
      const viewport = el("map-viewport");
      const ratio = viewport.clientWidth / Math.max(1,viewport.clientHeight);
      let w=homeCamera.w, h=homeCamera.h;
      if (w/h < ratio) w=h*ratio; else h=w/ratio;
      camera={x:homeCamera.x+(homeCamera.w-w)/2,y:homeCamera.y+(homeCamera.h-h)/2,w,h};
      mapZoom=1; applyCamera();
    }
    function applyCamera() {
      el("factory-svg").setAttribute("viewBox",`${camera.x} ${camera.y} ${camera.w} ${camera.h}`);
      el("map-zoom-label").textContent=`${Math.round(mapZoom*100)}%`;
    }
    function zoomMap(factor, fx=.5, fy=.5) {
      const next=Math.max(.5,Math.min(4,mapZoom*factor));
      const scale=mapZoom/next;
      camera.x+=camera.w*(1-scale)*fx; camera.y+=camera.h*(1-scale)*fy;
      camera.w*=scale; camera.h*=scale; mapZoom=next; applyCamera();
    }
    el("map-zoom-in").addEventListener("click",()=>zoomMap(1.25));
    el("map-zoom-out").addEventListener("click",()=>zoomMap(1/1.25));
    el("map-readable").addEventListener("click",resizeMap);
    el("map-details").addEventListener("click",()=>{el("node-details").open=!el("node-details").open;if(el("node-details").open)el("node-details").scrollIntoView({behavior:"smooth",block:"start"});});
    el("map-proportion").addEventListener("click",()=>{
      originalProportions=!originalProportions;
      for(const node of Object.values(topology)) node.y=node.sourceY*(originalProportions?1:.45)+(node.type === "exit" && node.sourceY > 1400 && !originalProportions ? 50 : 0);
      el("map-proportion").setAttribute("aria-pressed",String(originalProportions));
      initMap();resizeMap();refresh();
    });
    const viewport=el("map-viewport");
    viewport.addEventListener("wheel",event=>{
      event.preventDefault(); const rect=viewport.getBoundingClientRect();
      zoomMap(Math.exp(-event.deltaY*.002),(event.clientX-rect.left)/rect.width,(event.clientY-rect.top)/rect.height);
    },{passive:false});
    let drag=null, dragged=false;
    viewport.addEventListener("pointerdown",event=>{
      if(event.button!==0)return;
      drag={x:event.clientX,y:event.clientY,cx:camera.x,cy:camera.y};dragged=false;
    });
    viewport.addEventListener("pointermove",event=>{
      if(!drag)return;
      const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
      if(!dragged && Math.hypot(dx,dy)<5)return;
      dragged=true;viewport.setPointerCapture(event.pointerId);viewport.classList.add("is-dragging");
      camera.x=drag.cx-dx*camera.w/viewport.clientWidth;
      camera.y=drag.cy-dy*camera.h/viewport.clientHeight;applyCamera();hideTooltip();
    });
    for(const type of ["pointerup","pointercancel"])viewport.addEventListener(type,()=>{drag=null;viewport.classList.remove("is-dragging");});
    viewport.addEventListener("click",event=>{if(dragged){event.stopPropagation();dragged=false;}},true);
    new ResizeObserver(resizeMap).observe(viewport);
    setInterval(renderSelection,500);

    async function bootstrap() {
      try {
        const loaded = await loadTopology();
        if (!loaded || !loaded.nodes) throw new Error("Invalid topology data structure");

        // Display layout only: keep physical corridor lengths on the ESP32.
        // Compress vertical whitespace for wide screens; original proportions
        // remain available without changing the topology or trigger distance.
        topology = Object.fromEntries(Object.entries(loaded.nodes).map(([id, node]) =>
          [id, {...node, sourceY: node.y, y: node.y * (originalProportions ? 1 : .45) + (node.type === "exit" && node.y > 1400 && !originalProportions ? 50 : 0)}]));
        edges = loaded.edges || [];
        limits = loaded.limits || {};

        el("sprinkler-target").replaceChildren(...sensorIds().filter(isSprinkler).map(id=>{
          const option=document.createElement("option");option.value=id;option.textContent=`${id} 號 · 撒水節點`;return option;
        }));
        el("map-proportion").setAttribute("aria-pressed",String(originalProportions));
        selectedNodeId="1";
        initMap();
        resizeMap();
        refresh();
        connectWebSocket();
      } catch (err) {
        console.error("Failed to bootstrap topology from backend:", err);
        el("conn-text").textContent = "拓樸載入失敗";
      }
    }

    bootstrap();