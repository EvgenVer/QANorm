# QANorm Security

## 1. Core Principles

Security in QANorm is built around the following rules:

- retrieved text is never treated as instruction text;
- session isolation is mandatory;
- provenance is recorded in the audit trail;
- trusted and open-web sources are clearly separated;
- verification and code guardrails are not replaced by prompts alone.

## 2. Prompt Injection

External documents, web pages, and extracted text are always treated as data, not as control instructions.

This is especially important for:

- open web search;
- trusted source content;
- text extracted from documents.

## 3. Session Isolation

Isolation applies to:

- message history;
- query state;
- tool state;
- background jobs;
- temporary artifacts;
- intermediate answer data.

## 4. Audit and Provenance

The audit trail is expected to cover:

- user queries;
- retrieval;
- tool calls;
- freshness checks;
- final answers;
- prompt and model versions.

## 5. External Sources

Non-normative sources follow stricter rules:

- trusted sources and open web are labeled separately;
- external information is never blended with normative content without explicit marking;
- user-visible answers must expose what is normative and what is not.

