import { capText } from "./capText";
// Named connections across an organization: the host-neutral screens a
// multi-project host (an org with projects and flows) mounts. The single-project
// local operator shows its connections in the integrations panel instead.
import { useRef, useState, type FormEvent } from "react";

import { DANGER_BUTTON, FIELD, PRIMARY_BUTTON, SECONDARY_BUTTON } from "./Integrations";

export type ConnectionKind = "shared" | "per_user";
export type ConnectionInstanceState =
  "waiting" | "connected" | "reconnect_needed" | "disconnected" | "disconnecting";

export interface OrgConnection {
  id: string;
  slug: string;
  integration: string;
  kind: ConnectionKind;
  owner: string;
}
/** Org-wide without `project`, a project with it, one flow with both. */
export interface OrgConnectionAssignment {
  connection_id: string;
  project?: string;
  flow?: string;
}
/** A shared connection's one account, or the viewer's own per-user account. */
export interface OrgConnectionInstance {
  id: string;
  connection_id: string;
  user_id: string | null;
  state: ConnectionInstanceState;
}
export interface OrgConnectionsDocument {
  connections: readonly OrgConnection[];
  assignments: readonly OrgConnectionAssignment[];
  instances: readonly OrgConnectionInstance[];
  /** Per-user connections: how many members have an account. */
  instance_counts?: Readonly<Record<string, number>>;
  limit: number | null;
}
export interface ConnectionTarget {
  project?: string;
  flow?: string;
}
/** The host's calls; each change is followed by a fresh document from the host. */
export interface OrgConnectionsApi {
  startLogin: (connection: string) => Promise<{ login_id: string; authorization_url: string }>;
  getLogin: (
    login: string,
    signal: AbortSignal,
  ) => Promise<{ status: "waiting" | "connected" | "failed"; error: string | null }>;
  disconnectMine: (connection: string) => Promise<void>;
  /** Present for org admins only. */
  create?: (body: { integration: string; slug: string; kind: ConnectionKind }) => Promise<void>;
  disconnect?: (connection: string) => Promise<void>;
  assign?: (connection: string, target: ConnectionTarget, revoke: boolean) => Promise<void>;
  loadFlows?: (project: string) => Promise<readonly string[]>;
}

const STATE_LABELS: Record<ConnectionInstanceState | "missing", string> = {
  waiting: "Waiting for login",
  connected: "Connected",
  reconnect_needed: "Reconnect needed",
  disconnected: "Disconnected",
  disconnecting: "Disconnecting",
  missing: "Not connected",
};

function message(error: unknown) {
  return error instanceof Error ? error.message : "The request failed";
}

function pause(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(signal.reason);
    });
  });
}

/** A browser login for one connection: the tab opens on the click, then the host is polled. */
function useConnectionLogin(api: Pick<OrgConnectionsApi, "startLogin" | "getLogin">) {
  const [pending, setPending] = useState<{ id: string; page?: string }>();
  const [error, setError] = useState<{ id: string; text: string }>();
  const stop = useRef<AbortController>(undefined);
  const authorize = async (id: string) => {
    const tab = window.open("about:blank", "_blank");
    const controller = new AbortController();
    stop.current?.abort();
    stop.current = controller;
    setPending({ id });
    setError(undefined);
    try {
      const login = await api.startLogin(id);
      setPending({ id, page: login.authorization_url });
      if (tab) {
        tab.opener = null;
        tab.location.replace(login.authorization_url);
      }
      for (;;) {
        await pause(1500, controller.signal);
        const status = await api.getLogin(login.login_id, controller.signal);
        if (status.status === "failed") throw new Error(status.error ?? "The login failed.");
        if (status.status === "connected") break;
      }
    } catch (failure) {
      tab?.close();
      if (!controller.signal.aborted) setError({ id, text: message(failure) });
    } finally {
      if (stop.current === controller) setPending(undefined);
    }
  };
  const cancel = () => {
    stop.current?.abort();
    setPending(undefined);
  };
  return { pending, error, authorize, cancel };
}

function StateBadge({ state }: { state: ConnectionInstanceState | "missing" }) {
  const good = state === "connected";
  return (
    <span
      data-state={state}
      className={`inline-flex items-center gap-1.5 font-mono text-[9px] uppercase ${good ? "text-mint" : "text-amber"}`}
    >
      {capText(
        <>
          <span
            aria-hidden="true"
            className={`size-[6px] rounded-full ${good ? "bg-mint" : "bg-amber"}`}
          />
          {STATE_LABELS[state]}
        </>,
      )}
    </span>
  );
}

function kindLabel(kind: ConnectionKind) {
  return kind === "per_user" ? "Per user" : "Shared";
}

/**
 * The organization's named connections: slug, integration, kind, account state
 * and assignments. Admins create, authorize, assign and disconnect; every member
 * sees the per-user connections and authorizes their own account.
 */
export function OrganizationConnectionsPanel({
  document,
  api,
  integrations,
  projects,
}: {
  document: OrgConnectionsDocument;
  api: OrgConnectionsApi;
  integrations: readonly { name: string; displayName: string }[];
  projects: readonly { id: string; name: string }[];
}) {
  const admin = api.create !== undefined;
  const login = useConnectionLogin(api);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<{ id: string; panel: "assign" | "disconnect" }>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const act = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(undefined);
    try {
      await action();
      return true;
    } catch (failure) {
      setError(message(failure));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const projectName = (id: string) => projects.find((project) => project.id === id)?.name ?? id;
  const target = (item: OrgConnectionAssignment) =>
    item.project === undefined
      ? "Organization"
      : item.flow === undefined
        ? projectName(item.project)
        : `${projectName(item.project)} / ${item.flow}`;
  const label = (name: string) =>
    integrations.find((item) => item.name === name)?.displayName ?? name;
  const rows = document.connections.filter(
    (connection) => admin || connection.kind === "per_user",
  );
  const used =
    document.instances.filter((instance) => instance.user_id === null).length +
    Object.values(document.instance_counts ?? {}).reduce((sum, count) => sum + count, 0);
  return (
    <section aria-label="Named connections" className="grid gap-3 text-xs text-ink">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="m-0 text-muted">
          {admin
            ? "Each connection is one shared account, or one account per member. Assign it to the organization, a project or a flow; the most specific assignment wins."
            : "Connect your own account for the per-user connections below. Only an org admin creates or assigns connections."}
          {document.limit !== null && ` ${used} of ${document.limit} accounts in use.`}
        </p>
        {admin && !creating && (
          <button type="button" className={PRIMARY_BUTTON} onClick={() => setCreating(true)}>
            New connection
          </button>
        )}
      </div>
      {creating && api.create && (
        <CreateConnectionForm
          integrations={integrations}
          onCancel={() => setCreating(false)}
          onCreate={async (body) => {
            if (await act(() => api.create!(body))) setCreating(false);
          }}
          busy={busy}
        />
      )}
      {error && (
        <p role="alert" className="m-0 text-error-ink">
          {error}
        </p>
      )}
      {rows.length === 0 ? (
        <p className="m-0 text-muted">
          {admin
            ? "No connections yet."
            : "No per-user connections yet. An org admin creates them."}
        </p>
      ) : (
        <div className="overflow-x-auto rounded border border-line bg-panel">
          <table className="w-full text-left [&_td]:px-3 [&_td]:py-2 [&_th]:px-3 [&_th]:py-2">
            <thead className="text-[10px] text-muted uppercase">
              <tr>
                <th>Slug</th>
                <th>Integration</th>
                <th>Kind</th>
                <th>{admin ? "State" : "Your account"}</th>
                <th>Accounts</th>
                <th>Assigned to</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((connection) => {
                const instance = document.instances.find(
                  (item) =>
                    item.connection_id === connection.id &&
                    (connection.kind === "shared") === (item.user_id === null),
                );
                const state = instance?.state ?? "missing";
                const assigned = document.assignments.filter(
                  (item) => item.connection_id === connection.id,
                );
                const own = connection.kind === "per_user";
                const authorizeLabel = own
                  ? instance
                    ? "Reconnect your account"
                    : "Connect your account"
                  : instance
                    ? "Reconnect"
                    : "Connect";
                const waiting = login.pending?.id === connection.id;
                return [
                  <tr
                    key={connection.id}
                    data-connection={connection.slug}
                    data-state={state}
                    className="border-t border-line align-top"
                  >
                    <td className="font-mono">{connection.slug}</td>
                    <td>{label(connection.integration)}</td>
                    <td>{kindLabel(connection.kind)}</td>
                    <td>
                      <StateBadge state={state} />
                      {own && admin && (
                        <span className="block text-[10px] text-muted">your account</span>
                      )}
                    </td>
                    <td>
                      {own
                        ? (document.instance_counts?.[connection.id] ?? 0)
                        : instance
                          ? 1
                          : 0}
                    </td>
                    <td>
                      <ul
                        aria-label={`Assignments of ${connection.slug}`}
                        className="m-0 flex list-none flex-wrap gap-1 p-0"
                      >
                        {assigned.length === 0 && <li className="text-muted">Not assigned</li>}
                        {assigned.map((item) => (
                          <li
                            key={target(item)}
                            className="inline-flex items-center gap-1 rounded border border-line px-2 py-0.5"
                          >
                            {capText(
                              <>
                                {target(item)}
                                {api.assign && (
                                  <button
                                    type="button"
                                    disabled={busy}
                                    aria-label={`Remove assignment ${target(item)} from ${connection.slug}`}
                                    className="cursor-pointer border-0 bg-transparent p-0 text-muted hover:text-ink"
                                    onClick={() =>
                                      void act(() =>
                                        api.assign!(
                                          connection.id,
                                          { project: item.project, flow: item.flow },
                                          true,
                                        ),
                                      )
                                    }
                                  >
                                    ×
                                  </button>
                                )}
                              </>,
                            )}
                          </li>
                        ))}
                      </ul>
                    </td>
                    <td className="space-x-2 whitespace-nowrap text-right">
                      {(own || admin) && (
                        <button
                          type="button"
                          className={SECONDARY_BUTTON}
                          disabled={waiting}
                          aria-label={`${authorizeLabel} ${connection.slug}`}
                          onClick={() => void login.authorize(connection.id)}
                        >
                          {authorizeLabel}
                        </button>
                      )}
                      {own && instance && (
                        <button
                          type="button"
                          className={SECONDARY_BUTTON}
                          disabled={busy}
                          aria-label={`Remove your account ${connection.slug}`}
                          onClick={() => void act(() => api.disconnectMine(connection.id))}
                        >
                          Remove your account
                        </button>
                      )}
                      {admin && (
                        <button
                          type="button"
                          className={SECONDARY_BUTTON}
                          aria-label={`Assign ${connection.slug}`}
                          onClick={() => setOpen({ id: connection.id, panel: "assign" })}
                        >
                          Assign
                        </button>
                      )}
                      {admin && (
                        <button
                          type="button"
                          className={DANGER_BUTTON}
                          aria-label={`Disconnect ${connection.slug}`}
                          onClick={() => setOpen({ id: connection.id, panel: "disconnect" })}
                        >
                          Disconnect
                        </button>
                      )}
                    </td>
                  </tr>,
                  (waiting || login.error?.id === connection.id) && (
                    <tr key={connection.id + ":login"}>
                      <td colSpan={7}>
                        {waiting ? (
                          <p role="status" className="m-0 text-secondary">
                            Waiting for the login in the opened tab.{" "}
                            {login.pending?.page && (
                              <a
                                href={login.pending.page}
                                target="_blank"
                                rel="noreferrer"
                                className="underline"
                              >
                                Open the login page
                              </a>
                            )}{" "}
                            <button type="button" className="underline" onClick={login.cancel}>
                              Cancel
                            </button>
                          </p>
                        ) : (
                          <p role="alert" className="m-0 text-error-ink">
                            {login.error?.text}
                          </p>
                        )}
                      </td>
                    </tr>
                  ),
                  open?.id === connection.id && open.panel === "assign" && api.assign && (
                    <tr key={connection.id + ":assign"}>
                      <td colSpan={7}>
                        <AssignForm
                          slug={connection.slug}
                          projects={projects}
                          loadFlows={api.loadFlows}
                          busy={busy}
                          onCancel={() => setOpen(undefined)}
                          onAssign={async (where) => {
                            if (await act(() => api.assign!(connection.id, where, false)))
                              setOpen(undefined);
                          }}
                        />
                      </td>
                    </tr>
                  ),
                  open?.id === connection.id &&
                    open.panel === "disconnect" &&
                    api.disconnect && (
                      <tr key={connection.id + ":disconnect"}>
                        <td colSpan={7}>
                          <div
                            role="group"
                            aria-label={`Disconnect ${connection.slug}`}
                            className="flex flex-wrap items-center gap-3 rounded border border-cancel-line bg-canvas p-3"
                          >
                            <p className="m-0 min-w-[14rem] flex-1 text-secondary">
                              Disconnect &apos;{connection.slug}&apos;? This removes{" "}
                              {own ? "every member's account" : "its account"} and{" "}
                              {assigned.length
                                ? `its assignments: ${assigned.map(target).join(", ")}`
                                : "it has no assignments"}
                              . Flows that use it no longer resolve it.
                            </p>
                            <button
                              type="button"
                              className={DANGER_BUTTON}
                              disabled={busy}
                              onClick={async () => {
                                if (await act(() => api.disconnect!(connection.id)))
                                  setOpen(undefined);
                              }}
                            >
                              Disconnect
                            </button>
                            <button
                              type="button"
                              className={SECONDARY_BUTTON}
                              onClick={() => setOpen(undefined)}
                            >
                              Cancel
                            </button>
                          </div>
                        </td>
                      </tr>
                    ),
                ];
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function CreateConnectionForm({
  integrations,
  onCreate,
  onCancel,
  busy,
}: {
  integrations: readonly { name: string; displayName: string }[];
  onCreate: (body: {
    integration: string;
    slug: string;
    kind: ConnectionKind;
  }) => Promise<void>;
  onCancel: () => void;
  busy: boolean;
}) {
  const [integration, setIntegration] = useState(integrations[0]?.name ?? "");
  const [slug, setSlug] = useState("");
  const [kind, setKind] = useState<ConnectionKind>("shared");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void onCreate({ integration, slug, kind });
  };
  return (
    <form
      aria-label="New connection"
      onSubmit={submit}
      className="flex flex-wrap items-end gap-3 rounded border border-line bg-canvas p-3"
    >
      <label className="grid gap-1 text-secondary">
        Integration
        <select
          aria-label="Integration"
          className={FIELD}
          value={integration}
          onChange={(event) => setIntegration(event.target.value)}
        >
          {integrations.map((item) => (
            <option key={item.name} value={item.name}>
              {item.displayName}
            </option>
          ))}
        </select>
      </label>
      <label className="grid gap-1 text-secondary">
        Slug
        <input
          className={FIELD}
          value={slug}
          required
          pattern="[a-z0-9]+(-[a-z0-9]+)*"
          placeholder="acme-hq"
          onChange={(event) => setSlug(event.target.value)}
        />
      </label>
      <fieldset className="m-0 flex gap-3 border-0 p-0 text-secondary">
        <legend className="sr-only">Kind</legend>
        {(["shared", "per_user"] as const).map((value) => (
          <label key={value} className="inline-flex items-center gap-1">
            <input
              type="radio"
              name="kind"
              checked={kind === value}
              onChange={() => setKind(value)}
            />
            {kindLabel(value)}
          </label>
        ))}
      </fieldset>
      <button type="submit" className={PRIMARY_BUTTON} disabled={busy}>
        Create
      </button>
      <button type="button" className={SECONDARY_BUTTON} onClick={onCancel}>
        Cancel
      </button>
      <p className="m-0 basis-full text-[10px] text-muted">
        The slug cannot change later: flows name it in code. Shared uses one account for
        everyone; per user lets each member connect their own.
      </p>
    </form>
  );
}

function AssignForm({
  slug,
  projects,
  loadFlows,
  busy,
  onAssign,
  onCancel,
}: {
  slug: string;
  projects: readonly { id: string; name: string }[];
  loadFlows?: (project: string) => Promise<readonly string[]>;
  busy: boolean;
  onAssign: (target: ConnectionTarget) => Promise<void>;
  onCancel: () => void;
}) {
  const [level, setLevel] = useState<"organization" | "project" | "flow">("organization");
  const [project, setProject] = useState("");
  const [flows, setFlows] = useState<readonly string[]>([]);
  const [flow, setFlow] = useState("");
  const pick = (id: string) => {
    setProject(id);
    setFlow("");
    setFlows([]);
    if (id && loadFlows) void loadFlows(id).then(setFlows, () => setFlows([]));
  };
  const ready =
    level === "organization" || (project !== "" && (level === "project" || flow !== ""));
  return (
    <form
      aria-label={`Assign ${slug}`}
      className="flex flex-wrap items-end gap-3 rounded border border-line bg-canvas p-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (ready)
          void onAssign(
            level === "organization"
              ? {}
              : level === "project"
                ? { project }
                : { project, flow },
          );
      }}
    >
      <label className="grid gap-1 text-secondary">
        Assign to
        <select
          aria-label="Assign to"
          className={FIELD}
          value={level}
          onChange={(event) => setLevel(event.target.value as typeof level)}
        >
          <option value="organization">The organization</option>
          <option value="project">A project</option>
          {loadFlows && <option value="flow">A flow</option>}
        </select>
      </label>
      {level !== "organization" && (
        <label className="grid gap-1 text-secondary">
          Project
          <select
            aria-label="Project"
            className={FIELD}
            value={project}
            onChange={(event) => pick(event.target.value)}
          >
            <option value="">Choose a project…</option>
            {projects.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {level === "flow" && (
        <label className="grid gap-1 text-secondary">
          Flow
          <select
            aria-label="Flow"
            className={FIELD}
            value={flow}
            onChange={(event) => setFlow(event.target.value)}
          >
            <option value="">Choose a flow…</option>
            {flows.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
      )}
      <button type="submit" className={PRIMARY_BUTTON} disabled={busy || !ready}>
        Assign
      </button>
      <button type="button" className={SECONDARY_BUTTON} onClick={onCancel}>
        Cancel
      </button>
    </form>
  );
}

/** One catalog declaration of one flow, as the host resolved it for the viewer. */
export interface ProjectConnectionRow {
  flow: string;
  integration: string;
  /** The slug the code names with `connection=`, if any. */
  declared: string | null;
  resolved: {
    connection_id: string;
    slug: string;
    kind: ConnectionKind;
    level: "flow" | "project" | "organization";
    /** The shared account's state, or the viewer's own per-user account's. */
    state: ConnectionInstanceState | null;
  } | null;
  /** The exact refusal a deploy or a run start by this viewer would give. */
  refusal: { code: string; message: string } | null;
}

/**
 * A project's slim view: for each built-in integration its flows declare, the
 * connection that resolves and how, or the refusal. Per-user rows carry the
 * viewer's own account and its Connect action; admins may assign an
 * organization connection to the project where nothing resolves.
 */
export function ProjectConnectionsPanel({
  rows,
  api,
  connections = [],
  onAssign,
  integrationLabel = (name) => name,
}: {
  rows: readonly ProjectConnectionRow[];
  api: Pick<OrgConnectionsApi, "startLogin" | "getLogin">;
  connections?: readonly OrgConnection[];
  onAssign?: (connection: string) => Promise<void>;
  integrationLabel?: (name: string) => string;
}) {
  const login = useConnectionLogin(api);
  const [error, setError] = useState<string>();
  if (rows.length === 0) return null;
  return (
    <section aria-label="Built-in connections" className="grid gap-2 text-xs text-ink">
      <ul className="m-0 grid list-none gap-2 p-0">
        {rows.map((row) => {
          const resolved = row.resolved;
          const key = `${row.flow}:${row.integration}:${row.declared ?? ""}`;
          const choices = connections.filter((item) => item.integration === row.integration);
          return (
            <li
              key={key}
              data-integration={row.integration}
              data-flow={row.flow}
              data-resolution={row.refusal ? "refused" : "resolved"}
              className="project-connection grid gap-1.5 rounded border border-line bg-panel p-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <strong className="text-[13px]">
                  {integrationLabel(row.integration)}
                  {row.declared && (
                    <span className="font-mono font-normal">
                      {" "}
                      · connection=&apos;{row.declared}&apos;
                    </span>
                  )}
                  <span className="font-normal text-muted"> in flow {row.flow}</span>
                </strong>
                {resolved && <StateBadge state={resolved.state ?? "missing"} />}
              </div>
              {resolved && (
                <p className="m-0 text-secondary">
                  Uses <span className="font-mono text-ink">{resolved.slug}</span> ·{" "}
                  {kindLabel(resolved.kind)} · assigned to the {resolved.level}
                </p>
              )}
              {row.refusal && (
                <p role="note" data-refusal={row.refusal.code} className="m-0 text-amber">
                  {row.refusal.message}
                </p>
              )}
              {resolved?.kind === "per_user" && resolved.state !== "connected" && (
                <div>
                  <button
                    type="button"
                    className={PRIMARY_BUTTON}
                    disabled={login.pending?.id === resolved.connection_id}
                    aria-label={`Connect your account ${resolved.slug}`}
                    onClick={() => void login.authorize(resolved.connection_id)}
                  >
                    {login.pending?.id === resolved.connection_id
                      ? "Waiting for the login…"
                      : "Connect your account"}
                  </button>
                </div>
              )}
              {login.error && login.error.id === resolved?.connection_id && (
                <p role="alert" className="m-0 text-error-ink">
                  {login.error.text}
                </p>
              )}
              {onAssign &&
                row.refusal?.code === "connection_not_resolved" &&
                choices.length > 0 && (
                  <select
                    aria-label={`Assign a ${row.integration} connection to this project`}
                    className={`${FIELD} justify-self-start`}
                    value=""
                    onChange={(event) => {
                      const chosen = event.target.value;
                      if (!chosen) return;
                      setError(undefined);
                      onAssign(chosen).catch((failure: unknown) => setError(message(failure)));
                    }}
                  >
                    <option value="">Assign a connection to this project…</option>
                    {choices.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.slug} · {kindLabel(item.kind)}
                      </option>
                    ))}
                  </select>
                )}
            </li>
          );
        })}
      </ul>
      {error && (
        <p role="alert" className="m-0 text-error-ink">
          {error}
        </p>
      )}
    </section>
  );
}
