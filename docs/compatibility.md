# Delta compatibility protocol

`avalanche.PROTOCOL_VERSION` (`major.minor`) is one version for everything Delta
relies on from Avalanche. Delta records it at deploy; a worker runs a deployment
iff the major matches and the worker's minor is not older
(`avalanche.protocol.supports`). Baseline: **1.0 at Avalanche 0.15.x**.

- **Minor:** additive. Every deployment built on an older minor of the same
  major still runs.
- **Major:** an already-built deployment can no longer run. A Pydantic major
  bump is always a protocol major.
- **No bump:** UI/TUI-only changes, and internal changes Delta cannot observe.
  Package versions are independent: a package bump never implies a protocol bump.

## Covered

The worker-side surface Delta uses, private names included: the `avalanche.dag`
binding hooks called by Delta's flow executor, `resolve_step_signature`,
`BaseContext`/`BaseInput`, reference rebinding, `File`/`Workspace` JSON shapes,
agent steps and evidence, operator discovery and its cache, `LocalExecutor`, and
the operator proto.

PredictRLM has no separate number. Delta reaches it only through Avalanche
(`avalanche.agent.DirectPythonBackend`, agent evidence, `Workspace`/`File`), and
Avalanche keeps its `predict-rlm` range to one minor. A PredictRLM change Delta
would notice needs an Avalanche protocol bump.

## Protocol 2.1: runtime maps

Runtime-map metadata was introduced in protocol **2.1**, extending 2.0. The
combined retries-and-waits and step-map SDK uses **3.0** for every workflow,
including mapped children: `workflow.required_protocol` follows
`avalanche.PROTOCOL_VERSION`. Protocol 2.x deployments must be redeployed; there
is no compatibility adapter. Package versions are not changed by this PR.

The additive operator fields are `WorkflowTopologyV2.map_specs` (`MapSpecV2` with
mapped/broadcast argument names, effective maximum, inheritance and `fail_fast`),
`RunSnapshotV2.expansions`, and `NodeSnapshotV2.map_parent` / `map_index`.
`RunStatusEnvelopeV2.nodes_expanded` publishes run-scoped topology and actual
expected counts before any new child event. A chained group's not-yet-created
items have no node records. Traces and evaluations retain their existing schema.
Older clients may ignore the additive fields, but need the expansion event to
render live map children. Delta's map scheduler integration is not included here.

## Protocol 3.1: classifier providers

Classifier steps and evaluations name their provider: `classifier=` beside
`model=` and `timeout=`, the `avalanche.classifiers` namespace, and a
`runtime.classifier` value in classifier and evaluation declarations. Protocol
**3.1** extends 3.0. A deployment built on 3.0 runs unchanged on a 3.1 worker,
and its declarations, which name no classifier, read as TypeSafe. A deployment
built on 3.1 needs a 3.1 worker, because the worker's Avalanche must know the
argument and the namespace.

## Protocol 3.2: skipped steps

A step can end as skipped with `ava.skip(reason)`. Protocol **3.2** extends 3.1.
A deployment built on 3.1 runs unchanged on a 3.2 worker. A deployment built on
3.2 needs a 3.2 worker: an older worker's Avalanche fails `ava.skip` as an
ordinary error. On Delta, a skipped step's report carries `skip_reason`, the
scheduler records the step as skipped (never retried, never a run failure, its
workspace writes kept as for a success), and a step reading its output binds it
with the same rule as the local operator (`avalanche.skips.bind_skipped_inputs`).

## Avalanche 0.18.0

Ray execution is removed: the `ray` extra, `RayExecutor` export, `--ray`
operator/dev flags, and the operator's `executor_factory`, `executor_backend`,
`ray_runtime_env`, and `ray_init_kwargs` arguments no longer exist. Use the
unchanged `LocalExecutor`; it is now always the default in-repository executor.
Delta's separate remote runtime is unchanged. **No protocol bump:** Delta does
not use the removed backend or its configuration.

## Retries-and-waits: retries and graph waits

Protocol **3.0** replaces the previous 2.x worker contract: discovery carries
per-invocation retry/uncertainty policies, worker exceptions carry terminal and
unknown-outcome flags, and `RunContext.step_key` uses the deployed invocation
identity. This requires redeployment, not a compatibility adapter. Delta's
scheduler enforcement and deployed waits require the run-manifest scheduler
follow-up; retries-and-waits lands after run-manifest. Local Avalanche supports
the full local contract. No release is made by this preparation.

## Release rule

A major change needs a `BREAKING-PROTOCOL` line in `CHANGELOG.md` explaining the
break. The release workflow checks this against the previous tag.
