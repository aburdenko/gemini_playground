---
name: mesop-skill
description: Google Mesop Python UI framework documentation, quickstart, syntax, and workflows. Use this skill when asked to create, run, edit, or debug Python UI applications using the Mesop framework.
---

# Mesop UI Framework Skill

This skill provides context for building applications with **Mesop** (`mesop`), an open-source Python UI framework from Google designed for rapid web app development using purely Python—no HTML, CSS, or JS required.

## Quickstart & Installation

- **Prerequisites**: Python 3.10+
- **Installation**: `pip install mesop`
- **Execution**: Run apps using the Mesop CLI:
  ```bash
  mesop app.py
  ```
  This enables hot reloading by default and serves on `http://localhost:32123`.

## Core Concepts & Syntax

### Basic Structure and Pages

Define a UI page using the `@me.page` decorator. Components are Python function calls.

```python
import mesop as me

@me.page(path="/")
def home():
    me.text("Hello, Mesop!")
```

### State Management

State in Mesop is class-based and managed via the `@me.stateclass` decorator. Access and mutate state within your event handlers or pages using `me.state(StateClass)`.

```python
import mesop as me

@me.stateclass
class AppState:
    count: int = 0
    user_name: str = ""

def increment(e: me.ClickEvent):
    state = me.state(AppState)
    state.count += 1

@me.page(path="/counter")
def counter_page():
    state = me.state(AppState)
    me.text(f"Current Count: {state.count}")
    me.button("Increment", on_click=increment)
```

### Event Handling

Event handlers are functions that accept an event argument, such as `me.ClickEvent`, `me.InputEvent`, etc. Modify the state directly inside the event handler. Mesop will automatically re-render the UI based on the updated state.

```python
import mesop as me

@me.stateclass
class FormState:
    text: str = ""

def on_input(e: me.InputEvent):
    state = me.state(FormState)
    state.text = e.value

@me.page(path="/form")
def form_page():
    state = me.state(FormState)
    me.input(label="Enter some text", on_input=on_input)
    me.text(f"You typed: {state.text}")
```

### Common UI Components

- `me.text("...")`
- `me.button("Label", on_click=handler)`
- `me.input(label="...", on_input=handler)`
- Layouts (like boxes or columns) are also built via python component functions.

## Workflows

1. **Scaffolding an App**:
   - Create an `app.py` file with basic imports and a `@me.page` route.
   - Define a `@me.stateclass` if state is required.
   
2. **Adding Interactivity**:
   - Write python event handler functions.
   - Bind them to component events (`on_click`, `on_input`, etc.).
   
3. **Running and Debugging**:
   - Always run the app using `mesop <filename>.py`.
   - Remind users that hot-reloading is active, so they only need to save the file and check the browser.

## Best Practices

- Do not use HTML or CSS templates. All UI should be constructed through `me.<component>` functions.
- Keep state classes clean and ensure they are properly initialized with default values.
- Never write Javascript when a user asks for interactivity; rely on the Python event handling provided by `mesop`.