# @avalanche-run/operator-ui

Embeddable React UI for inspecting and controlling Avalanche workflows. The package provides the
workflow graph, run list, run controls, logs, node details, and agent details; the embedding host
owns navigation, the operator connection, and presentation.

This source package shares Avalanche's version and release tag. It is no longer
published to GitHub Packages; 0.20.15 is the last published version.

## Consume from source

Add `web/operator` to the embedding host's pnpm workspace and declare
`"@avalanche-run/operator-ui": "workspace:*"` with React and React DOM peers.
Install with `pnpm install --frozen-lockfile`, then build the UI before
the host:

```sh
pnpm --filter @avalanche-run/operator-ui run build
```

The package exports its built `dist` files. Delta's console build performs this
UI build automatically; Avalanche's local host uses its web workspace. No npm
registry scope or token is needed. The release still packs a pinned-builder
archive as checksummed staging evidence, not for registry publication.

## Embed

Import the package stylesheet once, then render `OperatorUi` with a typed host:

```tsx
import "@avalanche-run/operator-ui/styles.css";
import { OperatorUi } from "@avalanche-run/operator-ui";
import type { OperatorUiHost } from "@avalanche-run/operator-ui";

const host: OperatorUiHost = {
  api: operatorApi,
  presentation: {
    rootLabel: "Example project",
    brandImageUrl: "/brand.svg",
    unavailableDescription: "The operator is unavailable.",
    workflowReloadDescription: "Workflow changes are being loaded.",
  },
};

export function WorkflowPage() {
  return <OperatorUi host={host} />;
}
```

Use `GrpcWebOperatorApi` when the host has a compatible gRPC-Web endpoint, or implement the
`OperatorApi` interface for another transport. `WorkflowWorkspace` is also available when the
host supplies its own surrounding navigation and layout.

A custom `OperatorApi` must implement
`setScheduleEnabled(workflowSelector, enabled, input?)`, which returns the updated
`FlowInfoMsg`. The run controls call it from the **Run on schedule** switch and the
**Use for schedule** action; `input` is sent only when enabling and replaces the input
the schedule's runs start with. `GrpcWebOperatorApi` implements it with the operator's
`SetScheduleEnabled` call.

File input fields (`ava.File` and `list[ava.File]`) use the optional
`stageInputFile(file, { workflow, field })` method. The input panel calls it as soon as
a file is picked; it stores the file where the host's operator can read it and returns
a `StagedInputFile`: `name`, `mediaType`, `sizeBytes`, `sha256`, and either
`objectUri`/`objectKey` or `inlineBytes`, matching the operator's `FileAttachmentV2`.
Run and the schedule then pass the staged files to `startRun(workflowSelector, input?,
inputFiles?)` and `setScheduleEnabled(workflowSelector, enabled, input?, inputFiles?)`
as `InputFileAttachment`s, each with its `fieldName`. The operator places each file by
its field's declared type: a list field gets its files in order, after any items listed
in `input`; a single file field is left out of `input`. A host without `stageInputFile` shows file fields as not editable.
`GrpcWebOperatorApi` uploads to the operator's `StageInputFile` call, which keeps the
file in the operator's own staging folder.

The UI bundles the same Nacelle Regular and SemiBold fonts as Delta Console;
code and filename references remain monospace.

`OperatorUi` uses a compact workflow sidebar with padded, square-cornered rows,
8px gaps between workflows, and straight tree connectors reaching the scan-target
icon. Selected labels use Delta's brand blue and retain it on hover. A catalog
with exactly one scan target groups every workflow directly under that target's
file or directory name, with the full path available on hover.
With zero or multiple targets, workflows remain in a
flat list rather than implying an unavailable per-target mapping. Source filename
references remain visible, and long names truncate within the sidebar. The target
is a label, not a project view. The sidebar has no dashboard, profile card, or
Navigator/Explorer heading; its pin/hover controls and reload diagnostics remain.
When the workflows declare integrations, an **Integrations** entry sits above the
pin control (see [Integrations](#integrations)).

Both hosts share one workspace with an optional selected run. With no run selected,
nodes expose their available definitions, without execution badges or logs. Selecting
a run uses its immutable topology, status, durations, and collapsible logs. Selecting
the newest run enables following newly created runs; selecting an older run pins it.
**Current** shows the workflow definition until a new run arrives, then selects
that run and follows subsequent arrivals, including runs discovered on reconnect.
Existing history is not selected when opening Current. The current
graph and inspector stay visible while another run snapshot loads. The inspected
node stays selected when its ID exists in the destination topology.
Loaded historical runs remain available if their current workflow definition is
removed from the catalog. Completing a run-start request selects the new run only
if workflow/run navigation has not changed in the meantime.
While a run is **Requesting**, its nodes remain visible but dimmed and unavailable
for inspection, alongside a loading indicator. Until the run's own topology arrives,
the graph previews the current workflow definition; it does not represent execution
state. The preview stays visible through the prepared-snapshot refresh, then switches
to the run's topology and enables inspection.
The floating timeline pins **Current definition** above a virtualized run list.
It reveals loaded history in 25-run batches near the bottom, with no twenty-run cap.
The header's icon-only **Expand timeline** action opens expanded history. Selection
highlights Current definition or the selected run; the Current row has no trailing caret.
Both floating and expanded timelines use a trackless scroll handle that stays faintly
visible whenever the list can scroll, and reaches full opacity while hovering over
the timeline or dragging the handle. Rows extend beneath it with text padded clear.

The inspector has separate current-state and run modes. Its header identifies
`Agent · Current state` or `Step · Current state` in workflow view, and includes
the run ID in run view. Current agent definitions have no tabs: they show
instructions, input/output schemas, configured models, skills, tools, and runtime
configuration. Consistent section headings, underline separators, and spacing
establish hierarchy without boxed groups; inputs/outputs and model roles use quieter
subheadings. Current regular steps show source code.

Every inspector ends with a **Step interface** tab. Current regular steps open on
**Code**; run views never show code, which reflects the current deployment, and
instead offer **View current definition** to open the same node's current
definition. Regular steps in run view open on **Run I/O**, showing bounded previews
of the arguments the step received and the value it returned.

Run agent inspectors expose **Trace**, **Run I/O**, **Evaluations**, and **Step
interface** tabs. Trace occupies the
full panel with turn content. Run I/O pairs each historical field definition
directly with its retained invocation value; missing schemas and values remain
explicit instead of falling back to current metadata. Run headers retain execution
status, duration, model usage, and iteration details.
Run I/O uses the same section headings and separators as the current definition,
with unboxed fields and subtly shaded, padded retained-value explorers. Above the
turns, the Trace tab's **View initial prompt** opens a dialog with everything the
model was given before its first turn (system prompt, messages, tools and output
schema) from the step's `prompt.sent` event; it loads only when opened.

Open trace details take priority over background summary loading within the
eight-entry, 8 MiB detail cache. If open turns alone exceed that budget, an evicted
section offers **Reload step detail** without discarding its reasoning summary.
Failed requests expose explicit retry actions; oversized details retain their
summary but cannot be loaded beyond the browser limit.

Classifier calls use a compact table with Call, Outputs, Status, and Duration
columns, 25-row pages, and Previous/Next controls matching the expanded timeline.
Invocation numbers and typed compact answers come from paged and live activity
descriptors, without input previews or full-body prefetching.
Calls start at index 1 in ascending order, with every row collapsed. Only explicitly
expanded calls fetch full details. Their eight-entry, 8 MiB cache holds full bodies only.
Evicting a body leaves the list summary intact, and reopening its call reloads
details automatically. Classifier descriptors require `classifier_summary`,
including when the corresponding detail body is unavailable.

Run snapshots retain the selected node while its ID exists in the destination
topology. The canvas notice reads **Viewing a run snapshot** and states that the
view does not represent the workflow's current state.

Skill titles open a dedicated, wider Markdown popup that renders the complete
document with scrolling, without a **Show more** control. Non-empty **Packages** and
**Modules** sections follow the instructions, displaying declared dependency strings
and module names. Closing it returns focus to the selected skill without expanding
the sidebar. Tools expand inline to show
their full Python source, including docstrings, in a read-only code viewer that
fits short functions and scrolls internally once it reaches its height limit.
Tools without inspectable source show an explicit unavailable message.
Runtime configuration remains collapsible.

Authored Markdown in instructions and expanded resources has scoped heading, list,
paragraph, link, quote, and code typography. These styles do not affect inspector
controls or host content. Active HTML and remote images remain excluded.

Full instructions, current field schemas, configured models, resources, and code
come from the available workflow definition, not the historical run. Hosts can set
`definitionLabel` on `WorkflowWorkspace`, or on `OperatorUi`'s presentation, to
identify missing-definition messages.

For route-controlled embedding, pass
`navigation={{ selectedRunId, onSelectRun }}` to `WorkflowWorkspace`.
`onSelectRun(undefined)` clears the run; changing routes within a workflow should
retain the same component instance. Without `navigation`, selection is managed
locally and `onSelectedRunChange` can observe it. `onSelectRun(runId, nodeId)` also names
a step when a link points at one, such as a reused step's source. A host that remounts the
workspace per run passes that step back as `initialNodeId` to open it on mount.

To offer **Rerun from here**, implement the optional `startRerun(request)` on `OperatorApi`.
It resolves to the new run's ID and rejects with `RerunRefusedError(problems, earliestStart)`
when the rerun is refused, so the dialog can list every problem and the earliest step. A
host without it offers no rerun. `request.requestId` stays the same across retries of one
attempt, so use it to make a retry return the first run. Steps are offered from
`FlowInfoMsg.nodeSlugs`. A `deployments` prop on the workspace adds a deployment picker to
the dialog. `GrpcWebOperatorApi` implements `startRerun` for the local operator.

Pass `bottomRightPanel` to `WorkflowWorkspace` to replace the graph's floating
actions with host controls. Omit it to retain built-in controls, or pass `null`
to hide the panel. The panel stays inside the graph when the inspector opens.

### Optional host timeline props

`WorkflowWorkspace` accepts these flat, additive optional props. With all omitted,
Avalanche shows **Current definition**, no trailing action or tabs, and its usual
run history. Hosts supply data and callbacks; operator-ui owns the rendered UI.
`WorkspaceDeployment` and `WorkspaceDeployments` are exported package types.

| Prop                                                             | Default / behavior                                                                                                                               |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `layoutStorageKey?: string`                                      | `"avalanche-operator-ui.layout"`. Namespaces the single JSON panel-layout record for a host.                                                     |
| `currentLabel?: string`                                          | `"Current definition"` (nullish fallback). Delta uses `"Current deployment"`.                                                                    |
| `currentRowAction?: { label: string; onClick: () => void }`      | No action or caret. When supplied, lowercase link text, underline on the text itself (removed on hover), normal weight; does not select Current. |
| `showCurrentRow?: boolean`                                       | `true`: pinned current row on the Overview tab. `false`: no pinned row on any tab; the list starts under the tabs.                               |
| `deployments?: WorkspaceDeployments`                             | Absent: **Timeline** title, no tabs. Present: **Overview \| Deployments** tabs in both compact and expanded headers.                             |
| `timelineView?: "runs" \| "deployments"`                         | Omitted: internal state starts at `"runs"`. Supplied: controlled by the host. Without `deployments`, only runs show.                             |
| `onTimelineViewChange?: (view: "runs" \| "deployments") => void` | No callback by default. Called on tab requests in both modes; controlled hosts must update `timelineView`.                                       |

Panel layout is global per browser user, not per workflow, deployment or run:
inspector width, log height and open/closed state, and timeline expansion survive
workspace unmounts, keyed route replacements and page reloads. Both embedded
`WorkflowWorkspace` and stock `ava operator` use the same default storage key.
State is read synchronously on mount (no default-then-restore flash), with panel
sizes clamped to the viewport and existing resize bounds. Writes are debounced
by 150 ms and flushed on unmount. Missing/corrupt JSON, SSR, disabled storage and
quota errors fall back to normal defaults or session-only changes. Hosts can
supply `layoutStorageKey` to isolate their layout from other embeddings.

```tsx
const [timelineView, setTimelineView] = useState<"runs" | "deployments">("runs");

<WorkflowWorkspace
  api={operatorApi}
  workflowId={workflowId}
  currentLabel="Current deployment"
  currentRowAction={{ label: "See all", onClick: () => setTimelineView("deployments") }}
  timelineView={timelineView}
  onTimelineViewChange={setTimelineView}
  deployments={deployments}
/>;
```

The host's `deployments: WorkspaceDeployments` contains:

- `items: WorkspaceDeployment[]`: newest-first, workflow-filtered
  `{ id: string; label: string; createdAt: number; current: boolean }` rows.
  `createdAt` is epoch seconds. `label` is displayed; full `id` is in the title
  and accessible name. Mark at most one item `current` for the
  **current ●** status text (lowercase, green dot after, not a link) in the Status column and `aria-current="true"` on its row.
- `hasMore: boolean`, `loading: boolean`, `loadMore?: () => void`: virtualized
  infinite scrolling requests a page within four row-heights of the bottom.
  It latches each request to avoid duplicate scroll calls until the item count
  changes or the host's loading state changes. The host must set `loading` while
  fetching and clear it after settling. No loader is called while loading,
  after `hasMore` becomes false, or while `error` is set. Omit `loadMore` for a
  fully loaded list.
- `selectedId?: string` (default none) highlights a row.
  `onSelect: (id: string) => void` asks the host to show its definition; it does
  not move the current pointer or select a run.
- `error?: string` (default none) shows an inline error. Loading and empty states
  are rendered by operator-ui, not by the host.
- `setCurrent?: { label?: string; confirmTitle?: string; confirmText: (item: WorkspaceDeployment) => string;
onConfirm: (id: string) => Promise<void> }` (default none) adds a non-current-row
  link-style button, labeled **set current** unless overridden (rendered lowercase),
  styled exactly like `currentRowAction`: underlined at rest, underline removed on hover,
  text only (no icon at rest; a small spinner shows while confirming), no border/background/padding;
  keyboard focus has a visible ring. It opens a page-level modal `alertdialog` portalled to
  `document.body`, outside the timeline. The portal carries the package's CSS scope class;
  light/dark colors follow `.dark` on the host's `html` or `body`.
  `confirmTitle` defaults to **Set current deployment?**; `confirmText(item)` supplies the body,
  followed by the abbreviated deployment ID (full ID on hover). Initial focus is on **Cancel**;
  Tab/Shift+Tab stay inside, Escape or backdrop click cancels and returns focus to the row action.
  **Set current** invokes `onConfirm`. Both dialog buttons are disabled and the primary button
  shows a spinner during the request; Escape/backdrop cannot cancel it. Success closes the modal;
  failure leaves it open with the rejection's `Error.message` in an alert inside the modal.
  Row actions are disabled while the modal is open; the target row shows a spinner and `aria-busy`
  throughout confirmation and the request. Neither the action nor its confirmation selects a
  deployment. The host updates `items[].current` after success.

The pinned Current row and its optional action appear only on **Overview**, not on
**Deployments**. Run status chips and run filters appear only in the expanded
**Overview** view. Changing tabs does not
change run selection or expand/collapse the timeline.

The header shows **Overview | Deployments** tabs when `deployments` is supplied. In stock
mode (no `deployments`) it shows a plain **Timeline** title in the same header style, with
no underline indicator, followed by the pinned **Current definition** row. The selected tab has a 2px
primary-color underline on the header border; inactive tabs are muted. ArrowLeft,
ArrowRight, Home and End focus and request the corresponding tab. Controlled hosts
remain responsible for updating `timelineView`. The visible **Overview** label keeps
its public `timelineView` / `onTimelineViewChange` value `"runs"` unchanged.
Only the Status header and status/action column are right-aligned in both lists;
deployment IDs, created dates and run timestamps stay left-aligned.

The runs panel is titled **Runs** (it was "Timeline"; the run's event timeline
is now the bottom pane's Timeline tab). The floating panel's icon-only **Expand runs** action (also its tooltip and
accessible name) expands it into a wider, full-height timeline panel on the left of
the workspace, independently of node inspection on the right. Above 1000px it
occupies its own column beside the canvas and logs;
below that breakpoint it remains an overlay. It paginates the runs supplied by
the API baseline in 25-run pages, with single-select status count chips and
inclusive local-date filtering. Run-ID search is available
under the initially collapsed **Advanced filters** section. **Collapse runs**
or Escape restores the floating timeline and returns focus to its trigger.
Filters and run selection survive expansion and collapse.
Expansion and collapse animate the timeline's size and position unless reduced
motion is requested.

Pagination covers loaded history only; it does not retrieve records unavailable
through the host's `OperatorApi`.

This package does not run an operator server, create an authentication boundary, or provide
multi-tenant behavior. Those concerns stay with the embedding host.

## Integrations

A workflow step can declare the outside services it uses (`integrations=[...]`).
The package shows them when the host implements these optional `OperatorApi`
methods; a host without them is unchanged and shows no integration UI:

- `listIntegrations(signal?)` returns `IntegrationStatus[]`: each declared
  integration, its declaration, whether it has an account (`connected`,
  `connected_at`), whether a step cannot start without one (`requires_account`),
  the ways to connect it (`login`: `"browser"`, `"api_key"`, the default first)
  and the steps that use it. It must start no process, call no outside service
  and return no credential. The field names are the operator's JSON document;
  `parseIntegrationStatusList` validates one.
- `connectIntegration(name, credential)` stores an account
  (`{ kind: "api_key", key, header? }`) and `disconnectIntegration(name)` removes
  it. Both return the integration's new status. Without them the screen is
  read-only.
- Named-capable hosts set `named_connections` and include `connections` on each
  catalog status: slug, shared/per-user kind, viewer state, instance count and
  assignment chips. The shared panel creates named connections, reconnects the
  same slug/kind, and confirms that disconnect removes the operator assignment.
  `IntegrationLoginClient.slug` and `.kind` carry the connection selection;
  `disconnectIntegration(name, slug?)` removes that connection without overloading
  the integration name. Hosts omitting the capability keep the single-account UI.
  Catalog declarations accept optional `connection` and use its slug as the tool
  prefix; the connection tables never receive tokens or instance credentials.
- `startIntegrationLogin(name, client?)` starts a browser login and returns an
  `IntegrationLogin` with the consent page to open (`authorization_url`);
  `client` carries the client ID and secret a person typed for a Connector's
  app. `getIntegrationLogin(loginId, signal?)` returns how far it is
  (`waiting`, `connected`, or `failed` with `error`) and may wait some seconds.
  The host receives the browser's return itself and stores the tokens: no
  token, code or secret is ever given to the page. Without both methods the
  connect form offers a key only.
- `getIntegrationIconUrl(name, signal?)` returns a URL of the declared icon. It
  is used only as the `src` of an `<img>`, so an SVG icon cannot run script in
  the page. Reject when there is no icon to serve; the UI shows a generic one.
  `GrpcWebOperatorApi` returns a `data:` URL.

Each step's `StepIntegrations` document is supplied per node in
`FlowInfoMsg.integrationDeclarationsJson` (`FlowInfoMsg.create` defaults it to
none). With it, step cards carry one badge per integration, the inspector lists
a step's integrations with their kind and a Connector's tools, a failed step
whose account was missing says so, and traced tool calls name their
integration. Run views read the current definition's declarations.

`OperatorUi` adds an **Integrations** entry to its sidebar when the workflows
declare any, and shows the screen in place of the workspace. A host that
composes its own page renders the exported `IntegrationsPanel`:

```tsx
<IntegrationsPanel api={operatorApi} focusName={name} revision={deploymentId} />
```

and passes `onOpenIntegrations={(name) => ...}` to `WorkflowWorkspace`, so a
step that failed for a missing account links to that page. `focusName` brings
one integration into view with its connect form open; change `revision` to
list again. The lists refresh when the catalog changes, after the page's own
connect or disconnect, and when the update stream carries
`{ oneofKind: "integrationAccountsChanged" }`, which the local operator sends
when an account is connected or removed from anywhere. The connect form sends
the key once and never reads it back; a stored key is never displayed. For a
browser login the form opens the consent page in a new tab, opened while the
click is handled so the browser does not block it, and waits for the host to
report the end of the login.

The real-browser proof runs against a real local operator. From
`pkgs/avalanche`, after `make web-build`:

```bash
uv run --frozen --package avalanche-ai --with playwright==1.55.0 python \
  web/operator/scripts/integrations_browser_proof.py --evidence /path/to/evidence
uv run --frozen --package avalanche-ai --with playwright==1.55.0 python \
  web/operator/scripts/browser_login_browser_proof.py --evidence /path/to/evidence
```

The second one connects a Connector and two MCP servers through the browser
login, against a local login service that speaks OAuth.

## Machine runs (phase A contract)

A machine instance is a run, and its page is the normal run page: same
timeline, graph canvas, side inspector, log pane and run controls. A catalog flow
can supply `machineSpec: MachineSpec`, the parsed discovery `machine_spec_json`
(`avalanche.machine/v1`). Instances are ordinary rows in the run timeline,
labelled by `RunSummaryMsg.label`; hosts derive it from the key with
`runKeyLabel` (`["acme/api", 44]` reads `acme/api #44`). Keyed flow runs
(`@ava.workflow(key=...)`) use the same label, in the same places (timeline row
and breadcrumb). Ordinary workflows are otherwise unchanged.

Machine-only elements extend the nearest run component; there is no machine
page, instance table or extra panel type:

| Machine element                                          | Run component it extends                                                 |
| -------------------------------------------------------- | ------------------------------------------------------------------------ |
| State graph                                              | `GraphCanvas` (`machine` mode): same cards, grid, controls, input dot    |
| Current state                                            | The graph's node card (ring + run status)                                |
| One arc per trigger, loops, timer arcs                   | The graph's edges (cubic arcs, dotted timers with countdown)             |
| State entries                                                   | Entries of the run's Timeline tab (bottom pane, beside Logs)             |
| A state entry's steps                                          | The run graph and side inspector (outputs, attempts, trace) and log pane |
| Send, retry state entry, dev timer                             | Run controls, beside Cancel                                              |
| Send form                                                | The side panel, like the run input form                                  |
| Pending timer, deferred events, open waits, decisions    | The Timeline tab's pinned Pending rows                                   |
| Events, receipts, timers, waits, audits, cancel, outcome | Timeline entries, in time order, each naming its state and arc           |
| Close a waiting run                                      | The normal Cancel run (`cancelRun`)                                      |
| Start                                                    | The normal run input form and Run (`startRun`, files and workspaces)     |
| Send payload fields, File and Workspace included         | The run input form's fields (`InputFieldList`) and upload path           |
| Expired history                                          | The run row and run page read `expired`; the key label stays             |

Clicking a state on the graph filters the Timeline to that state; a state entry
entry's **Steps** action opens that state entry's steps on the graph, and "State
machine" in the Timeline's breadcrumb returns to the state graph. The host
supplies `MachineStateEntry.snapshot`, a run snapshot scoped to that state entry (qualified
node IDs, retained I/O, trace and log tokens); the UI never synthesizes step
detail. The pieces are exported for hosts composing the page (`MachineRunActions`,
`MachineSendPanel`, `RunTimeline` and the timeline builders, the `useMachine*` and
`useRunWaits` hooks and `runKeyLabel`). `MachineRunWaits`, `MachineStateEntryWaits` and
`MachineStateEntryTimeline` were removed with the side-pane sections they drew.

All machine methods on `OperatorApi` are optional:

- Reads: `getMachineInstance(runId, signal?)`, `getMachineHistory(runId, page?, signal?)`,
  `getMachineStateEntry(runId, entry, signal?)`, and
  `getMachineStateEntryEvents(runId, entry, page?, signal?)`.
- Machine-only writes: `sendMachineEvent(runId, event, payload, idempotencyKey, inputFiles?)`,
  `retryMachineStateEntry(runId)` (interrupted state entries) and `fireNextMachineTimer(runId)`
  (development hosts only).
- `listRunWaits(runId, entry?)` takes an optional state entry, filtered by the host.

There is no machine start, list, close or rerun method, and the run page has no
rerun control for machine runs: retrying an interrupted state entry makes a new state entry
in the same run, and a replay is a new key. Cancel is the normal run cancel. The exported types in
`src/machine.ts` follow the public machine JSON field names (`run_id`,
`next_cursor`, `entered_at`, etc.). A deployment-based host supplies
`MachineInstance.machine_spec` with the pinned definition; a local host may omit
it to use the catalog definition. `catalog_revision`, `pending_events` and
`receipts` are optional local-operator extensions; when a host omits them (or
`getMachineStateEntryEvents`), the Timeline leaves those entries out and shows one
neutral line ("… aren't listed for this run"), never an empty inbox.

### Run timeline (bottom pane)

The bottom pane has two tabs, **Logs** and **Timeline**, for every run. A
machine run opens on Timeline and a flow run on Logs until the reader picks a
tab (kept in the layout storage as `runPaneTab`). The Timeline lists the run's
events oldest first, newest at the bottom, with what the run waits for now
pinned on top:

| Run kind | Entries                                                                                                                                                           | Reads (existing `OperatorApi` only)                                                                   |
| -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Flow     | run started, step started/finished (attempts), wait opened/resolved, outcome                                                                                      | `getLatestRunSnapshot` (live projection), `listRunWaits`                                              |
| Machine  | state entry entered/left (with the arc), steps, event accepted/deferred/ignored/dropped/delivered with its receipt, timer armed/fired/cancelled, waits, cancel, outcome | `getMachineInstance`, `getMachineHistory`, `getMachineStateEntry`, `getMachineStateEntryEvents`, `listRunWaits` |

Times read as offsets from the run start (`+1m 05s`); a wall-clock time is the
row's tooltip when the host reports one. Every entry names its state, arc or
step: hovering it (or selecting it) highlights that state card and transition
arc on the graph (a flow's step card), and picking a state or step on the graph
filters the Timeline to its entries. A selected entry offers **Steps** (a
machine state entry) and **Logs** (that state entry's step logs). Reads follow the run's
revision like the other machine reads: a state entry the run has left is read once,
only the live state entry re-reads, one read at a time and never on a timer of its
own. The newest 10 state entries are listed first (earlier ones on request) and at
most 200 rows render at once.

The Logs tab uses the run's ordinary log read: `listLogPage` with the run
snapshot's `logPageToken` (the machine run's own snapshot covers every state entry),
returning `LogRecordDescriptorMsg` rows whose `nodeId` is the state-entry-qualified
step (`e<entry>.<step>`, `.map` items `e<entry>.<step>[i]`). Those rows read
"step · entry N" and open that state entry's step. With a state entry open, the pane reads
that state entry's own snapshot token, and an inspected step filters with the exact
`nodeId`. A host that returns no rows gets the normal empty state.

The send form is the run input form over each transition's payload schema
(`InputFieldList` with a payload draft): the same text fields, and the same file
picker for `ava.File` / `list[ava.File]` fields. Picked files upload right away
through `stageInputFile({ workflow, field, event })`, the run input upload path
with the event named, and are passed to `sendMachineEvent` as `inputFiles`
exactly as `startRun` receives them; the host binds them before it returns the
accepted receipt. Workspace fields take the same reference value as run inputs.
A host that cannot stage payload files rejects the pick with an explicit error
(`GrpcWebOperatorApi` does until its protocol names the event and carries attachments). The host
validates the full schema. A failed send keeps its idempotency key when retried
with the same event, payload and files.

Retention: a run whose history and artifacts expired keeps its run, status and
label; the host sets `RunSummaryMsg.expired` and returns a snapshot without
topology or nodes. The timeline row and run page read `expired`. An expired
machine state entry has `disposition: "expired"` and no `snapshot`; its waits, facts
and steps are not read. The state graph (the definition) still draws. Missing write methods hide their controls, so read-only hosts issue no
mutation; `runActionsEnabled={false}` or `bottomRightPanel={null}` also hide them.

`GrpcWebOperatorApi` implements the machine methods against the local operator's
`ControlMachine` RPC (typed JSON: `get`, `history`, `entry`, `events`, `send`,
`retry`). It maps `FlowInfoV2.machine_spec_json` to `FlowInfoMsg.machineSpec`,
`RunSummaryV2.key_json` to the run label and `read_state: "expired"` to
`expired`, parses a state entry's host snapshot (`RunSnapshotV2` JSON) with the same
mapping as any run snapshot, keeps only a state entry's qualified (`e<entry>.`) wait
cards for `listRunWaits(runId, entry)` and maps retained decision receipts to
`WaitCard.audit` (the receipt carries the payload digest, not the payload). It
does not offer `fireNextMachineTimer` (the operator advertises no development
capability) and refuses event payload files (`ControlMachine` has no attachment
field). The console port follows separately.

### Browser proof

Use Node 22 (as in browser CI) and the workspace's pinned pnpm. From `web/`:

```bash
pnpm install --frozen-lockfile
uv run --no-project --python 3.13 --with playwright==1.55.0 playwright install chromium firefox
uv run --no-project --python 3.13 --with playwright==1.55.0 python \
  operator/scripts/machine_browser_proof.py --evidence /path/to/evidence
```

The script owns and stops a loopback Vite server. It renders the real
`WorkflowWorkspace` against the typed fake `OperatorApi` in
`src/test/machineHarness.tsx`, which is excluded from the published package. Both
browsers check that a machine and a flow share the canvas and the run page (same
cards, grid, controls, input dot, timeline, side panel and logs), then exercise: run-form start with typed input, staged
file and workspace values; one timeline row per instance and same-key identity;
one arc per trigger (self-loop, back and timer arcs) and a stable first frame;
interrupted-state-entry retry; state entries in the timeline opening real step detail (side
inspector outputs and attempts, scoped logs, `.map` items, wait and decision
cards, terminal audits); paged event facts; typed
send with a lost-response retry and receipts; host timer countdown on the page and
the arc; dev timer firing; normal run cancel; transient read recovery; ordinary
flows; read-only hosts and a missing-run deep link. It writes screenshots
(including workflow-run versus machine-run and definition pairs), host calls and a JSON result to the
evidence path. No backend persistence, protocol or restart claim is made.
