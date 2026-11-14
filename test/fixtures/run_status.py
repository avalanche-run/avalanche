"""Run-summary E2E fixtures. Error text must remain only in node detail."""

import time
from datetime import timedelta

import avalanche as ava
from avalanche.policies import Event


class Approval(Event):
    approved: bool


class Payment(Event):
    paid: bool


@ava.step(retries=0)
def finish(approval: Approval, payment: Payment) -> str:
    time.sleep(0.3)  # Make the no-open-waits/running row observable to real clients.
    return "finished"


def _waits_graph(cooldown_seconds: float):
    approve = ava.decision(Approval, prompt="Approve", timeout=None, slug="approve")
    paid = ava.wait_for_event(Payment, key="paid", timeout=None, slug="paid")
    cooldown = ava.sleep(timedelta(seconds=cooldown_seconds), slug="cooldown")
    return (approve & paid & cooldown) >> finish(approve, paid)


@ava.workflow
def waits3():
    return _waits_graph(3600)


@ava.workflow
def waits3_short():
    # ControlWait intentionally has no clock-advance operation. The operator proof
    # also exercises expiry with a bounded real timer, not a mocked coordinator.
    return _waits_graph(8)


@ava.step(retries=0)
def charge_card() -> None:
    raise ValueError("card sk-test-4242 for jane@acme.test declined")


@ava.workflow
def boom():
    return charge_card()
