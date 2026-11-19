"""Versioned business definitions and clarification rules selected by the caller."""

import re
from dataclasses import dataclass
from pathlib import Path

from ._json import decode_json
from .connector import QueryBridgeError


class ProfileError(QueryBridgeError):
    code = "profile_error"


@dataclass(frozen=True)
class BusinessDefinition:
    id: str
    text: str


@dataclass(frozen=True)
class ClarificationRule:
    when: str
    question: str
    unless: str = ""

    def matches(self, question: str) -> bool:
        return bool(re.search(self.when, question, re.IGNORECASE)) and (
            not self.unless or not re.search(self.unless, question, re.IGNORECASE)
        )


@dataclass(frozen=True)
class BusinessProfile:
    id: str = "generic"
    version: str = "1"
    definitions: tuple[BusinessDefinition, ...] = ()
    clarifications: tuple[ClarificationRule, ...] = ()

    def clarify(self, question: str) -> str | None:
        return next((rule.question for rule in self.clarifications if rule.matches(question)), None)

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
                if not isinstance(item, dict) or item.keys() not in ({"when", "question"}, {"when", "unless", "question"}):
                    raise ValueError("Clarifications require when, question, and optionally unless.")
                rule = ClarificationRule(
                    _string(item["when"], 512), _string(item["question"], 1024),
                    _string(item["unless"], 512) if "unless" in item else "",
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
