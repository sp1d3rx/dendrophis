// Dendrophis Web Observability Interface - Client Logic
(function () {
  let socket = null;
  let eventCount = 0;
  let trackedFiles = {};
  let fileDetails = {};
  let memoryNodesMap = new Map();
  let toolExecutions = [];
  let activeStreamFilter = 'all';
  let streamSearchQuery = '';
  let fileSearchQuery = '';
  let autoScrollPaused = false;
  const streamEntriesById = new Map();

  // DOM Elements
  const statusEl = document.getElementById('connection-status');
  const thoughtStreamEl = document.getElementById('thought-stream');
  const fileInspectorEl = document.getElementById('file-inspector');
  const todoInspectorEl = document.getElementById('todo-inspector');
  const eventCountEl = document.getElementById('event-count-val');
  const fileCountEl = document.getElementById('file-count-badge');
  const todoCountEl = document.getElementById('todo-count-badge');
  const memoryCountEl = document.getElementById('memory-count-badge');
  const timelineCountEl = document.getElementById('timeline-count');
  const timelineListEl = document.getElementById('tool-timeline-list');
  const clearBtn = document.getElementById('clear-stream-btn');
  const pauseBtn = document.getElementById('pause-stream-btn');
  const liveBadgeEl = document.getElementById('stream-live-badge');
  const streamSearchInput = document.getElementById('stream-search-input');
  const fileSearchInput = document.getElementById('file-search-input');

  // Header telemetry elements
  const activeModelEl = document.getElementById('active-model-val');
  const contextUsageEl = document.getElementById('context-usage-val');
  const contextBarFillEl = document.getElementById('context-bar-fill');
  const compactionBadgeEl = document.getElementById('compaction-badge');
  const speedValEl = document.getElementById('speed-val');
  const ttftValEl = document.getElementById('ttft-val');
  const tokensValEl = document.getElementById('tokens-val');
  const cacheHitValEl = document.getElementById('cache-hit-val');
  const costValEl = document.getElementById('cost-val');
  const turnCountValEl = document.getElementById('turn-count-val');

  // Inspector Drawer elements
  const drawerEl = document.getElementById('inspector-drawer');
  const drawerBadgeEl = document.getElementById('drawer-badge');
  const drawerTitleEl = document.getElementById('drawer-title');
  const drawerMetaEl = document.getElementById('drawer-meta');
  const drawerBodyEl = document.getElementById('drawer-body');
  const drawerCloseBtn = document.getElementById('drawer-close-btn');

  // Initialize WebSocket Connection
  function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    updateStatus('CONNECTING', false);
    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      updateStatus('CONNECTED', true);
    };

    socket.onmessage = (messageEvent) => {
      try {
        const parsedMessage = JSON.parse(messageEvent.data);
        handleTelemetryEvent(parsedMessage);
      } catch (parseError) {
        console.error('Failed to parse WebSocket message:', parseError);
      }
    };

    socket.onclose = () => {
      updateStatus('DISCONNECTED', false);
      setTimeout(connectWebSocket, 2000);
    };

    socket.onerror = () => {
      socket.close();
    };
  }

  function updateStatus(statusText, isConnected) {
    if (!statusEl) return;
    const textEl = statusEl.querySelector('.status-text');
    if (textEl) textEl.textContent = statusText;
    if (isConnected) {
      statusEl.classList.add('connected');
    } else {
      statusEl.classList.remove('connected');
    }
  }

  // Handle Event Routing
  function handleTelemetryEvent(messageObj) {
    eventCount++;
    if (eventCountEl) eventCountEl.textContent = eventCount.toLocaleString();

    const { type, payload, timestamp } = messageObj;

    switch (type) {
      case 'THOUGHT_LOG':
        appendOrUpdateThoughtLog(payload, timestamp);
        break;
      case 'SUBAGENT_STATE':
        updateSubagentGraph(payload);
        break;
      case 'TOOL_TIMELINE':
        updateToolTimeline(payload);
        break;
      case 'MEMORY_RETRIEVAL':
        updateMemoryNebula(payload);
        break;
      case 'FILESYSTEM_CHANGE':
        updateFilesystemInspector(payload);
        break;
      case 'SESSION_STATS':
        updateSessionStats(payload);
        break;
    }
  }

  // 0. Header & Session Stats Telemetry
  function updateSessionStats(statsPayload) {
    if (!statsPayload) return;

    if (activeModelEl && statsPayload.model) {
      const shortModel = String(statsPayload.model).split('/').pop();
      activeModelEl.textContent = shortModel;
      activeModelEl.title = statsPayload.model;
    }

    const contextTokens = Number(statsPayload.context_tokens || 0);
    const contextLimit = Number(statsPayload.context_limit || 131072);
    const contextPct = Number(
      statsPayload.context_pct || (contextLimit > 0 ? (contextTokens / contextLimit) * 100 : 0)
    );

    if (contextUsageEl) {
      contextUsageEl.textContent = `${contextTokens.toLocaleString()} / ${contextLimit.toLocaleString()} (${contextPct.toFixed(1)}%)`;
    }
    if (contextBarFillEl) {
      const clampedPct = Math.min(100, Math.max(0, contextPct));
      contextBarFillEl.style.width = `${clampedPct}%`;
      if (clampedPct >= 80) {
        contextBarFillEl.classList.add('warn');
      } else {
        contextBarFillEl.classList.remove('warn');
      }
    }

    const compactionsCount = Number(statsPayload.compactions_count || 0);
    const reclaimedTokens = Number(statsPayload.tokens_reclaimed || 0);
    if (compactionBadgeEl) {
      if (compactionsCount > 0) {
        compactionBadgeEl.classList.remove('hidden');
        compactionBadgeEl.textContent = `${compactionsCount} Compact (-${reclaimedTokens.toLocaleString()})`;
      } else {
        compactionBadgeEl.classList.add('hidden');
      }
    }

    if (speedValEl) {
      const tokPerSec = Number(statsPayload.tokens_per_sec || 0);
      speedValEl.textContent = `${tokPerSec.toFixed(1)} tok/s`;
    }
    if (ttftValEl) {
      const ttftSeconds = Number(statsPayload.time_to_first_token || 0);
      ttftValEl.textContent = ttftSeconds > 0 ? `TTFT: ${ttftSeconds.toFixed(2)}s` : 'TTFT: —';
    }
    if (tokensValEl) {
      const promptTokens = Number(statsPayload.prompt_tokens || 0);
      const completionTokens = Number(statsPayload.completion_tokens || 0);
      tokensValEl.textContent = `${promptTokens.toLocaleString()} in / ${completionTokens.toLocaleString()} out`;
    }
    if (cacheHitValEl) {
      const cachePct = Number(statsPayload.cache_hit_pct || 0);
      const cachedCount = Number(statsPayload.cached_tokens || 0);
      cacheHitValEl.textContent = `Cache: ${cachePct.toFixed(1)}% (${cachedCount.toLocaleString()})`;
    }
    if (costValEl) {
      const costUsd = Number(statsPayload.total_cost_usd || 0);
      costValEl.textContent = `$${costUsd.toFixed(4)}`;
    }
    if (turnCountValEl) {
      const turnCount = Number(statsPayload.turn_count || 0);
      turnCountValEl.textContent = `Turns: ${turnCount}`;
    }

    if (Array.isArray(statsPayload.todos)) {
      renderTodoInspector(statsPayload.todos);
    }
  }

  // 1. Thought & Activity Stream Renderer (with stream_id coalescing & filtering)
  function appendOrUpdateThoughtLog(payload, timestamp) {
    if (!thoughtStreamEl || !payload) return;

    const systemInitMsg = thoughtStreamEl.querySelector('.system-msg');
    if (systemInitMsg) {
      systemInitMsg.remove();
    }

    const levelName = payload.level || 'info';
    const streamId = payload.stream_id || null;
    const entryText = payload.text || '';
    const timeString = timestamp
      ? new Date(timestamp).toLocaleTimeString()
      : new Date().toLocaleTimeString();

    if (streamId && streamEntriesById.has(streamId)) {
      const existingEntry = streamEntriesById.get(streamId);
      const textSpan = existingEntry.querySelector('.msg-text');
      if (textSpan) {
        textSpan.textContent = entryText;
      }
      existingEntry.dataset.searchText = entryText.toLowerCase();
      applyStreamEntryVisibility(existingEntry);
      if (!autoScrollPaused) {
        thoughtStreamEl.scrollTop = thoughtStreamEl.scrollHeight;
      }
      return;
    }

    const entryEl = document.createElement('div');
    entryEl.className = `log-entry ${levelName}`;
    entryEl.dataset.level = levelName;
    entryEl.dataset.searchText = entryText.toLowerCase();

    const timeSpan = document.createElement('span');
    timeSpan.className = 'timestamp';
    timeSpan.textContent = `[${timeString}]`;

    const levelTag = document.createElement('span');
    levelTag.className = 'tag-pill';
    levelTag.textContent = levelName;

    const textSpan = document.createElement('span');
    textSpan.className = 'msg-text';
    textSpan.textContent = entryText;

    entryEl.appendChild(timeSpan);
    entryEl.appendChild(levelTag);
    entryEl.appendChild(textSpan);

    if (payload.tool_call_id) {
      entryEl.classList.add('clickable');
      entryEl.title = 'Click to inspect tool execution';
      entryEl.addEventListener('click', () => {
        const matchingExec = toolExecutions.find(
          (execItem) => execItem.id === payload.tool_call_id || execItem.name === payload.tool_name
        );
        if (matchingExec) {
          openToolInspector(matchingExec);
        }
      });
    }

    if (streamId) {
      streamEntriesById.set(streamId, entryEl);
    }

    applyStreamEntryVisibility(entryEl);
    thoughtStreamEl.appendChild(entryEl);

    if (!autoScrollPaused) {
      thoughtStreamEl.scrollTop = thoughtStreamEl.scrollHeight;
    }
  }

  function applyStreamEntryVisibility(entryEl) {
    const entryLevel = entryEl.dataset.level || 'info';
    const entryText = entryEl.dataset.searchText || '';

    let matchesFilter = true;
    if (activeStreamFilter !== 'all') {
      if (activeStreamFilter === 'error') {
        matchesFilter = entryLevel === 'error' || entryLevel === 'warning';
      } else {
        matchesFilter = entryLevel === activeStreamFilter;
      }
    }

    const matchesSearch = !streamSearchQuery || entryText.includes(streamSearchQuery);
    if (matchesFilter && matchesSearch) {
      entryEl.classList.remove('hidden');
    } else {
      entryEl.classList.add('hidden');
    }
  }

  function refreshAllStreamVisibility() {
    if (!thoughtStreamEl) return;
    const allEntries = thoughtStreamEl.querySelectorAll('.log-entry');
    allEntries.forEach((entryEl) => applyStreamEntryVisibility(entryEl));
  }

  // Stream Filter Pills, Search, Pause, Clear
  const filterContainer = document.getElementById('stream-filters');
  if (filterContainer) {
    filterContainer.addEventListener('click', (clickEvent) => {
      const buttonEl = clickEvent.target.closest('.filter-pill');
      if (!buttonEl) return;
      filterContainer.querySelectorAll('.filter-pill').forEach((btn) => btn.classList.remove('active'));
      buttonEl.classList.add('active');
      activeStreamFilter = buttonEl.dataset.filter || 'all';
      refreshAllStreamVisibility();
    });
  }

  if (streamSearchInput) {
    streamSearchInput.addEventListener('input', () => {
      streamSearchQuery = streamSearchInput.value.trim().toLowerCase();
      refreshAllStreamVisibility();
    });
  }

  if (pauseBtn) {
    pauseBtn.addEventListener('click', () => {
      autoScrollPaused = !autoScrollPaused;
      pauseBtn.textContent = autoScrollPaused ? '▶️' : '⏸️';
      if (liveBadgeEl) {
        liveBadgeEl.textContent = autoScrollPaused ? 'PAUSED' : 'LIVE';
        liveBadgeEl.classList.toggle('paused', autoScrollPaused);
      }
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      if (thoughtStreamEl) thoughtStreamEl.innerHTML = '';
      streamEntriesById.clear();
    });
  }

  // 2. Subagent Orchestrator Graph & Tool Execution Timeline
  const orchestratorTabs = document.getElementById('orchestrator-tabs');
  const graphViewEl = document.getElementById('orchestrator-graph-view');
  const timelineViewEl = document.getElementById('orchestrator-timeline-view');

  if (orchestratorTabs) {
    orchestratorTabs.addEventListener('click', (clickEvent) => {
      const tabBtn = clickEvent.target.closest('.tab-btn');
      if (!tabBtn) return;
      orchestratorTabs.querySelectorAll('.tab-btn').forEach((btn) => btn.classList.remove('active'));
      tabBtn.classList.add('active');
      const selectedTab = tabBtn.dataset.tab;
      if (selectedTab === 'timeline') {
        graphViewEl.classList.add('hidden');
        timelineViewEl.classList.remove('hidden');
      } else {
        timelineViewEl.classList.add('hidden');
        graphViewEl.classList.remove('hidden');
      }
    });
  }

  function updateSubagentGraph(payload) {
    const agentsList = payload.agents || [];
    const graphNodes = agentsList.map((agentItem) => ({
      id: agentItem.id,
      name: agentItem.name,
      kind: agentItem.kind || (agentItem.id === 'dendrophis-core' ? 'root' : 'tool'),
      status: agentItem.status,
      task: agentItem.task || '',
      calls: agentItem.calls || 0,
    }));

    const graphLinks = [];
    agentsList.forEach((agentItem) => {
      if (agentItem.parent) {
        graphLinks.push({ source: agentItem.parent, target: agentItem.id });
      }
    });

    renderD3SubagentGraph(graphNodes, graphLinks);
  }

  function renderD3SubagentGraph(graphNodes, graphLinks) {
    const svg = d3.select('#subagent-graph');
    if (svg.empty()) return;

    const width = svg.node().clientWidth || 420;
    const height = svg.node().clientHeight || 280;

    svg.selectAll('*').remove();
    if (graphNodes.length === 0) return;

    const simulation = d3
      .forceSimulation(graphNodes)
      .force('link', d3.forceLink(graphLinks).id((nodeDatum) => nodeDatum.id).distance(88))
      .force('charge', d3.forceManyBody().strength(-240))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(32));

    const linkSelection = svg
      .append('g')
      .selectAll('line')
      .data(graphLinks)
      .enter()
      .append('line')
      .attr('stroke', 'rgba(56, 189, 248, 0.38)')
      .attr('stroke-width', 1.8)
      .attr('stroke-dasharray', (linkDatum) =>
        String(linkDatum.target.id || linkDatum.target).startsWith('agent-') ? 'none' : '4,3'
      );

    const nodeSelection = svg
      .append('g')
      .selectAll('g')
      .data(graphNodes)
      .enter()
      .append('g')
      .style('cursor', 'pointer')
      .on('click', (_clickEvent, nodeDatum) => {
        openNodeInspector(nodeDatum);
      });

    nodeSelection
      .append('circle')
      .attr('r', (nodeDatum) => {
        if (nodeDatum.kind === 'root') return 15;
        if (nodeDatum.kind === 'subagent') return 12;
        return 9;
      })
      .attr('fill', (nodeDatum) => {
        if (nodeDatum.status === 'executing' || nodeDatum.status === 'thinking') return '#a855f7';
        if (nodeDatum.status === 'error') return '#f43f5e';
        if (nodeDatum.status === 'active') return '#22c55e';
        return nodeDatum.kind === 'subagent' ? '#38bdf8' : '#475569';
      })
      .attr('stroke', '#e2e8f0')
      .attr('stroke-width', 1.5)
      .style('filter', 'drop-shadow(0 0 6px rgba(56, 189, 248, 0.5))');

    nodeSelection
      .append('text')
      .text((nodeDatum) =>
        nodeDatum.calls > 1 ? `${nodeDatum.name} (${nodeDatum.calls}×)` : nodeDatum.name
      )
      .attr('x', 15)
      .attr('y', 3)
      .attr('fill', '#e2e8f0')
      .attr('font-size', '10px')
      .attr('font-family', 'monospace');

    nodeSelection
      .append('text')
      .text((nodeDatum) => (nodeDatum.task ? String(nodeDatum.task).slice(0, 32) : ''))
      .attr('x', 15)
      .attr('y', 14)
      .attr('fill', '#94a3b8')
      .attr('font-size', '8.5px')
      .attr('font-family', 'monospace');

    simulation.on('tick', () => {
      linkSelection
        .attr('x1', (linkDatum) => linkDatum.source.x)
        .attr('y1', (linkDatum) => linkDatum.source.y)
        .attr('x2', (linkDatum) => linkDatum.target.x)
        .attr('y2', (linkDatum) => linkDatum.target.y);

      nodeSelection.attr('transform', (nodeDatum) => {
        const clampedX = Math.max(24, Math.min(width - 110, nodeDatum.x));
        const clampedY = Math.max(24, Math.min(height - 24, nodeDatum.y));
        return `translate(${clampedX},${clampedY})`;
      });
    });
  }

  function updateToolTimeline(payload) {
    if (!payload || !Array.isArray(payload.executions)) return;
    toolExecutions = payload.executions;
    if (timelineCountEl) {
      timelineCountEl.textContent = String(toolExecutions.length);
    }
    if (!timelineListEl) return;

    if (toolExecutions.length === 0) {
      timelineListEl.innerHTML =
        '<div class="empty-state">No tool or subagent executions recorded yet.</div>';
      return;
    }

    timelineListEl.innerHTML = '';
    const reversedExecutions = [...toolExecutions].reverse();
    reversedExecutions.forEach((execItem) => {
      const rowEl = document.createElement('div');
      rowEl.className = `timeline-item ${execItem.status || 'running'}`;

      const statusIcon =
        execItem.status === 'success' ? '✓' : execItem.status === 'error' ? '✗' : '⚡';
      const durationBadge =
        execItem.duration_seconds !== null && execItem.duration_seconds !== undefined
          ? `${execItem.duration_seconds}s`
          : execItem.status === 'running'
          ? 'running…'
          : '';

      const mainDiv = document.createElement('div');
      mainDiv.className = 'timeline-main';

      const titleDiv = document.createElement('div');
      titleDiv.className = 'timeline-title';
      titleDiv.textContent = `${statusIcon} ${execItem.name}`;

      const subDiv = document.createElement('div');
      subDiv.className = 'timeline-sub';
      subDiv.textContent =
        execItem.description ||
        (execItem.arguments ? String(execItem.arguments).slice(0, 90) : 'Click to inspect');

      mainDiv.appendChild(titleDiv);
      mainDiv.appendChild(subDiv);

      const metaDiv = document.createElement('div');
      metaDiv.className = 'timeline-meta';
      if (execItem.diff) {
        const diffBadge = document.createElement('span');
        diffBadge.className = 'file-diff-pill';
        diffBadge.textContent = 'DIFF';
        metaDiv.appendChild(diffBadge);
      }
      const durSpan = document.createElement('span');
      durSpan.textContent = durationBadge;
      metaDiv.appendChild(durSpan);

      rowEl.appendChild(mainDiv);
      rowEl.appendChild(metaDiv);
      rowEl.addEventListener('click', () => openToolInspector(execItem));
      timelineListEl.appendChild(rowEl);
    });
  }

  // 3. Memory Nebula Visualization (Deterministic Layout + Tooltip + Inspector)
  function computeDeterministicCoords(nodeIdentifier, indexNumber) {
    let hashNumber = 0;
    const idString = String(nodeIdentifier);
    for (let charIdx = 0; charIdx < idString.length; charIdx++) {
      hashNumber = (hashNumber * 31 + idString.charCodeAt(charIdx)) | 0;
    }
    const goldenAngle = indexNumber * 2.399963229728653 + (hashNumber % 360) * 0.01745;
    const radius = Math.min(115, 22 + Math.sqrt(indexNumber + 1) * 22);
    return {
      coordX: Math.cos(goldenAngle) * radius,
      coordY: Math.sin(goldenAngle) * radius,
    };
  }

  function updateMemoryNebula(payload) {
    if (!payload) return;
    const validActions = ['saved', 'retrieved', 'association', 'primer'];
    if (!validActions.includes(payload.action)) return;

    const memoryId = payload.id || `mem-${memoryNodesMap.size + 1}`;
    const existingNode = memoryNodesMap.get(memoryId);
    const coords = existingNode
      ? { coordX: existingNode.coordX, coordY: existingNode.coordY }
      : computeDeterministicCoords(memoryId, memoryNodesMap.size);

    memoryNodesMap.set(memoryId, {
      id: memoryId,
      content: payload.content || '',
      relevance: Number(payload.relevance || 0.8),
      action: payload.action,
      tags: Array.isArray(payload.tags) ? payload.tags : [],
      coordX: coords.coordX,
      coordY: coords.coordY,
    });

    if (memoryCountEl) {
      memoryCountEl.textContent = `${memoryNodesMap.size} Nodes`;
    }
    renderD3MemoryNebula();
  }

  function renderD3MemoryNebula() {
    const svg = d3.select('#memory-nebula');
    const tooltipEl = document.getElementById('memory-tooltip');
    if (svg.empty()) return;

    const width = svg.node().clientWidth || 400;
    const height = svg.node().clientHeight || 280;

    svg.selectAll('*').remove();

    const memoryArray = Array.from(memoryNodesMap.values());
    if (memoryArray.length === 0) {
      svg
        .append('text')
        .attr('x', width / 2)
        .attr('y', height / 2)
        .attr('text-anchor', 'middle')
        .attr('fill', '#64748b')
        .attr('font-size', '12px')
        .attr('font-family', 'monospace')
        .text('Awaiting memory retrieval / associations...');
      return;
    }

    const centerGroup = svg
      .append('g')
      .attr('transform', `translate(${width / 2},${height / 2})`);

    // Subtle links from center to memory nodes
    centerGroup
      .selectAll('line')
      .data(memoryArray)
      .enter()
      .append('line')
      .attr('x1', 0)
      .attr('y1', 0)
      .attr('x2', (memDatum) => memDatum.coordX)
      .attr('y2', (memDatum) => memDatum.coordY)
      .attr('stroke', 'rgba(148, 163, 184, 0.14)')
      .attr('stroke-width', 1);

    const nodeGroups = centerGroup
      .selectAll('g')
      .data(memoryArray)
      .enter()
      .append('g')
      .attr('transform', (memDatum) => `translate(${memDatum.coordX},${memDatum.coordY})`)
      .style('cursor', 'pointer')
      .on('mouseenter', (mouseEvent, memDatum) => {
        if (!tooltipEl) return;
        const tagsLabel =
          memDatum.tags && memDatum.tags.length ? ` [${memDatum.tags.join(', ')}]` : '';
        tooltipEl.textContent = `${memDatum.action.toUpperCase()}${tagsLabel}: ${memDatum.content}`;
        tooltipEl.classList.remove('hidden');
        tooltipEl.style.left = `${Math.min(width - 260, mouseEvent.offsetX + 12)}px`;
        tooltipEl.style.top = `${Math.max(10, mouseEvent.offsetY - 10)}px`;
      })
      .on('mouseleave', () => {
        if (tooltipEl) tooltipEl.classList.add('hidden');
      })
      .on('click', (_clickEvent, memDatum) => {
        openMemoryInspector(memDatum);
      });

    nodeGroups
      .append('circle')
      .attr('r', (memDatum) => Math.max(6, memDatum.relevance * 11))
      .attr('fill', (memDatum) => {
        if (memDatum.action === 'saved') return '#38bdf8';
        if (memDatum.action === 'association') return '#f59e0b';
        if (memDatum.action === 'primer') return '#22c55e';
        return '#a855f7';
      })
      .attr('stroke', '#e2e8f0')
      .attr('stroke-width', 1)
      .attr('opacity', 0.88)
      .style('filter', 'drop-shadow(0 0 8px rgba(56, 189, 248, 0.65))');

    nodeGroups
      .append('text')
      .text((memDatum) => String(memDatum.id).slice(0, 12))
      .attr('x', 12)
      .attr('y', 3)
      .attr('fill', '#94a3b8')
      .attr('font-size', '9px')
      .attr('font-family', 'monospace');
  }

  // 4. Filesystem, Diff & Todo Inspector
  const workspaceTabs = document.getElementById('workspace-tabs');
  if (workspaceTabs) {
    workspaceTabs.addEventListener('click', (clickEvent) => {
      const tabBtn = clickEvent.target.closest('.tab-btn');
      if (!tabBtn) return;
      workspaceTabs.querySelectorAll('.tab-btn').forEach((btn) => btn.classList.remove('active'));
      tabBtn.classList.add('active');
      if (tabBtn.dataset.tab === 'todos') {
        fileInspectorEl.classList.add('hidden');
        todoInspectorEl.classList.remove('hidden');
      } else {
        todoInspectorEl.classList.add('hidden');
        fileInspectorEl.classList.remove('hidden');
      }
    });
  }

  if (fileSearchInput) {
    fileSearchInput.addEventListener('input', () => {
      fileSearchQuery = fileSearchInput.value.trim().toLowerCase();
      renderFilesList();
    });
  }

  function updateFilesystemInspector(payload) {
    if (!payload) return;

    if (payload.files) {
      trackedFiles = payload.files;
    } else if (payload.path) {
      trackedFiles[payload.path] = payload.action || 'modified';
    }
    if (payload.details) {
      fileDetails = payload.details;
    }

    renderFilesList();
  }

  function renderFilesList() {
    if (!fileInspectorEl) return;
    const allPaths = Object.keys(trackedFiles);
    if (fileCountEl) fileCountEl.textContent = `${allPaths.length} Tracked`;

    const filteredPaths = allPaths.filter(
      (filePath) => !fileSearchQuery || filePath.toLowerCase().includes(fileSearchQuery)
    );

    if (filteredPaths.length === 0) {
      fileInspectorEl.innerHTML = '<div class="empty-state">No matching files tracked.</div>';
      return;
    }

    fileInspectorEl.innerHTML = '';
    filteredPaths.forEach((filePath) => {
      const actionType = trackedFiles[filePath];
      const detailRecord = fileDetails[filePath] || {};
      const itemEl = document.createElement('div');
      itemEl.className = 'file-item';

      const pathSpan = document.createElement('span');
      pathSpan.className = 'file-path';
      pathSpan.textContent = filePath;
      pathSpan.title = filePath;

      const badgesContainer = document.createElement('div');
      badgesContainer.className = 'file-badges';

      if (detailRecord.touch_count && detailRecord.touch_count > 1) {
        const countSpan = document.createElement('span');
        countSpan.className = 'file-touch-count';
        countSpan.textContent = `${detailRecord.touch_count}×`;
        badgesContainer.appendChild(countSpan);
      }

      if (detailRecord.diff) {
        const diffPill = document.createElement('span');
        diffPill.className = 'file-diff-pill';
        diffPill.textContent = 'DIFF';
        badgesContainer.appendChild(diffPill);
      }

      const actSpan = document.createElement('span');
      actSpan.className = `file-action ${actionType}`;
      actSpan.textContent = actionType;
      badgesContainer.appendChild(actSpan);

      itemEl.appendChild(pathSpan);
      itemEl.appendChild(badgesContainer);
      itemEl.addEventListener('click', () => openFileInspector(filePath, actionType, detailRecord));
      fileInspectorEl.appendChild(itemEl);
    });
  }

  function renderTodoInspector(todosList) {
    if (todoCountEl) {
      todoCountEl.textContent = String(todosList.length);
    }
    if (!todoInspectorEl) return;
    if (todosList.length === 0) {
      todoInspectorEl.innerHTML = '<div class="empty-state">No active todo items.</div>';
      return;
    }
    todoInspectorEl.innerHTML = '';
    todosList.forEach((todoItem) => {
      const isDone = Boolean(todoItem.completed || todoItem.done);
      const rowEl = document.createElement('div');
      rowEl.className = `todo-item ${isDone ? 'completed' : ''}`;
      rowEl.textContent = `${isDone ? '☑' : '☐'} ${todoItem.text || todoItem.title || ''}`;
      todoInspectorEl.appendChild(rowEl);
    });
  }

  // 5. Slide-Over Inspector Drawer Helpers
  if (drawerCloseBtn) {
    drawerCloseBtn.addEventListener('click', () => {
      if (drawerEl) drawerEl.classList.add('hidden');
    });
  }

  function formatJsonIfPossible(rawString) {
    if (!rawString) return '';
    try {
      const parsed = JSON.parse(rawString);
      return JSON.stringify(parsed, null, 2);
    } catch (_err) {
      return String(rawString);
    }
  }

  function createColoredDiffBlock(diffString) {
    const preEl = document.createElement('pre');
    preEl.className = 'code-block';
    const diffLines = String(diffString).split('\n');
    diffLines.forEach((lineText) => {
      const lineSpan = document.createElement('span');
      if (lineText.startsWith('+') && !lineText.startsWith('+++')) {
        lineSpan.className = 'diff-line-add';
      } else if (lineText.startsWith('-') && !lineText.startsWith('---')) {
        lineSpan.className = 'diff-line-del';
      } else if (lineText.startsWith('@@')) {
        lineSpan.className = 'diff-line-hunk';
      }
      lineSpan.textContent = lineText + '\n';
      preEl.appendChild(lineSpan);
    });
    return preEl;
  }

  function openToolInspector(execItem) {
    if (!drawerEl) return;
    drawerBadgeEl.textContent = (execItem.kind || 'TOOL').toUpperCase();
    drawerTitleEl.textContent = execItem.name;
    drawerMetaEl.textContent = `Status: ${execItem.status || 'unknown'} | ID: ${execItem.id}${
      execItem.duration_seconds !== null && execItem.duration_seconds !== undefined
        ? ` | Duration: ${execItem.duration_seconds}s`
        : ''
    }`;

    drawerBodyEl.innerHTML = '';

    if (execItem.arguments) {
      const argLabel = document.createElement('div');
      argLabel.className = 'drawer-section-label';
      argLabel.textContent = 'Arguments';
      const argPre = document.createElement('pre');
      argPre.className = 'code-block';
      argPre.textContent = formatJsonIfPossible(execItem.arguments);
      drawerBodyEl.appendChild(argLabel);
      drawerBodyEl.appendChild(argPre);
    }

    if (execItem.diff) {
      const diffLabel = document.createElement('div');
      diffLabel.className = 'drawer-section-label';
      diffLabel.textContent = 'Unified Diff';
      drawerBodyEl.appendChild(diffLabel);
      drawerBodyEl.appendChild(createColoredDiffBlock(execItem.diff));
    }

    if (execItem.output) {
      const outLabel = document.createElement('div');
      outLabel.className = 'drawer-section-label';
      outLabel.textContent = 'Output';
      const outPre = document.createElement('pre');
      outPre.className = 'code-block';
      outPre.textContent = formatJsonIfPossible(execItem.output);
      drawerBodyEl.appendChild(outLabel);
      drawerBodyEl.appendChild(outPre);
    }

    drawerEl.classList.remove('hidden');
  }

  function openFileInspector(filePath, actionType, detailRecord) {
    if (!drawerEl) return;
    drawerBadgeEl.textContent = actionType.toUpperCase();
    drawerTitleEl.textContent = filePath;
    drawerMetaEl.textContent = `Touches: ${detailRecord.touch_count || 1} | Last Tool: ${
      detailRecord.tool_name || 'filesystem'
    }`;

    drawerBodyEl.innerHTML = '';
    if (detailRecord.diff) {
      const diffLabel = document.createElement('div');
      diffLabel.className = 'drawer-section-label';
      diffLabel.textContent = 'Latest Diff';
      drawerBodyEl.appendChild(diffLabel);
      drawerBodyEl.appendChild(createColoredDiffBlock(detailRecord.diff));
    } else {
      const infoBlock = document.createElement('div');
      infoBlock.className = 'code-block';
      infoBlock.textContent = `File ${filePath} was ${actionType} during this session.`;
      drawerBodyEl.appendChild(infoBlock);
    }
    drawerEl.classList.remove('hidden');
  }

  function openMemoryInspector(memDatum) {
    if (!drawerEl) return;
    drawerBadgeEl.textContent = `MEMORY (${memDatum.action.toUpperCase()})`;
    drawerTitleEl.textContent = memDatum.id;
    drawerMetaEl.textContent = `Relevance: ${(memDatum.relevance * 100).toFixed(0)}% | Tags: ${
      memDatum.tags && memDatum.tags.length ? memDatum.tags.join(', ') : 'none'
    }`;

    drawerBodyEl.innerHTML = '';
    const contentBlock = document.createElement('pre');
    contentBlock.className = 'code-block';
    contentBlock.textContent = memDatum.content;
    drawerBodyEl.appendChild(contentBlock);
    drawerEl.classList.remove('hidden');
  }

  function openNodeInspector(nodeDatum) {
    if (!drawerEl) return;
    drawerBadgeEl.textContent = (nodeDatum.kind || 'NODE').toUpperCase();
    drawerTitleEl.textContent = nodeDatum.name;
    drawerMetaEl.textContent = `Status: ${nodeDatum.status} | Calls: ${nodeDatum.calls || 0}`;

    drawerBodyEl.innerHTML = '';
    const taskBlock = document.createElement('pre');
    taskBlock.className = 'code-block';
    taskBlock.textContent = nodeDatum.task || 'Idle';
    drawerBodyEl.appendChild(taskBlock);
    drawerEl.classList.remove('hidden');
  }

  // Start WebSocket on Load
  window.addEventListener('DOMContentLoaded', () => {
    renderD3MemoryNebula();
    connectWebSocket();
  });
})();
