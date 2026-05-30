---
name: py-style
description: >
  Python style guide based on Raymond Hettinger's principles for beautiful, pythonic code.
  Enforces meaningful naming, avoids single-letter variables, and promotes idiomatic
  Python patterns like EAFP, comprehensions, context managers, and proper unpacking.
  Provides auto-fixes for safe cases and outputs suggestions in diff/patch format.
---

# Python Style Guide: Raymond Hettinger's Principles

This skill analyzes Python code and provides suggestions based on Raymond Hettinger's
advice for writing beautiful, pythonic code. It flags unpythonic patterns and suggests
improvements with clear explanations.

**Python Version:** 3.10+ only
**Severity:** All issues are style/optional (non-blocking)
**Output Format:** Diff/patch format with balanced verbosity (issue + why it matters)

---

## Core Rules

### 1. No Single-Letter Variables (Strict)

**Why it matters:** Single-letter names fail to communicate intent. Code should read like
prose, and every variable name should tell you what it represents without studying the
surrounding code.

**Bad:**
```python
def process(d, k):
    return d[k]
```

**Good:**
```python
def get_user_by_id(userDict, userId):
    return userDict[userId]
```

**Auto-fix:** None (renaming requires understanding context, but we flag all violations)

---

### 2. Name Variables Based on What They Store (Be Pedantic)

**Why it matters:** A variable name should be a complete sentence fragment that describes
exactly what the variable holds. If you read the code as a sentence, the name should make
sense.

**Bad:**
```python
result = database.query("SELECT * FROM users")  # What is 'result'?
data = fetch_api()  # What kind of data?
```

**Good:**
```python
userRecords = database.query("SELECT * FROM users")
apiResponseJson = fetch_api()
```

**Flag these vague names:** `data`, `item`, `value`, `result`, `obj`, `info`, `thing`, `stuff`

---

## Naming Conventions

### 3. Casing Rules

**Why it matters:** Consistent casing makes the distinction between types, functions, and variables immediately obvious.

- **Functions:** `snake_case` (e.g., `get_user_data()`)
- **Variables:** `camelCase` (e.g., `userData`)

### 4. Boolean Naming: Prefix Booleans with `is_`, `has_`, `can_`, `should_`

**Why it matters:** Boolean variables answer yes/no questions. The prefix makes it clear
what question is being answered. Note that following our casing rules, the prefix is 
part of the `camelCase` name.

**Bad:**
```python
valid = check_password(pwd)
if valid:
    
visible = user.permissions.admin
```

**Good:**
```python
isValid = check_password(pwd)
if isValid:
    
hasAdminPermissions = user.permissions.admin
```

**Common prefixes:**
- `is_` - state: `isValid`, `isActive`, `isEnabled`
- `has_` - possession: `hasPermission`, `hasChildren`
- `can_` - ability: `canEdit`, `canDelete`
- `should_` - recommendation: `shouldUpdate`, `shouldRetry`

**Auto-fix:** Add `is_` prefix to boolean variables detected without proper prefix

---

## Function and Variable Naming

### 5. Functions Are Verbs, Variables Are Nouns

**Why it matters:** Functions do things (verbs), variables hold things (nouns). This
convention makes code read naturally.

**Bad:**
```python
user_get(id)  # Sounds like a description, not an action
result_calculation = compute()
```

**Good:**
```python
get_user(id)
calculationResult = compute()
```

### 6. No Abbreviations (Spell It Out)

**Why it matters:** Modern IDEs have excellent autocompletion. There's no reason to
sacrifice clarity for a few saved keystrokes.

**Bad:**
```python
usr = get_current_user()
cfg = load_config()
msg = "Hello"
```

**Good:**
```python
user = get_current_user()
config = load_config()
message = "Hello"
```

**Exceptions:** Standard abbreviations like `id`, `url`, `api`, `http`, `https`, `io`

---

## Pythonic Patterns

### 7. EAFP vs LBYL (Easier to Ask For Forgiveness than Permission)

**Why it matters:** Python's philosophy favors trying an operation and handling the
exception (EAFP) over checking preconditions (LBYL). This leads to cleaner, more
readable code.

**LBYL (Bad):**
```python
if key in dictionary:
    value = dictionary[key]
else:
    value = default
```

**EAFP (Good):**
```python
try:
    value = dictionary[key]
except KeyError:
    value = default
```

**Even better - use dict.get():**
```python
value = dictionary.get(key, default)
```

---

### 8. List Comprehensions Over Loops

**Why it matters:** List comprehensions are more concise, often faster, and more
readable for simple transformations and filtering.

**Bad:**
```python
squares = []
for i in range(10):
    squares.append(i ** 2)
```

**Good:**
```python
squares = [i ** 2 for i in range(10)]
```

**For filtering:**
```python
# Bad
evenNumbers = []
for i in range(20):
    if i % 2 == 0:
        evenNumbers.append(i)

# Good
evenNumbers = [i for i in range(20) if i % 2 == 0]
```

**Auto-fix:** Convert simple for-loops with append to list comprehensions

---

### 9. Generator Expressions for Memory Efficiency

**Why it matters:** Generators produce values on-demand instead of creating the
entire list in memory. Use them when you only need to iterate once.

**Bad (creates full list in memory):**
```python
sum([x ** 2 for x in range(1000000)])
```

**Good (memory efficient):**
```python
sum(x ** 2 for x in range(1000000))
```

**When to use:**
- Passing to functions that consume iterables once: `sum()`, `any()`, `all()`, `max()`, `min()`
- Large datasets where memory is a concern
- Chaining operations: `sum(x * 2 for x in data if x > 0)`

---

### 10. Context Managers (with Statements)

**Why it matters:** Context managers ensure resources are properly cleaned up, even
if an exception occurs. They make code more readable and less error-prone.

**Bad:**
```python
f = open("file.txt", "r")
data = f.read()
f.close()
```

**Good:**
```python
with open("file.txt", "r") as f:
    data = f.read()
```

**For multiple files:**
```python
with open("in.txt") as fin, open("out.txt", "w") as fout:
    fout.write(fin.read())
```

**Auto-fix:** Wrap file opens with `with` statements

**Custom context managers:**
```python
from contextlib import contextmanager

@contextmanager
def timer(name):
    import time
    startTime = time.time()
    try:
        yield
    finally:
        elapsedTime = time.time() - startTime
        print(f"{name}: {elapsedTime:.2f}s")

with timer("database query"):
    result = db.query("SELECT * FROM users")
```

---

### 11. Use heapq When Appropriate

**Why it matters:** When you need a priority queue or want to find the N largest/smallest
items, `heapq` is more efficient and elegant than sorting.

**Bad (O(n log n)):**
```python
def get_top_10(items, key):
    return sorted(items, key=key, reverse=True)[:10]
```

**Good (O(n log k)):**
```python
import heapq

def get_top_10(items, key):
    return heapq.nlargest(10, items, key=key)
```

**For priority queues:**
```python
import heapq

queue = []
heapq.heappush(queue, (priority, item))
priority, item = heapq.heappop(queue)
```

---

## Data Structure Patterns

### 12. Unpacking Sequences

**Why it matters:** Unpacking makes code more readable and avoids index-based access
which is prone to off-by-one errors.

**Basic unpacking:**
```python
# Bad
first = items[0]
second = items[1]
rest = items[2:]

# Good
first, second, *rest = items
```

**Swapping variables:**
```python
# Bad
temp = a
a = b
b = temp

# Good
a, b = b, a
```

**Ignoring values:**
```python
# Use _ for ignored values
first, _, third = data

# Or with * for many ignored values
first, *_, last = data
```

**Dictionary unpacking:**
```python
# Merge dictionaries
defaults = {"color": "red", "size": "medium"}
overrides = {"color": "blue"}
settings = {**defaults, **overrides}  # {"color": "blue", "size": "medium"}
```

**Extended unpacking in function calls:**
```python
def func(a, b, *args, c=None, **kwargs):
    pass

# Unpack iterable into *args
argsList = [1, 2, 3]
func(*argsList)  # func(1, 2, 3)

# Unpack dict into **kwargs
kwargsDict = {"c": 4, "d": 5}
func(**kwargsDict)  # func(1, 2, c=4, d=5)
```

---

### 13. Dictionary Techniques

**Using defaultdict:**
```python
from collections import defaultdict

# Bad
counts = {}
for item in items:
    if item not in counts:
        counts[item] = 0
    counts[item] += 1

# Good
counts = defaultdict(int)
for item in items:
    counts[item] += 1
```

**Using Counter:**
```python
from collections import Counter

# Count occurrences
wordCounts = Counter(text.split())

# Most common
mostCommon = wordCounts.most_common(5)
```

**Using dict.get() with default:**
```python
# Bad
value = d[key] if key in d else default

# Good
value = d.get(key, default)
```

**Using setdefault:**
```python
# Bad
if key not in d:
    d[key] = []
d[key].append(value)

# Good
d.setdefault(key, []).append(value)
```

**Dictionary comprehensions:**
```python
# Bad
squares = {}
for x in range(10):
    squares[x] = x ** 2

# Good
squares = {x: x ** 2 for x in range(10)}
```

---

### 14. Class Design and ABCs

**Why it matters:** Abstract Base Classes (ABCs) help define clear interfaces and enable
polymorphism. They make your code more maintainable and easier to test.

**Define an abstract base class:**
```python
from abc import ABC, abstractmethod

class Shape(ABC):
    @abstractmethod
    def area(self) -> float:
        pass
    
    @abstractmethod
    def perimeter(self) -> float:
        pass

class Rectangle(Shape):
    def __init__(self, width, height):
        self.width = width
        self.height = height
    
    def area(self) -> float:
        return self.width * self.height
    
    def perimeter(self) -> float:
        return 2 * (self.width + self.height)
```

**Using ABCs for type checking:**
```python
from collections.abc import Iterable, Mapping, Callable

def process_iterable(items: Iterable[int]) -> int:
    return sum(items)

def process_mapping(data: Mapping[str, int]) -> int:
    return sum(data.values())

def apply_function(func: Callable[[int], int], value: int) -> int:
    return func(value)
```

**Duck typing vs explicit inheritance:**
```python
# Prefer duck typing when possible
class Duck:
    def quack(self):
        print("Quack!")

class Person:
    def quack(self):
        print("I'm quacking like a duck!")

def make_it_quack(duckLike):
    duckLike.quack()

make_it_quack(Duck())  # Works
make_it_quack(Person())  # Also works
```

**Dataclasses for data containers:**
```python
from dataclasses import dataclass

@dataclass
class Point:
    x: float
    y: float
    
    def distance_from_origin(self) -> float:
        return (self.x ** 2 + self.y ** 2) ** 0.5

p = Point(3.0, 4.0)
print(p)  # Point(x=3.0, y=4.0)
```

---

## Zen of Python (PEP 20)

These principles should guide all Python code:

```
>>> import this
```

**Key principles we enforce:**

1. **Beautiful is better than ugly** - Write code that's pleasing to read
2. **Explicit is better than implicit** - Clear code over clever code
3. **Simple is better than complex** - Keep it simple
4. **Complex is better than complicated** - If it must be complex, keep it organized
5. **Flat is better than nested** - Avoid deep nesting
6. **Sparse is better than dense** - Don't cram too much on one line
7. **Readability counts** - The most important principle
8. **Special cases aren't special enough to break the rules** - Be consistent
9. **Although practicality beats purity** - Sometimes break rules for good reason
10. **Errors should never pass silently** - Handle errors explicitly
11. **Unless explicitly silenced** - But sometimes it's OK to ignore them
12. **In the face of ambiguity, refuse the temptation to guess** - Be explicit
13. **There should be one-- and preferably only one --obvious way to do it** - Pythonic
14. **Although that way may not be obvious at first unless you're Dutch** - Learn the idioms
15. **Now is better than never** - Don't over-engineer
16. **Although never is often better than *right* now** - But don't rush
17. **If the implementation is hard to explain, it's a bad idea** - Simplicity wins
18. **If the implementation is easy to explain, it may be a good idea** - Clarity matters
19. **Namespaces are one honking great idea** - Use modules, classes, functions
20. **Let's do more of those!** - Organize your code

---

## Auto-Fix Capabilities

This skill can automatically fix the following safe cases:

### 1. Add `is_` prefix to boolean variables

```diff
- valid = True
+ isValid = True

- if active:
+ if isActive:
```

### 2. Rename vague variables to meaningful names

Context-aware renaming based on usage patterns:

```diff
- data = fetch_users()
+ userRecords = fetch_users()

- result = calculate_sum(numbers)
+ totalSum = calculate_sum(numbers)
```

### 3. Convert simple loops to comprehensions

Only when the transformation is safe and clear:

```diff
- squares = []
- for i in range(10):
-     squares.append(i ** 2)
+ squares = [i ** 2 for i in range(10)]
```

### 4. Add context managers to file operations

```diff
- f = open("file.txt")
- data = f.read()
- f.close()
+ with open("file.txt") as f:
+     data = f.read()
```

---

## Output Format

All suggestions are output in diff/patch format for easy application:

```
=== file: path/to/file.py
--- original
+++ suggested
@@ -line,count +line,count @@
- old_code
+ new_code

Reason: [Explanation of why this matters]
```

---

## When We Don't Flag Issues

**Test code:** Analyzed normally (no special treatment)

**Generated code:** If you explicitly mark code as generated (e.g., `# @generated`), we skip it

**Type annotations:** Single-letter type variables are OK:
```python
T = TypeVar('T')
E = TypeVar('E', bound=Exception)
```

**Standard library conventions:** We respect established Python conventions even if they
use short names (e.g., `i` in `for i, item in enumerate()` is allowed when used idiomatically)

---

## Usage

### As a Linter (Pre-commit Hook)

See `.pre-commit-config.yaml` for sample configuration.

### In VS Code

See `vscode-extension` directory for IDE integration.

### Interactive Mode

Activate with `/py-style` command or automatically on file save/open.

---

## Quick Reference Card

| Pattern | Bad | Good |
|---------|-----|------|
| Single-letter vars | `x`, `d`, `i` | `index`, `dataDict`, `item` |
| Boolean naming | `valid` | `isValid` |
| Vague names | `data` | `userRecords` |
| Function naming | `user_get` | `get_user` |
| Loop pattern | `for i in range(n): result.append(i*2)` | `[i*2 for i in range(n)]` |
| File handling | `f = open(...); f.close()` | `with open(...) as f:` |
| Dict access | `if k in d: x = d[k]` | `x = d.get(k, default)` |
| Priority queue | `sorted(items)[:10]` | `heapq.nlargest(10, items)` |
```