"""Steps that skip themselves, and what the steps after them receive.

Run it through the local operator:

    uv run ava dev examples/skipped_steps.py
    uv run ava run skipped_steps --input '{"name": "Acme", "employees": 3}'
"""

import avalanche as ava


class Applicant(ava.BaseInput):
    name: str
    employees: int


@ava.step
def qualify(applicant: Applicant) -> bool:
    return applicant.employees >= 10


@ava.step
def score_team(applicant: Applicant, qualified: bool) -> int:
    if not qualified:
        ava.skip(f"{applicant.name} has fewer than 10 employees")
    return min(applicant.employees, 100)


@ava.step
def score_market(applicant: Applicant, qualified: bool) -> int:
    if not qualified:
        ava.skip(f"{applicant.name} has fewer than 10 employees")
    return 50


@ava.step
def total(team: int, market: int) -> int:
    """Not nullable: skipped in turn when either score was skipped."""
    return team + market


@ava.step
def decide(applicant: Applicant, score: int | None) -> str:
    """Nullable: runs with None when the scores were skipped."""
    if score is None:
        return f"{applicant.name}: pass (not qualified)"
    return f"{applicant.name}: {'advance' if score >= 100 else 'pass'} ({score})"


@ava.step
def tag_prefix(qualified: bool) -> str:
    if not qualified:
        ava.skip("Unqualified applicants get no tag prefix")
    return "lead:"


@ava.step
def per_tag(tag: str, prefix: str | None) -> str:
    """A broadcast input from a skipped step is None here; the items still run."""
    if tag.startswith("_"):
        ava.skip(f"private tag {tag}")
    return f"{prefix or ''}{tag.upper()}"


@ava.step
def tags() -> list[str]:
    return ["ai", "_internal", "saas"]


@ava.step
def labels(values: list[str]) -> str:
    """A map's skipped items are left out of the collected list."""
    return ", ".join(values)


@ava.workflow(input=Applicant)
def skipped_steps():
    applicant = ava.input
    qualified = qualify(applicant)
    score = total(score_team(applicant, qualified), score_market(applicant, qualified))
    prefixed = per_tag.map(tags(), prefix=tag_prefix(qualified))
    return decide(applicant, score), labels(prefixed)
