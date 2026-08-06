// Dendrophis Web Observability Interface - Client Logic
(function () {
  let socket = null;
  let eventCount = 0;
  let trackedFiles = {};
  let subagentData = { nodes: [], links: [] };
  let memoryData = [];

  // DOM Elements
  const statusEl = document.getElementById('connection-status');
  const thoughtStreamEl = document.getElementById('thought-stream');
  const fileInspectorEl = document.getElementById('file-inspector');
  const eventCountEl = document.getElementById('event-count-val');
  const fileCountEl = document.getElementById('file-count-badge');
  const clearBtn = document.getElementById('clear-stream-btn');

  // Initialize WebSocket Connection
  function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    updateStatus('CONNECTING', false);
    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      updateStatus('CONNECTED', true);
      console.log('🌐 Connected to Dendrophis Telemetry Stream');
    };

    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleTelemetryEvent(msg);
      } catch (err) {
        console.error('Failed to parse WebSocket message:', err);
      }
    };

    socket.onclose = () => {
      updateStatus('DISCONNECTED', false);
      // Auto-reconnect in 2 seconds
      setTimeout(connectWebSocket, 2000);
    };

    socket.onerror = (err) => {
      console.error('WebSocket error:', err);
      socket.close();
    };
  }

  function updateStatus(text, isConnected) {
    if (!statusEl) return;
    const textEl = statusEl.querySelector('.status-text');
    if (textEl) textEl.textContent = text;
    if (isConnected) {
      statusEl.classList.add('connected');
    } else {
      statusEl.classList.remove('connected');
    }
  }

  // Handle Event Routing
  function handleTelemetryEvent(msg) {
    eventCount++;
    if (eventCountEl) eventCountEl.textContent = eventCount;

    const { type, payload, timestamp } = msg;

    switch (type) {
      case 'THOUGHT_LOG':
        appendThoughtLog(payload, timestamp);
        break;
      case 'SUBAGENT_STATE':
        updateSubagentGraph(payload);
        break;
      case 'MEMORY_RETRIEVAL':
        updateMemoryNebula(payload);
        break;
      case 'FILESYSTEM_CHANGE':
        updateFilesystemInspector(payload);
        break;
    }
  }

  // 1. Thought Stream Renderer
  function appendThoughtLog(payload, timestamp) {
    if (!thoughtStreamEl) return;

    const timeStr = timestamp ? new Date(timestamp).toLocaleTimeString() : new Date().toLocaleTimeString();
    const entry = document.createElement('div');
    entry.className = `log-entry ${payload.level || 'info'}`;
    
    const timeSpan = document.createElement('span');
    timeSpan.className = 'timestamp';
    timeSpan.textContent = `[${timeStr}]`;
    
    const textSpan = document.createElement('span');
    textSpan.className = 'msg-text';
    textSpan.textContent = payload.text || '';

    entry.appendChild(timeSpan);
    entry.appendChild(textSpan);

    thoughtStreamEl.appendChild(entry);
    thoughtStreamEl.scrollTop = thoughtStreamEl.scrollHeight;
  }

  // Clear Thought Stream Button
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      if (thoughtStreamEl) thoughtStreamEl.innerHTML = '';
    });
  }

  // 2. Subagent Orchestrator D3 Force Graph
  function updateSubagentGraph(payload) {
    const agents = payload.agents || [];
    const nodes = agents.map(a => ({
      id: a.id,
      name: a.name,
      status: a.status,
      task: a.task,
    }));

    const links = [];
    agents.forEach(a => {
      if (a.parent) {
        links.push({ source: a.parent, target: a.id });
      }
    });

    renderD3SubagentGraph(nodes, links);
  }

  function renderD3SubagentGraph(nodes, links) {
    const svg = d3.select('#subagent-graph');
    if (svg.empty()) return;

    const width = svg.node().clientWidth || 400;
    const height = svg.node().clientHeight || 300;

    svg.selectAll('*').remove();

    if (nodes.length === 0) return;

    const simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(d => d.id).distance(80))
      .force('charge', d3.forceManyBody().strength(-200))
      .force('center', d3.forceCenter(width / 2, height / 2));

    const link = svg.append('g')
      .selectAll('line')
      .data(links)
      .enter().append('line')
      .attr('stroke', 'rgba(56, 189, 248, 0.4)')
      .attr('stroke-width', 2);

    const node = svg.append('g')
      .selectAll('g')
      .data(nodes)
      .enter().append('g');

    node.append('circle')
      .attr('r', d => (d.id === 'dendrophis-core' ? 14 : 10))
      .attr('fill', d => {
        if (d.status === 'executing' || d.status === 'thinking') return '#a855f7';
        if (d.status === 'active') return '#22c55e';
        return '#38bdf8';
      })
      .attr('stroke', '#fff')
      .attr('stroke-width', 1.5)
      .style('filter', 'drop-shadow(0 0 6px rgba(168, 85, 247, 0.6))');

    node.append('text')
      .text(d => d.name)
      .attr('x', 14)
      .attr('y', 4)
      .attr('fill', '#e2e8f0')
      .attr('font-size', '10px')
      .attr('font-family', 'monospace');

    simulation.on('tick', () => {
      link
        .attr('x1', d => d.source.x)
        .attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x)
        .attr('y2', d => d.target.y);

      node
        .attr('transform', d => `translate(${d.x},${d.y})`);
    });
  }

  // 3. Memory Nebula Visualization
  function updateMemoryNebula(payload) {
    if (payload.action === 'saved' || payload.action === 'retrieved' || payload.action === 'association') {
      memoryData.push({
        id: payload.id || `mem-${memoryData.length}`,
        content: payload.content,
        relevance: payload.relevance || 0.8,
        action: payload.action,
        x: (Math.random() - 0.5) * 200,
        y: (Math.random() - 0.5) * 200,
      });
      renderD3MemoryNebula();
    }
  }

  function renderD3MemoryNebula() {
    const svg = d3.select('#memory-nebula');
    if (svg.empty()) return;

    const width = svg.node().clientWidth || 400;
    const height = svg.node().clientHeight || 300;

    svg.selectAll('*').remove();

    if (memoryData.length === 0) {
      svg.append('text')
        .attr('x', width / 2)
        .attr('y', height / 2)
        .attr('text-anchor', 'middle')
        .attr('fill', '#64748b')
        .attr('font-size', '12px')
        .attr('font-family', 'monospace')
        .text('Awaiting memory retrieval / associations...');
      return;
    }

    const g = svg.append('g').attr('transform', `translate(${width / 2},${height / 2})`);

    g.selectAll('circle')
      .data(memoryData)
      .enter().append('circle')
      .attr('cx', d => d.x)
      .attr('cy', d => d.y)
      .attr('r', d => Math.max(4, d.relevance * 10))
      .attr('fill', d => (d.action === 'saved' ? '#38bdf8' : '#a855f7'))
      .attr('opacity', 0.8)
      .style('filter', 'drop-shadow(0 0 8px rgba(56, 189, 248, 0.8))');
  }

  // 4. Filesystem Inspector Renderer
  function updateFilesystemInspector(payload) {
    if (!fileInspectorEl) return;

    if (payload.files) {
      trackedFiles = payload.files;
    } else if (payload.path) {
      trackedFiles[payload.path] = payload.action || 'modified';
    }

    const paths = Object.keys(trackedFiles);
    if (fileCountEl) fileCountEl.textContent = `${paths.length} Tracked`;

    if (paths.length === 0) {
      fileInspectorEl.innerHTML = '<div class="empty-state">No files tracked yet.</div>';
      return;
    }

    fileInspectorEl.innerHTML = '';
    paths.forEach(p => {
      const action = trackedFiles[p];
      const item = document.createElement('div');
      item.className = 'file-item';

      const pathSpan = document.createElement('span');
      pathSpan.className = 'file-path';
      pathSpan.textContent = p;
      pathSpan.title = p;

      const actSpan = document.createElement('span');
      actSpan.className = `file-action ${action}`;
      actSpan.textContent = action;

      item.appendChild(pathSpan);
      item.appendChild(actSpan);
      fileInspectorEl.appendChild(item);
    });
  }

  // Start WebSocket on Load
  window.addEventListener('DOMContentLoaded', () => {
    renderD3MemoryNebula();
    connectWebSocket();
  });
})();
