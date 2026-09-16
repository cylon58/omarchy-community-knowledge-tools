"""Bounded bridge between the shell companion and reviewed toolkit commands."""
from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
import time


MAX_CLI_JSON_BYTES = 64 * 1024
MAX_DISPLAY_CHARS = 12_000
DEFAULT_TIMEOUT_SECONDS = 8


class ProcessLimitError(RuntimeError):
    pass


def _required(fields, name):
    value = fields.get(name)
    if type(value) is not str or not value or len(value) > 4096:
        raise ValueError(f"Missing or invalid {name}")
    return value


def build_cli_argv(operation: str, fields: dict, *, executable: str = "omarchy-knowledge") -> list[str]:
    if type(fields) is not dict:
        raise ValueError("Panel request must be an object")
    if operation == "search":
        intent = fields.get("intent", "corrective")
        if intent not in {"corrective", "optional", "undetermined", "all"}:
            raise ValueError("Invalid search intent")
        query = fields.get("query", "")
        if type(query) is not str or len(query) > 4096:
            raise ValueError("Invalid search query")
        argv = [executable, "search", "--cache", _required(fields, "cache"),
                "--query", query, "--intent", intent]
        environment = fields.get("environment", "")
        if environment:
            argv.extend(("--environment", _required(fields, "environment")))
        return argv + ["--json"]
    if operation == "status":
        return [executable, "status", "--cache", _required(fields, "cache"), "--json"]
    if operation == "preview":
        destination = fields.get("destination")
        if destination not in {"ledger", "plugin", "toolkit", "upstream"}:
            raise ValueError("Invalid preview destination")
        return [
            executable, "preview", _required(fields, "draft"),
            "--config", _required(fields, "config"), "--destination", destination,
            "--title", _required(fields, "title"), "--body", _required(fields, "body"),
            "--attribution", _required(fields, "attribution"),
        ]
    raise ValueError("Unsupported panel operation")


def run_bounded_process(argv: list[str], *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
                        output_limit: int = MAX_CLI_JSON_BYTES) -> tuple[int, bytes, bytes]:
    if not argv or timeout_seconds <= 0 or output_limit <= 0:
        raise ValueError("Invalid bounded process request")
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    streams = selectors.DefaultSelector()
    for stream in (process.stdout, process.stderr):
        os.set_blocking(stream.fileno(), False)
        streams.register(stream, selectors.EVENT_READ)
    output = {process.stdout: bytearray(), process.stderr: bytearray()}
    deadline = time.monotonic() + timeout_seconds
    try:
        while streams.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessLimitError("Toolkit command exceeded the panel time limit")
            for key, _ in streams.select(min(remaining, 0.05)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    streams.unregister(key.fileobj)
                    continue
                output[key.fileobj].extend(chunk)
                if sum(len(value) for value in output.values()) > output_limit:
                    raise ProcessLimitError("Toolkit response exceeded the panel size limit")
        return_code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        return return_code, bytes(output[process.stdout]), bytes(output[process.stderr])
    except (ProcessLimitError, subprocess.TimeoutExpired):
        process.kill()
        process.wait()
        raise ProcessLimitError("Toolkit command exceeded the panel time or size limit")
    except Exception:
        if process.poll() is None:
            process.kill()
            process.wait()
        raise
    finally:
        streams.close()
        process.stdout.close()
        process.stderr.close()


def _parse_json(raw: bytes):
    if len(raw) > MAX_CLI_JSON_BYTES:
        raise ProcessLimitError("Toolkit response exceeded the panel size limit")
    try:
        text = raw.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=lambda pairs: _unique_object(pairs),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("Toolkit did not return valid JSON") from error


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _bounded_display(value) -> str:
    rendered = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True)
    if len(rendered) > MAX_DISPLAY_CHARS:
        return rendered[: MAX_DISPLAY_CHARS - 52] + "\n\n[Display truncated by the companion size limit.]"
    return rendered


def render_cli_json(operation: str, raw: bytes) -> dict:
    try:
        value = _parse_json(raw)
        if type(value) is not dict:
            raise ValueError("Toolkit JSON response must be an object")
        if operation == "search":
            required = {"query", "intent", "data_revision", "trust", "results"}
            if not required.issubset(value) or type(value["results"]) is not list:
                raise ValueError("Toolkit search response has an unexpected shape")
        elif operation == "status":
            if "trust" not in value:
                raise ValueError("Toolkit status response has an unexpected shape")
        elif operation == "preview":
            if value.get("published") is not False or "preview" not in value:
                raise ValueError("Toolkit preview response has an unexpected shape")
        else:
            raise ValueError("Unsupported panel operation")
        return {"ok": True, "status": "Local toolkit response", "display": _bounded_display(value)}
    except ProcessLimitError as error:
        return {"ok": False, "status": str(error), "display": ""}
    except ValueError as error:
        return {"ok": False, "status": str(error), "display": ""}


def run_panel_request(operation: str, fields: dict, *, executable: str = "omarchy-knowledge") -> dict:
    try:
        argv = build_cli_argv(operation, fields, executable=executable)
        return_code, stdout, stderr = run_bounded_process(argv)
        if return_code != 0:
            message = stderr.decode("utf-8", "replace").strip()
            if len(message) > 500:
                message = message[:497] + "..."
            return {"ok": False, "status": message or "Toolkit rejected the local request", "display": ""}
        return render_cli_json(operation, stdout)
    except FileNotFoundError:
        return {"ok": False, "status": "Install the Omarchy knowledge toolkit to use this panel.", "display": ""}
    except (OSError, ProcessLimitError, ValueError) as error:
        return {"ok": False, "status": str(error), "display": ""}


def panel_main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="omarchy-knowledge-panel",
        description="Bounded local bridge for the Omarchy knowledge companion panel.",
    )
    parser.add_argument("operation", choices=("search", "status", "preview"))
    parser.add_argument("--request", required=True, help="bounded JSON object of local panel fields")
    args = parser.parse_args(argv)
    try:
        if len(args.request.encode()) > 16 * 1024:
            raise ValueError("Panel request exceeded the size limit")
        fields = json.loads(args.request)
        result = run_panel_request(args.operation, fields)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        result = {"ok": False, "status": "Panel request was malformed or oversized.", "display": ""}
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(panel_main())
