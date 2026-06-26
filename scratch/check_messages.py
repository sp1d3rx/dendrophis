import asyncio
from pathlib import Path

from textual.app import App

from dendrophis.config.loader import ConfigLoader
from dendrophis.session.factory import SessionFactory
from dendrophis.ui.screens.main import MainScreen
from dendrophis.ui.widgets.chat_view import ChatView


class WelcomeCheckApp(App):
    CSS_PATH = Path(__file__).parent.parent / "dendrophis" / "ui" / "styles" / "dendrophis.tcss"

    def __init__(self, session_instance):
        super().__init__()
        self._session = session_instance
        self.main_screen = None

    def on_mount(self):
        self.main_screen = MainScreen(self._session, self._session._event_bus)
        self.push_screen(self.main_screen)


async def check_welcome_message():
    config_loader = ConfigLoader.load(config_path="omlx.yaml")
    print("Config pr_enabled:", config_loader.config.caching.pr_enabled)

    # Create session
    from dendrophis.events import EventBus

    event_bus = EventBus()
    session_instance = SessionFactory.create_session(config_loader=config_loader, event_bus=event_bus)

    app_instance = WelcomeCheckApp(session_instance)
    async with app_instance.run_test() as pilot:
        # Wait for auto load timer (which triggers welcome screen)
        await asyncio.sleep(0.5)
        await pilot.pause()

        chat_view = app_instance.main_screen.query_one(ChatView)
        print("\n--- CHAT MESSAGES ---")
        for message_widget in chat_view.children:
            text_content = str(message_widget.render())
            print(f"[{type(message_widget).__name__}]: {text_content!r}")


if __name__ == "__main__":
    asyncio.run(check_welcome_message())
