# CLAUDE.md

## Code Review Standards

After completing any implementation, review the code for:
- Functions longer than 30 lines (likely doing too much)
- Logic duplicated more than twice (extract to utility)
- Any `any` type usage in TypeScript (replace with real types)
- Components with more than 3 props that could be grouped into an object
- Missing error handling on async operations
- Secrets stored in plaintext (see Storing Secrets)

Run /simplify before presenting code to the user.

## Documentation

Every change that alters behaviour, configuration, an endpoint, a table, a UI screen or a service updates the docs in the same change. Do not defer it.

- Run the `documentation` agent after implementation, before presenting the work. It lists where each kind of fact lives.
- A user-visible change needs both a `docs/` page (maintainers) and a `webapp/src/docs/` chapter (users). Check the guide's tab list, dashboard tile list, glossary and troubleshooting chapter.
- Each fact has one home. Link to it instead of copying it.
- Rewrite the stale sentence. Do not add a note beside it.
- Do not write freshness claims such as "not yet built", a commit hash or a line count.
- Run the link and anchor check before finishing. A change is not done while a doc contradicts the code.

## Storing Secrets

Never store a secret in plaintext, in the database or in a file the app writes. Pick the storage by whether the app needs the value back:

- **The app only checks it** (user passwords, reset and confirmation tokens, recovery codes): store a one-way hash. Use `hash_password` (bcrypt) for passwords. Use `hash_token` for 256-bit random tokens. Use `hash_recovery_code` (keyed HMAC) for anything short enough to brute-force offline. Never use plain SHA-256 on a low-entropy value.
- **The app must send it somewhere** (device tokens, third-party passwords, API keys, TOTP secrets): encrypt it with the `EncryptedString` or `EncryptedJSON` column types in `core-api/core_api/encrypted_types.py`. Hashing would make the value useless.

Rules that follow from this:
- A new column that holds a credential, token, key or secret must use one of the above. Adding a migration that encrypts or rehashes existing rows is part of the same change.
- Encrypted columns use a random IV, so you cannot compare or filter on them in SQL. Load the row and compare in Python, or guard with `IS NOT NULL`.
- Never print, log or paste secret values into replies, commit messages or docs. When you must inspect a table that holds secrets, select only the non-secret columns.
- Keys and shared secrets (`SECRETS_ENCRYPTION_KEY`, `JWT_SECRET`, the collector keys) come from environment variables, never from source or committed files. `.env.example` files hold placeholders only, never real values.
- Tests that need the encryption key get it from `tests/conftest.py`. Do not hardcode real keys elsewhere.

## Writing Style

Follow `.claude/rules/avoid-ai-writing.md` for all prose: code comments, docs, commit messages, PR descriptions, and replies.
