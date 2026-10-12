---
name: backend-design
description: Guidance for deliberate backend architecture and data-model design when building new services, APIs, or schemas, or reshaping existing ones. Helps choose module boundaries, layering, and data models that fit the actual problem instead of defaulting to generic CRUD/microservice/repository-pattern templates. Use whenever starting new backend code, designing an API, or modeling a database schema, even if the user doesn't explicitly ask for "architecture" or "design."
---

# Backend Design

Approach this as the staff engineer other engineers call in before they commit to a structure, known for asking "what does this system actually need to do" before reaching for a pattern. Past reviews from this engineer have killed proposals that were over-built for the traffic they'll see and under-built for the consistency they need. The job is to make deliberate, justified choices about boundaries, data shape, and failure handling that fit this system's actual constraints, not the ones a tutorial would assume.

## Ground the design in the actual system

Before proposing a structure, work out — from the brief, the existing codebase, or by asking — the things that should drive every downstream choice:
- **Scale and access pattern**: rough request volume, read/write ratio, and whether load is steady or spiky. A design for 50 requests/day and one for 50,000/second are not the same shape.
- **Consistency requirements**: what must never be wrong even for a moment (money, inventory, auth) versus what can be eventually consistent (a view count, a recommendation feed).
- **Team and operational reality**: team size, how many services they already run and can operate, and what they're already using for storage/queues/infra. Introducing a new datastore or message broker is a standing operational cost, not a free architectural upgrade.
- **Lifespan and change rate**: a prototype validating an idea and a system that will carry ten years of migrations should not be structured the same way.

If this context isn't available, state the assumption you're making and flag it as something to confirm, rather than silently picking the default that would suit any project.

## Design principles

**Module and service boundaries** should follow where the system actually changes together and where failure should actually be contained, not a generic template. A boundary is worth its cost — a network hop, a deploy, a contract to version — only when the two sides genuinely need to scale, deploy, or fail independently. Splitting a low-traffic CRUD app into microservices before there's a team or load reason to costs far more in coordination than it saves; conversely, jamming unrelated domains (billing and content moderation, say) into one module to avoid that cost creates coupling that's harder to undo later. Prefer clear boundaries inside one deployable first; split into a separate service when there's a concrete forcing function (independent scaling, independent deploy cadence, a different team owning it, a hard failure-isolation requirement).

**Layering should reflect real seams, not a fixed template applied regardless of size.** A controller/service/repository split earns its keep when there's real logic to isolate between the transport layer and the data layer. For a handler that reads a request and does one query, that's three files and two interfaces standing between the code and the one thing it does — collapse it. Add a layer when something concrete would otherwise leak across it (business rules in a route handler, SQL scattered through call sites), not by default.

**Data models should represent what the domain actually is**, not the easiest thing to CRUD. Before adding a table or a field, ask what invariant it protects and who is responsible for keeping it true. Normalize to remove real update anomalies; denormalize deliberately, and say why, when read performance or an existing access pattern demands it — not as a first move. Choose primary keys for the access pattern they'll actually serve (a natural key when it's stable and used to look up by, a generated key — and consider a sortable one, like a ULID/UUIDv7, over random UUIDv4, when insertion order or index locality matters). Foreign keys and constraints belong in the schema when the database can enforce an invariant more cheaply and reliably than application code can.

**Failure and partial-failure handling is part of the design, not an afterthought.** For any operation that crosses a network boundary (a downstream service call, a queue publish, a second database), decide up front: what happens on timeout, on partial completion, on retry — and whether the operation needs to be idempotent because of it. An API that accepts money, inventory changes, or anything else where a duplicate is costly needs an idempotency key or equivalent from the start, not bolted on after the first double-charge.

**APIs are a contract with a future you don't control.** Design the request/response shape around what the caller needs to express, not around the database table underneath it. Decide the versioning and deprecation story before the first breaking change is needed, not during it. Errors should tell the caller what happened and whether retrying will help — a bare 500 or a generic "error" string pushes the debugging work onto whoever's downstream.

## Calibration: defaults to justify, not reach for

These patterns are legitimate when the system's actual constraints call for them, but they show up by reflex far more often than the constraints justify. Before using one, check it against the grounding above:

1. **Microservices from day one** for a system with one team and no proven independent-scaling need — the standard tell is a "user service," "order service," and "notification service" each with their own database and deploy pipeline, built before there's any traffic or team pressure forcing the split.
2. **A repository/service/controller trio for every entity**, including the ones with one query and no business logic — generates a wall of pass-through boilerplate that hides the one place that actually has logic worth isolating.
3. **CQRS, event sourcing, or a message bus** introduced for a straightforward CRUD domain because it's "more scalable," when the actual read/write pattern is simple and the team now has to operate an event store and reconcile eventual consistency they didn't need.
4. **A generic `status: string` or `metadata: jsonb` field** used in place of modeling the actual states and their transitions — it compiles, but nothing stops an invalid state, and every consumer re-implements the state machine by hand.
5. **UUIDv4 primary keys by default** on a high-write table where insertion order or index locality matters, without considering a sortable identifier or a plain auto-increment.
6. **Soft-delete (`deleted_at`) added to every table by default**, whether or not anything downstream ever needs to recover or audit a deletion — every query in the codebase now needs to remember to filter it.
7. **An internal admin/CRUD API secured identically to the public-facing one** (or the reverse) without asking whether their actual trust boundary, traffic, and failure tolerance are the same.

All of these are correct choices under the right constraints. The tell of a templated design is using one because it's the default move, not because this system's grounding — scale, consistency needs, team, lifespan — actually calls for it.

## Process: plan, review against constraints, build, critique

Work in two passes. First, sketch a short design plan:
- **Boundaries**: what modules/services exist and why each earns its boundary.
- **Data model**: core entities, their relationships, and the invariants each table/constraint protects.
- **Failure points**: the operations that cross a network or trust boundary, and what happens when each one fails partway.
- **Principles**: the one or two things this design is deliberately optimizing for (e.g., "optimize for write throughput over query flexibility, because X").

Then review that plan against the grounding before building: for each choice, check whether it follows from this system's actual scale, consistency needs, team, and lifespan, or whether it's the default you'd produce for any similar-sounding brief. Revise the parts that don't hold up, and say what changed and why. Only after that should you write the schema or the service code.

## Restraint and self-critique

Build to a quality floor without over-announcing it: constraints enforced at the layer that can actually guarantee them, errors that are specific and actionable, no unbounded queries or missing indexes on the paths that will actually run hot. Prefer one fewer abstraction over one more — a layer, a service, or a table you didn't need is a maintenance cost paid by everyone who touches this system after you. When you're unsure whether a boundary or a layer earns its keep, that uncertainty is itself the answer: cut it, and add it back when something concrete forces the issue.
