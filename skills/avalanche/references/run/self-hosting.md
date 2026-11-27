# Self-hosting the operator

Run `ava operator` as a long-lived service on your own machine or server: one process, one
SQLite file, no broker and no other service. It runs the same workflows as `ava dev`.

## When to use it

- Use it to keep workflows, schedules, webhooks and state machines running on a machine you
  control.
- For the edit-and-run loop, use `ava dev`: [local.md](local.md).
- For a managed runtime where each step runs in its own sandbox and in-flight runs survive
  restarts, use Avalanche Cloud: [cloud.md](cloud.md).

## Interface

```bash
PYTHONUNBUFFERED=1 uv run ava operator [FLOW ...] \
  --host 127.0.0.1 --port 7433 --web-port 7435 --webhook-port 7434 --login-port 7436 \
  --state-db /var/lib/avalanche/state.db --log-level INFO
```

| Option | Default | Meaning |
| --- | --- | --- |
| `FLOW ...` | `[tool.avalanche].flow_targets` | Workflow files or directories. Without them, the nearest `pyproject.toml` is read from the current directory upward. |
| `--host` | `127.0.0.1` | gRPC listen host. Any other host logs a warning: the operator has no authentication. |
| `--port` | `7433` | gRPC port, used by `ava run`, `ava result`, `ava tui`, `ava web`. |
| `--web-port` | `7435` | Web UI and REST API. Always bound to `127.0.0.1`, whatever `--host` is. |
| `--no-web` | off | Serve gRPC only. |
| `--webhook-port` | `7434` | Webhook HTTP listener, on `127.0.0.1`. Started only when a workflow declares a webhook. |
| `--login-port` | `7436` | Loopback port a browser login for an integration returns to. |
| `--state-db` | `.avalanche/operator/state.db` | SQLite state file, relative to the current directory. `:memory:` keeps nothing. |
| `--schedules-enabled` | off | Turn every cron schedule on for this start only. |
| `--discovery-timeout` | `60` | Seconds allowed for one scan of the workflow files. |
| `--log-level` | `WARNING` | `DEBUG`, `INFO`, `WARNING`, `ERROR`; logs go to the terminal. |

The operator reads no configuration from environment variables. Steps inherit the
operator's environment, so model keys and other secrets are set in the environment of the
`ava operator` process.

`ava web --connect HOST:PORT [--host H] [--port P] [--trusted-proxy]` serves the UI and REST
API as a separate process, for an operator started with `--no-web` or on another machine.

## What persists across a restart

State is the SQLite file and the folders next to it (`results/`, `step-outputs/`,
`input-files/`, `schedule-inputs/`, `workspaces/`). See the tree in [local.md](local.md).

| Kept | Not kept |
| --- | --- |
| Finished runs: status, steps, inputs and output previews, logs, agent traces, evaluations, results and result files | A flow run that was running or waiting (on a sleep, an event, a decision) when the operator stopped. At the next start it is marked `failed`: `Operator restarted before the run finished; it was not resumed`. |
| Saved step outputs, so a failed run can be rerun from a step | Evaluations that were still running |
| State machine runs, including waiting ones; their timers keep counting | Workflow definitions: they are rediscovered from the files at every start |
| Flow runs parked on a child run they called | |
| Each schedule's on/off choice and its input | |
| Accepted events, decisions and their receipts | |
| Accounts connected with `ava connect` | |

Verified: after a restart, a flow waiting on `ava.sleep` showed `failed`; a machine waiting
for an event stayed `waiting`, accepted the event, and its 2-second state timer fired.

To continue a flow run a restart interrupted, rerun it from the step that did not finish:
`ava run FLOW --rerun-of RUN_ID --from SLUG` ([local.md](local.md#rerun)). A flow with `key=`
cannot be rerun: start it again under a new key value
([retries.md](../orchestration/retries.md#run-keys)).

## Access control

- The operator has no built-in authentication, no users and no TLS. Anyone who can reach
  the gRPC port or the web port can start runs, cancel them and read their data.
- Defaults bind everything to loopback. `--host 0.0.0.0` exposes gRPC only; the web UI,
  REST API and webhook listener stay on `127.0.0.1`.
- To reach the UI from another machine, put an authenticating reverse proxy or a tunnel in
  front of the web port. `ava web --host <non-loopback>` refuses to start without
  `--trusted-proxy`, which only records that you did so.
- Webhooks are served on loopback only. To receive webhooks from the internet, forward a
  public URL to `http://127.0.0.1:7434/webhooks/...` yourself. See
  [triggers.md](../connect/triggers.md).
- `ava tui --token`, `--tls`, `--tls-ca-cert` (and `AVALANCHE_TUI_GRPC_TOKEN`,
  `AVALANCHE_TUI_GRPC_TLS`, `AVALANCHE_TUI_GRPC_TLS_CA_CERT`) configure the TUI client for an
  endpoint that terminates TLS and checks a bearer token in front of the operator. The
  operator itself checks neither.

## Storage, backup and cleanup

- Put `--state-db` on a local disk. The file is opened in WAL mode with an exclusive lock;
  a filesystem that cannot do that fails with `could not use WAL journaling ... Put it on a
  local filesystem.`
- One process per database. A second operator, or any `ava state` command while the operator
  runs, fails with `... is already in use by another process`.
- There is no backup command. Stop the operator, then copy the directory that holds the
  database (the `.db` file, its `-wal` file if present, and the sibling folders) as one unit.
- Nothing is deleted automatically. With the operator stopped:
  `ava state info`, `ava state prune --keep-last N` or `--before 2026-11-01`,
  `ava state delete RUN_ID ...`, `ava state clear --yes [--include-schedules]`.
- `prune` deletes finished runs only. Disk space returns to the filesystem when at least
  25% of the database is free, or after `clear`.
- The schema is migrated when the database is opened by a newer version.

## Limits

Defaults in the operator code; none has a CLI flag.

| Limit | Value |
| --- | --- |
| One gRPC message | 48 MiB |
| REST request body | 4 MiB, `application/json` with `Content-Length` |
| Webhook request body | 1 MiB |
| One input file staged by the operator (UI upload) | 32 MiB |
| Logs per run | 16 MiB and 100,000 entries |
| One agent trace | 32 MiB |
| One agent event | 8 MiB |
| Details per step / per run (logs, events, traces) | 64 MiB / 128 MiB |
| Result written by `ava result --output-dir` | 2,048 files and workspaces |
| Run ID | 256 bytes |

All steps of all runs execute on the operator's machine, in processes it starts. There is
no worker pool on other machines and no per-step sandbox.

## Example

A systemd unit for a project in `/srv/orders`, with keys in an environment file. Adapt the
paths; the flags are the ones in the table above.

```ini
[Unit]
Description=Avalanche operator
After=network.target

[Service]
WorkingDirectory=/srv/orders
EnvironmentFile=/etc/avalanche/orders.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/env uv run ava operator --state-db /var/lib/avalanche/orders/state.db --log-level INFO
KillSignal=SIGINT
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Check it from the same machine:

```bash
uv run ava flow list --connect 127.0.0.1:7433
uv run ava run list --connect 127.0.0.1:7433
curl -s http://127.0.0.1:7435/api/v1/flows
```

## Rules

- Design flows for restarts: a flow run does not survive one. Put long waits in a state
  machine ([state-machines.md](../building-blocks/state-machines.md)), or accept a rerun.
- Keep steps safe to run twice; a rerun executes the interrupted step again
  ([retries.md](../orchestration/retries.md)).
- Schedules start disabled. Turn each one on once with `ava schedule enable SELECTOR
  [--input JSON]`; the choice is stored. `--schedules-enabled` applies to one start only.
- Stop with `SIGINT` or `SIGTERM`: the operator shuts down and releases the database lock.
  After `kill -9` the OS releases the lock, and the next start stops leftover run processes
  and marks their runs failed.
- If any workflow file fails to import at startup, the operator exits with code 1 and prints
  the file and the exception. Fix the file; a broken file is not skipped. The exception:
  the file of a machine that has unfinished instances does not block startup.
- Editing a workflow file while the operator runs reloads it; there is no flag to turn the
  watcher off. Deploy new code by replacing the files, or by restarting.
- Never expose port 7433 or 7435 to a network you do not trust.
- Do not point two projects at the same `--state-db`.

## In the UI

The web UI served on `--web-port` is the same as under `ava dev`. It has no login page.

## See also

- [local.md](local.md): commands, state layout, reading runs, reruns.
- [cloud.md](cloud.md): the managed alternative.
- [../connect/triggers.md](../connect/triggers.md): schedules and webhooks.
- [../reference/ava-cli.md](../reference/ava-cli.md), [../reference/operator-api.md](../reference/operator-api.md).
