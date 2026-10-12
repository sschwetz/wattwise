# Avoid AI Writing Patterns

Applies to all prose output: code comments, documentation, PR descriptions, commit messages, issue text, and conversational replies. Based on Wikipedia's "Signs of AI writing". These patterns make writing sound generic, inflated, and machine-generated.

## Banned "AI vocabulary" words

Never use these words in prose. Find a plain, specific alternative instead.

| Banned | Plain alternative |
|--------|------------------|
| additionally (starting a sentence) | also, and, next, then, or restructure |
| align with | match, fit, follow, support |
| boasts (meaning "has") | has, includes, contains |
| bolstered | strengthened, improved, supported |
| comprehensive | cover what it actually covers, or cut it |
| crucial | critical, required, essential, or cut it |
| delve | look at, examine, explore |
| diverse array | range, variety, set |
| emphasizing / emphasises | says, notes, shows, argues |
| enduring | lasting, or cut it |
| enhance / enhancing | improve, extend, add to |
| encompassing | covering, including |
| fostering | building, supporting, encouraging |
| garner | get, earn, attract |
| groundbreaking | new, novel, or be specific |
| highlight / highlights (as a verb) | shows, reveals, points to, notes |
| holistic | describe the parts it covers |
| interplay | interaction, relationship, tension |
| intricate / intricacies | complex, detailed, or be specific |
| key (as an adjective) | important, main, primary, or cut it |
| landscape (abstract noun) | field, space, area, context |
| leverage | use |
| meticulous / meticulously | careful, thorough, precise |
| nestled | located, situated, in |
| pivotal | important, decisive, critical, or cut it |
| profound | deep, significant, or be specific |
| renowned | well-known, noted, or cut it |
| rich (cultural/historical) | describe the specifics instead |
| robust | solid, reliable, strong, or be specific |
| seamlessly | say how the parts connect |
| showcase / showcasing | show, display, demonstrate |
| stands as / serves as | is |
| tapestry (abstract noun) | mix, combination, range |
| testament | proof, sign, evidence |
| underscore (as a verb) | shows, demonstrates, confirms |
| valuable | useful, important, or cut it |
| vibrant | busy, lively, active, or be specific |

## Do not inflate significance

Do not add sentences explaining why something is important, significant, or part of a broader trend, unless that claim is the point being made.

Banned patterns:

- "X marks a pivotal moment in the evolution of Y"
- "X represents a significant shift toward Y"
- "X is part of a broader movement to Y"
- "X underscores its importance to Y"
- "X reflects broader Y"
- "X sets the stage for Y"
- "X has left an indelible mark on Y"
- "X is deeply rooted in Y"
- "X symbolises its ongoing/enduring relevance"
- "X contributes to the evolving landscape of Y"

If something matters, state what it does concretely.

## Use "is", not "serves as / stands as / represents / marks"

```text
BAD:  Gallery 825 serves as LAAA's exhibition space.
GOOD: Gallery 825 is LAAA's exhibition space.

BAD:  The event marks a key turning point in regional governance.
GOOD: The event changed how the region governed itself.
```

Do not use "refers to" as a lead-in when writing about something directly rather than defining a term.

## No promotional or travel-guide language

Banned patterns:

- "in the heart of X"
- "vibrant X with a rich cultural heritage"
- "offers visitors a fascinating glimpse"
- "commitment to excellence / sustainability / innovation"
- "dedicated to craftsmanship"
- "maintains an active social media presence"
- "seamlessly connecting X"
- "exceptional / world-class / cutting-edge"

Write what something does or is, not how impressive it is.

## No vague attributions

Do not attribute claims to unnamed authorities or imply consensus from thin evidence.

Banned patterns:

- "Experts argue..."
- "Observers have noted..."
- "Industry reports suggest..."
- "Some critics argue..."
- "Several sources indicate..." (when only one source exists)
- "It has been widely recognised that..."

Name a specific source, or state the claim plainly without attribution.

## No "challenges and future prospects" endings

Do not end documents, sections, or summaries with a formulaic "despite X, Y faces challenges" paragraph followed by vague optimism.

Banned structures:

- "Despite its [positive], X faces challenges including..."
- "Despite these challenges, X continues to..."
- "Future Prospects / Future Outlook / Challenges and Legacy" sections that speculate generically
- "As X continues to evolve..." as a closing sentence

If challenges or future work are relevant, state them specifically and briefly.

## No "not just X, but also Y"

This pattern argues against a strawman. Write the positive claim directly.

Banned patterns:

- "Not only X, but also Y"
- "This is not just X — it's Y"
- "Not a X, but a Y"
- "No X, no Y, just Z"
- "Not merely X, but Y"

```text
BAD:  This is not just a sourcing issue — it's a framing issue.
GOOD: The real problem is framing, not sourcing.
```

## Do not overuse the rule of three

Do not group items in threes to make an analysis look complete.

```text
BAD:  It improves reliability, performance, and maintainability.
GOOD: It improves reliability. (if that is the actual point)
```

If there are three or more distinct points, list them. Do not invent a third item to complete the pattern.

## No padding "-ing" clauses

Do not tack present-participle phrases onto the end of a sentence to suggest analysis.

```text
BAD:  The API returns a 404, highlighting the importance of input validation.
GOOD: The API returns a 404. Validate inputs before calling it.

BAD:  The migration runs in under a second, demonstrating the efficiency of the schema design.
GOOD: The migration runs in under a second.
```

## No generic scope or ecosystem claims

Do not say a component "plays a vital role in the broader ecosystem" or "contributes to the overall architecture" unless that relationship is the point.

```text
BAD:  This service plays a critical role in the overall data pipeline, contributing to the system's resilience.
GOOD: This service fetches rules from the database and caches them for 60 seconds.
```

## Prefer concrete specifics

LLMs regress toward the average, replacing specific facts with generic importance claims. Go the other way.

```text
BAD:  She is a pioneering figure in the field who has made significant contributions.
GOOD: She wrote the compiler used by 80% of the team.

BAD:  The function handles edge cases gracefully.
GOOD: The function returns an empty list when the input is nil, rather than panicking.
```

## Checklist

Before finalising any prose, scan for:

- [ ] Any word from the banned vocabulary list
- [ ] Sentences that exist only to say something is important or significant
- [ ] "serves as / stands as / represents" instead of "is"
- [ ] Promotional or travel-guide adjectives
- [ ] Vague attributions ("experts say", "observers note")
- [ ] "Despite X... faces challenges" conclusions
- [ ] "Not only X but also Y" constructions
- [ ] Three-item lists added to appear thorough
- [ ] Trailing "-ing" clauses that add no information
- [ ] Generic scope or ecosystem claims that could apply to anything
