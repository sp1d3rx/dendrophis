# Textual CSS (TCSS) Specialist Skill

This skill provides deep conceptual knowledge and technical rules for styling Textual TUIs.

## 1. Conceptual Foundation: Terminal vs. Web

TCSS is **not** HTML CSS. The underlying physics are different.

| Feature | Web CSS | TCSS | 
| :--- | :--- | :--- |
| **Basic Unit** | Pixel (`px`) | Character Cell (one grid block) |
| **Scaling** | Sub-pixel precision | Integer-based (discrete) |
| **Flow** | Floats/Flex/Grid | Nested Containers/Grid |
| **Precision** | High (0.5px exists) | Low (Must be whole cells) |

## 2. Units & Sizing

### Integer Units (The Default)
Setting a size to an integer specifies the number of character cells.
```css
/* 20 characters wide, 5 rows high */
#sidebar {
    width: 20;
    height: 5;
}
```

### Percentage Units
Relative to the parent container.
```css
.header {
    width: 100%;
}
```

### Fractional Units (`fr`)
Used in `Grid` layouts to distribute remaining space. Similar to CSS Grid.
```css
/* Column 1 takes 1 part, Column 2 takes 2 parts */
Grid {
    grid-size: 2;
    columns: 1fr 2fr;
}
```

## 3. Layout & Alignment

### No Floats
**Do not attempt to use `float`.** It does not exist in TCSS. Layout is driven by the widget hierarchy defined in `compose()` and managed via containers.

### Alignment Logic
Textual uses a specific `align` shorthand. Standard Flexbox `align-items` is not supported.

- **The `align` property**: Combines horizontal and vertical alignment.
  - `align: left middle;` 
  - `align: center center;` 
  - `align: right top;` 

### Centering Elements
Because there is no `margin: 0 auto;`, to center a child:
1. Set `align-horizontal: center;` on the **parent** container.
2. Define a `width` or `max-width` on the **child**.

## 4. Selectors

TCSS uses selectors targeting Textual widget types, classes, and IDs.

- **Widget Type**: `Button { ... }` (targets all Button widgets).
- **Class**: `.warning { ... }` (targets widgets with `classes="warning"`).
- **ID**: `#main-view { ... }` (targets widget with `id="main-view"`).
- **Descendant**: `Vertical > Label { ... }` (targets Labels that are direct children of a Vertical container).

## 5. Visual Styling Rules

- **Colors**: Use theme variables (`$primary`, `$surface`, `$accent`, `$error`) for consistency. Avoid hardcoded hex unless necessary.
- **Borders**: Use `border: [style] [color];` (e.g., `border: solid $primary;`).
- **Padding & Margin**: Use integer cell counts. `margin: 1 2;` (1 cell top/bottom, 2 left/right).
- **Scrolling**: Use `scrollbar-gutter: stable;` to prevent UI "jitter" when scrollbars appear/disappear.
