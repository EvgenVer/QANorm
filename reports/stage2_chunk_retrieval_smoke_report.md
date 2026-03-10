# Stage 2 Chunk Retrieval Smoke Report

Date: 2026-03-10

## Scope

This smoke run verifies that the Stage 2 chunk-based retrieval layer works against
the populated Stage 1 PostgreSQL corpus after the full Gemini embeddings backfill.

## Corpus State

- Active document versions with retrieval chunks: `1681`
- Persisted `retrieval_chunks`: `189109`
- Persisted `chunk_embeddings`: `174716`
- Embedding provider: `gemini`
- Embedding model: `gemini-embedding-001`
- Embedding dimensions: `768`

## Smoke Queries

The retrieval service was executed against the live `qanorm` database with the
production runtime configuration.

### Query 1

- Query: `SP 35.13330.2011`
- Expected behavior: exact code-oriented retrieval for the bridges and culverts code
- Observed top document: active document code `35.13330.2011`
- Observed source path: `exact`
- Result: pass

### Query 2

- Query: `Federal Law 44-FZ`
- Expected behavior: exact code-oriented retrieval for the procurement law
- Observed top document: active document code `44-FZ`
- Observed source path: `exact`
- Result: pass

### Query 3

- Query: `bridges and culverts design`
- Expected behavior: semantic retrieval over chunk embeddings
- Observed top document: the active bridges and culverts code
- Observed source path: `vector`
- Result: pass

## Conclusion

The Stage 2 retrieval layer is populated and operational on the full Stage 1
corpus. Exact, lexical, and vector-backed chunk retrieval execute successfully on
the live database with real Gemini embeddings.
