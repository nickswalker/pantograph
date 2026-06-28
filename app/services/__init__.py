"""Service / domain layer.

Modules here own the application's business rules (state machines, multi-step
operations) independent of HTTP. Route handlers should parse the request, call
into a service, and serialize the result. Services raise domain exceptions
(e.g. TeamStateError) that callers translate into HTTP responses.
"""
