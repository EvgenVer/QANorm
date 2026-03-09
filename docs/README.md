# QANorm Documentation

This directory contains the main engineering documentation for QANorm: architecture, configuration, retrieval, API, security, testing, and operations.

## Recommended Reading Order

- [architecture.md](architecture.md)  
  High-level architecture of Stage 1 and Stage 2, major modules, and data flows.

- [retrieval.md](retrieval.md)  
  Chunk-based retrieval, embeddings, hybrid search, and backfill workflows.

- [operations.md](operations.md)  
  Local runtime commands, Docker workflows, health checks, logs, metrics, and background processes.

- [configuration.md](configuration.md)  
  Environment variables, YAML configuration files, and provider selection.

- [data-model.md](data-model.md)  
  Core Stage 1 and Stage 2 tables and the role of each data layer.

- [agents.md](agents.md)  
  The orchestrator-first agent system, prompts, verification, and bounded repair loops.

- [api.md](api.md)  
  Main HTTP endpoints, SSE transport, and access channels.

- [security.md](security.md)  
  Session isolation, prompt injection boundaries, provenance, and audit.

- [testing.md](testing.md)  
  Testing strategy and release acceptance expectations.

## Related Top-Level Documents

- [README.md](../README.md)  
  Short project overview.

- [SPECIFICATION.md](../SPECIFICATION.md)  
  Product and architecture requirements.

- [Plan.md](../Plan.md)  
  Implementation plan by stage.

- [Tasks.md](../Tasks.md)  
  Detailed engineering backlog.

## Documentation Roles

- `SPECIFICATION.md` explains what the system must do.
- `Plan.md` explains how the implementation is organized.
- `docs/*` explains how the system is structured and how to work with it.

