"""Debug framework for Dendrophis - manual testing of chat processing and events.

Usage:
    # Run with defaults (uses config from DENDROPHIS_CONFIG or dendrophis.yaml)
    python debug_chat.py

    # Run with explicit config
    DENDROPHIS_CONFIG=/path/to/config.yaml python debug_chat.py

    # Run with environment variables
    DENDROPHIS_API_KEY=xxx DENDROPHIS_BASE_URL=xxx DENDROPHIS_MODEL=xxx python debug_chat.py

Features:
    - Shows all events fired during chat processing
    - Shows raw request/response payloads
    - Shows tool execution details
    - Interactive chat loop with manual input
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Imports - everything from dendrophis
# ---------------------------------------------------------------------------
# Config
from dendrophis.config.loader import ConfigLoader
from dendrophis.config.schema import DendrophisConfig

# Events
from dendrophis.events import (
    AnyEvent,
    DoneEvent,
    ErrorEvent,
    MessageSentEvent,
    ReasoningDeltaEvent,
    RetryEvent,
    StreamingFinishedEvent,
    StreamingStartedEvent,
    TextDeltaEvent,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallDoneEvent,
    ToolCallStartEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
    ToolResultEvent,
    TurnResult,
    UsageEvent,
    get_event_bus,
    set_event_bus,
)
from dendrophis.events.bus import EventBus

# LLM
from dendrophis.llm.client import LLMClient

# Tools
from dendrophis.tools import ToolExecutor, ToolRegistry, create_builtin_registry

# ---------------------------------------------------------------------------
# Setup logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("debug_chat")


# ---------------------------------------------------------------------------
# Debug Event Printer
# ---------------------------------------------------------------------------


def _format_event(event: AnyEvent) -> str:
    """Format an event for debug output."""
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    type_name = type(event).__name__

    if isinstance(event, TextDeltaEvent):
        delta = event.delta.replace("\n", "\\n") if event.delta else ""
        return f"[{ts}] TextDelta delta={delta!r}"
    if isinstance(event, ReasoningDeltaEvent):
        delta = event.delta.replace("\n", "\\n") if event.delta else ""
        return f"[{ts}] ReasoningDelta delta={delta!r}"
    if isinstance(event, ToolCallStartEvent):
        return f"[{ts}] ToolCallStart index={event.index} id={event.id} name={event.name}"
    if isinstance(event, ToolCallDeltaEvent):
        args = event.arguments_delta.replace("\n", "\\n") if event.arguments_delta else ""
        return f"[{ts}] ToolCallDelta index={event.index} args_delta={args!r}"
    if isinstance(event, ToolCallDoneEvent):
        tc = event.tool_call
        return f"[{ts}] ToolCallDone index={tc.index} id={tc.id} name={tc.name} args={tc.arguments!r}"
    if isinstance(event, ToolResultEvent):
        content = event.content.replace("\n", "\\n") if event.content else ""
        return f"[{ts}] ToolResult name={event.name} content={content[:200]!r}"
    if isinstance(event, UsageEvent):
        return (
            f"[{ts}] Usage prompt={event.prompt_tokens} "
            f"completion={event.completion_tokens} cached={event.cached_tokens}"
        )
    if isinstance(event, DoneEvent):
        return f"[{ts}] Done finish_reason={event.finish_reason}"
    if isinstance(event, ErrorEvent):
        return f"[{ts}] Error message={event.message!r}"
    if isinstance(event, RetryEvent):
        return f"[{ts}] Retry attempt={event.attempt} delay={event.delay:.2f}s message={event.message!r}"
    if isinstance(event, StreamingStartedEvent):
        msg = event.user_message.replace("\n", "\\n") if event.user_message else ""
        return f"[{ts}] StreamingStarted user_message={msg[:100]!r}"
    if isinstance(event, StreamingFinishedEvent):
        return f"[{ts}] StreamingFinished"
    if isinstance(event, MessageSentEvent):
        msg = event.message_text.replace("\n", "\\n") if event.message_text else ""
        return f"[{ts}] MessageSent message={msg[:100]!r}"
    if isinstance(event, TurnResult):
        text = event.text.replace("\n", "\\n") if event.text else ""
        reasoning = event.reasoning.replace("\n", "\\n") if event.reasoning else ""
        return (
            f"[{ts}] TurnResult text={text[:100]!r} "
            f"reasoning={reasoning[:50]!r} tool_calls={len(event.tool_calls)} "
            f"finish={event.finish_reason}"
        )
    return f"[{ts}] {type_name} {event}"


class DebugEventPrinter:
    """Subscribes to all events and prints them for debugging."""

    def __init__(self, bus: EventBus | None = None):
        self.bus = bus or get_event_bus()
        self._subscriptions = []

    def start(self) -> None:
        """Subscribe to all event types."""

        # All stream events
        stream_types = (
            TextDeltaEvent,
            ReasoningDeltaEvent,
            ToolCallStartEvent,
            ToolCallDeltaEvent,
            ToolCallDoneEvent,
            ToolResultEvent,
            UsageEvent,
            DoneEvent,
            ErrorEvent,
            RetryEvent,
            TurnResult,
            StreamingStartedEvent,
            StreamingFinishedEvent,
            MessageSentEvent,
        )

        for event_type in stream_types:
            self._subscriptions.append((event_type, self.bus.subscribe(event_type, self._handle_event)))

    def stop(self) -> None:
        """Unsubscribe from all events."""

        for event_type, _ in self._subscriptions:
            self.bus.unsubscribe_all(event_type)
        self._subscriptions.clear()

    def _handle_event(self, event: AnyEvent) -> None:
        """Print event details."""
        print(_format_event(event), file=sys.stderr)


# ---------------------------------------------------------------------------
# Debug Chat Session
# ---------------------------------------------------------------------------


class DebugChatSession:
    """Minimal chat session for debugging LLM processing."""

    def __init__(
        self,
        config: DendrophisConfig,
        event_bus: EventBus | None = None,
        tool_registry: ToolRegistry | None = None,
    ):
        self.config = config
        self.bus = event_bus or get_event_bus()
        self.tool_registry = tool_registry
        self._llm_client: LLMClient | None = None
        self._tool_executor: ToolExecutor | None = None
        self._messages: list[dict[str, Any]] = []
        self._turn_count = 0

    @property
    def llm_client(self) -> LLMClient:
        if self._llm_client is None:
            self._llm_client = LLMClient(self.config.llm)
        return self._llm_client

    @property
    def tool_executor(self) -> ToolExecutor:
        if self._tool_executor is None:
            registry = self.tool_registry or ToolRegistry()
            self._tool_executor = ToolExecutor(registry)
        return self._tool_executor

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self._messages

    def add_system_message(self, content: str) -> None:
        """Add a system message."""
        self._messages.append({"role": "system", "content": content})

    def add_user_message(self, content: str) -> None:
        """Add a user message."""
        self._messages.append({"role": "user", "content": content})

    def add_assistant_message(
        self,
        content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> None:
        """Add an assistant message."""
        msg: dict[str, Any] = {"role": "assistant"}
        if content is not None:
            msg["content"] = content
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        self._messages.append(msg)

    async def run_turn(
        self,
        user_message: str,
        tools: list[dict[str, Any]] | None = None,
        max_turns: int = 1,
    ) -> TurnResult:
        """Run a single turn and return the result."""
        self._turn_count += 1

        # Add user message
        self.add_user_message(user_message)

        # Publish streaming started
        self.bus.publish(StreamingStartedEvent(user_message=user_message))

        # Get tools from registry if not provided
        if tools is None and self.tool_registry:
            tools = [t.schema for t in self.tool_registry.all()]

        # Collect all events
        all_events: list[Any] = []
        turn_result: TurnResult | None = None

        async for event in self.llm_client.stream_chat(
            self.messages,
            tools=tools,
            enable_cache_control=True,
            tool_choice="auto",
        ):
            all_events.append(event)
            self.bus.publish(event)

            if isinstance(event, TurnResult):
                turn_result = event
                break

        if turn_result is None:
            # If we didn't get a TurnResult, create one from accumulated events
            text_parts = []
            reasoning_parts = []
            tool_calls_list = []
            finish_reason = "stop"

            for evt in all_events:
                if isinstance(evt, TextDeltaEvent):
                    text_parts.append(evt.delta)
                elif isinstance(evt, ReasoningDeltaEvent):
                    reasoning_parts.append(evt.delta)
                elif isinstance(evt, ToolCallDoneEvent):
                    tool_calls_list.append(evt.tool_call)
                elif isinstance(evt, DoneEvent):
                    finish_reason = evt.finish_reason

            turn_result = TurnResult(
                text="".join(text_parts),
                reasoning="".join(reasoning_parts),
                tool_calls=tool_calls_list,
                finish_reason=finish_reason,
            )

        # Add assistant message to context
        self.add_assistant_message(
            content=turn_result.text or None,
            tool_calls=[
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in turn_result.tool_calls
            ]
            if turn_result.tool_calls
            else None,
            reasoning_content=turn_result.reasoning or None,
        )

        self.bus.publish(StreamingFinishedEvent())
        return turn_result

    async def _execute_tools(self, tool_calls: list[ToolCall]) -> list[Any]:
        """Execute tool calls and return results."""
        results = []
        for tc in tool_calls:
            self.bus.publish(
                ToolExecutionStartedEvent(
                    tool_name=tc.name,
                    arguments=tc.arguments,
                    tool_call_index=tc.index,
                )
            )

            try:
                result = await self.tool_executor.execute(tc)
                self.bus.publish(
                    ToolResultEvent(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=result.content,
                    )
                )
                results.append(result)
            except Exception as e:
                self.bus.publish(
                    ToolResultEvent(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=json.dumps({"error": str(e)}),
                    )
                )

            self.bus.publish(
                ToolExecutionFinishedEvent(
                    tool_name=tc.name,
                    success=True,
                )
            )

        return results


# ---------------------------------------------------------------------------
# Raw Request/Response Logger
# ---------------------------------------------------------------------------


class RawPayloadLogger:
    """Logs raw request and response payloads for debugging."""

    def __init__(self, client: LLMClient):
        self.client = client
        self._original_send = client._http.send
        self._request_count = 0

    def start(self) -> None:
        """Start intercepting requests."""
        # Monkey-patch the send method
        original_send = self.client._http.send

        async def patched_send(*args, **kwargs):
            self._request_count += 1
            req = args[0] if args else kwargs.get("request")

            if req:
                print(f"\n>>> REQUEST #{self._request_count}", file=sys.stderr)
                print(f"    URL: {req.url}", file=sys.stderr)
                print(f"    Method: {req.method}", file=sys.stderr)

                # Try to pretty print JSON body
                content = getattr(req, "content", None) or kwargs.get("content")
                if content:
                    try:
                        if isinstance(content, bytes):
                            content = content.decode("utf-8")
                        data = json.loads(content)
                        print(f"    Body: {json.dumps(data, indent=2)}", file=sys.stderr)
                    except Exception:
                        print(f"    Body: {content[:500]}", file=sys.stderr)

                headers = getattr(req, "headers", {})
                auth = headers.get("authorization", "Bearer [REDACTED]")
                print(f"    Auth: {auth}", file=sys.stderr)

            resp = await original_send(*args, **kwargs)

            print(f"\n<<< RESPONSE #{self._request_count}", file=sys.stderr)
            print(f"    Status: {resp.status_code}", file=sys.stderr)
            print(f"    Headers: {dict(resp.headers)}", file=sys.stderr)

            # Stream responses need special handling
            return resp

        self.client._http.send = patched_send

    def stop(self) -> None:
        """Stop intercepting requests."""
        # This is tricky - we'd need to restore original
        pass


# ---------------------------------------------------------------------------
# Programmatic API
# ---------------------------------------------------------------------------


async def run_single_chat(
    message: str,
    config: DendrophisConfig | None = None,
    system_prompt: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run a single chat turn programmatically and return results.

    Useful for testing specific scenarios without interactive mode.

    Args:
        message: The user message to send
        config: Optional DendrophisConfig, otherwise loaded from defaults
        system_prompt: Optional system prompt override
        tools: Optional list of tool schemas to provide
        verbose: Whether to print debug output

    Returns:
        Dict with keys: text, reasoning, tool_calls, events, usage
    """
    if config is None:
        try:
            config_loader = ConfigLoader.load()
            config = config_loader.config
        except Exception:
            from dendrophis.config.schema import DendrophisConfig, LLMConfig

            config = DendrophisConfig(llm=LLMConfig())

    bus = EventBus()
    bus.set_event_loop(asyncio.get_event_loop())

    import time

    start_time = time.perf_counter()
    first_token_time = None

    # Collect events
    collected_events: list[Any] = []

    def collect_event(event: AnyEvent) -> None:
        nonlocal first_token_time
        collected_events.append(event)
        if isinstance(event, (TextDeltaEvent, ReasoningDeltaEvent)) and first_token_time is None:
            first_token_time = time.perf_counter()
        if verbose:
            print(_format_event(event), file=sys.stderr)

    # Subscribe to all event types

    # We need to subscribe to the base types
    stream_types = (
        TextDeltaEvent,
        ReasoningDeltaEvent,
        ToolCallStartEvent,
        ToolCallDeltaEvent,
        ToolCallDoneEvent,
        ToolResultEvent,
        UsageEvent,
        DoneEvent,
        ErrorEvent,
        RetryEvent,
        TurnResult,
        StreamingStartedEvent,
        StreamingFinishedEvent,
        MessageSentEvent,
    )
    for evt_type in stream_types:
        bus.subscribe(evt_type, collect_event)

    # Create tool registry with built-in tools
    tool_registry = create_builtin_registry(bus, interactive=False)

    if verbose:
        print(f"  Loaded {len(list(tool_registry.all()))} tools", file=sys.stderr)

    session = DebugChatSession(config, event_bus=bus, tool_registry=tool_registry)

    if system_prompt:
        session.add_system_message(system_prompt)
    elif config.system_prompt:
        session.add_system_message(config.system_prompt)

    # Add raw payload logging if verbose
    if verbose:
        logger = RawPayloadLogger(session.llm_client)
        logger.start()

    try:
        result = await session.run_turn(message, tools=tools)
        end_time = time.perf_counter()

        # Extract usage info
        usage_info = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
        for event in collected_events:
            if isinstance(event, UsageEvent):
                usage_info = {
                    "prompt_tokens": event.prompt_tokens,
                    "completion_tokens": event.completion_tokens,
                    "cached_tokens": event.cached_tokens,
                }

        prefill_duration = (first_token_time - start_time) if first_token_time else None
        generation_duration = (end_time - first_token_time) if first_token_time else None
        total_duration = end_time - start_time

        return {
            "text": result.text,
            "reasoning": result.reasoning,
            "tool_calls": [
                {"name": tool_call.name, "arguments": tool_call.arguments} for tool_call in result.tool_calls
            ],
            "finish_reason": result.finish_reason,
            "events": collected_events,
            "usage": usage_info,
            "messages": session.messages,
            "prefill_duration": prefill_duration,
            "generation_duration": generation_duration,
            "total_duration": total_duration,
        }
    finally:
        await session.llm_client.aclose()
        bus.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Main Debug CLI
# ---------------------------------------------------------------------------


async def main():
    """Main debug entry point."""
    print("=" * 80, file=sys.stderr)
    print("DENDROPHIS DEBUG CHAT FRAMEWORK", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print("Type messages to chat, or 'exit' to quit.", file=sys.stderr)
    print("-" * 80, file=sys.stderr)

    # Load config
    print("\n[LOADING CONFIG]", file=sys.stderr)
    try:
        config_loader = ConfigLoader.load()
        config = config_loader.config
        print(f"  Model: {config.llm.model}", file=sys.stderr)
        print(f"  Base URL: {config.llm.base_url}", file=sys.stderr)
        print(f"  Max tokens: {config.llm.max_tokens}", file=sys.stderr)
        print(f"  Temperature: {config.llm.temperature}", file=sys.stderr)
    except Exception as e:
        print(f"  ERROR loading config: {e}", file=sys.stderr)
        # Use defaults
        from dendrophis.config.schema import DendrophisConfig, LLMConfig

        config = DendrophisConfig(llm=LLMConfig())

    # Create event bus
    print("\n[SETTING UP EVENT BUS]", file=sys.stderr)
    bus = EventBus()
    bus.set_event_loop(asyncio.get_event_loop())
    set_event_bus(bus)

    # Start debug printer
    printer = DebugEventPrinter(bus)
    printer.start()

    # Create session
    print("\n[CREATING SESSION]", file=sys.stderr)

    # Create tool registry
    tool_registry = create_builtin_registry(bus, interactive=False)
    print(f"  Loaded {len(list(tool_registry.all()))} built-in tools", file=sys.stderr)

    session = DebugChatSession(config, event_bus=bus, tool_registry=tool_registry)

    # Optionally add system prompt
    if config.system_prompt:
        session.add_system_message(config.system_prompt)

    # Start raw payload logging
    logger = RawPayloadLogger(session.llm_client)
    logger.start()

    # Interactive loop
    print("\n[READY FOR INPUT]", file=sys.stderr)
    print("Enter your message:", file=sys.stderr)

    try:
        while True:
            try:
                user_input = input(">>> ")
            except (EOFError, KeyboardInterrupt):
                print("\nExiting...", file=sys.stderr)
                break

            if user_input.strip().lower() in ("exit", "quit", "q"):
                print("\nExiting...", file=sys.stderr)
                break

            if not user_input.strip():
                continue

            print(f"\n--- Turn {session._turn_count + 1} ---", file=sys.stderr)
            print(f"USER: {user_input}", file=sys.stderr)

            try:
                import time

                start_time = time.perf_counter()
                first_token_time = None
                completion_tokens = 0
                prompt_tokens = 0
                cached_tokens = 0

                def on_event(event: AnyEvent) -> None:
                    nonlocal first_token_time, completion_tokens, prompt_tokens, cached_tokens
                    if isinstance(event, (TextDeltaEvent, ReasoningDeltaEvent)) and first_token_time is None:
                        first_token_time = time.perf_counter()
                    elif isinstance(event, UsageEvent):
                        completion_tokens = event.completion_tokens
                        prompt_tokens = event.prompt_tokens
                        cached_tokens = event.cached_tokens

                token_sub = bus.subscribe(TextDeltaEvent, on_event)
                reasoning_sub = bus.subscribe(ReasoningDeltaEvent, on_event)
                usage_sub = bus.subscribe(UsageEvent, on_event)

                try:
                    result = await session.run_turn(user_input)
                finally:
                    bus.unsubscribe(TextDeltaEvent, token_sub)
                    bus.unsubscribe(ReasoningDeltaEvent, reasoning_sub)
                    bus.unsubscribe(UsageEvent, usage_sub)

                end_time = time.perf_counter()

                print(f"\nASSISTANT: {result.text}", file=sys.stderr)
                if result.reasoning:
                    print(f"REASONING: {result.reasoning[:200]}...", file=sys.stderr)
                if result.tool_calls:
                    print(f"TOOL CALLS: {len(result.tool_calls)}", file=sys.stderr)
                    for tool_call in result.tool_calls:
                        print(f"  - {tool_call.name}({tool_call.arguments[:100]})", file=sys.stderr)

                # Print speed performance statistics
                print("-" * 40, file=sys.stderr)
                print("PERFORMANCE STATISTICS:", file=sys.stderr)
                prefill_duration = (first_token_time - start_time) if first_token_time else (end_time - start_time)
                print(f"  Time-to-first-token (Prefill): {prefill_duration:.4f}s", file=sys.stderr)
                if first_token_time:
                    generation_duration = end_time - first_token_time
                    print(f"  Generation duration: {generation_duration:.4f}s", file=sys.stderr)
                    if completion_tokens > 0 and generation_duration > 0:
                        speed = completion_tokens / generation_duration
                        print(f"  Generation speed: {speed:.2f} tok/s ({completion_tokens} tokens)", file=sys.stderr)
                print(f"  Total time: {end_time - start_time:.4f}s", file=sys.stderr)
                if prompt_tokens > 0:
                    print(f"  Usage: prompt={prompt_tokens}, cached={cached_tokens}", file=sys.stderr)
                print("-" * 40, file=sys.stderr)
            except Exception as e:
                print(f"\nERROR: {e}", file=sys.stderr)
                import traceback

                traceback.print_exc()

            print("\nEnter your message:", file=sys.stderr)

    finally:
        printer.stop()
        await session.llm_client.aclose()
        bus.shutdown(wait=False)


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Dendrophis Debug Chat Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode (default)
  python debug_chat.py

  # Programmatic mode - run a single message
  python debug_chat.py --message "Hello, world!"

  # With custom config file
  python debug_chat.py --config /path/to/config.yaml -m "Test"

  # With environment variables
  DENDROPHIS_API_KEY=xxx DENDROPHIS_BASE_URL=xxx DENDROPHIS_MODEL=xxx python debug_chat.py

  # With config env var
  DENDROPHIS_CONFIG=/path/to/config.yaml python debug_chat.py
""",
    )
    parser.add_argument(
        "--message",
        "-m",
        type=str,
        help="Run a single message in programmatic mode and exit",
    )
    parser.add_argument(
        "--system",
        "-s",
        type=str,
        help="Override system prompt",
    )
    parser.add_argument(
        "--no-verbose",
        action="store_true",
        help="Suppress debug output (programmatic mode only)",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Force interactive mode (default if no --message)",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default=None,
        help="Path to config file (overrides DENDROPHIS_CONFIG env var)",
    )
    parser.add_argument(
        "--log",
        action="store_true",
        help="Enable detailed tool execution logging to tool_log.txt",
    )
    parser.add_argument(
        "--no-parallel-tools",
        action="store_true",
        help="Execute tools sequentially instead of in parallel",
    )

    args = parser.parse_args()

    # Set config path in environment if provided
    if args.config:
        os.environ["DENDROPHIS_CONFIG"] = args.config

    # Enable tool logging if requested
    if args.log:
        os.environ["DENDROPHIS_TOOL_LOG"] = "1"
        print("🔍 Tool execution logging enabled - check tool_log.txt")

    # Set parallel tools config based on --no-parallel-tools flag
    if args.no_parallel_tools:
        # We need to load the config to set this flag
        from dendrophis.config.loader import ConfigLoader

        config_path = (
            args.config if args.config else os.environ.get("DENDROPHIS_CONFIG", "~/.config/dendrophis/config.yaml")
        )
        loader = ConfigLoader.load(config_path=config_path)
        loader.config.tools.parallel_tools = False
        print("🔧 Parallel tool execution disabled")

    if args.message:
        # Programmatic mode
        async def run():
            result = await run_single_chat(
                message=args.message,
                system_prompt=args.system,
                verbose=not args.no_verbose,
            )
            if not args.no_verbose:
                print("\n" + "=" * 60, file=sys.stderr)
                print("RESULT SUMMARY", file=sys.stderr)
                print("=" * 60, file=sys.stderr)
            print(f"Text: {result['text']}", file=sys.stderr)
            if result["reasoning"]:
                print(f"Reasoning: {result['reasoning'][:200]}...", file=sys.stderr)
            print(f"Tool calls: {len(result['tool_calls'])}", file=sys.stderr)
            print(f"Finish reason: {result['finish_reason']}", file=sys.stderr)
            print(f"Usage: {result['usage']}", file=sys.stderr)
            print(f"Total events: {len(result['events'])}", file=sys.stderr)

            # Print speed performance statistics
            if result.get("prefill_duration") is not None:
                print(f"Prefill duration (Time-to-first-token): {result['prefill_duration']:.4f}s", file=sys.stderr)
            if result.get("generation_duration") is not None:
                print(f"Generation duration: {result['generation_duration']:.4f}s", file=sys.stderr)
                completion_tokens = result["usage"]["completion_tokens"]
                if completion_tokens > 0 and result["generation_duration"] > 0:
                    speed = completion_tokens / result["generation_duration"]
                    print(f"Generation speed: {speed:.2f} tok/s ({completion_tokens} tokens)", file=sys.stderr)
            if result.get("total_duration") is not None:
                print(f"Total duration: {result['total_duration']:.4f}s", file=sys.stderr)

        asyncio.run(run())
    else:
        # Interactive mode
        asyncio.run(main())
