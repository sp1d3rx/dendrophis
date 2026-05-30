import time
from dataclasses import dataclass

from dendrophis.events import EventBus


@dataclass
class Msg:
    text: str


bus = EventBus()


def handler(event):
    with open("/tmp/event_log.txt", "a") as f:
        f.write(f"Handler called: {event.text}\n")
    print(f"Received: {event.text}")


bus.subscribe(Msg, handler)
print("Publishing event...")
bus.publish(Msg(text="test123"))
print("Waiting...")
time.sleep(1)
print("Done")
bus.shutdown()
