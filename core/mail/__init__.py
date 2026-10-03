"""Gmail account capability for the serving MO Agent.

The package is deliberately import-light.  OAuth, HTTP, and private state are
loaded by an explicit mail action, never by ``core.agent.agent`` startup.
"""
