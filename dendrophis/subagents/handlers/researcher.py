"""Researcher subagent handler — read-only analysis."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dendrophis.subagents.messages import SubagentRequest, SubagentResponse

try:
    from dendrophis.tools.builtins.filesystem import GlobTool, ReadTool, RipgrepTool
except ImportError:
    GlobTool = None
    ReadTool = None
    RipgrepTool = None

if TYPE_CHECKING:
    from dendrophis.memory import MemoryStore


class ResearcherHandler:
    """Handler for researcher subagent."""

    def __init__(self, memory_store: MemoryStore | None = None) -> None:
        self.glob_tool = GlobTool() if GlobTool is not None else None
        self.read_tool = ReadTool() if ReadTool is not None else None
        self.ripgrep_tool = RipgrepTool() if RipgrepTool is not None else None
        self._memory_store = memory_store

    def _get_memory_tools(self):
        """Lazy init memory tools if store available."""
        if self._memory_store is None:
            return None, None
        from dendrophis.tools.builtins.memory import RecallMemoryTool, SearchMemoryTool

        return SearchMemoryTool(self._memory_store), RecallMemoryTool(self._memory_store)

    async def execute(self, request: SubagentRequest) -> SubagentResponse:
        """Execute research task."""
        query = request.payload.get("query", "")
        sources = request.payload.get("sources", ["files", "memory", "codebase"])
        depth = request.payload.get("depth", "quick")
        context = request.context

        findings: list[dict[str, Any]] = []
        gaps: list[str] = []

        try:
            # Search codebase if requested
            if "codebase" in sources or "files" in sources:
                file_findings = await self._search_codebase(query, context)
                findings.extend(file_findings)

            # Search memory if requested
            if "memory" in sources:
                memory_findings = await self._search_memories(query, context)
                findings.extend(memory_findings)

            # Sort by relevance and limit based on depth
            findings.sort(key=lambda x: x.get("relevance", 0), reverse=True)
            if depth == "quick":
                findings = findings[:5]
            elif depth == "thorough":
                findings = findings[:15]
            # exhaustive = no limit

            # Synthesize
            synthesis = self._synthesize(query, findings)

            return SubagentResponse(
                agent="researcher",
                task_id=request.task_id,
                status="success",
                result={
                    "findings": findings,
                    "synthesis": synthesis,
                    "gaps": gaps if findings else ["No relevant information found"],
                    "confidence": self._calculate_confidence(findings),
                },
            )

        except Exception as e:
            return SubagentResponse(
                agent="researcher",
                task_id=request.task_id,
                status="failure",
                result={"error": str(e)},
            )

    async def _search_codebase(self, query: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Search codebase for relevant files and content."""
        findings = []

        # Use ripgrep for content search
        if self.ripgrep_tool is not None:
            try:
                rg_result = await self.ripgrep_tool.execute(
                    pattern=query[:50],  # Truncate for regex safety
                    include="*.py",
                )
                if isinstance(rg_result, dict) and "matches" in rg_result:
                    findings.extend(
                        [
                            {
                                "source": f"{match['file']}:{match['line']}",
                                "relevance": 0.7,
                                "summary": match["content"][:200],
                                "type": "code",
                            }
                            for match in rg_result["matches"][:5]  # Limit initial matches
                        ]
                    )
            except Exception:
                pass  # ripgrep might fail on complex patterns

        # Check specific files if provided
        for file_path in context.get("file_paths", []):
            if self.read_tool is None:
                continue
            try:
                content = await self.read_tool.execute(file_path=file_path)
                if isinstance(content, dict) and "content" in content:
                    # Simple relevance: does query appear in content?
                    relevance = 0.5
                    if query.lower() in content["content"].lower():
                        relevance = 0.9

                    findings.append(
                        {
                            "source": file_path,
                            "relevance": relevance,
                            "summary": f"File content ({content.get('total_lines', 0)} lines)",
                            "type": "file",
                        }
                    )
            except Exception:
                pass

        return findings

    async def _search_memories(self, query: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Search memory for relevant entries."""
        findings = []

        search_tool, _ = self._get_memory_tools()
        if search_tool is None:
            return findings

        try:
            result = await search_tool.execute(
                query=query,
                limit=context.get("memory_limit", 5),
            )
            if isinstance(result, dict) and "results" in result:
                findings.extend(
                    [
                        {
                            "source": f"memory:{mem.get('memory_id', 'unknown')}",
                            "relevance": mem.get("score", 0.5),
                            "summary": mem.get("summary", "")[:200],
                            "type": "memory",
                        }
                        for mem in result["results"]
                    ]
                )
        except Exception:
            pass

        return findings

    def _synthesize(self, query: str, findings: list[dict[str, Any]]) -> str:
        """Create synthesis from findings."""
        if not findings:
            return f"No information found for: {query}"

        # Simple synthesis: summarize top findings
        top = findings[:3]
        parts = [f"Found {len(findings)} relevant items for '{query}':"]
        parts.extend([f"- {f['source']}: {f['summary'][:100]}..." for f in top])

        return "\n".join(parts)

    def _calculate_confidence(self, findings: list[dict[str, Any]]) -> str:
        """Calculate confidence level based on findings."""
        if not findings:
            return "low"
        avg_relevance = sum(f.get("relevance", 0) for f in findings) / len(findings)
        if avg_relevance > 0.7:
            return "high"
        if avg_relevance > 0.4:
            return "medium"
        return "low"
