# Textual TUI Development Skill

This skill provides best practices and proven code patterns for creating high-fidelity, responsive Terminal User Interfaces (TUIs) using the Textual framework.

## 1. Core Principles
- **Prefer `VerticalScroll` for Scrolling**: A raw `Static` with `overflow-y: scroll` often fails to trigger scrollbars correctly when nested inside `height: auto` parents. Always use `VerticalScroll` for content areas that might grow.
- **Interactive Widgets**: Ensure any widget meant to be scrollable via mouse wheel or keyboard has `can_focus = True`.
- **Aesthetic Precision**: Use `$panel`, `$surface`, and `$accent` variables from the theme instead of hardcoded hex values. Use `scrollbar-size-vertical: 1` for slim indicators.

## 2. Proven Code Patterns

### Sleek Autocomplete Menus
Use `OptionList` with absolute positioning and slim scrollbars for non-intrusive overlays.

```python
class FileAutocomplete(OptionList):
    DEFAULT_CSS = """
    FileAutocomplete {
        width: 44;
        max-height: 14;
        background: $surface;
        border: solid $primary;
        layer: top;
        dock: bottom;
        offset: 2 -6;
        scrollbar-size-vertical: 1;
    }
    """
```

### Dynamic Height Scrollable Containers
For widgets that should grow with content but lock to a specific height once they reach a limit (e.g., Reasoning/Thought bubbles):

```python
class ThoughtBubble(VerticalScroll):
    DEFAULT_CSS = """
    ThoughtBubble {
        background: $panel;
        height: auto;
        max-height: 6;
        scrollbar-gutter: stable;
        scrollbar-size-vertical: 1;
    }
    """

    def append_text(self, text: str) -> None:
        self.query_one("#text", Static).update(text)
        # Force height lock once limit is reached to enable internal scrolling
        if self.virtual_size.height > 6:
            self.styles.height = 6
        self.scroll_end(animate=False)
```

### Dynamic Button Injection
To inject custom widgets (like Copy buttons) above Markdown code blocks, iterate through the children of a `Markdown` widget after rendering:

```python
def finalize(self) -> None:
    async def _inject() -> None:
        # MarkdownFence is the internal Textual widget for code blocks
        for child in list(self._markdown.children):
            if isinstance(child, MarkdownFence) and child.code:
                # Mount flush above the code block
                await self._markdown.mount(CopyCodeButton(child.code), before=child)
    
    self.run_worker(_inject())
```

### Layout Stability
Use `scrollbar-gutter: stable` to prevent the UI from shifting horizontally when a vertical scrollbar appears or disappears.

## 3. Custom Widget Creation

There are two primary ways to define the content and behavior of a custom `Widget` subclass:

### Using `compose()` (Structural/Compound)
Use `compose()` when your widget is a container for other widgets. You `yield` child widgets, and Textual manages the hierarchy.

- **Best for**: Complex UI blocks, sidebars, or headers.
- **Implementation**: Return a `ComposeResult` (an iterable of widgets).

```python
class MyCompoundWidget(Widget):
    def compose(self) -> ComposeResult:
        yield Static("Title")
        yield Button("Click Me")
```

### Using `render()` (Visual/Leaf)
Use `render()` when you want to define exactly how the widget's surface looks using Rich renderables (strings with markup, Tables, etc.).

- **Best for**: Labels, custom status indicators, or data visualizations.
- **Performance**: More efficient than `compose()` for non-interactive content as it avoids child widget overhead.
- **Implementation**: Return a `RenderResult` (any Rich-compatible object).

```python
class StatusBadge(Widget):
    def render(self) -> RenderResult:
        return "[bold white on green] ACTIVE [/]"
```

*Note: If you use both, `render()` defines the background/canvas and `compose()` layers children on top.*

## 4. Architectural Improvement: "Render-Only" Panels

Many of the sidebar panels in Dendrophis (e.g., `CostPanel`, `ModelPanel`) currently inherit from `Static` and use `self.update()` manually. A better, more "Textual" way to handle these is to use `render()`:

### The "Better Way" Pattern
1.  **Inherit from `Widget`** (not `Static`).
2.  **Use `render()`** to define the visual state.
3.  **Use `self.refresh()`** in event handlers to trigger a re-render.
4.  **Rely on `border_title`** for labels instead of internal `Static` children.

**Comparison:**
- **Current (Compound)**: Container Widget → Title Static → Value Static (3 widgets per panel).
- **Improved (Render-Only)**: Single Widget with `border_title` and `render()` logic (1 widget per panel).

```python
class ImprovedPanel(Widget):
    def on_mount(self) -> None:
        self.border_title = "My Panel"
        self._event_bus.subscribe(DataEvent, self.refresh) # Standard refresh triggers render()

    def render(self) -> RenderResult:
        data = self.get_data()
        return f"[bold green]{data.value}[/]"
```

*This pattern significantly reduces the total widget count in the sidebar and simplifies data-to-display logic.*


