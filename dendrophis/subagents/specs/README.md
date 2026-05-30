# Subagent Specifications

Complete specifications for the dendrophis subagent architecture.

## Agents

| Agent | Purpose | Modifies Files? |
|-------|---------|-----------------|
| orchestrator | User-facing coordinator, delegates to workers | No |
| researcher | Gather and synthesize information | No |
| planner | Decompose tasks into executable steps | No |
| code-writer | Implement changes precisely | Yes |
| code-reviewer | Review changes for correctness | No |
| test-runner | Execute tests, analyze failures | No |
| debugger | Diagnose root causes | No |

## Architecture

- **Orchestrator** is the only user-facing agent
- All other agents are headless workers invoked by orchestrator
- Each agent has strict input/output schemas
- Agents communicate via structured messages, not raw text
- Execution is sequential within a plan, parallel where dependencies allow
