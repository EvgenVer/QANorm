# QANorm Testing

## 1. Testing Layers

The project uses:

- unit tests;
- integration tests;
- smoke tests.

## 2. What the Test Suite Must Cover

The test suite should verify:

- ingestion and retrieval logic;
- session and query runtime;
- answer synthesis;
- verification behavior;
- freshness logic;
- web and Telegram integration;
- observability and audit hooks.

## 3. Acceptance Checks

Before release, the system should pass:

- retrieval smoke;
- answer flow smoke;
- freshness smoke;
- web UI smoke;
- Telegram smoke;
- observability smoke.

## 4. Purpose of the Test System

The test system must confirm that:

- the regulatory database is available;
- retrieval returns correct citations;
- answers are built only from supported evidence;
- external sources are labeled correctly;
- session isolation and audit behavior work as intended.

