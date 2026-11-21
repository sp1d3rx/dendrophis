"""Debug framework for Dendrophis - manual testing of chat processing and events."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any

from dendrophis.config.loader import ConfigLoader
from dendrophis.config.schema import DendrophisConfig, LLMConfig
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
    shutdown_global_event_bus,
)
from dendrophis.events.bus import EventBus, Subscription
from dendrophis.llm.client import LLMClient
from dendrophis.tools import ToolExecutor, ToolRegistry, create_builtin_registry

logger = logging.getLogger("debug_chat")


def _configure_cli_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def _format_event(event: AnyEvent) -> str:
    """Format an event for debug output."""
    timestamp_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    type_name = type(event).__name__

    if isinstance(event, TextDeltaEvent):
        delta = event.delta.replace("\n", "\\n") if event.delta else ""
        return f"[{timestamp_str}] TextDelta delta={delta!r}"
    if isinstance(event, ReasoningDeltaEvent):
        delta = event.delta.replace("\n", "\\n") if event.delta else ""
        return f"[{timestamp_str}] ReasoningDelta delta={delta!r}"
    if isinstance(event, ToolCallStartEvent):
        return f"[{timestamp_str}] ToolCallStart index={event.index} id={event.id} name={event.name}"
    if isinstance(event, ToolCallDeltaEvent):
        args = event.arguments_delta.replace("\n", "\\n") if event.arguments_delta else ""
        return f"[{timestamp_str}] ToolCallDelta index={event.index} args_delta={args!r}"
    if isinstance(event, ToolCallDoneEvent):
        tool_call = event.tool_call
        return (
            f"[{timestamp_str}] ToolCallDone index={tool_call.index} id={tool_call.id} "
            f"name={tool_call.name} args={tool_call.arguments!r}"
        )
    if isinstance(event, ToolResultEvent):
        content = event.content.replace("\n", "\\n") if event.content else ""
        return f"[{timestamp_str}] ToolResult name={event.name} content={content[:200]!r}"
    if isinstance(event, UsageEvent):
        return (
            f"[{timestamp_str}] Usage prompt={event.prompt_tokens} "
            f"completion={event.completion_tokens} cached={event.cached_tokens}"
        )
    if isinstance(event, DoneEvent):
        return f"[{timestamp_str}] Done finish_reason={event.finish_reason}"
    if isinstance(event, ErrorEvent):
        return f"[{timestamp_str}] Error message={event.message!r}"
    if isinstance(event, RetryEvent):
        return f"[{timestamp_str}] Retry attempt={event.attempt} delay={event.delay:.2f}s message={event.message!r}"
    if isinstance(event, StreamingStartedEvent):
        msg = event.user_message.replace("\n", "\\n") if event.user_message else ""
        return f"[{timestamp_str}] StreamingStarted user_message={msg[:100]!r}"
    if isinstance(event, StreamingFinishedEvent):
        return f"[{timestamp_str}] StreamingFinished"
    if isinstance(event, MessageSentEvent):
        msg = event.message_text.replace("\n", "\\n") if event.message_text else ""
        return f"[{timestamp_str}] MessageSent message={msg[:100]!r}"
    if isinstance(event, TurnResult):
        text = event.text.replace("\n", "\\n") if event.text else ""
        reasoning = event.reasoning.replace("\n", "\\n") if event.reasoning else ""
        return (
            f"[{timestamp_str}] TurnResult text={text[:100]!r} "
            f"reasoning={reasoning[:50]!r} tool_calls={len(event.tool_calls)} "
            f"finish={event.finish_reason}"
        )
    return f"[{timestamp_str}] {type_name} {event}"


class DebugEventPrinter:
    """Subscribes to all events and prints them for debugging."""

    def __init__(self, bus: EventBus | None = None) -> None:
        self.bus = bus or get_event_bus()
        self._subscriptions: list[Subscription] = []

    def start(self) -> None:
        """Subscribe to all event types."""
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
            self._subscriptions.append(self.bus.subscribe(event_type, self._handle_event))

    def stop(self) -> None:
        """Unsubscribe only this printer's handlers."""
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        self._subscriptions.clear()

    def _handle_event(self, event: AnyEvent) -> None:
        """Print event details."""
        print(_format_event(event), file=sys.stderr)


class DebugChatSession:
    """Minimal chat session for debugging LLM processing."""

    def __init__(
        self,
        config: DendrophisConfig,
        event_bus: EventBus | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
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
        self._messages.append({"role": "system", "content": content})

    def add_user_message(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})

    def add_assistant_message(
        self,
        content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> None:
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
        tool_choice: str = "auto",
    ) -> TurnResult:
        """Run a single turn and return the result."""
        self._turn_count += 1
        self.add_user_message(user_message)
        self.bus.publish(StreamingStartedEvent(user_message=user_message))

        if tools is None and self.tool_registry:
            tools = [tool_item.schema for tool_item in self.tool_registry.all()]

        all_events: list[Any] = []
        turn_result: TurnResult | None = None

        async for event in self.llm_client.stream_chat(
            self.messages,
            tools=tools,
            enable_cache_control=True,
            tool_choice=tool_choice,
        ):
            all_events.append(event)
            self.bus.publish(event)

            if isinstance(event, TurnResult):
                turn_result = event
                break

        if turn_result is None:
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

        self.add_assistant_message(
            content=turn_result.text or None,
            tool_calls=[
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {"name": tool_call.name, "arguments": tool_call.arguments},
                }
                for tool_call in turn_result.tool_calls
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
        for tool_call in tool_calls:
            self.bus.publish(
                ToolExecutionStartedEvent(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    tool_call_index=tool_call.index,
                )
            )
            succeeded = False
            try:
                result = await self.tool_executor.execute(tool_call)
                succeeded = result.success
                self.bus.publish(
                    ToolResultEvent(
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        content=result.content,
                    )
                )
                results.append(result)
            except Exception as tool_error:
                logger.error("Error executing tool %s: %s", tool_call.name, tool_error, exc_info=True)
                self.bus.publish(
                    ToolResultEvent(
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        content=json.dumps({"error": str(tool_error)}),
                    )
                )

            self.bus.publish(
                ToolExecutionFinishedEvent(
                    tool_name=tool_call.name,
                    success=succeeded,
                )
            )

        return results


class RawPayloadLogger:
    """Logs raw request and response payloads for debugging."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client
        self._original_send = client._http.send
        self._request_count = 0

    def start(self) -> None:
        """Start intercepting requests."""
        original_send = self._original_send

        async def patched_send(*args: Any, **kwargs: Any) -> Any:
            self._request_count += 1
            req = args[0] if args else kwargs.get("request")

            if req:
                print(f"\n>>> REQUEST #{self._request_count}", file=sys.stderr)
                print(f"    URL: {req.url}", file=sys.stderr)
                print(f"    Method: {req.method}", file=sys.stderr)

                content = getattr(req, "content", None) or kwargs.get("content")
                if content:
                    try:
                        if isinstance(content, bytes):
                            content = content.decode("utf-8")
                        parsed_body = json.loads(content)
                        print(f"    Body: {json.dumps(parsed_body, indent=2)}", file=sys.stderr)
                    except Exception:
                        print(f"    Body: {content[:500]}", file=sys.stderr)

                headers = getattr(req, "headers", {})
                has_auth = "authorization" in {header_key.lower() for header_key in headers}
                auth_display = "Bearer [REDACTED]" if has_auth else "(none)"
                print(f"    Auth: {auth_display}", file=sys.stderr)

            resp = await original_send(*args, **kwargs)

            print(f"\n<<< RESPONSE #{self._request_count}", file=sys.stderr)
            print(f"    Status: {resp.status_code}", file=sys.stderr)
            print(f"    Headers: {dict(resp.headers)}", file=sys.stderr)
            return resp

        self.client._http.send = patched_send  # type: ignore[method-assign]

    def stop(self) -> None:
        """Restore original HTTP send method."""
        self.client._http.send = self._original_send  # type: ignore[method-assign]


async def run_single_chat(
    message: str,
    config: DendrophisConfig | None = None,
    system_prompt: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    verbose: bool = True,
    tool_choice: str = "auto",
) -> dict[str, Any]:
    """Run a single chat turn programmatically and return results."""
    if config is None:
        try:
            config_loader = ConfigLoader.load()
            config = config_loader.config
        except Exception:
            config = DendrophisConfig(llm=LLMConfig())

    bus = EventBus()
    bus.set_event_loop(asyncio.get_running_loop())

    start_time = time.perf_counter()
    first_token_time: float | None = None
    collected_events: list[Any] = []

    def collect_event(event: AnyEvent) -> None:
        nonlocal first_token_time
        collected_events.append(event)
        if isinstance(event, (TextDeltaEvent, ReasoningDeltaEvent)) and first_token_time is None:
            first_token_time = time.perf_counter()
        if verbose:
            print(_format_event(event), file=sys.stderr)

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

    tool_registry = create_builtin_registry(bus, interactive=False)

    if verbose:
        print(f"  Loaded {len(list(tool_registry.all()))} tools", file=sys.stderr)

    session = DebugChatSession(config, event_bus=bus, tool_registry=tool_registry)

    if system_prompt:
        session.add_system_message(system_prompt)
    elif config.system_prompt:
        session.add_system_message(config.system_prompt)

    payload_logger: RawPayloadLogger | None = None
    if verbose:
        payload_logger = RawPayloadLogger(session.llm_client)
        payload_logger.start()

    try:
        result = await session.run_turn(message, tools=tools, tool_choice=tool_choice)
        end_time = time.perf_counter()

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
        if payload_logger is not None:
            payload_logger.stop()
        await session.llm_client.aclose()
        bus.shutdown(wait=False)


async def main(config_override: DendrophisConfig | None = None) -> None:
    """Main debug entry point."""
    _configure_cli_logging()
    print("=" * 80, file=sys.stderr)
    print("DENDROPHIS DEBUG CHAT FRAMEWORK", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print("Type messages to chat, or 'exit' to quit.", file=sys.stderr)
    print("-" * 80, file=sys.stderr)

    print("\n[LOADING CONFIG]", file=sys.stderr)
    if config_override is not None:
        config = config_override
    else:
        try:
            config_loader = ConfigLoader.load()
            config = config_loader.config
        except Exception as config_error:
            logger.error("ERROR loading config: %s", config_error, exc_info=True)
            print(f"  ERROR loading config: {config_error}", file=sys.stderr)
            config = DendrophisConfig(llm=LLMConfig())

    print(f"  Model: {config.llm.model}", file=sys.stderr)
    print(f"  Base URL: {config.llm.base_url}", file=sys.stderr)
    print(f"  Max tokens: {config.llm.max_tokens}", file=sys.stderr)
    print(f"  Temperature: {config.llm.temperature}", file=sys.stderr)

    print("\n[SETTING UP EVENT BUS]", file=sys.stderr)
    bus = EventBus()
    bus.set_event_loop(asyncio.get_running_loop())
    set_event_bus(bus)

    printer = DebugEventPrinter(bus)
    printer.start()

    print("\n[CREATING SESSION]", file=sys.stderr)
    tool_registry = create_builtin_registry(bus, interactive=False)
    print(f"  Loaded {len(list(tool_registry.all()))} built-in tools", file=sys.stderr)

    session = DebugChatSession(config, event_bus=bus, tool_registry=tool_registry)
    if config.system_prompt:
        session.add_system_message(config.system_prompt)

    payload_logger = RawPayloadLogger(session.llm_client)
    payload_logger.start()

    print("\n[READY FOR INPUT]", file=sys.stderr)
    print("Enter your message:", file=sys.stderr)

    try:
        while True:
            try:
                user_input = await asyncio.to_thread(input, ">>> ")
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
                start_time = time.perf_counter()
                first_token_time: float | None = None
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
                    token_sub.unsubscribe()
                    reasoning_sub.unsubscribe()
                    usage_sub.unsubscribe()

                end_time = time.perf_counter()

                print(f"\nASSISTANT: {result.text}", file=sys.stderr)
                if result.reasoning:
                    print(f"REASONING: {result.reasoning[:200]}...", file=sys.stderr)
                if result.tool_calls:
                    print(f"TOOL CALLS: {len(result.tool_calls)}", file=sys.stderr)
                    for tool_call in result.tool_calls:
                        print(f"  - {tool_call.name}({tool_call.arguments[:100]})", file=sys.stderr)

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
            except Exception as turn_error:
                logger.error("Turn execution error: %s", turn_error, exc_info=True)
                print(f"\nERROR: {turn_error}", file=sys.stderr)

            print("\nEnter your message:", file=sys.stderr)

    finally:
        payload_logger.stop()
        printer.stop()
        await session.llm_client.aclose()
        bus.shutdown(wait=False)
        shutdown_global_event_bus()


if __name__ == "__main__":
    _configure_cli_logging()
    parser = argparse.ArgumentParser(
        description="Dendrophis Debug Chat Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--message", "-m", type=str, help="Run a single message in programmatic mode and exit")
    parser.add_argument("--system", "-s", type=str, help="Override system prompt")
    parser.add_argument("--no-verbose", action="store_true", help="Suppress debug output (programmatic mode only)")
    parser.add_argument("--interactive", "-i", action="store_true", help="Force interactive mode")
    parser.add_argument("--config", "-c", type=str, default=None, help="Path to config file")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode and tool execution logging")
    parser.add_argument("--log", action="store_true", help="Enable detailed tool execution logging to tool_log.txt")
    parser.add_argument(
        "--no-parallel-tools", action="store_true", help="Execute tools sequentially instead of in parallel"
    )

    cli_args = parser.parse_args()

    if cli_args.config:
        os.environ["DENDROPHIS_CONFIG"] = cli_args.config

    if cli_args.debug or cli_args.log:
        os.environ["DENDROPHIS_TOOL_LOG"] = "1"
        print("🔍 Tool execution logging enabled - check tool_log.txt")

    loaded_cli_config: DendrophisConfig | None = None
    if cli_args.no_parallel_tools:
        config_path = cli_args.config or os.environ.get("DENDROPHIS_CONFIG")
        loader = ConfigLoader.load(config_path=config_path)
        loader.config.tools.parallel_tools = False
        loaded_cli_config = loader.config
        print("🔧 Parallel tool execution disabled")

    if cli_args.message:

        async def _run_programmatic() -> None:
            result = await run_single_chat(
                message=cli_args.message,
                config=loaded_cli_config,
                system_prompt=cli_args.system,
                verbose=not cli_args.no_verbose,
            )
            if not cli_args.no_verbose:
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

        asyncio.run(_run_programmatic())
    else:
        asyncio.run(main(config_override=loaded_cli_config))
