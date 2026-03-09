# QANorm API

## 1. Main Endpoint Groups

The Stage 2 API includes:

- sessions API;
- chat/query API;
- SSE event streaming;
- health endpoints;
- metrics endpoint.

## 2. Sessions

The API supports:

- creating a session;
- listing sessions;
- reading a session;
- reading message history for a session.

## 3. Queries

The API supports:

- submitting a query inside a session;
- fetching query details;
- reading the final answer and evidence;
- streaming progress events through SSE.

## 4. Service Endpoints

- `/health/live`
- `/health/ready`
- `/metrics`

## 5. Access Channels

The same application core is exposed through:

- web UI;
- Telegram adapter.

The transport layer differs, but the query/session/answer runtime is shared.

