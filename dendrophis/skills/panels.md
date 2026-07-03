# Panel Development Skill

This skill provides patterns for creating consistent, reactive, and efficient UI panels in Dendrophis.

## 1. Architectural Patterns

There are two primary ways to implement a panel depending on its complexity and interactivity.

### A. Compound Panels (Structural/Interactive)
Use this when the panel needs to contain multiple interactive widgets (e.g., `Checkbox`, `Input`, `Button`).

- **Inheritance**: Inherit from `Vertical` or `VerticalScroll`.
- **Implementation**: Use `compose()` to define the widget hierarchy.
- **Best For**: Sidebars with user input, like the `TodoPanel`.

```python
class TodoPanel(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Vertical(id="todo-list-container")
        yield Input(placeholder="Add todo...", id="todo-input")

    def refresh_ui(self) -> None:
        # Manually clear and re-mount widgets for structural changes
        container = self.query_one("#todo-list-container")
        container.query("*").remove()
        # ... mount new items
```

### B. Render-Only Panels (Visual/Read-Only)
Use this for high-performance, read-only information displays. This pattern significantly reduces the total widget count in the sidebar.

- **Inheritance**: Inherit from `Widget` (not `Static`).
- **Implementation**: 
    1. Set `self.border_title = "Panel Name"`.
    2. Use `render()` to return a Rich renderable (string with markup, Table, etc.).
    3. Call `self.refresh()` to trigger a re-render.
- **Best For**: Data-heavy read-only displays, like `CostPanel` or `ModelPanel`.

```python
class CostPanel(Widget):
    def on_mount(self) -> None:
        self.border_title = "Usage Stats"
        self._event_bus.subscribe(StatsUpdatedEvent, self.refresh)

    def render(self) -> RenderResult:
        # Logic to get data and format as Rich markup
        return f"[bold green]${self.current_cost:.2f}[/]"
```

## 2. Lifecycle & Reactivity

Panels must be "good citizens" of the `EventBus`.

1.  **Subscription**: Always subscribe to relevant events in `on_mount()`.
2.  **Reactivity**:
    - For **Compound Panels**: Call a custom `refresh_ui()` method that clears and updates children.
    - For **Render-Only Panels**: Call `self.refresh()`.
3.  **Cleanup**: **CRITICAL**. Always `unsubscribe` from the `EventBus` in `on_unmount()` to prevent memory leaks and unexpected behavior in new sessions.

```python
def on_unmount(self) -> None:
    self._event_bus.unsubscribe(TodoUpdatedEvent, self._on_todo_updated)
```

## 3. Layout Stability

- **Avoid `recompose()`**: Do not use `recompose()` to toggle visibility or size of children. This causes layout jumps and can swallow pointer events.
- **Preferred Visibility Toggling**: For compound widgets, toggle `self.styles.display = "block" | "none"`.
- **Preferred Content Updating**: Use `.update()` for `Static` widgets or `self.refresh()` for `Widget.render()`.
