# Upstream Gateway — Development Roadmap

## 1. Project Overview

Upstream Gateway is a lightweight, asynchronous API gateway built with FastAPI.

Its primary responsibility is to forward incoming requests to multiple upstream providers, select the most suitable provider, handle failures, and return a normalized response.

The project aims to provide:

- Intelligent provider selection
- Automatic fallback
- Provider-level concurrency control
- Failure handling and cooldown
- Request persistence
- Performance monitoring

## 2. Current Implementation

### Core Features

- [x] FastAPI application
- [x] Multiple upstream provider configuration
- [x] Asynchronous HTTP communication using HTTPX
- [x] Provider selection based on latency and error rate
- [x] Fallback on connection failures
- [x] HTTP 429 handling
- [x] Provider cooldown
- [x] Configurable retry policy
- [x] Provider-level and global concurrency limits
- [x] PostgreSQL request persistence with SQLite test support
- [x] Structured JSON logging

### Integration Testing

- [x] Successful upstream request
- [x] Connection error handling
- [x] Fallback behavior
- [x] Timeout handling
- [x] Request persistence
- [x] HTTP 429 handling
- [x] Cooldown behavior (initial verification)

Note: Exact cooldown duration and concurrency limits still require dedicated automated tests.

## 3. Development Roadmap

### Phase 1 — Reliability and Correctness

Priority: High

- [ ] Implement an overall request deadline.
- [ ] Add request and response body size limits.
- [ ] Review retry behavior for ambiguous failures.
- [ ] Improve timeout and cancellation handling.
- [ ] Add automated tests for cooldown expiration.
- [ ] Add concurrency and overload tests.
- [ ] Verify behavior when all providers are unavailable.

### Phase 2 — Architecture and Scalability

Priority: High

- [ ] Introduce a cleaner provider registry.
- [ ] Separate provider selection strategy from routing orchestration.
- [ ] Implement provider health checks.
- [ ] Review routing behavior under high concurrency.
- [ ] Evaluate Redis for shared provider state.
- [x] Migrate persistent request storage to PostgreSQL.
- [ ] Support multiple application workers safely.

### Phase 3 — Observability

Priority: Medium

- [ ] Add Prometheus metrics.
- [ ] Expose provider-level success and error rates.
- [ ] Track request latency percentiles.
- [ ] Track provider selection and fallback statistics.
- [ ] Add Grafana dashboards.
- [ ] Evaluate distributed tracing.

### Phase 4 — Production Readiness

Priority: High

- [ ] Implement API authentication.
- [ ] Add consumer-level rate limiting.
- [ ] Add secure provider configuration.
- [x] Add Docker and Docker Compose support.
- [ ] Implement CI/CD workflows.
- [ ] Add deployment documentation.
- [ ] Review logging, data retention, and security policies.

## 4. Technical Constraints

The current implementation has several limitations that must be considered before production deployment:

- Provider routing state is process-local.
- Multiple workers may not share cooldown and routing statistics.
- PostgreSQL is used for persistence; schema changes require Alembic migrations.
- Authentication is not implemented.
- Public deployment requires additional security controls.
- Total request deadline and response size limits need implementation.

## 5. Development Principles

1. Prefer simple and maintainable architecture over unnecessary complexity.
2. Keep provider integrations independent from routing logic.
3. Make failure handling explicit and predictable.
4. Avoid unsafe retries for operations with ambiguous outcomes.
5. Add automated tests for every significant behavior.
6. Document architectural decisions and trade-offs.
7. Prioritize reliability and observability before adding advanced features.

## 6. Current Development Focus

The next development cycle should prioritize Phase 1, starting with request lifecycle management, resource limits, and automated reliability tests.
