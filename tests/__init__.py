""" pytest test suite for the Data Extraction Service.

1. ``unit``        - isolated business logic
2. ``integration`` - endpoints + database
3. ``contract``    - request/response schemas and OpenAPI
4. ``workflow``    - start -> poll -> results -> cancel -> remove lifecycles
5. ``resilience``  - retries, recovery, concurrency, failure injection
6. ``security``    - authentication, authorization, tenant isolation
7. ``e2e``         - live third-party integration (opt-in)
"""
