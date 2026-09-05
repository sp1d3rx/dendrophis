"""Researcher subagent handler — read-only codebase and memory analysis."""

from __future__ import annotations

import contextlib
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dendrophis.config.schema import DendrophisConfig
from dendrophis.events import TextDeltaEvent
from dendrophis.llm.client import LLMClient
from dendrophis.subagents.messages import SubagentRequest, SubagentResponse

try:
    from dendrophis.tools.builtins.filesystem import GlobTool, ReadTool, RipgrepTool
except ImportError:
    GlobTool = None
    ReadTool = None
    RipgrepTool = None

if TYPE_CHECKING:
    from dendrophis.memory import MemoryStore

logger = logging.getLogger(__name__)

RESEARCHER_SYNTHESIS_SYSTEM_PROMPT = """You are Dendrophis Researcher, an expert codebase research subagent.

Your job is to analyze gathered code snippets, directory matches, and project memories to provide an authoritative,
accurate, and concise research report answering the user's research query.

### Requirements:
1. Answer the query directly and precisely with specific file paths, function/class names, and line references.
2. Explain the architectural flow, component interactions, and key design patterns found in the evidence.
3. If there are uncertainties or areas not covered in the evidence, clearly identify them under 'Knowledge Gaps'.
4. Format your output in clean Markdown (no LaTeX math formatting; use unicode arrows -> or →).
"""


class ResearcherHandler:
    """Handler for researcher subagent."""

    def __init__(
        self,
        memory_store: MemoryStore | None = None,
        llm_client: LLMClient | None = None,
        config: DendrophisConfig | None = None,
    ) -> None:
        self.glob_tool = GlobTool() if GlobTool is not None else None
        self.read_tool = ReadTool() if ReadTool is not None else None
        self.ripgrep_tool = RipgrepTool() if RipgrepTool is not None else None
        self._memory_store = memory_store
        self._llm_client = llm_client
        self._owned_llm_clients: set[LLMClient] = set()
        self._config = config
        self._logger = logger

    async def __call__(self, request: SubagentRequest) -> SubagentResponse:
        return await self.execute(request)

    @property
    def llm(self) -> LLMClient | None:
        """Lazily obtain or create LLM client (cached; closed by aclose)."""
        if self._llm_client is not None:
            return self._llm_client

        if self._config is not None:
            self._llm_client = LLMClient(self._config.llm)
            self._owned_llm_clients.add(self._llm_client)
            return self._llm_client

        try:
            from dendrophis.config.loader import ConfigLoader

            config_loader = ConfigLoader.load()
            self._llm_client = LLMClient(config_loader.config.llm)
            self._owned_llm_clients.add(self._llm_client)
            return self._llm_client
        except Exception as exc:
            self._logger.debug("Could not create LLM client from default config: %s", exc)
            return None

    async def aclose(self) -> None:
        """Close LLM clients this handler created itself.

        Injected clients are owned by the caller and are not closed here.
        The owned client slot is reset so a later use recreates a fresh client.
        """
        for client in self._owned_llm_clients:
            with contextlib.suppress(Exception):
                await client.aclose()
        if self._llm_client in self._owned_llm_clients:
            self._llm_client = None
        self._owned_llm_clients.clear()

    def _get_memory_tools(self):
        """Lazy init memory tools if store available."""
        if self._memory_store is None:
            return None, None
        from dendrophis.tools.builtins.memory import RecallMemoryTool, SearchMemoryTool

        return SearchMemoryTool(self._memory_store), RecallMemoryTool(self._memory_store)

    async def execute(self, request: SubagentRequest) -> SubagentResponse:
        """Execute research task."""
        query_text = (
            request.payload.get("query") or request.payload.get("task") or request.payload.get("question") or ""
        )
        sources_list = request.payload.get("sources", ["files", "memory", "codebase"])
        depth_level = request.payload.get("depth", "quick")
        context_data = request.context or {}

        findings_list: list[dict[str, Any]] = []
        search_metadata: dict[str, Any] = {
            "patterns_attempted": [],
            "match_counts": {},
            "code_findings": 0,
            "errors": [],
        }

        try:
            # 1. Search codebase / files if requested
            if "codebase" in sources_list or "files" in sources_list:
                codebase_findings, codebase_meta = await self._search_codebase(query_text, context_data, sources_list)
                findings_list.extend(codebase_findings)
                search_metadata["patterns_attempted"].extend(codebase_meta.get("patterns_attempted", []))
                search_metadata["match_counts"].update(codebase_meta.get("match_counts", {}))
                search_metadata["errors"].extend(codebase_meta.get("errors", []))
                search_metadata["code_findings"] = len(codebase_findings)

            # 2. Search memory if requested
            if "memory" in sources_list:
                memory_findings = await self._search_memories(query_text, context_data)
                findings_list.extend(memory_findings)

            # 3. Sort by relevance and limit based on depth
            total_findings_count = len(findings_list)
            findings_list.sort(key=lambda item: item.get("relevance", 0), reverse=True)
            if depth_level == "quick":
                findings_list = findings_list[:12]
            elif depth_level == "thorough":
                findings_list = findings_list[:30]

            search_metadata["code_findings_discovered"] = search_metadata["code_findings"]
            search_metadata["findings_returned"] = len(findings_list)
            search_metadata["capped"] = total_findings_count > len(findings_list)

            # 4. Synthesize with LLM if available, otherwise template synthesis
            synthesis_summary, knowledge_gaps = await self._synthesize(query_text, findings_list, context_data)

            return SubagentResponse(
                agent="researcher",
                task_id=request.task_id,
                status="success",
                result={
                    "query": query_text,
                    "findings": findings_list,
                    "synthesis": synthesis_summary,
                    "gaps": knowledge_gaps,
                    "confidence": self._calculate_confidence(findings_list),
                    "search_meta": search_metadata,
                },
            )

        except Exception as research_error:
            self._logger.error(f"Researcher execution failed: {research_error}", exc_info=True)
            return SubagentResponse(
                agent="researcher",
                task_id=request.task_id,
                status="failure",
                result={"error": str(research_error), "search_meta": search_metadata},
            )

    def _derive_search_patterns(self, query_text: str, context_data: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Derive content search patterns and filename candidates from context and query text."""
        content_patterns_set: set[str] = set()
        filename_candidates_set: set[str] = set()

        # Common file extensions
        file_extensions = (
            ".py",
            ".md",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".rst",
            ".txt",
            ".ts",
            ".js",
            ".html",
            ".css",
            ".sh",
        )

        # 1. Explicit patterns passed by orchestrator
        explicit_patterns = context_data.get("patterns") or context_data.get("pattern_list") or []
        for explicit_pattern in explicit_patterns:
            if isinstance(explicit_pattern, str) and explicit_pattern.strip():
                trimmed_pattern = explicit_pattern.strip()
                if any(trimmed_pattern.endswith(extension_item) for extension_item in file_extensions):
                    filename_candidates_set.add(trimmed_pattern)
                else:
                    content_patterns_set.add(trimmed_pattern)

        # 2. Extract backticked symbols from query (e.g. `invoke_subagent` or `README.md`)
        backticked_symbols = re.findall(r"`([^`]+)`", query_text)
        for symbol_item in backticked_symbols:
            trimmed_symbol = symbol_item.strip()
            if trimmed_symbol:
                if any(trimmed_symbol.endswith(extension_item) for extension_item in file_extensions):
                    filename_candidates_set.add(trimmed_symbol)
                else:
                    content_patterns_set.add(trimmed_symbol)

        # 3. Extract identifier and filename candidates
        stop_words = {
            "a",
            "an",
            "the",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "can",
            "could",
            "should",
            "would",
            "of",
            "to",
            "in",
            "for",
            "on",
            "with",
            "by",
            "from",
            "at",
            "about",
            "into",
            "and",
            "or",
            "but",
            "not",
            "as",
            "if",
            "this",
            "that",
            "these",
            "those",
            "how",
            "what",
            "where",
            "when",
            "which",
            "why",
            "who",
            "whom",
            "map",
            "report",
            "identify",
            "survey",
            "check",
            "find",
            "list",
            "show",
            "explain",
            "tell",
            "give",
            "me",
            "please",
            "structure",
            "responsible",
        }
        word_tokens = re.findall(r"\b[A-Za-z0-9_.-]+\b", query_text)
        extracted_keywords: list[str] = []

        for word_token in word_tokens:
            token_lower = word_token.lower()
            if token_lower in stop_words:
                continue

            # Check if token is a filename candidate
            if any(word_token.endswith(extension_item) for extension_item in file_extensions):
                filename_candidates_set.add(word_token)
            elif len(word_token) >= 4 and (
                "_" in word_token or word_token[0].isupper() or any(character.isupper() for character in word_token[1:])
            ):
                content_patterns_set.add(word_token)
            elif len(word_token) >= 4 and token_lower not in stop_words:
                extracted_keywords.append(word_token)

        # 4. Fallback for natural language tasks without explicit patterns or code identifiers
        if not content_patterns_set and not filename_candidates_set and extracted_keywords:
            # Sort extracted domain keywords by length descending (longer terms are typically more specific)
            unique_keywords = list(dict.fromkeys(extracted_keywords))
            unique_keywords.sort(key=len, reverse=True)
            for domain_keyword in unique_keywords[:6]:
                content_patterns_set.add(domain_keyword)

        return sorted(content_patterns_set), sorted(filename_candidates_set)

    async def _search_codebase(
        self, query: str, context: dict[str, Any], sources: list[str] | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Search codebase for relevant files and content."""
        codebase_findings: list[dict[str, Any]] = []
        search_metadata: dict[str, Any] = {
            "patterns_attempted": [],
            "match_counts": {},
            "errors": [],
        }

        active_sources = sources if sources is not None else ["files", "codebase"]
        search_path = context.get("path")
        include_filter = context.get("include")
        already_processed_files: set[str] = set()

        derived_content_patterns, derived_filename_candidates = self._derive_search_patterns(query, context)

        def _normalize_file_key(raw_path_string: str) -> str:
            try:
                candidate_path = Path(raw_path_string)
                if candidate_path.is_absolute():
                    current_working_directory = Path.cwd().resolve()
                    if candidate_path.resolve().is_relative_to(current_working_directory):
                        return str(candidate_path.resolve().relative_to(current_working_directory))
                return str(candidate_path)
            except Exception:
                return raw_path_string

        # 1. Resolve filename candidates via GlobTool or file existence
        for filename_candidate in derived_filename_candidates:
            search_metadata["patterns_attempted"].append(f"file:{filename_candidate}")
            discovered_paths: list[str] = []

            # Check direct existence at root or search_path
            for candidate_base in (Path.cwd(), Path(search_path) if search_path else Path.cwd()):
                direct_file_path = candidate_base / filename_candidate
                if direct_file_path.is_file():
                    normalized_key = _normalize_file_key(str(direct_file_path))
                    if normalized_key not in discovered_paths:
                        discovered_paths.append(normalized_key)

            # Run GlobTool if available
            if self.glob_tool is not None and not discovered_paths:
                try:
                    glob_result = await self.glob_tool.execute(
                        pattern=f"**/{filename_candidate}",
                        path=search_path,
                    )
                    if isinstance(glob_result, dict) and "files" in glob_result:
                        for globbed_file in glob_result["files"][:3]:
                            normalized_key = _normalize_file_key(globbed_file)
                            if normalized_key not in discovered_paths:
                                discovered_paths.append(normalized_key)
                except Exception as glob_error:
                    self._logger.debug(f"Glob search failed for {filename_candidate}: {glob_error}")

            search_metadata["match_counts"][f"file:{filename_candidate}"] = len(discovered_paths)

            # Read discovered files (avoiding re-reading already processed files)
            if self.read_tool is not None:
                for discovered_path in discovered_paths:
                    if discovered_path in already_processed_files:
                        continue
                    already_processed_files.add(discovered_path)
                    try:
                        read_result = await self.read_tool.execute(file_path=discovered_path)
                        if isinstance(read_result, dict) and "content" in read_result:
                            file_text = read_result["content"]
                            preview_length = 8000
                            content_preview = file_text[:preview_length]
                            if len(file_text) > preview_length:
                                content_preview += f"\n... (+{len(file_text) - preview_length} more characters)"

                            codebase_findings.append(
                                {
                                    "source": discovered_path,
                                    "relevance": 0.95,
                                    "summary": content_preview,
                                    "type": "file",
                                }
                            )
                    except Exception as candidate_read_error:
                        self._logger.debug(f"File read failed for {discovered_path}: {candidate_read_error}")

        # 2. Execute ripgrep for each derived content pattern (only if codebase in sources)
        if self.ripgrep_tool is not None and "codebase" in active_sources:
            for search_pattern in derived_content_patterns:
                search_metadata["patterns_attempted"].append(search_pattern)
                try:
                    ripgrep_result = await self.ripgrep_tool.execute(
                        pattern=search_pattern,
                        path=search_path,
                        include=include_filter,
                    )
                    if isinstance(ripgrep_result, dict):
                        if "error" in ripgrep_result:
                            search_metadata["errors"].append(
                                f"ripgrep pattern '{search_pattern}': {ripgrep_result['error']}"
                            )
                            continue

                        file_match_entries = ripgrep_result.get("matches", [])
                        total_pattern_matches = 0
                        for file_match_entry in file_match_entries:
                            matched_file_path = file_match_entry.get("file", "")
                            individual_matches = file_match_entry.get("matches", [])
                            total_pattern_matches += len(individual_matches)

                            for line_match_item in individual_matches[:5]:
                                line_number = line_match_item.get("line", 1)
                                match_content = line_match_item.get("content", "").strip()
                                line_relevance = 0.85
                                if (
                                    match_content.startswith(("def ", "async def ", "class "))
                                    or "register_" in match_content
                                    or " = " in match_content
                                ):
                                    line_relevance = 0.92

                                codebase_findings.append(
                                    {
                                        "source": f"{matched_file_path}:{line_number}",
                                        "relevance": line_relevance,
                                        "summary": match_content[:400],
                                        "type": "code",
                                    }
                                )
                        search_metadata["match_counts"][search_pattern] = total_pattern_matches
                except Exception as ripgrep_error:
                    error_message = f"Ripgrep search failed for pattern '{search_pattern}': {ripgrep_error}"
                    self._logger.debug(error_message)
                    search_metadata["errors"].append(error_message)

        # Check explicitly provided file paths
        specified_files = context.get("files") or context.get("file_paths") or []
        depth_setting = context.get("depth", "quick")
        for target_file in specified_files:
            normalized_target = _normalize_file_key(target_file)
            if normalized_target in already_processed_files:
                continue
            already_processed_files.add(normalized_target)

            if self.read_tool is None:
                continue
            try:
                read_result = await self.read_tool.execute(file_path=target_file)
                if isinstance(read_result, dict):
                    result_type = read_result.get("type")
                    if result_type == "file" and "content" in read_result:
                        file_text = read_result["content"]
                        relevance_score = 0.6
                        if query and query.lower() in file_text.lower():
                            relevance_score = 0.95

                        preview_length = 8000
                        content_preview = file_text[:preview_length]
                        if len(file_text) > preview_length:
                            content_preview += f"\n... (+{len(file_text) - preview_length} more characters)"

                        codebase_findings.append(
                            {
                                "source": normalized_target,
                                "relevance": relevance_score,
                                "summary": content_preview,
                                "type": "file",
                            }
                        )
                    elif result_type == "directory":
                        directory_entries = read_result.get("entries", [])
                        entry_preview = ", ".join(directory_entries[:25])
                        if len(directory_entries) > 25:
                            entry_preview += f" ... (+{len(directory_entries) - 25} more)"
                        codebase_findings.append(
                            {
                                "source": normalized_target,
                                "relevance": 0.85,
                                "summary": f"Directory listing ({len(directory_entries)} entries): {entry_preview}",
                                "type": "directory",
                            }
                        )
                        # Read top-level files in the directory for deeper context (limit in quick mode)
                        clean_dir_path = target_file.rstrip("/")
                        max_child_reads = 8 if depth_setting == "thorough" else 2
                        read_count = 0
                        for entry_name in directory_entries:
                            if not entry_name.endswith("/") and read_count < max_child_reads:
                                child_path = f"{clean_dir_path}/{entry_name}"
                                normalized_child = _normalize_file_key(child_path)
                                if normalized_child in already_processed_files:
                                    continue
                                already_processed_files.add(normalized_child)
                                try:
                                    child_read = await self.read_tool.execute(file_path=child_path)
                                    if isinstance(child_read, dict) and "content" in child_read:
                                        child_content = child_read["content"]
                                        child_relevance = 0.7
                                        if query and query.lower() in child_content.lower():
                                            child_relevance = 0.9
                                        codebase_findings.append(
                                            {
                                                "source": normalized_child,
                                                "relevance": child_relevance,
                                                "summary": child_content[:4000],
                                                "type": "file",
                                            }
                                        )
                                        read_count += 1
                                except Exception as child_read_error:
                                    self._logger.debug(f"Child file read error for {child_path}: {child_read_error}")
            except Exception as file_read_error:
                self._logger.debug(f"File read error for {target_file}: {file_read_error}")

        # If no explicit files were specified, provide structural directory context from search_path or workspace root
        if not specified_files and self.read_tool is not None:
            directory_target = search_path or "."
            normalized_dir_target = _normalize_file_key(directory_target)
            if normalized_dir_target not in already_processed_files:
                already_processed_files.add(normalized_dir_target)
                try:
                    dir_result = await self.read_tool.execute(file_path=directory_target)
                    if isinstance(dir_result, dict) and dir_result.get("type") == "directory":
                        dir_entries = dir_result.get("entries", [])
                        entry_preview = ", ".join(dir_entries[:35])
                        if len(dir_entries) > 35:
                            entry_preview += f" ... (+{len(dir_entries) - 35} more)"

                        summary_text = (
                            f"Directory structure for '{directory_target}' ({len(dir_entries)} entries): "
                            f"{entry_preview}"
                        )
                        codebase_findings.append(
                            {
                                "source": normalized_dir_target,
                                "relevance": 0.82,
                                "summary": summary_text,
                                "type": "directory",
                            }
                        )

                        # In scoped path, read key entry files (__init__.py, __main__.py) if present
                        if directory_target != ".":
                            clean_base_dir = directory_target.rstrip("/")
                            for key_file_name in ("__init__.py", "__main__.py"):
                                if key_file_name in dir_entries:
                                    key_file_path = f"{clean_base_dir}/{key_file_name}"
                                    normalized_key_file = _normalize_file_key(key_file_path)
                                    if normalized_key_file not in already_processed_files:
                                        already_processed_files.add(normalized_key_file)
                                        try:
                                            key_read = await self.read_tool.execute(file_path=key_file_path)
                                            if isinstance(key_read, dict) and "content" in key_read:
                                                codebase_findings.append(
                                                    {
                                                        "source": normalized_key_file,
                                                        "relevance": 0.90,
                                                        "summary": key_read["content"][:4000],
                                                        "type": "file",
                                                    }
                                                )
                                        except Exception as key_read_error:
                                            self._logger.debug(
                                                f"Failed to read entry file {key_file_path}: {key_read_error}"
                                            )
                except Exception as dir_scan_error:
                    self._logger.debug(f"Directory scan error for {directory_target}: {dir_scan_error}")

        # Deduplicate findings by source, preserving the highest relevance score
        deduplicated_findings = self._deduplicate_findings(codebase_findings)
        return deduplicated_findings, search_metadata

    @staticmethod
    def _deduplicate_findings(findings_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate findings by source, retaining the entry with the highest relevance score."""
        deduplicated_map: dict[str, dict[str, Any]] = {}
        for finding_item in findings_list:
            source_identifier = finding_item.get("source", "")
            if not source_identifier:
                continue
            existing_entry = deduplicated_map.get(source_identifier)
            if existing_entry is None or finding_item.get("relevance", 0) > existing_entry.get("relevance", 0):
                deduplicated_map[source_identifier] = finding_item
        return list(deduplicated_map.values())

    async def _search_memories(self, query: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Search memory for relevant entries, filtering out low-relevance noise."""
        memory_findings: list[dict[str, Any]] = []

        search_tool, _ = self._get_memory_tools()
        if search_tool is None or not query:
            return memory_findings

        try:
            search_result = await search_tool.execute(
                query=query,
                limit=context.get("memory_limit", 5),
            )
            if isinstance(search_result, dict) and "results" in search_result:
                # Filter out low-signal memory noise (score threshold >= 0.2)
                for memory_item in search_result["results"]:
                    memory_score = float(memory_item.get("score", 0.7))
                    if memory_score >= 0.2:
                        memory_findings.append(
                            {
                                "source": f"memory:{memory_item.get('memory_id', 'unknown')}",
                                "relevance": memory_score,
                                "summary": memory_item.get("summary", "")[:400],
                                "type": "memory",
                            }
                        )
        except Exception as memory_search_error:
            self._logger.debug(f"Memory search failed: {memory_search_error}")

        return memory_findings

    @staticmethod
    def _extract_knowledge_gaps(synthesis_text: str) -> list[str]:
        """Extract bullet points or list items from Knowledge Gaps section."""
        import re

        knowledge_gaps: list[str] = []
        gaps_match = re.search(
            r"(?:###?\s*Knowledge Gaps|Knowledge Gaps:?)\s*\n(.*?)(?=\n###?|\Z)",
            synthesis_text,
            re.DOTALL | re.IGNORECASE,
        )
        if gaps_match:
            gaps_block = gaps_match.group(1).strip()
            for line_text in gaps_block.splitlines():
                stripped_line = line_text.strip()
                if stripped_line.startswith(("-", "*", "•")) or (
                    len(stripped_line) > 2 and stripped_line[0].isdigit() and stripped_line[1] in (".", ")")
                ):
                    gap_item = re.sub(r"^[-*•\d.)\s]+", "", stripped_line).strip()
                    if gap_item and gap_item.lower() not in ("none", "none.", "n/a", "no gaps identified"):
                        knowledge_gaps.append(gap_item)
        return knowledge_gaps

    async def _synthesize(
        self,
        query: str,
        findings: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> tuple[str, list[str]]:
        """Create synthesis from findings using LLM if available."""
        if not findings:
            return f"No relevant information found for query: '{query}'", ["No findings discovered"]

        client = self.llm
        if client is not None:
            evidence_blocks = [
                f"[{finding_item['type'].upper()}] {finding_item['source']}:\n{finding_item['summary']}"
                for finding_item in findings
            ]
            user_prompt = (
                f"Research Query: {query}\n\n"
                f"Gathered Evidence:\n" + "\n\n".join(evidence_blocks) + "\n\n"
                f"Context: {context}\n\n"
                f"Please synthesize the above evidence and provide a structured research answer."
            )

            messages = [
                {"role": "system", "content": RESEARCHER_SYNTHESIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            try:
                response_text = ""
                async for event in client.stream_chat(messages):
                    if isinstance(event, TextDeltaEvent):
                        response_text += event.delta
                if response_text.strip():
                    knowledge_gaps = self._extract_knowledge_gaps(response_text)
                    return response_text.strip(), knowledge_gaps
            except Exception as llm_synthesis_error:
                self._logger.debug(f"LLM synthesis failed, falling back: {llm_synthesis_error}")

        # Fallback template synthesis
        top_findings = findings[:4]
        summary_lines = [f"Found {len(findings)} relevant item(s) for '{query}':"]
        summary_lines.extend(
            f"- {top_finding['source']}: {top_finding['summary'][:120]}..." for top_finding in top_findings
        )

        return "\n".join(summary_lines), []

    def _calculate_confidence(self, findings: list[dict[str, Any]]) -> str:
        """Calculate confidence level based on code and memory findings."""
        if not findings:
            return "low"

        code_findings = [
            finding_item for finding_item in findings if finding_item.get("type") in ("code", "file", "directory")
        ]
        if code_findings:
            total_code_relevance = sum(finding_item.get("relevance", 0) for finding_item in code_findings)
            average_code_relevance = total_code_relevance / len(code_findings)
            if len(code_findings) >= 2 and average_code_relevance >= 0.7:
                return "high"
            if average_code_relevance >= 0.5:
                return "medium"

        average_overall_relevance = sum(finding_item.get("relevance", 0) for finding_item in findings) / len(findings)
        if average_overall_relevance > 0.75:
            return "high"
        if average_overall_relevance > 0.45:
            return "medium"
        return "low"
