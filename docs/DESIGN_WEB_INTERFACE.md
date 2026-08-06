# Design Document: Dendrophis Cognitive Observability Interface

## 1. Introduction
The Dendrophis Interface is a real-time web-based observability platform designed to visualize the internal cognitive processes of the Dendrophis agent. Unlike a standard chat interface, this platform provides a "window into the machine," allowing users to monitor subagent orchestration, thought processes, memory retrieval, and filesystem interactions as they happen.

## 2. Core Philosophy
* **Python-Centric:** Logic is implemented via Brython to maintain a unified Python mental model.
* **Minimalist & High-Performance:** Avoidance of heavy JS frameworks (React/Tailwind) in favor of vanilla CSS and direct D3.js integration.
* **Real-Time Transparency:** Every internal "thought" and subagent transition must be reflected visually with minimal latency.
* **Organic Machine Aesthetic:** A dark, terminal-inspired UI that feels alive, using subtle animations to represent computational flow.

## 3. Technical Stack
* **Backend:** FastAPI (Python) providing a WebSocket stream for real-time event broadcasting.
* **Frontend Logic:** Brython (Python in the browser) for DOM manipulation and event handling.
* **Visualization Engine:** D3.js (via Brython `window` calls) for complex graph and nebula renderings.
* **Templating:** Handlebars.js for server-side/client-side component structure.
* **Styling:** Vanilla CSS (Monospace, dark-theme, high contrast).
* **Communication Protocol:** JSON-based WebSocket messages.

## 4. Architectural Components

### 4.1 The Thought Stream (`<div id="thought-stream">`)
A scrolling, chronological log of the agent's internal reasoning.
* **Function:** Displays `THOUGHT_LOG` events.
* **Visuals:** Color-coded text levels (Info, Warning, Error, Success) with a monospace font.

### 4.2 The Subagent Orchestrator (`<svg id="subagent-graph">`)
A force-directed graph visualizing the active hierarchy of subagents.
* **Function:** Displays `SUBAGENT_STATE` events.
* **Nodes:** Represent subagents (e.g., `planner`, `researcher`, `code-writer`).
* **Edges:** Represent active calls or task delegations.
* **Interactivity:** Nodes change color/pulse based on activity status (Thinking, Executing, Idle).

### 4.3 The Memory Nebula (`<svg id="memory-nebula">`)
A cluster diagram visualizing the relationship between current context and retrieved long-term memories.
* **Function:** Displays `MEMORY_RETRIEVAL` events.
* **Visuals:** Nodes represent memory fragments; proximity represents semantic relevance to the current task.

### 4.4 The Filesystem Inspector (`<div id="file-inspector">`)
A live view of the agent's current working directory and active files.
* **Function:** Displays `FILESYSTEM_CHANGE` events.
* **Visuals:** A hierarchical tree view with indicators for modified or active files.

## 5. Data Protocol (WebSocket Schema)
All communication follows a strict JSON structure:
```json
{
  "type": "EVENT_TYPE",
  "payload": { ... },
  "timestamp": "ISO-8601"
}
```

**Supported Event Types:**
| Type | Payload Content | Target Component |
| :--- | :--- | :--- |
| `THOUGHT_LOG` | `{ "text": string, "level": string }` | Thought Stream |
| `SUBAGENT_STATE` | `{ "name": string, "status": string, "task": string }` | Subagent Graph |
| `MEMORY_RETRIEVAL`| `{ "id": string, "content": string, "relevance": float }` | Memory Nebula |
| `FILESYSTEM_CHANGE`| `{ "path": string, "action": string }` | File Inspector |

## 6. Implementation Roadmap
1.  **Phase 1 (Infrastructure):** Setup FastAPI backend and WebSocket broadcast loop.
2.  **Phase 2 (The Shell):** Create HTML/Handlebars templates and basic CSS layout.
3.  **Phase 3 (The Pulse):** Implement Brython logic to consume WebSockets and populate the Thought Stream.
4.  **Phase 4 (The Vision):** Integrate D3.js for the Subagent Graph and Memory Nebula.
5.  **Phase 5 (The Mirror):** Connect the actual Dendrophis agent logs to the interface for live demonstration.
