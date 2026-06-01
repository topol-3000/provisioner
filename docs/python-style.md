# Python 3.14 style and design rules

You are an expert Python 3.14 engineer.

Generate code that is production-oriented, maintainable, strongly typed, and easy to evolve.

## Core principles

- Target Python 3.14 syntax and standard library features.
- Prefer readability over cleverness.
- Prefer explicit design over hidden behavior.
- Keep modules cohesive and small.
- Keep functions and methods focused on one responsibility.
- Follow SOLID principles in class and module design.
- Avoid deep nesting. Use guard clauses, early returns, and helper methods.
- Prefer composition over inheritance unless inheritance models a true subtype relationship.
- Keep business logic separate from transport, framework, persistence, and presentation concerns.
- Write code that is easy to test.

## Typing

- Use modern Python typing syntax.
- Prefer `list[str]`, `dict[str, Any]`, `set[int]`, `tuple[str, ...]` over legacy `List`, `Dict`, `Set`, `Tuple`.
- Prefer `X | None` over `Optional[X]`.
- Use `typing.TypeAlias` or `type` aliases when a domain concept benefits from a named type.
- Use `Literal`, `Protocol`, `TypedDict`, `Final`, `Self`, `TypeVar`, `ParamSpec`, and generics when they improve correctness and clarity.
- Do not add useless type annotations where the type is trivial and local, but always annotate public APIs.
- Every function, method, class attribute, and constant that is part of the public surface must be typed.
- Prefer precise return types. Do not use `Any` unless there is a strong reason.
- Avoid untyped `dict` payloads for domain data when a structured model is more appropriate.
- Prefer `Protocol` for interface-style abstractions instead of inheritance-only base classes when possible.

## Data modeling

- Do not pass raw primitives through multiple layers when they represent domain concepts.
- Prefer `@dataclass(slots=True)` for internal immutable or near-immutable domain data.
- Prefer `@dataclass(frozen=True, slots=True)` for value objects.
- Use Pydantic models for:
  - external input validation
  - API request/response schemas
  - settings/configuration
  - parsing untrusted data
- Use dataclasses for:
  - internal domain models
  - service inputs/outputs
  - command/query objects
  - pure in-memory state containers
- Replace long primitive parameter lists with a dataclass or Pydantic model.
- Avoid boolean flag parameters when they change behavior significantly. Model intent explicitly.

## Function and method design

- Keep functions small and intention-revealing.
- A function should do one thing.
- Prefer at most 3–5 parameters. If more are needed, introduce a structured input object.
- Avoid long methods with many branches.
- Avoid deep nested `if/for/try` blocks.
- Extract private helper methods when logic can be named clearly.
- Use early returns instead of wrapping the full function in nested conditionals.
- Separate orchestration from transformation.
- Methods should either:
  - return data, or
  - perform side effects,
  but avoid mixing both when possible.
- Raise meaningful exceptions at boundaries where failures should be explicit.
- Do not silently swallow exceptions.
- Catch exceptions at the correct abstraction boundary.

## Class design

- Classes must have a single clear responsibility.
- Use classes when there is state, lifecycle, or a meaningful abstraction boundary.
- Prefer plain functions for stateless transformations.
- Keep constructors simple.
- Do not perform heavy I/O or complex business logic in `__init__`.
- Inject dependencies through the constructor.
- Depend on abstractions, not concrete implementations.
- Avoid god objects.
- Avoid utility classes full of static methods when module-level functions are clearer.
- Use private attributes and private helper methods for implementation details.
- Prefix internal-only members with `_`.
- Do not expose internal mutable state directly.
- Prefer read-only properties only when they add domain meaning, not as boilerplate.

## Encapsulation

- Mark fields and methods as private when they are used only internally.
- Do not call private methods from outside the class.
- Keep public APIs minimal and stable.
- Internal implementation details should be easy to change without affecting callers.

## SOLID guidance

### Single Responsibility Principle
- Each module, class, and function should have one reason to change.

### Open/Closed Principle
- Prefer extension through composition, protocols, and strategy objects instead of editing central conditional logic repeatedly.

### Liskov Substitution Principle
- Subclasses must preserve expected behavior of the parent abstraction.
- Do not use inheritance only for code reuse.

### Interface Segregation Principle
- Prefer small focused protocols/interfaces over broad ones.
- Clients should not depend on methods they do not use.

### Dependency Inversion Principle
- High-level modules must depend on abstractions.
- Pass repositories, gateways, and clients as dependencies instead of constructing them inside services.

## Module structure

- Organize code by domain and responsibility, not by vague technical buckets alone.
- Prefer a modular layout like:

  - `domain/` — entities, value objects, domain services, protocols
  - `application/` — use cases, orchestration, commands, queries
  - `infrastructure/` — database, APIs, file system, external services
  - `presentation/` — HTTP handlers, CLI, message consumers
  - `shared/` — cross-cutting utilities that are truly shared

- Keep framework code at the edges.
- Domain code must not depend on web frameworks, ORMs, or transport details.
- Avoid circular imports by designing clear boundaries.

## Docstrings

- Write docstrings for all public modules, classes, and functions.
- Use concise, high-value docstrings.
- Explain:
  - purpose
  - important parameters
  - return value
  - raised exceptions when relevant
  - side effects when relevant
- Do not write docstrings that merely restate the function name.
- Prefer Google-style docstrings unless the existing codebase uses another standard consistently.
- Private helpers do not need docstrings unless the logic is non-obvious.

## Validation and boundaries

- Validate external input at the boundary.
- Convert external schemas into domain models early.
- Keep validation rules close to the model that owns them.
- Do not let raw request dictionaries leak into business logic.
- Parse environment variables into typed settings objects.

## Error handling

- Use domain-specific exceptions for domain failures.
- Use infrastructure-specific exceptions only inside infrastructure layers.
- Translate low-level exceptions into meaningful higher-level ones at layer boundaries.
- Error messages must be actionable and specific.
- Never use bare `except:`.

## State and immutability

- Prefer immutable data structures for domain values when practical.
- Minimize shared mutable state.
- Make state transitions explicit.
- Avoid hidden mutations across layers.

## Logging

- Use structured, meaningful logging.
- Log at boundaries and important state transitions.
- Do not log secrets, tokens, passwords, or personal data.
- Avoid noisy logs inside tight loops.
- Use exceptions for failures, logs for observability, not as a substitute for control flow.

## Testing expectations

- Generate code that is easy to unit test.
- Prefer constructor injection so collaborators can be mocked or replaced.
- Avoid hardcoded globals and hidden singletons.
- Keep pure logic separate from I/O.
- Write deterministic code.
- For new business logic, include tests or propose test cases.
- Test behavior, not implementation details.
- Do not test private methods directly unless there is a compelling reason.

## Style and readability

- Follow PEP 8 and standard Python conventions.
- Use descriptive names.
- Avoid abbreviations unless they are well-known in the domain.
- Prefer `match` only when it genuinely improves clarity.
- Avoid over-engineering.
- Avoid metaprogramming unless it provides clear value.
- Avoid excessive comments. Prefer self-explanatory code.
- Keep line length reasonable and readability high.

## Async code

- Use `async` only for genuine I/O-bound workflows.
- Do not mix sync and async carelessly.
- Keep async boundaries explicit.
- Avoid blocking calls inside async functions.

## Imports

- Keep imports clean and grouped:
  1. standard library
  2. third-party
  3. local application
- Avoid wildcard imports.
- Import only what is needed.

## Configuration

- Do not hardcode environment-specific values.
- Use typed configuration objects.
- Prefer Pydantic settings or an equivalent typed settings model for app configuration.

## Code generation constraints

- Do not generate monolithic files when the problem naturally requires multiple modules.
- Propose file structure when implementing a feature of non-trivial size.
- When creating classes, include only the methods that belong to that responsibility.
- When a parameter list becomes primitive-heavy or ambiguous, introduce a dataclass or Pydantic model.
- When logic becomes nested or branch-heavy, refactor into small private methods or strategy objects.
- Use `_private_fields` and `_private_methods` for internal implementation details.
- Do not expose internal state unless required by the use case.
- Prefer domain names like `UserId`, `Money`, `CreateOrderCommand`, `OrderRepository` over generic names like `data`, `utils`, `manager`, or `helper`.

## Preferred patterns

- Strategy pattern for interchangeable behaviors
- Repository pattern for persistence boundaries
- Service layer for orchestration
- Value objects for validated domain concepts
- Command/query models instead of long argument lists
- Protocol-based dependency inversion
- Small pure functions for transformation logic

## Avoid

- Deeply nested control flow
- Primitive obsession
- God classes
- Feature envy
- Hidden side effects
- Boolean flag arguments controlling major behavior
- Broad `Any` usage
- Framework-dependent domain logic
- Static utility classes when functions suffice
- Leaky abstractions
- Public mutable attributes without a good reason

## Default style when generating code

- modern typing
- structured models instead of primitive bags of data
- dataclasses for internal domain data
- Pydantic for validation and external schemas
- constructor-based dependency injection
- small methods
- private helpers for internal logic
- minimal but useful docstrings
- modular architecture with clean boundaries
