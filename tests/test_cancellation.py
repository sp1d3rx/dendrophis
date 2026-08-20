import asyncio
import os

from dendrophis.config.loader import ConfigLoader
from dendrophis.events import get_event_bus
from dendrophis.events.types import StreamingFinishedEvent, StreamingStartedEvent, TextDeltaEvent
from dendrophis.session.factory import SessionFactory


async def test_session_cancellation() -> None:
    # Load config and create session
    config_loader = ConfigLoader.load(config_path="dendrophis.yaml")

    # Create event bus and session
    event_bus = get_event_bus()
    event_bus.set_event_loop(asyncio.get_event_loop())

    session = SessionFactory.create_session(config_loader, event_bus)

    # Track events
    all_events = []
    text_received = asyncio.Event()

    def collect_events(event) -> None:
        all_events.append(event)
        if isinstance(event, StreamingStartedEvent):
            print("Streaming started!")
        elif isinstance(event, TextDeltaEvent):
            print(f"Text: '{event.delta}'")
            if len(all_events) >= 5:  # Wait for several text events
                text_received.set()

    event_bus.subscribe(TextDeltaEvent, collect_events)
    event_bus.subscribe(StreamingStartedEvent, collect_events)
    event_bus.subscribe(StreamingFinishedEvent, collect_events)

    # Start a long response
    async def run_chat() -> None:
        await session.send_message(
            "Tell me a very long story about Python programming that goes on for many paragraphs"
        )

    # Start the chat task
    chat_task = asyncio.create_task(run_chat())

    # Wait for some actual text to be received
    print("Waiting for text to start streaming...")
    try:
        await asyncio.wait_for(text_received.wait(), timeout=15.0)
        print("Got some text, waiting a bit more...")
        await asyncio.sleep(1)  # Let more text accumulate
    except TimeoutError:
        print("No text received!")

    # Cancel the streaming
    print("\n--- CANCELLING ---")
    session.cancel_streaming()

    # Wait for cancellation to complete
    try:
        await asyncio.wait_for(chat_task, timeout=5.0)
    except TimeoutError:
        print("Chat task timed out (expected after cancellation)")

    # Check what we got
    print("\n--- RESULTS ---")
    text_events = [event for event in all_events if isinstance(event, TextDeltaEvent)]
    print(f"Text delta events received: {len(text_events)}")

    # Check context
    messages = session.context.get_messages_for_api()
    print(f"Messages in context: {len(messages)}")
    if len(messages) > 2:  # Should have system + user + assistant
        assistant_message = messages[-1]
        content = assistant_message.get("content", "")
        print(f"Assistant message content length: {len(content)}")
