"""Narrow, nonexecuting content warnings; absence is not a safety certificate."""
import re


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def dangerous_content_flags(record):
    """Return deliberately small fixed warnings without interpreting record text."""
    flags = set()
    for text in _strings(record.get("payload", {})):
        if re.search(r"\b(?:curl|wget)\b[^\n]{0,4096}\|\s*(?:sudo\s+)?(?:bash|sh|zsh)\b", text, re.I):
            flags.add("REMOTE_INTERPRETER_EXECUTION")
        if re.search(r"\brm\s+-[A-Za-z]*[rR][A-Za-z]*\s+(?:/|~|\$HOME)\b", text):
            flags.add("BROAD_FILESYSTEM_DESTRUCTION")
    return tuple(sorted(flags))
