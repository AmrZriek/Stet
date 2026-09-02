"""Unified Correction Engine Contracts & Protocols (Phase 1c).

Defines TaskSpec, BudgetPolicy, GuardSet, CorrectionRequest, and CorrectionResult
for the single engine entry point: CorrectionEngine.run().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Protocol

from stet.core.input import TargetToken


class TaskType(str, Enum):
    CORRECT = "correct"
    REWRITE = "rewrite"
    SAVED_ACTION = "saved_action"


@dataclass(frozen=True)
class GuardSet:
    """Configured safety guards and validation thresholds for a correction run."""

    preserve_code_blocks: bool = True
    preserve_reference_markers: bool = True
    preserve_markdown_formatting: bool = True
    max_divergence_ratio: float = 0.50
    hallucination_guard: bool = True
    output_unrelated_guard: bool = True


@dataclass(frozen=True)
class BudgetPolicy:
    """Closed token budget arithmetic policy for slot-scoped context allocation."""

    context_size: int = 4096
    safety_margin: int = 256
    growth_multiplier: float = 1.25
    min_generation_tokens: int = 64
    max_generation_tokens: int = 2048


@dataclass(frozen=True)
class TaskSpec:
    """Complete, self-contained specification for a correction or rewrite task."""

    task_type: TaskType = TaskType.CORRECT
    user_instruction: str = ""
    mode_index: int = 0
    newline_policy: str = "target_default"
    budget_policy: BudgetPolicy = field(default_factory=BudgetPolicy)
    guard_set: GuardSet = field(default_factory=GuardSet)
    stream_output: bool = True


@dataclass(frozen=True)
class CorrectionRequest:
    """Request envelope passed to CorrectionEngine.run()."""

    text: str
    task_spec: TaskSpec = field(default_factory=TaskSpec)
    target_token: Optional[TargetToken] = None
    request_id: str = ""


@dataclass(frozen=True)
class CorrectionResult:
    """Unified result emitted by CorrectionEngine.run()."""

    text: str
    changed: bool
    status: str = "success"  # success | generation_truncated | aborted_guard | error
    finish_reason: str = "stop"  # stop | length | abort
    token_usage: Dict[str, int] = field(default_factory=dict)
    message: str = ""
    retry_count: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "success" and self.finish_reason == "stop"


class CorrectionEngine(Protocol):
    """Single entry-point interface for the rewrite engine."""

    def run(self, request: CorrectionRequest) -> CorrectionResult:
        """Execute a correction or rewrite task end-to-end."""
        ...
