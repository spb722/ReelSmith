"""The run's budget ledger: per-attempt reservations (Story 4.3, FR7, AD-7).

Every shot attempt reserves its worst case before its query starts: the Claude
cap (`CLAUDE_SHOT_ATTEMPT_CAP_USD`) plus the attempt's paid-call costs. What
it really costs is debited as reported, and whatever it didn't use goes back
when the reservation is released.

    unreserved = MAX_BUDGET_USD - budget_spent_usd - unspent part of open reservations

A debit beyond its reservation (one Claude turn past the cap) is taken from
the unreserved pool. A pool driven below zero is a systemic `budget_exhausted`.

Spend lives in the run manifest; the only state the ledger adds is its open
reservations, and `run_ledger` keeps one ledger per run on the manifest
object. Every change runs under `STATE_LOCK`, the same lock as the
manifest saves and production-asset upserts (AD-10).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestrator.state.run_manifest import (
    RUN_STATE_DIR,
    STATE_LOCK,
    RunManifest,
    save_run_manifest,
)

# Float sums must not refuse a reservation the budget exactly covers.
_EPSILON = 1e-9


# eq=False: two reservations with the same numbers are still two holds, and
# `release` must remove this one, not the first equal one.
@dataclass(eq=False)
class Reservation:
    label: str
    claude_cap_usd: float
    external_usd: float
    debited_usd: float = 0.0
    open: bool = True

    @property
    def amount_usd(self) -> float:
        return self.claude_cap_usd + self.external_usd

    @property
    def unspent_usd(self) -> float:
        return max(0.0, self.amount_usd - self.debited_usd)

    @property
    def paid_tool_allowance_usd(self) -> float:
        """What the attempt's paid-tool guard may allow.

        The unspent reservation less the Claude cap, which the query's own
        `max_budget_usd` holds. Handing the guard the whole reservation would
        let paid calls spend the Claude share while Claude spends it too.
        """

        return max(0.0, self.unspent_usd - self.claude_cap_usd)


class BudgetLedger:
    def __init__(self, *, max_budget_usd: float, manifest: RunManifest, state_dir: Path = RUN_STATE_DIR):
        self.max_budget_usd = max_budget_usd
        self.manifest = manifest
        self.state_dir = state_dir
        self._open: list[Reservation] = []

    def unreserved_usd(self) -> float:
        with STATE_LOCK:
            held = sum(r.unspent_usd for r in self._open)
            return self.max_budget_usd - self.manifest.budget_spent_usd - held

    def reserve(self, *, label: str, claude_cap_usd: float, external_usd: float) -> Reservation | None:
        """Hold one attempt's worst case, or return None when the pool can't cover it."""

        reservation = Reservation(label=label, claude_cap_usd=claude_cap_usd, external_usd=external_usd)
        with STATE_LOCK:
            if reservation.amount_usd > self.unreserved_usd() + _EPSILON:
                return None
            self._open.append(reservation)
        return reservation

    def debit(self, reservation: Reservation, amount_usd: float) -> None:
        """Charge a real cost against the attempt; any excess comes from the pool."""

        with STATE_LOCK:
            reservation.debited_usd += amount_usd
            self.manifest.budget_spent_usd += amount_usd
            save_run_manifest(self.manifest, state_dir=self.state_dir)

    def release(self, reservation: Reservation) -> None:
        """Give back whatever the attempt didn't spend. Safe to call twice."""

        with STATE_LOCK:
            if reservation.open:
                reservation.open = False
                self._open.remove(reservation)

    def holding(self, *, besides: Reservation | None = None) -> bool:
        """Whether any open reservation (other than `besides`) still holds money."""

        with STATE_LOCK:
            return any(r is not besides and r.unspent_usd > _EPSILON for r in self._open)

    def overdrawn(self, *, besides: Reservation | None = None) -> bool:
        """Whether the pool is below zero, not counting what `besides` still
        holds: an attempt judging its own overshoot is about to give that back."""

        with STATE_LOCK:
            own = besides.unspent_usd if besides is not None and besides.open else 0.0
            return self.unreserved_usd() + own < -_EPSILON


def run_ledger(manifest: RunManifest, *, max_budget_usd: float) -> BudgetLedger:
    """The one ledger for this run, kept on the run's manifest object.

    Planning and both shot stages get the same instance, so each sees every
    reservation the others hold. It is never serialised: open reservations
    only live as long as their attempt.
    """

    with STATE_LOCK:
        ledger = getattr(manifest, "_budget_ledger", None)
        if ledger is None or ledger.max_budget_usd != max_budget_usd:
            ledger = BudgetLedger(max_budget_usd=max_budget_usd, manifest=manifest)
            manifest._budget_ledger = ledger
        return ledger
