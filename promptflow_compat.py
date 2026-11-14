"""Prompt Flow supplies the decorator in its runtime; local helpers need no SDK."""
try:
    from promptflow import tool
except ModuleNotFoundError as exc:
    if exc.name != "promptflow":
        raise

    def tool(function):
        return function
