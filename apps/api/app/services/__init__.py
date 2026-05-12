"""Application services — code that lives between routes and adapters.

A service composes adapters (LLM, ASR, content-safety APIs) and the
data layer into business operations. Routes call services; services
never know what HTTP looks like.
"""
