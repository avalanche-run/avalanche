import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { LoaderCircle, Plug, X } from "lucide-react";

import {
  integrationAccountState,
  integrationForTool,
  integrationKindLabel,
  integrationLabel,
  integrationTakesAccount,
  integrationsMissingAccount,
  type IntegrationAccountState,
  type IntegrationDeclaration,
  type IntegrationLoginKind,
  type IntegrationStatus,
} from "./integrationAccounts";
import {
  IntegrationsProvider,
  useIntegrationsView,
  useStepIntegrations,
  type IntegrationsApi,
} from "./integrationsView";
import { formatAbsoluteTime } from "./timeAgo";
import { IntegrationVendorGrid } from "./IntegrationCatalog";

const ACCOUNT_LABELS: Record<IntegrationAccountState, string> = {
  connected: "Connected",
  missing: "Not connected",
  reconnect_needed: "Reconnect needed",
  none: "No account needed",
};

/**
 * The integration's declared icon, or the generic one. The file is drawn by an
 * `<img>` element and never placed in the page as markup, so an SVG icon cannot
 * run script here.
 */
export function IntegrationIcon({
  declaration,
  className = "size-4",
}: {
  declaration: IntegrationDeclaration;
  className?: string;
}) {
  const url = useIntegrationsView().iconUrl(declaration.name);
  const [failedUrl, setFailedUrl] = useState<string>();
  if (url === undefined || failedUrl === url) {
    return (
      <Plug
        aria-hidden="true"
        data-integration-icon="generic"
        className={`integration-icon shrink-0 ${className}`}
      />
    );
  }
  return (
    <img
      src={url}
      alt=""
      draggable={false}
      data-integration-icon="declared"
      className={`integration-icon shrink-0 object-contain ${className}`}
      onError={() => setFailedUrl(url)}
    />
  );
}

function accountState(
  status: IntegrationStatus | undefined,
  declaration: IntegrationDeclaration,
): IntegrationAccountState | undefined {
  return status ? integrationAccountState(status, declaration) : undefined;
}

function declarationKey(declaration: IntegrationDeclaration): string {
  return `${declaration.name}:${declaration.kind === "catalog" ? (declaration.connection ?? "") : ""}`;
}

function declarationLabel(
  declaration: IntegrationDeclaration,
  status?: IntegrationStatus,
): string {
  const label = integrationLabel(declaration, status);
  return declaration.kind === "catalog" && declaration.connection
    ? `${label} (${declaration.connection})`
    : label;
}

/** One badge per integration a step declares, drawn on its node card. */
export function NodeIntegrationBadges({
  declarations,
  compact,
}: {
  declarations: readonly IntegrationDeclaration[];
  compact: boolean;
}) {
  const view = useIntegrationsView();
  return (
    <ul
      className={`node-integrations m-0 flex min-w-0 list-none flex-wrap gap-1 p-0 ${compact ? "mt-2 justify-center" : ""}`}
      aria-label="Integrations"
    >
      {declarations.map((declaration) => {
        const status = view.status(declaration.name);
        const state = accountState(status, declaration);
        // Only an account a step cannot start without is flagged on the graph.
        const blocking =
          state === "reconnect_needed" ||
          (state === "missing" &&
            (status?.requires_account === true || status?.named_connections === true));
        const label = declarationLabel(declaration, status);
        return (
          <li
            key={declarationKey(declaration)}
            data-connection={
              declaration.kind === "catalog" ? declaration.connection : undefined
            }
            data-integration={declaration.name}
            data-account={state}
            title={`${label} · ${integrationKindLabel(declaration)}${state ? ` · ${ACCOUNT_LABELS[state]}` : ""}`}
            className={`node-integration-badge inline-flex min-w-0 max-w-full items-center gap-1 rounded border px-1.5 py-0.5 font-mono ${compact ? "text-sm" : "text-[9px]"} ${blocking ? "border-warning-line bg-warning-surface text-warning-ink" : "border-line bg-canvas text-secondary"}`}
          >
            <IntegrationIcon
              declaration={declaration}
              className={compact ? "size-4" : "size-3"}
            />
            <span className="min-w-0 truncate">{label}</span>
            {blocking && <span className="sr-only">, {ACCOUNT_LABELS[state]}</span>}
          </li>
        );
      })}
    </ul>
  );
}

function AccountState({ state }: { state: IntegrationAccountState }) {
  return (
    <span
      className={`integration-account inline-flex items-center gap-1.5 font-mono text-[9px] uppercase ${state === "connected" ? "text-mint" : state === "missing" || state === "reconnect_needed" ? "text-amber" : "text-muted"}`}
      data-account={state}
    >
      <span
        aria-hidden="true"
        className={`size-[6px] rounded-full ${state === "connected" ? "bg-mint" : state === "missing" || state === "reconnect_needed" ? "bg-amber" : "bg-muted"}`}
      />
      {ACCOUNT_LABELS[state]}
    </span>
  );
}

const LINK_BUTTON =
  "cursor-pointer border-0 bg-transparent p-0 text-[11px] font-medium text-acid underline hover:no-underline focus-visible:rounded focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid";

/**
 * The integrations the inspected step declares: kind, account and, for a
 * Connector, its tools. A failed step whose account was missing says so and
 * links to the place to connect it.
 */
export function StepIntegrationsSection({ error }: { error?: string }) {
  const declarations = useStepIntegrations();
  const view = useIntegrationsView();
  if (declarations.length === 0) return null;
  const missing = integrationsMissingAccount(error, declarations, view.statuses);
  const label = (declaration: IntegrationDeclaration) =>
    declarationLabel(declaration, view.status(declaration.name));
  return (
    <section
      aria-label="Integrations"
      className="step-integrations max-h-[40%] shrink-0 overflow-auto border-b border-line px-5 py-3"
    >
      <h3 className="inspector-section-title">Integrations</h3>
      {missing.length > 0 && (
        <div
          role="alert"
          className="integration-missing-account mb-3 rounded border border-warning-line bg-warning-surface px-3 py-2 text-[11px] text-warning-ink"
        >
          <p className="m-0">
            {missing.map(label).join(" and ")} {missing.length === 1 ? "needs" : "need"} a
            connected account to start this step.
          </p>
          {view.openIntegrations && (
            <p className="m-0 mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
              {missing.map((declaration) => (
                <button
                  key={declarationKey(declaration)}
                  type="button"
                  className={LINK_BUTTON}
                  onClick={() => view.openIntegrations?.(declaration.name)}
                >
                  Connect {label(declaration)}
                </button>
              ))}
            </p>
          )}
        </div>
      )}
      <ul className="m-0 grid list-none gap-2.5 p-0">
        {declarations.map((declaration) => {
          const status = view.status(declaration.name);
          const state = accountState(status, declaration);
          return (
            <li
              key={declarationKey(declaration)}
              data-integration={declaration.name}
              className="min-w-0"
            >
              <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                <IntegrationIcon declaration={declaration} className="size-4" />
                <strong className="min-w-0 text-xs text-ink [overflow-wrap:anywhere]">
                  {declarationLabel(declaration, view.status(declaration.name))}
                </strong>
                <span className="rounded border border-line px-1.5 py-px font-mono text-[8px] tracking-[.08em] text-secondary uppercase">
                  {integrationKindLabel(declaration)}
                </span>
                {state && <AccountState state={state} />}
              </div>
              {status?.provider_error && (
                <p role="alert" className="m-0 mt-1 text-[11px] text-error-ink">
                  {status.provider_error}
                </p>
              )}
              {declaration.kind === "connector" ? (
                <details className="mt-1 min-w-0 pl-6">
                  <summary className="cursor-pointer text-[11px] text-secondary focus-visible:outline-2 focus-visible:outline-acid">
                    {declaration.tools.length} tool{declaration.tools.length === 1 ? "" : "s"}
                  </summary>
                  <ul
                    className="m-0 mt-1.5 grid list-none gap-1.5 p-0"
                    aria-label={`${declarationLabel(declaration, view.status(declaration.name))} tools`}
                  >
                    {declaration.tools.map((tool) => (
                      <li key={tool.name} className="min-w-0">
                        <code className="text-[10px] text-ink [overflow-wrap:anywhere]">
                          {tool.name}
                        </code>
                        {tool.description && (
                          <p className="m-0 mt-0.5 text-[10px] text-muted">
                            {tool.description}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : (
                <p className="m-0 mt-1 pl-6 text-[10px] text-muted [overflow-wrap:anywhere]">
                  {declaration.kind === "mcp"
                    ? (declaration.url ?? declaration.command?.join(" "))
                    : "Provider-hosted tools"}
                  {declaration.allow
                    ? ` · tools: ${declaration.allow.join(", ")}`
                    : " · every tool the server offers"}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Names the integration a traced tool call belongs to, when its step declares one. */
export function ToolIntegrationTag({ toolName }: { toolName: string }) {
  const declaration = integrationForTool(toolName, useStepIntegrations());
  const view = useIntegrationsView();
  if (!declaration) return null;
  return (
    <span
      className="tool-integration ml-1.5 inline-flex max-w-full items-center gap-1 rounded border border-line bg-panel px-1.5 py-px align-middle font-mono text-[8px] font-normal text-secondary"
      data-integration={declaration.name}
      title={`${integrationKindLabel(declaration)} ${declaration.name}`}
    >
      <IntegrationIcon declaration={declaration} className="size-2.5" />
      <span className="truncate">
        {declarationLabel(declaration, view.status(declaration.name))}
      </span>
    </span>
  );
}

const FIELD =
  "h-8 min-w-0 rounded border border-line-strong bg-panel px-2 font-mono text-[11px] text-ink focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-acid";
const SECONDARY_BUTTON =
  "inline-flex h-8 cursor-pointer items-center gap-1.5 rounded border border-line-strong bg-panel px-3 text-[11px] font-medium text-ink hover:bg-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid disabled:cursor-default disabled:opacity-60";
const PRIMARY_BUTTON =
  "inline-flex h-8 cursor-pointer items-center gap-1.5 rounded border border-acid bg-acid px-3 text-[11px] font-bold text-on-accent hover:border-acid-hover hover:bg-acid-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid disabled:cursor-default disabled:opacity-60";
const DANGER_BUTTON =
  "inline-flex h-8 cursor-pointer items-center gap-1.5 rounded border border-cancel-line bg-panel px-3 text-[11px] font-medium text-cancel-ink hover:bg-cancel-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid disabled:cursor-default disabled:opacity-60";

function failureMessage(error: unknown): string {
  return error instanceof Error ? error.message : "The request failed";
}

const LOGIN_KIND_LABELS: Record<IntegrationLoginKind, string> = {
  api_key: "Paste a key",
  browser: "Browser login",
};

/**
 * The connect form of one integration: an API key, a browser login, or a choice
 * between the two for an MCP server, which takes either.
 */
function ConnectForm({ status, onClose }: { status: IntegrationStatus; onClose: () => void }) {
  const view = useIntegrationsView();
  const kinds = status.login.filter((kind) =>
    kind === "api_key" ? view.connect !== undefined : view.login !== undefined,
  );
  const [chosen, setChosen] = useState<IntegrationLoginKind>();
  const kind = chosen ?? kinds[0];
  const label = integrationLabel(status.declaration, status);
  const choice = (
    <div
      role="radiogroup"
      aria-label="Login"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[9px] tracking-[.08em] text-muted uppercase"
    >
      <span>Login</span>
      {kinds.map((option) => (
        <label key={option} className="inline-flex cursor-pointer items-center gap-1">
          <input
            type="radio"
            name={`integration-login-${status.name}`}
            checked={option === kind}
            disabled={kinds.length === 1}
            onChange={() => setChosen(option)}
          />
          {LOGIN_KIND_LABELS[option]}
        </label>
      ))}
    </div>
  );
  if (kind === "browser" && view.login) {
    return (
      <BrowserLoginForm
        key="browser"
        status={status}
        label={label}
        choice={choice}
        onLogin={view.login}
        onClose={onClose}
      />
    );
  }
  if (kind === "api_key" && view.connect) {
    return (
      <KeyForm
        key="api_key"
        status={status}
        choice={choice}
        onConnect={view.connect}
        onClose={onClose}
      />
    );
  }
  return null;
}

/**
 * A browser login. The client secret lives only in this form's field until it
 * is sent; tokens never reach the page: the host receives and stores them.
 */
function BrowserLoginForm({
  status,
  label,
  choice,
  onLogin,
  onClose,
  connection,
}: {
  connection?: NonNullable<IntegrationStatus["connections"]>[number];
  status: IntegrationStatus;
  label: string;
  choice: ReactNode;
  onLogin: NonNullable<ReturnType<typeof useIntegrationsView>["login"]>;
  onClose: () => void;
}) {
  const clientIdId = useId();
  const secretId = useId();
  const [slug, setSlug] = useState(connection?.slug ?? status.name);
  const [perUser, setPerUser] = useState(connection?.kind === "per_user");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [waiting, setWaiting] = useState(false);
  const [page, setPage] = useState<string>();
  const [error, setError] = useState<string>();
  const stop = useRef<AbortController>(undefined);
  const { declaration } = status;
  // A Connector's app is registered by its owner, who has its client ID. An MCP
  // server names its client in the declaration, or registers one by itself.
  const asksClientId = declaration.kind === "connector";
  const asksSecret =
    asksClientId || (declaration.kind === "mcp" && declaration.client_id !== null);
  useEffect(() => () => stop.current?.abort(), []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (waiting || (asksClientId && !clientId.trim())) return;
    // Opened while the click is being handled, or the browser blocks it as a popup.
    const tab = window.open("about:blank", "_blank");
    const controller = new AbortController();
    stop.current = controller;
    setWaiting(true);
    setPage(undefined);
    setError(undefined);
    let opened = false;
    onLogin(
      status.name,
      {
        clientId: asksClientId ? clientId.trim() : undefined,
        clientSecret: asksSecret && secret ? secret : undefined,
        ...(status.named_connections ? { slug, kind: perUser ? "per_user" : "shared" } : {}),
      },
      (url) => {
        opened = true;
        setPage(url);
        if (tab) {
          tab.opener = null;
          tab.location.replace(url);
        }
      },
      controller.signal,
    ).then(
      () => {
        setSecret("");
        if (!controller.signal.aborted) onClose();
      },
      (failure: unknown) => {
        if (!opened) tab?.close();
        if (controller.signal.aborted) return;
        setWaiting(false);
        setPage(undefined);
        setError(failureMessage(failure));
      },
    );
  };

  return (
    <form
      className="integration-connect-form mt-3 grid gap-3 rounded border border-line bg-canvas p-3"
      aria-label={`Connect ${label}`}
      data-login="browser"
      onSubmit={submit}
    >
      {choice}
      {status.named_connections && (
        <>
          <label className="grid gap-1 text-xs text-secondary">
            Connection slug
            <input
              className={FIELD}
              value={slug}
              required
              pattern="[a-z0-9]+(-[a-z0-9]+)*"
              readOnly={connection !== undefined}
              onChange={(event) => setSlug(event.target.value)}
            />
          </label>
          <label className="text-xs text-secondary">
            <input
              type="checkbox"
              checked={perUser}
              disabled={connection !== undefined}
              onChange={(event) => setPerUser(event.target.checked)}
            />{" "}
            Per user
          </label>
        </>
      )}
      <div className="flex min-w-0 flex-wrap items-end gap-3">
        {asksClientId && (
          <label
            className="grid min-w-[12rem] flex-1 gap-1 text-[11px] text-secondary"
            htmlFor={clientIdId}
          >
            Client ID
            <input
              id={clientIdId}
              className={FIELD}
              type="text"
              autoComplete="off"
              spellCheck={false}
              required
              value={clientId}
              disabled={waiting}
              onChange={(event) => setClientId(event.target.value)}
            />
          </label>
        )}
        {asksSecret && (
          <label
            className="grid min-w-[12rem] flex-1 gap-1 text-[11px] text-secondary"
            htmlFor={secretId}
          >
            Client secret (if the app has one)
            <input
              id={secretId}
              className={FIELD}
              type="password"
              name="integration-client-secret"
              autoComplete="off"
              spellCheck={false}
              value={secret}
              disabled={waiting}
              onChange={(event) => setSecret(event.target.value)}
            />
          </label>
        )}
        <div className="flex gap-2">
          <button
            type="submit"
            className={PRIMARY_BUTTON}
            disabled={waiting || (asksClientId && !clientId.trim())}
          >
            {waiting && (
              <LoaderCircle
                aria-hidden="true"
                className="size-3 animate-spin motion-reduce:animate-none"
              />
            )}
            {status.connected || status.account_state === "reconnect_needed"
              ? "Log in again"
              : "Log in with browser"}
          </button>
          <button type="button" className={SECONDARY_BUTTON} onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
      {waiting && (
        <p role="status" className="m-0 text-[11px] text-secondary">
          Waiting for the login to finish in the browser.{" "}
          {page && (
            <a
              className="text-ink underline"
              href={page}
              target="_blank"
              rel="noreferrer noopener"
            >
              Open the login page
            </a>
          )}
        </p>
      )}
      <p className="m-0 text-[10px] text-muted">
        {declaration.kind === "catalog"
          ? "The provider's login opens in a new tab. The provider keeps the account; no credential is read back."
          : `${asksClientId ? "Use the client ID and secret of your registered app. " : "Use the login the server describes. "}The login opens in a new tab. The operator stores the tokens and never shows them.`}
      </p>
      {error && (
        <p role="alert" className="m-0 text-[11px] text-error-ink">
          {error}
        </p>
      )}
    </form>
  );
}

/**
 * The API key form of one integration. The key lives only in this form's
 * fields until it is sent; it is cleared afterwards and never read back.
 */
function KeyForm({
  status,
  choice,
  onConnect,
  onClose,
}: {
  status: IntegrationStatus;
  choice: ReactNode;
  onConnect: (
    name: string,
    credential: { kind: "api_key"; key: string; header?: string },
  ) => Promise<void>;
  onClose: () => void;
}) {
  const keyId = useId();
  const headerId = useId();
  const keyInput = useRef<HTMLInputElement>(null);
  const [key, setKey] = useState("");
  const [header, setHeader] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();
  const { declaration } = status;
  useEffect(() => keyInput.current?.focus(), []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmedKey = key.trim();
    if (!trimmedKey || sending) return;
    setSending(true);
    setError(undefined);
    onConnect(status.name, {
      kind: "api_key",
      key: trimmedKey,
      header: declaration.kind === "mcp" && header.trim() ? header.trim() : undefined,
    }).then(
      () => {
        setKey("");
        onClose();
      },
      (failure: unknown) => {
        setSending(false);
        setError(failureMessage(failure));
      },
    );
  };

  return (
    <form
      className="integration-connect-form mt-3 grid gap-3 rounded border border-line bg-canvas p-3"
      aria-label={`Connect ${integrationLabel(declaration, status)}`}
      onSubmit={submit}
    >
      {choice}
      <div className="flex min-w-0 flex-wrap items-end gap-3">
        <label
          className="grid min-w-[14rem] flex-1 gap-1 text-[11px] text-secondary"
          htmlFor={keyId}
        >
          API key
          <input
            ref={keyInput}
            id={keyId}
            className={FIELD}
            type="password"
            name="integration-api-key"
            autoComplete="off"
            spellCheck={false}
            required
            value={key}
            disabled={sending}
            onChange={(event) => setKey(event.target.value)}
          />
        </label>
        {declaration.kind === "mcp" && (
          <label
            className="grid min-w-[10rem] gap-1 text-[11px] text-secondary"
            htmlFor={headerId}
          >
            Header (optional)
            <input
              id={headerId}
              className={FIELD}
              type="text"
              autoComplete="off"
              spellCheck={false}
              placeholder="Authorization"
              value={header}
              disabled={sending}
              onChange={(event) => setHeader(event.target.value)}
            />
          </label>
        )}
        <div className="flex gap-2">
          <button type="submit" className={PRIMARY_BUTTON} disabled={sending || !key.trim()}>
            {sending && (
              <LoaderCircle
                aria-hidden="true"
                className="size-3 animate-spin motion-reduce:animate-none"
              />
            )}
            {status.connected ? "Replace key" : "Connect"}
          </button>
          <button
            type="button"
            className={SECONDARY_BUTTON}
            disabled={sending}
            onClick={onClose}
          >
            Cancel
          </button>
        </div>
      </div>
      <p className="m-0 text-[10px] text-muted">
        {declaration.kind === "connector" && declaration.auth?.kind === "api_key"
          ? `Sent to ${declaration.base_url} in the ${declaration.auth.header} header. `
          : ""}
        The key is stored and is never shown again.
      </p>
      {error && (
        <p role="alert" className="m-0 text-[11px] text-error-ink">
          {error}
        </p>
      )}
    </form>
  );
}

function IntegrationRow({
  status,
  focused,
  connecting,
  onConnecting,
}: {
  status: IntegrationStatus;
  focused: boolean;
  connecting: boolean;
  onConnecting: (open: boolean) => void;
}) {
  const view = useIntegrationsView();
  const row = useRef<HTMLLIElement>(null);
  const [confirming, setConfirming] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string>();
  const { declaration } = status;
  const state = integrationAccountState(status);
  const label = integrationLabel(declaration, status);
  const takesAccount = integrationTakesAccount(declaration);
  const steps = [...new Set(status.used_by.map((use) => `${use.workflow} / ${use.node_id}`))];
  const canConnect = status.login.some((kind) =>
    kind === "api_key" ? view.connect !== undefined : view.login !== undefined,
  );
  // What connecting again replaces: a key, or a login.
  const again = status.login[0] === "browser" ? "Replace login" : "Replace key";

  useEffect(() => {
    if (focused) row.current?.scrollIntoView({ block: "nearest" });
  }, [focused]);

  const remove = () => {
    if (!view.disconnect) return;
    setRemoving(true);
    setError(undefined);
    view.disconnect(status.name).then(
      () => {
        setRemoving(false);
        setConfirming(false);
      },
      (failure: unknown) => {
        setRemoving(false);
        setError(failureMessage(failure));
      },
    );
  };

  return (
    <li
      ref={row}
      className={`integration-row border-b border-line px-5 py-4 last:border-b-0 ${focused ? "bg-selected" : ""}`}
      data-integration={status.name}
      data-account={state}
      aria-current={focused ? "true" : undefined}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-3">
        <span className="grid size-9 shrink-0 place-items-center rounded border border-line bg-canvas text-secondary">
          <IntegrationIcon declaration={declaration} className="size-5" />
        </span>
        <div className="min-w-[12rem] flex-1">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <strong className="text-sm text-ink [overflow-wrap:anywhere]">{label}</strong>
            <span className="rounded border border-line px-1.5 py-px font-mono text-[8px] tracking-[.08em] text-secondary uppercase">
              {integrationKindLabel(declaration)}
            </span>
          </div>
          <p className="m-0 mt-0.5 font-mono text-[10px] text-muted [overflow-wrap:anywhere]">
            {status.name}
            {declaration.kind === "connector"
              ? ` · ${declaration.base_url}`
              : declaration.kind === "mcp"
                ? ` · ${declaration.url ?? declaration.command?.join(" ")}`
                : ""}
          </p>
          {steps.length > 0 && (
            <p className="m-0 mt-1 text-[10px] text-secondary [overflow-wrap:anywhere]">
              Used by {steps.join(", ")}
            </p>
          )}
        </div>
        <div className="grid min-w-[9rem] gap-0.5">
          <AccountState state={state} />
          {status.connected_at !== null && (
            <span className="text-[10px] text-muted">
              since {formatAbsoluteTime(status.connected_at * 1000)}
            </span>
          )}
          {state === "missing" && !status.requires_account && (
            <span className="text-[10px] text-muted">
              Needed only if the server asks for a login
            </span>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          {takesAccount && canConnect && !connecting && (
            <button
              type="button"
              className={status.connected ? SECONDARY_BUTTON : PRIMARY_BUTTON}
              aria-label={`${status.named_connections ? "New connection for" : state === "reconnect_needed" ? "Reconnect" : status.connected ? `${again} of` : "Connect"} ${label}`}
              onClick={() => {
                setConfirming(false);
                onConnecting(true);
              }}
            >
              {status.named_connections
                ? "New connection"
                : state === "reconnect_needed"
                  ? "Reconnect"
                  : status.connected
                    ? again
                    : "Connect"}
            </button>
          )}
          {!status.named_connections &&
            (status.connected || state === "reconnect_needed") &&
            view.disconnect &&
            !confirming && (
              <button
                type="button"
                className={DANGER_BUTTON}
                aria-label={`Disconnect ${label}`}
                onClick={() => {
                  onConnecting(false);
                  setConfirming(true);
                }}
              >
                Disconnect
              </button>
            )}
        </div>
      </div>
      {status.named_connections && <NamedConnections status={status} />}
      {status.provider_error && (
        <p role="alert" className="m-0 mt-2 text-[11px] text-error-ink">
          {status.provider_error}
        </p>
      )}
      {connecting && canConnect && (
        <ConnectForm status={status} onClose={() => onConnecting(false)} />
      )}
      {confirming && (
        <div
          role="group"
          aria-label={`Disconnect ${label}`}
          className="mt-3 flex flex-wrap items-center gap-3 rounded border border-cancel-line bg-canvas p-3"
        >
          <p className="m-0 min-w-[14rem] flex-1 text-[11px] text-secondary">
            Remove the stored account of {label}? Steps that use it will not start until it is
            connected again.
          </p>
          <button type="button" className={DANGER_BUTTON} disabled={removing} onClick={remove}>
            {removing && (
              <LoaderCircle
                aria-hidden="true"
                className="size-3 animate-spin motion-reduce:animate-none"
              />
            )}
            Remove account
          </button>
          <button
            type="button"
            className={SECONDARY_BUTTON}
            disabled={removing}
            onClick={() => setConfirming(false)}
          >
            Cancel
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="m-0 mt-2 text-[11px] text-error-ink">
          {error}
        </p>
      )}
    </li>
  );
}

/** Shared rendering for the local operator and hosts embedding the panel. */
function NamedConnections({ status }: { status: IntegrationStatus }) {
  const view = useIntegrationsView();
  const [selected, setSelected] = useState<string>();
  const [error, setError] = useState<string>();
  const connection = status.connections?.find((item) => item.slug === selected);
  return (
    <section
      aria-label={`${status.name} connections`}
      className="mt-3 overflow-x-auto text-xs text-secondary"
    >
      <table className="w-full text-left [&_td]:px-2 [&_th]:px-2 [&_th]:py-2">
        <thead>
          <tr>
            <th>Slug</th>
            <th>Kind</th>
            <th>Your state</th>
            <th>Instances</th>
            <th>Assignments</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {status.connections?.map((item) => (
            <tr key={item.slug} data-connection={item.slug}>
              <td className="py-2 font-mono">{item.slug}</td>
              <td>{item.kind === "per_user" ? "Per user" : "Shared"}</td>
              <td>{item.state.replaceAll("_", " ")}</td>
              <td>{item.instances}</td>
              <td>{item.assignments.join(", ")}</td>
              <td className="space-x-2 whitespace-nowrap">
                {view.login && (
                  <button
                    type="button"
                    className={SECONDARY_BUTTON}
                    onClick={() => setSelected(item.slug)}
                  >
                    {item.kind === "per_user" && item.state === "disconnected"
                      ? "Connect your account"
                      : "Reconnect"}
                  </button>
                )}
                {view.disconnect && (
                  <button
                    type="button"
                    className={DANGER_BUTTON}
                    onClick={() => {
                      if (
                        window.confirm(
                          `Disconnect '${item.slug}'? This removes its operator assignment and all accounts.`,
                        )
                      ) {
                        view
                          .disconnect?.(status.name, item.slug)
                          .catch((failure: unknown) => setError(failureMessage(failure)));
                      }
                    }}
                  >
                    Disconnect
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {connection && view.login && (
        <BrowserLoginForm
          key={connection.slug}
          status={status}
          connection={connection}
          label={connection.slug}
          choice={null}
          onLogin={view.login}
          onClose={() => setSelected(undefined)}
        />
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

interface IntegrationsScreenProps {
  /** Project hosts show only declarations and supply their own grant picker. */
  declaredOnly?: boolean;
  renderGrant?: (status: IntegrationStatus) => ReactNode;
  description?: string;
  /** The integration to bring into view with its connect form open. */
  focusName?: string;
  className?: string;
  /** Shows a close action that returns to where the screen was opened from. */
  onClose?: () => void;
}

export interface IntegrationsPanelProps extends IntegrationsScreenProps {
  api: IntegrationsApi;
  /** Change it to list the integrations again, such as after a deployment. */
  revision?: unknown;
}

/**
 * Every integration the host's workflows declare, each connected or not, with
 * the connect and disconnect actions. It lists through the host's integration
 * calls (`IntegrationsApi`, a part of `OperatorApi`) and shows no secret. A host
 * that leaves out the connect or disconnect call shows the state without that
 * action. `OperatorUi` shows the same screen itself.
 */
export function IntegrationsPanel({ api, revision, ...screen }: IntegrationsPanelProps) {
  return (
    <div className="avalanche-operator-ui h-full min-h-0">
      <IntegrationsProvider api={api} catalog={revision}>
        <IntegrationsScreen {...screen} className={`h-full ${screen.className ?? ""}`} />
      </IntegrationsProvider>
    </div>
  );
}

/** The integrations screen, inside an integrations view. */
export function IntegrationsScreen({
  focusName,
  className = "",
  onClose,
  declaredOnly = false,
  renderGrant,
  description = "The outside services the workflows declare. Built-ins can have named shared or per-user connections, assigned to this operator. Stored credentials are never shown.",
}: IntegrationsScreenProps) {
  const view = useIntegrationsView();
  const [connectingName, setConnectingName] = useState<string | undefined>(focusName);
  const [seenFocus, setSeenFocus] = useState(focusName);
  if (seenFocus !== focusName) {
    setSeenFocus(focusName);
    setConnectingName(focusName);
  }
  const connecting = view.statuses.find(
    (status) => status.name === connectingName && integrationTakesAccount(status.declaration),
  )?.name;
  const missing = view.statuses.filter(
    (status) => integrationAccountState(status) === "missing" && status.requires_account,
  ).length;

  const reconnect = view.statuses.filter(
    (status) => integrationAccountState(status) === "reconnect_needed",
  ).length;

  return (
    <section
      className={`integrations-panel min-h-0 min-w-0 overflow-auto bg-canvas ${className}`}
      aria-label="Integrations"
    >
      <div className="mx-auto grid w-full max-w-[60rem] gap-4 px-6 py-6">
        <header className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="m-0 text-lg font-semibold tracking-[-0.02em] text-ink">
              Integrations
            </h1>
            <p className="m-0 mt-1 text-xs text-secondary">{description}</p>
          </div>
          {onClose && (
            <button
              type="button"
              className="icon-button grid size-[30px] shrink-0 cursor-pointer place-items-center rounded border border-line bg-panel p-0 text-secondary hover:border-secondary hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"
              aria-label="Close integrations"
              onClick={onClose}
            >
              <X aria-hidden="true" className="size-4" strokeWidth={1.8} />
            </button>
          )}
        </header>
        {view.error && (
          <p
            role="alert"
            className="m-0 rounded border border-error-line bg-error-surface px-3 py-2 text-xs text-error-ink"
          >
            Integrations could not be listed: {view.error}
          </p>
        )}
        {!view.loaded && !view.error ? (
          <p role="status" className="m-0 text-xs text-muted">
            Loading integrations…
          </p>
        ) : view.loaded && view.statuses.length === 0 ? (
          <p className="m-0 rounded border border-line bg-panel px-5 py-6 text-xs text-muted">
            No workflow declares an integration. A step declares one with{" "}
            <code>integrations=[...]</code>.
          </p>
        ) : (
          view.loaded && (
            <>
              <p className="m-0 font-mono text-[10px] text-muted" role="status">
                {view.statuses.length} integration{view.statuses.length === 1 ? "" : "s"}
                {missing > 0 ? ` · ${missing} not connected` : ""}
                {reconnect > 0 ? ` · ${reconnect} reconnect needed` : ""}
              </p>
              {!declaredOnly &&
                view.statuses.some(
                  (status) =>
                    status.declaration.kind === "catalog" && !status.named_connections,
                ) && (
                  <IntegrationVendorGrid
                    vendors={view.statuses
                      .filter(
                        (status) =>
                          status.declaration.kind === "catalog" && !status.named_connections,
                      )
                      .map((status) => ({
                        name: status.name,
                        displayName: status.display_name ?? status.name,
                        connected: status.connected,
                        enabled: status.login.includes("browser"),
                      }))}
                    requestConnectorUrl="https://github.com/avalanche-run/avalanche/issues/new?title=Connector%20request"
                    renderVendor={(vendor) => {
                      const status = view.statuses.find((item) => item.name === vendor.name)!;
                      return (
                        <ul className="m-0 list-none p-0">
                          <IntegrationRow
                            status={status}
                            focused={status.name === focusName}
                            connecting={status.name === connecting}
                            onConnecting={(open) =>
                              setConnectingName(open ? status.name : undefined)
                            }
                          />
                        </ul>
                      );
                    }}
                  />
                )}
              <ul
                className={`m-0 list-none rounded border-line bg-panel p-0 ${declaredOnly || view.statuses.some((status) => status.declaration.kind !== "catalog" || status.named_connections) ? "border" : ""}`}
              >
                {view.statuses
                  .filter(
                    (status) =>
                      declaredOnly ||
                      status.declaration.kind !== "catalog" ||
                      status.named_connections,
                  )
                  .map((status) =>
                    renderGrant && status.declaration.kind === "catalog" ? (
                      <li
                        key={status.name}
                        className="integration-row border-b border-line p-4"
                        data-integration={status.name}
                        data-account={integrationAccountState(status)}
                      >
                        <strong className="text-xs text-ink">
                          {status.display_name ?? status.name}
                        </strong>
                        <span className="ml-3 text-xs text-secondary">
                          {ACCOUNT_LABELS[integrationAccountState(status)]}
                        </span>
                        {renderGrant(status)}
                      </li>
                    ) : (
                      <IntegrationRow
                        key={status.name}
                        status={status}
                        focused={status.name === focusName}
                        connecting={status.name === connecting}
                        onConnecting={(open) =>
                          setConnectingName(open ? status.name : undefined)
                        }
                      />
                    ),
                  )}
              </ul>
            </>
          )
        )}
      </div>
    </section>
  );
}
