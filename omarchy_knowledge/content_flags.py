"""Narrow, nonexecuting admission flags; neither schema validity nor safety proof.

Patterns deliberately flag quoted examples as well as instructions. Exceptions
need governed review outside the automated lane; this MVP has no bypass switch.
"""
import re
import shlex


_INTERPRETER = r"(?:bash|sh|zsh|dash|python[0-9.]*|perl|ruby|node)\b"
_SUDO_OPTION = (r"(?:(?:-[ughpCTrt]|--(?:user|group|host|prompt|chdir|chroot|role|type))"
                r"\s+[^\s;|&()]{1,128}|--?[A-Za-z][A-Za-z0-9=-]{0,127})\s+")
_RUN = r"(?:sudo\s+(?:" + _SUDO_OPTION + r"){0,16})?" + _INTERPRETER
_REMOTE_PIPE = re.compile(r"\b(?:curl|wget)\b[^;\n]{0,4096}(?:[|;]|&&)\s*" + _RUN, re.IGNORECASE)
_INTERPRETER_OPTIONS = r"(?:--?[A-Za-z][A-Za-z0-9=-]{0,31}\s+){0,8}"
_REMOTE_SUBSTITUTION = re.compile(_INTERPRETER + r"\s+" + _INTERPRETER_OPTIONS
                                  + r"[<(\"']*\$?\(\s*(?:curl|wget)\b", re.IGNORECASE)
_EVAL = re.compile(r"\beval(?:\s+\S|\s*\()|\bexec\s*(?:\(|[\"'$])", re.IGNORECASE)
_DECODE_EXEC = re.compile(r"\b(?:base64|openssl)\b[^;\n]{0,4096}\|\s*" + _RUN, re.IGNORECASE)
_RM = re.compile(r"\brm\s+([^\n;|]{1,4096})")
_DEVICE = r"/dev/(?:sd[a-z]|vd[a-z]|xvd[a-z]|nvme\d+n\d+|mmcblk\d+)(?:p?\d+)?\b"
_BLOCK_OPERATION = re.compile(r"\b(?:mkfs(?:\.[a-z0-9]+)?|wipefs|blkdiscard|shred)\b[^;\n]{0,4096}" + _DEVICE)
_BLOCK_WRITE = re.compile(r"(?:\bof\s*=\s*|>\s*)[\"']?" + _DEVICE)
_BROAD_TARGETS = {"/", "/*", "/home", "/home/*", "/usr", "/etc", "/var", "~", "~/", "~/*",
                  "$HOME", "$HOME/", "$HOME/*", "${HOME}", "${HOME}/", "${HOME}/*", "$PWD", "${PWD}",
                  ".", "./", "*", "{user_home}", "{user_home}/*"}


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
    """Inspect an already bounded/validated record, returning only fixed codes.

    Procedures, rollback/validation/activation instructions and any backtick
    snippets in other record text are inert strings. Nothing is decoded, expanded,
    fetched, imported, or executed. Obfuscated or novel hazards can evade these
    narrow flags; absence of flags must never be presented as proof of safety.
    """
    texts = []
    if record["type"] == "change":
        for key in ("procedure", "rollback", "validation_plan", "activation"):
            texts.extend(_strings(record["payload"].get(key)))
    texts.extend(text for text in _strings(record) if "`" in text)
    flags = set()
    for text in texts:
        if _REMOTE_PIPE.search(text) or _REMOTE_SUBSTITUTION.search(text):
            flags.add("REMOTE_INTERPRETER_EXECUTION")
        if _EVAL.search(text) or _DECODE_EXEC.search(text):
            flags.add("ENCODED_OR_EVAL_EXECUTION")
        if _BLOCK_OPERATION.search(text) or _BLOCK_WRITE.search(text):
            flags.add("BLOCK_DEVICE_DESTRUCTION")
        for match in _RM.finditer(text):
            try:
                args = shlex.split(match.group(1), comments=False, posix=True)
            except ValueError:
                continue
            recursive = any(arg == "--recursive" or (arg.startswith("-") and not arg.startswith("--")
                                                    and ("r" in arg or "R" in arg)) for arg in args)
            if recursive and any(arg.rstrip("`") in _BROAD_TARGETS for arg in args):
                flags.add("BROAD_FILESYSTEM_DESTRUCTION")
    return tuple(sorted(flags))
