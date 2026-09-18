"""Pure, bounded scheduling traversal for lifetime pull-request intake.

The cursor and list rows are untrusted scheduling inputs.  This module confers no
admission, import, receipt, or write authority; callers provide exact imported
heads and an evaluator that performs any authenticated candidate work.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re
from typing import Any


MAX_INT = 2_147_483_647
PAGE_SIZE = 20
MAX_PAGE_FETCHES = 10
MAX_RETURNED_ROWS = 200
MAX_EVALUATIONS = 20
_OID = re.compile(r"[0-9a-f]{40}\Z")
_CURSOR_FIELDS = {
    "version", "page", "offset", "after_pull_request", "cycle",
    "last_full_cycle_at",
}


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z") or len(value) > 40:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


@dataclass(frozen=True)
class IntakeCursor:
    version: int
    page: int
    offset: int
    after_pull_request: int | None
    cycle: int
    last_full_cycle_at: str | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "IntakeCursor":
        if not isinstance(value, Mapping) or set(value) != _CURSOR_FIELDS:
            raise ValueError("invalid intake cursor")
        version = value["version"]
        page = value["page"]
        offset = value["offset"]
        after = value["after_pull_request"]
        cycle = value["cycle"]
        completed = value["last_full_cycle_at"]
        if type(version) is not int or version != 1:
            raise ValueError("invalid intake cursor")
        if not _integer(page, 1, MAX_INT) or not _integer(offset, 0, PAGE_SIZE):
            raise ValueError("invalid intake cursor")
        if offset == 0:
            if after is not None:
                raise ValueError("invalid intake cursor")
        elif not _integer(after, 1, MAX_INT):
            raise ValueError("invalid intake cursor")
        if not _integer(cycle, 0, MAX_INT):
            raise ValueError("invalid intake cursor")
        if completed is not None and not _utc_timestamp(completed):
            raise ValueError("invalid intake cursor")
        return cls(version, page, offset, after, cycle, completed)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "page": self.page,
            "offset": self.offset,
            "after_pull_request": self.after_pull_request,
            "cycle": self.cycle,
            "last_full_cycle_at": self.last_full_cycle_at,
        }


@dataclass(frozen=True)
class PullRequestRow:
    pull_request: int
    state: str
    head: str | None


class EvaluationOutcome(str, Enum):
    PLAN = "plan"
    REJECTED = "rejected"
    NOT_READY = "not-ready"


@dataclass(frozen=True)
class CandidateEvaluation:
    outcome: EvaluationOutcome
    plan: Any | None = None

    def __post_init__(self) -> None:
        if type(self.outcome) is not EvaluationOutcome:
            raise ValueError("invalid candidate evaluation")
        if (self.outcome is EvaluationOutcome.PLAN) != (self.plan is not None):
            raise ValueError("invalid candidate evaluation")


class ScanOutcome(str, Enum):
    PLANNED = "planned"
    NO_ELIGIBLE = "no-eligible"


class StopReason(str, Enum):
    PLAN = "plan"
    PREPARATION_LIMIT = "preparation-limit"
    FETCH_LIMIT = "fetch-limit"
    CYCLE_COMPLETE = "cycle-complete"


@dataclass(frozen=True)
class ScanCounters:
    page_fetches: int
    rows_returned: int
    rows_consumed: int
    evaluations: int
    closed: int
    imported: int
    rejected: int
    not_ready: int
    plans: int
    cursor_drifts: int


@dataclass(frozen=True)
class ScanTransition:
    before: IntakeCursor
    proposed_after: IntakeCursor
    outcome: ScanOutcome
    stop_reason: StopReason
    plan: Any | None
    counters: ScanCounters


def _normalize_page(value: Any) -> tuple[PullRequestRow, ...]:
    if type(value) is not list or len(value) > PAGE_SIZE:
        raise ValueError("invalid pull-request page")
    normalized = []
    identities = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("invalid pull-request row")
        state = item.get("state")
        expected = {"pull_request", "state", "head"} if state == "open" else {
            "pull_request", "state",
        }
        if set(item) != expected or state not in {"open", "closed"}:
            raise ValueError("invalid pull-request row")
        number = item["pull_request"]
        if not _integer(number, 1, MAX_INT) or number in identities:
            raise ValueError("invalid pull-request row")
        identities.add(number)
        head = item.get("head")
        if state == "open" and (not isinstance(head, str) or _OID.fullmatch(head) is None):
            raise ValueError("invalid pull-request row")
        normalized.append(PullRequestRow(number, state, head))
    return tuple(normalized)


def _normalize_imported(value: Any) -> frozenset[tuple[int, str]]:
    if not isinstance(value, (set, frozenset)):
        raise ValueError("invalid imported-head facts")
    normalized = set()
    for item in value:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError("invalid imported-head facts")
        number, head = item
        if not _integer(number, 1, MAX_INT) or not isinstance(head, str) \
                or _OID.fullmatch(head) is None:
            raise ValueError("invalid imported-head facts")
        normalized.add((number, head))
    return frozenset(normalized)


def scan(
    cursor: Mapping[str, Any],
    *,
    imported_heads: Set[tuple[int, str]],
    fetch_page: Callable[[int], Any],
    evaluate: Callable[[PullRequestRow], CandidateEvaluation],
    now: str,
) -> ScanTransition:
    """Scan bounded lifetime pages and propose, but never persist, cursor progress.

    Callback exceptions deliberately propagate.  A malformed page/evaluation also
    raises ``ValueError``; neither path can return a usable after-state.
    """
    before = IntakeCursor.from_mapping(cursor)
    imported = _normalize_imported(imported_heads)
    if not _utc_timestamp(now):
        raise ValueError("invalid scan timestamp")
    if not callable(fetch_page) or not callable(evaluate):
        raise ValueError("invalid scan callback")

    counts = {
        "page_fetches": 0, "rows_returned": 0, "rows_consumed": 0,
        "evaluations": 0, "closed": 0, "imported": 0, "rejected": 0,
        "not_ready": 0, "plans": 0, "cursor_drifts": 0,
    }
    page = before.page
    offset = before.offset
    after_pull_request = before.after_pull_request
    cycle = before.cycle
    completed = before.last_full_cycle_at
    seen_in_sequence: set[int] = set()

    def current_cursor() -> IntakeCursor:
        return IntakeCursor(1, page, offset, after_pull_request, cycle, completed)

    def counters() -> ScanCounters:
        return ScanCounters(**counts)

    def transition(outcome: ScanOutcome, stop: StopReason,
                   plan: Any | None = None) -> ScanTransition:
        return ScanTransition(before, current_cursor(), outcome, stop, plan, counters())

    def complete_cycle() -> None:
        nonlocal page, offset, after_pull_request, cycle, completed
        if cycle == MAX_INT:
            raise ValueError("intake cursor cycle exhausted")
        page = 1
        offset = 0
        after_pull_request = None
        cycle += 1
        completed = now

    while True:
        if counts["page_fetches"] == MAX_PAGE_FETCHES:
            return transition(ScanOutcome.NO_ELIGIBLE, StopReason.FETCH_LIMIT)

        rows = _normalize_page(fetch_page(page))
        counts["page_fetches"] += 1
        counts["rows_returned"] += len(rows)
        if counts["rows_returned"] > MAX_RETURNED_ROWS:
            raise ValueError("returned-row budget exceeded")

        if offset > 0 and (len(rows) < offset
                           or rows[offset - 1].pull_request != after_pull_request):
            counts["cursor_drifts"] += 1
            page = 1
            offset = 0
            after_pull_request = None
            # Repetition with the discarded anchor response is expected only here.
            # Fetch/row/evaluation counters are intentionally not reset.
            seen_in_sequence.clear()
            continue

        identities = {row.pull_request for row in rows}
        if identities & seen_in_sequence:
            raise ValueError("repeated pull-request page content")
        seen_in_sequence.update(identities)

        for index in range(offset, len(rows)):
            row = rows[index]
            counts["rows_consumed"] += 1
            offset = index + 1
            after_pull_request = row.pull_request

            if row.state == "closed":
                counts["closed"] += 1
            elif (row.pull_request, row.head) in imported:
                counts["imported"] += 1
            else:
                evaluation = evaluate(row)
                counts["evaluations"] += 1
                if type(evaluation) is not CandidateEvaluation:
                    raise ValueError("invalid candidate evaluation")
                if evaluation.outcome is EvaluationOutcome.PLAN:
                    counts["plans"] += 1
                    if offset == len(rows) and len(rows) < PAGE_SIZE:
                        complete_cycle()
                    return transition(ScanOutcome.PLANNED, StopReason.PLAN,
                                      evaluation.plan)
                if evaluation.outcome is EvaluationOutcome.REJECTED:
                    counts["rejected"] += 1
                elif evaluation.outcome is EvaluationOutcome.NOT_READY:
                    counts["not_ready"] += 1
                else:
                    raise ValueError("invalid candidate evaluation")

                if counts["evaluations"] == MAX_EVALUATIONS:
                    if offset == len(rows) and len(rows) < PAGE_SIZE:
                        complete_cycle()
                    return transition(ScanOutcome.NO_ELIGIBLE,
                                      StopReason.PREPARATION_LIMIT)

        if len(rows) < PAGE_SIZE:
            complete_cycle()
            return transition(ScanOutcome.NO_ELIGIBLE, StopReason.CYCLE_COMPLETE)

        # A full page remains at offset 20 until a later page is fetched.  If this
        # run has no fetch budget left, the retained cursor is returned as-is.
        if page == MAX_INT:
            raise ValueError("intake cursor page exhausted")
        if counts["page_fetches"] == MAX_PAGE_FETCHES:
            return transition(ScanOutcome.NO_ELIGIBLE, StopReason.FETCH_LIMIT)
        page += 1
        offset = 0
        after_pull_request = None
