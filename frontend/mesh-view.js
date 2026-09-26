    // Dashboard: draws what the ESP32 mesh reports. It decides nothing itself.
    //
    // Topology comes from GET /api/topology once at load; live state arrives as
    // a full snapshot over WebSocket every update and every 2s. Each node's
    // arrow points at the next_hop its own ESP32 chose, so an arrow here and an
    // LED arrow on the model always agree.

    const SVG_NS = "http://www.w3.org/2000/svg";
    const NODE_R = 19;
    const EXIT_SIZE = 38;

    // Status is encoded three ways at once: colour, glyph, and a word. Red and
    // green are indistinguishable to many people, so colour can never be alone.
    const STATUS = {
      NORMAL:  { color:"var(--node)",     glyph:"·", label:"正常" },
      WARNING: { color:"var(--warning)",  glyph:"!", label:"警示" },
      HAZARD:  { color:"var(--critical)", glyph:"✕", label:"危險阻斷" },
      OFFLINE: { color:"var(--offline)",  glyph:"?", label:"斷線" },
      EXIT:    { color:"var(--good)",     glyph:"✓", label:"安全出口" },
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

        const isExit = id => !!topology[id] && topology[id].type === "exit";
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

    // Nodes can sit close together, so a label always drawn below would land on
    // top of the neighbour. Put it above when something is directly underneath.
    function noteBelow(id) {
      const self = topology[id];
      return !Object.keys(topology).some(other => {
        if (other === id) return false;
        const p = topology[other];
        return Math.abs(p.x - self.x) < 45 && p.y > self.y && p.y - self.y < 60;
      });
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
      if (id === selectedNodeId) {
        shape.setAttribute("stroke", "#FFE600");
        shape.setAttribute("stroke-width", "3");
      } else {
        shape.setAttribute("stroke", "rgba(255,255,255,.16)");
        shape.setAttribute("stroke-width", "2");
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
      if (ring) ring.classList.toggle("active", !!state && state.next_hop === "TRAPPED");

      // Label abnormal nodes only. Normal readings live in the side table, which
      // keeps the map readable where nodes are close together.
      const note = el(`note-${id}`);
      if (note) {
        let text = "";
        if (state && state.status === "OFFLINE") {
          text = `離線 ${Math.max(0, Math.round(frameTime - (state.last_seen || frameTime)))}s`;
        } else if (state && state.next_hop === "TRAPPED") {
          text = "無路徑";
        } else if (state && (state.status === "HAZARD" || state.status === "WARNING")) {
          const hot = state.temp != null && limits.temp_warning != null
            && state.temp >= limits.temp_warning;
          text = hot ? `${state.smoke} ADC · ${state.temp.toFixed(0)}°C` : `${state.smoke} ADC`;
        }
        note.textContent = text;
      }
    }

    // Also the accessible fallback: every value on the map is readable as text.
    function renderTable() {
      const tbody = el("node-tbody");
      tbody.innerHTML = "";

      sensorIds().forEach(id => {
        const state = nodeState[id];
        const meta = statusOf(id);
        const row = document.createElement("tr");
        if (id === selectedNodeId) row.classList.add("selected");
        row.addEventListener("click", () => selectNode(id));

        const risk = state && state.risk_factor != null ? `×${state.risk_factor.toFixed(2)}` : "∞";
        const cells = [
          ["id", id],
          ["num mono", state ? `${state.smoke}` : "—"],
          ["num mono", tempLabel(state)],
          ["num mono", state && state.status !== "OFFLINE" ? risk : "—"],
          ["mono", hopLabel(id)]
        ];

        cells.forEach(([cls, value]) => {
          const td = document.createElement("td");
          td.className = cls;
          td.textContent = value;
          row.appendChild(td);
        });

        const statusTd = document.createElement("td");
        const wrap = document.createElement("span");
        wrap.className = "status-cell";
        const mark = document.createElement("span");
        mark.className = "status-mark";
        mark.style.backgroundColor = meta.color;
        mark.textContent = meta.glyph;
        wrap.appendChild(mark);
        wrap.appendChild(document.createTextNode(
          state && state.next_hop === "TRAPPED" ? "孤立待援" : meta.label));
        statusTd.appendChild(wrap);
        row.appendChild(statusTd);

        tbody.appendChild(row);
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
      renderTable();
      updateKpis();
    }

    function selectNode(id) {
      selectedNodeId = (selectedNodeId === id) ? null : id;
      refresh();
    }

    function showTooltip(id, evt) {
      const state = nodeState[id];
      const meta = statusOf(id);
      const rect = mapContainer.getBoundingClientRect();

      const rows = [`<div class="tt-title">${id} · ${meta.label}</div>`];
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
      const pad = 58;
      const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
      const x = Math.min(...xs) - pad, y = Math.min(...ys) - pad;
      const w = Math.max(...xs) - Math.min(...xs) + pad * 2;
      const h = Math.max(...ys) - Math.min(...ys) + pad * 2;
      const side = Math.max(w, h);

      el("factory-svg").setAttribute("viewBox",
        `${x - (side - w) / 2} ${y - (side - h) / 2} ${side} ${side}`);
      document.querySelectorAll("#factory-svg > rect").forEach(r => {
        r.setAttribute("x", x - side); r.setAttribute("y", y - side);
        r.setAttribute("width", side * 3); r.setAttribute("height", side * 3);
      });
    }

    function initMap() {
      fitViewBox();
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
        if (isExit(id)) {
          shape = document.createElementNS(SVG_NS, "rect");
          shape.setAttribute("x", node.x - EXIT_SIZE / 2);
          shape.setAttribute("y", node.y - EXIT_SIZE / 2);
          shape.setAttribute("width", EXIT_SIZE);
          shape.setAttribute("height", EXIT_SIZE);
          shape.setAttribute("rx", 8);
        } else {
          shape = document.createElementNS(SVG_NS, "circle");
          shape.setAttribute("cx", node.x);
          shape.setAttribute("cy", node.y);
          shape.setAttribute("r", NODE_R);
        }
        shape.setAttribute("id", `shape-${id}`);
        shape.setAttribute("class", "node-shape");
        shape.setAttribute("fill", "var(--line-strong)");
        g.appendChild(shape);

        const label = document.createElementNS(SVG_NS, "text");
        label.setAttribute("x", node.x);
        label.setAttribute("y", node.y + 4);
        label.setAttribute("class", "node-label");
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

        const note = document.createElementNS(SVG_NS, "text");
        note.setAttribute("x", node.x);
        note.setAttribute("y", node.y + (noteBelow(id) ? hitRadius(id) + 14 : -(hitRadius(id) + 9)));
        note.setAttribute("id", `note-${id}`);
        note.setAttribute("class", "node-note");
        g.appendChild(note);

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

    async function bootstrap() {
      try {
        const loaded = await loadTopology();
        if (!loaded || !loaded.nodes) throw new Error("Invalid topology data structure");

        topology = loaded.nodes;
        edges = loaded.edges || [];
        limits = loaded.limits || {};

        initMap();
        refresh();
        connectWebSocket();
      } catch (err) {
        console.error("Failed to bootstrap topology from backend:", err);
        el("conn-text").textContent = "拓樸載入失敗";
      }
    }

    bootstrap();