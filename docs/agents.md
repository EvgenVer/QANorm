# QANorm Agent System

## 1. General Approach

QANorm uses an `orchestrator-first` architecture.

One main orchestrator:

- analyzes the user request;
- decomposes it into subtasks;
- invokes retrieval and tool calls;
- coordinates synthesis, verification, and freshness checks;
- produces the final answer.

## 2. Main Roles

- `query_analyzer`
- `task_decomposer`
- `normative_retriever`
- `answer_synthesizer`
- `verification layer`
- `freshness branch`
- `trusted/open web researchers`

## 3. Prompt Layer

System prompts are not hardcoded directly inside agent logic.

The runtime uses:

- prompt registry;
- prompt templates;
- prompt fragments;
- versioned prompt metadata.

This keeps model behavior versionable and traceable independently from orchestration code.

## 4. Verification and Bounded Repair Loop

After a draft answer is produced, the verification layer checks:

- coverage;
- citations;
- supportedness;
- source labeling;
- safety constraints.

If the issue is repairable, the system runs a bounded repair pass. The number of repair cycles is strictly limited.

## 5. Freshness

Document freshness checks do not block the main answer.

The system:

- answers from the local corpus;
- checks freshness in parallel;
- warns when a stale local version is used;
- may trigger background refresh.

