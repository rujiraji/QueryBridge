"""Versioned business definitions and clarification rules selected by the caller."""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ._json import decode_json
from .connector import QueryBridgeError
from .temporal import _ISO_DATETIME


class ProfileError(QueryBridgeError):
    code = "profile_error"


_ISO_TIMESTAMP = _ISO_DATETIME + r"(?:Z|[+-][0-9]{2}:?[0-5][0-9]|\s+UTC)?"
_TIMESTAMP_INTERVAL = re.compile(
    r"(?:\b(?P<utc>UTC)\s+(?:range|interval|window|bounds)\s*:?\s*)?"
    r"(?:(?P<between>\bbetween)\s+|\bfrom\s+|\bstart\s*[:=]\s*)?"
    rf"(?<![\w:])(?P<start>{_ISO_TIMESTAMP})(?:\s+(?:inclusive|\(inclusive\)))?"
    r"(?:\s+(?:to|through|until)\s+(?:before\s+)?|\s+(?P<and>and)\s+|\s*/\s*|\s+[-–—]\s+"
    r"|\s*(?:[,;]\s*|\band\s+)?\bend\s*[:=]?\s*)"
    rf"(?P<end>{_ISO_TIMESTAMP})(?![\w:+-]|\.\d)",
    re.IGNORECASE,
)


def _utc_bounds(match: re.Match[str]) -> tuple[datetime, datetime] | None:
    range_is_utc = match["utc"] is not None
    if match["and"] and not (match["between"] or range_is_utc):
        return None
    bounds = []
    for name in ("start", "end"):
        text = match[name].upper()
        if text.endswith("UTC"):
            text = text[:-3].rstrip() + "+00:00"
        try:
            bound = datetime.fromisoformat(text)
        except ValueError:
            return None
        if bound.tzinfo is None and range_is_utc:
            bound = bound.replace(tzinfo=timezone.utc)
        if bound.tzinfo != timezone.utc:
            return None
        bounds.append(bound)
    start, end = bounds
    return (start, end) if start < end else None


def _has_utc_interval(question: str) -> bool:
    return any(_utc_bounds(match) is not None for match in _TIMESTAMP_INTERVAL.finditer(question))


@dataclass(frozen=True)
class BusinessDefinition:
    id: str
    text: str


@dataclass(frozen=True)
class ClarificationRule:
    when: str
    question: str
    unless: str = ""
    unless_utc_interval: bool = False

    def matches(self, question: str) -> bool:
        return bool(re.search(self.when, question, re.IGNORECASE)) and (
            not self.unless or not re.search(self.unless, question, re.IGNORECASE)
        ) and not (self.unless_utc_interval and _has_utc_interval(question))


@dataclass(frozen=True)
class BusinessProfile:
    id: str = "generic"
    version: str = "1"
    definitions: tuple[BusinessDefinition, ...] = ()
    clarifications: tuple[ClarificationRule, ...] = ()

    def clarify(self, question: str) -> str | None:
        return next((rule.question for rule in self.clarifications if rule.matches(question)), None)

    def generation_question(self, question: str) -> str:
        """Write validated bounds in the timestamp format accepted by SQLite queries."""
        if not any(rule.unless_utc_interval for rule in self.clarifications):
            return question

        parts = []
        previous_end = 0
        for match in _TIMESTAMP_INTERVAL.finditer(question):
            bounds = _utc_bounds(match)
            if bounds is None:
                continue
            for name, bound in zip(("start", "end"), bounds):
                start, end = match.span(name)
                parts.append(question[previous_end:start])
                parts.append(bound.isoformat().removesuffix("+00:00") + "Z")
                previous_end = end
        parts.append(question[previous_end:])
        return "".join(parts)

    @classmethod
    def load(cls, path: Path) -> "BusinessProfile":
        try:
            with path.open("rb") as file:
                raw = file.read(65_537)
            if len(raw) > 65_536:
                raise ValueError("Profile files are limited to 64 KiB.")
            value = decode_json(raw)
            if not isinstance(value, dict) or value.keys() != {"id", "version", "definitions", "clarifications"}:
                raise ValueError("Expected id, version, definitions, and clarifications.")
            identity, version = _string(value["id"], 128), _string(value["version"], 128)
            if not isinstance(value["definitions"], list) or not isinstance(value["clarifications"], list):
                raise ValueError("Definitions and clarifications must be arrays.")
            definitions = []
            for item in value["definitions"]:
                if not isinstance(item, dict) or item.keys() != {"id", "text"}:
                    raise ValueError("Definitions require an id and text.")
                definitions.append(BusinessDefinition(_string(item["id"], 128), _string(item["text"], 4096)))
            if len({item.id for item in definitions}) != len(definitions):
                raise ValueError("Definition IDs must be unique.")
            rules = []
            for item in value["clarifications"]:
                required = {"when", "question"}
                if (not isinstance(item, dict) or not required <= item.keys()
                        or item.keys() - required - {"unless", "unless_utc_interval"}):
                    raise ValueError("Clarifications require when and question, with optional unless or unless_utc_interval.")
                unless_utc_interval = item.get("unless_utc_interval", False)
                if not isinstance(unless_utc_interval, bool):
                    raise ValueError("unless_utc_interval must be a boolean.")
                rule = ClarificationRule(
                    _string(item["when"], 512), _string(item["question"], 1024),
                    _string(item["unless"], 512) if "unless" in item else "",
                    unless_utc_interval=unless_utc_interval,
                )
                re.compile(rule.when, re.IGNORECASE)
                re.compile(rule.unless, re.IGNORECASE)
                rules.append(rule)
            return cls(identity, version, tuple(definitions), tuple(rules))
        except (OSError, ValueError, RecursionError, re.error) as error:
            raise ProfileError(f"Cannot read business profile {path}. {error}") from error


def _string(value: object, max_bytes: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > max_bytes:
        raise ValueError("Expected nonempty text within the profile field limit.")
    return value
