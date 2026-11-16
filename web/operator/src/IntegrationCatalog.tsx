import { useState, type ReactNode } from "react";

/** Host-neutral catalog data. Adding vendors does not change the grid. */
export interface IntegrationVendor {
  name: string;
  displayName: string;
  connected: boolean;
  enabled: boolean;
}

export function IntegrationVendorGrid({
  vendors,
  renderVendor,
  requestConnectorUrl,
}: {
  vendors: readonly IntegrationVendor[];
  renderVendor: (vendor: IntegrationVendor) => ReactNode;
  requestConnectorUrl?: string;
}) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const visible = vendors
    .filter(
      (vendor) =>
        `${vendor.name} ${vendor.displayName}`
          .toLocaleLowerCase()
          .includes(search.toLocaleLowerCase()) &&
        (filter === "all" || (filter === "connected" ? vendor.connected : !vendor.connected)),
    )
    .toSorted(
      (a, b) =>
        Number(b.connected) - Number(a.connected) ||
        Number(b.enabled) - Number(a.enabled) ||
        a.displayName.localeCompare(b.displayName),
    );
  return (
    <section aria-label="Vendor catalog" className="grid gap-3">
      <div className="flex flex-wrap gap-2">
        <input
          type="search"
          aria-label="Search vendors"
          placeholder="Search vendors"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="min-w-0 flex-1 rounded border border-line bg-panel px-3 py-2 text-xs text-ink"
        />
        <select
          aria-label="Filter vendors"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          className="rounded border border-line bg-panel px-3 py-2 text-xs text-ink"
        >
          <option value="all">All vendors</option>
          <option value="connected">Connected</option>
          <option value="available">Not connected</option>
        </select>
      </div>
      <ul
        aria-label="Vendors"
        className="m-0 grid list-none grid-cols-1 gap-3 p-0 sm:grid-cols-2 lg:grid-cols-3"
      >
        {visible.map((vendor) => (
          <li
            key={vendor.name}
            data-vendor={vendor.name}
            data-enabled={vendor.enabled}
            data-connected={vendor.connected}
            className="min-w-0 rounded border border-line bg-panel"
          >
            {vendor.enabled ? (
              renderVendor(vendor)
            ) : (
              <div className="p-4 text-xs text-muted">{vendor.displayName} · Not enabled</div>
            )}
          </li>
        ))}
      </ul>
      {visible.length === 0 && (
        <p role="status" className="text-xs text-muted">
          No matching vendors.
        </p>
      )}
      {requestConnectorUrl && (
        <a href={requestConnectorUrl} className="text-xs text-secondary underline">
          Request a connector
        </a>
      )}
    </section>
  );
}

export interface IntegrationConnection {
  id: string;
  name: string;
  owner: string;
  state: string;
  grants: Readonly<Record<string, readonly string[]>>;
}
export interface IntegrationGrantProject {
  id: string;
  name: string;
}

/** Organization hosts provide grants. A single-project operator never mounts this list. */
export function IntegrationConnectionsList({
  connections,
  projects,
  onGrant,
  onRevoke,
  onDisconnect,
}: {
  connections: readonly IntegrationConnection[];
  projects: readonly IntegrationGrantProject[];
  onGrant?: (record: string, project: string) => Promise<void>;
  onRevoke?: (record: string, project: string) => Promise<void>;
  onDisconnect?: (record: string) => Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const act = async (action: () => Promise<void>) => {
    setPending(true);
    setError(undefined);
    try {
      await action();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Connection change failed");
    } finally {
      setPending(false);
    }
  };
  return (
    <section
      aria-label="Connections and project grants"
      className="grid gap-3 text-xs text-ink"
    >
      <h2 className="m-0 text-sm font-semibold">Connections and project grants</h2>
      {error && (
        <p role="alert" className="text-error-ink">
          {error}
        </p>
      )}
      {connections.length === 0 && (
        <p className="text-muted">No organization connections yet.</p>
      )}
      <ul className="m-0 grid list-none gap-3 p-0">
        {connections.map((connection) => (
          <li
            key={connection.id}
            data-connection={connection.id}
            className="grid gap-2 rounded border border-line bg-panel p-4"
          >
            <div className="flex justify-between gap-3">
              <strong>
                {connection.name} · {connection.state}
              </strong>
              {onDisconnect && (
                <button
                  type="button"
                  disabled={pending}
                  onClick={() => {
                    if (
                      window.confirm(`Disconnect ${connection.name} for every granted project?`)
                    )
                      void act(() => onDisconnect(connection.id));
                  }}
                >
                  Disconnect {connection.name}
                </button>
              )}
            </div>
            <span className="text-muted">Connected by {connection.owner}</span>
            <ul aria-label={`Grants for ${connection.name}`} className="m-0 grid gap-1 pl-4">
              {Object.keys(connection.grants).map((project) => (
                <li key={project}>
                  {projects.find((item) => item.id === project)?.name ?? project}
                  {onRevoke && (
                    <button
                      type="button"
                      disabled={pending}
                      className="ml-3 underline"
                      onClick={() => void act(() => onRevoke(connection.id, project))}
                    >
                      Revoke {projects.find((item) => item.id === project)?.name ?? project}
                    </button>
                  )}
                </li>
              ))}
            </ul>
            {onGrant && (
              <select
                aria-label={`Grant ${connection.name} to project`}
                value=""
                disabled={pending || connection.state !== "connected"}
                className="rounded border border-line bg-canvas p-2"
                onChange={(event) => {
                  const project = event.target.value;
                  if (project) void act(() => onGrant(connection.id, project));
                }}
              >
                <option value="">Grant to a project…</option>
                {projects
                  .filter((project) => !connection.grants[project.id])
                  .map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
              </select>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function IntegrationGrantPicker({
  name,
  connections,
  project,
  onGrant,
}: {
  name: string;
  connections: readonly IntegrationConnection[];
  project: string;
  onGrant?: (record: string, project: string) => Promise<void>;
}) {
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState(false);
  return (
    <div className="mt-2 text-xs text-secondary">
      {onGrant ? (
        <select
          aria-label={`Connection for ${name}`}
          disabled={pending}
          defaultValue=""
          className="rounded border border-line bg-canvas p-2"
          onChange={async (event) => {
            const record = event.target.value;
            if (!record) return;
            setPending(true);
            setError(undefined);
            try {
              await onGrant(record, project);
            } catch (reason) {
              setError(reason instanceof Error ? reason.message : "Grant failed");
            } finally {
              setPending(false);
            }
          }}
        >
          <option value="">Choose an organization connection…</option>
          {connections
            .filter(
              (connection) => connection.name === name && connection.state === "connected",
            )
            .map((connection) => (
              <option key={connection.id} value={connection.id}>
                {connection.name} · {connection.owner}
                {connection.grants[project] ? " · granted" : " · available"}
              </option>
            ))}
        </select>
      ) : (
        <p>Ask an org admin to grant a connection.</p>
      )}
      {error && (
        <p role="alert" className="text-error-ink">
          {error}
        </p>
      )}
    </div>
  );
}
