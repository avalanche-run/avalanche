import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { OperatorApi } from "./api";
import {
  parseStepIntegrations,
  type IntegrationCredential,
  type IntegrationDeclaration,
  type IntegrationLoginClient,
  type IntegrationStatus,
} from "./integrationAccounts";
import type { FlowInfoMsg } from "./model";

/**
 * The host calls the integration views use. A host page that shows only the
 * integrations screen implements these and nothing else of `OperatorApi`.
 */
export type IntegrationsApi = Pick<
  OperatorApi,
  | "listIntegrations"
  | "connectIntegration"
  | "startIntegrationLogin"
  | "getIntegrationLogin"
  | "disconnectIntegration"
  | "getIntegrationIconUrl"
>;

/** What the integration views read: the host's list, its icons and its account calls. */
export interface IntegrationsView {
  /** False when the host lists no integrations; every view then stays hidden. */
  supported: boolean;
  /** False until the first list arrives. */
  loaded: boolean;
  statuses: readonly IntegrationStatus[];
  /** Why the last list failed; the previous list stays shown. */
  error?: string;
  status(name: string): IntegrationStatus | undefined;
  /** The declared icon as an image URL, once the host has served it. */
  iconUrl(name: string): string | undefined;
  /** Absent when the host cannot store accounts. */
  connect?: (name: string, credential: IntegrationCredential) => Promise<void>;
  /**
   * Log in through the browser. `onPage` receives the consent page to open; the
   * promise settles when the login ends. Absent when the host has no browser login.
   */
  login?: (
    name: string,
    client: IntegrationLoginClient,
    onPage: (url: string) => void,
    signal: AbortSignal,
  ) => Promise<void>;
  disconnect?: (name: string) => Promise<void>;
  /** Shows the integrations screen, on one integration when named. */
  openIntegrations?: (name?: string) => void;
}

const UNSUPPORTED: IntegrationsView = {
  supported: false,
  loaded: false,
  statuses: [],
  status: () => undefined,
  iconUrl: () => undefined,
};

const IntegrationsContext = createContext<IntegrationsView>(UNSUPPORTED);

export function useIntegrationsView(): IntegrationsView {
  return useContext(IntegrationsContext);
}

// A host may replace its `api` object while it still serves the same
// integrations (the Delta console builds a new one whenever a run changes). The
// last list and icons stay shown until the new object's own arrive, so badges
// and rows do not blink; `api` records which object the list came from.
interface Listed {
  api: IntegrationsApi;
  statuses?: IntegrationStatus[];
  error?: string;
}

function iconKey(status: IntegrationStatus): string {
  return `${status.name}\u0000${status.declaration.kind === "catalog" ? status.icon_url : status.declaration.icon}`;
}

function replaceStatus(listed: Listed | undefined, status: IntegrationStatus) {
  if (!listed?.statuses) return listed;
  return {
    api: listed.api,
    statuses: listed.statuses.map((item) => (item.name === status.name ? status : item)),
  };
}

interface IntegrationsProviderProps {
  api: IntegrationsApi;
  /** The catalog the list was read for; a new catalog lists again. */
  catalog: unknown;
  /** The last account change seen on the update stream; a new one lists again. */
  accountsEventUlid?: string;
  openIntegrations?: (name?: string) => void;
  children: ReactNode;
}

/**
 * Lists the host's integrations and keeps the list current: it reads again when
 * the catalog changes and when the host reports a connected or removed account.
 */
export function IntegrationsProvider({
  api,
  catalog,
  accountsEventUlid,
  openIntegrations,
  children,
}: IntegrationsProviderProps) {
  const [listed, setListed] = useState<Listed>();
  const [icons, setIcons] = useState<Record<string, string>>({});
  const requestedIcons = useRef<{ api: IntegrationsApi; keys: Set<string> }>(undefined);

  useEffect(() => {
    if (!api.listIntegrations) return;
    const controller = new AbortController();
    api.listIntegrations(controller.signal).then(
      (statuses) => {
        if (!controller.signal.aborted) setListed({ api, statuses });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setListed((current) => ({
          api,
          statuses: current?.statuses,
          error: error instanceof Error ? error.message : "Integrations unavailable",
        }));
      },
    );
    return () => controller.abort();
  }, [api, catalog, accountsEventUlid]);

  const statuses = listed?.statuses;
  useEffect(() => {
    const load = api.getIntegrationIconUrl?.bind(api);
    if (!load || !statuses) return;
    if (requestedIcons.current?.api !== api) requestedIcons.current = { api, keys: new Set() };
    const requested = requestedIcons.current.keys;
    for (const status of statuses) {
      if (status.declaration.kind === "catalog" || status.declaration.icon === null) continue;
      const key = iconKey(status);
      if (requested.has(key)) continue;
      requested.add(key);
      load(status.name).then(
        (url) => setIcons((current) => ({ ...current, [key]: url })),
        // No servable icon file: the integration keeps the generic icon.
        () => undefined,
      );
    }
  }, [api, statuses]);

  const connect = useCallback(
    async (name: string, credential: IntegrationCredential) => {
      if (!api.connectIntegration) throw new Error("This host cannot connect accounts");
      const status = await api.connectIntegration(name, credential);
      setListed((current) => replaceStatus(current, status));
    },
    [api],
  );
  const login = useCallback(
    async (
      name: string,
      client: IntegrationLoginClient,
      onPage: (url: string) => void,
      signal: AbortSignal,
    ) => {
      if (!api.startIntegrationLogin || !api.getIntegrationLogin) {
        throw new Error("This host has no browser login");
      }
      let current = await api.startIntegrationLogin(name, client);
      if (signal.aborted) return;
      onPage(current.authorization_url);
      while (current.status === "waiting" && !signal.aborted) {
        current = await api.getIntegrationLogin(current.login_id, signal);
      }
      if (current.status === "failed") throw new Error(current.error ?? "The login failed");
      if (current.status === "connected" && api.listIntegrations) {
        const statuses = await api.listIntegrations(signal);
        if (!signal.aborted) setListed({ api, statuses });
      }
    },
    [api],
  );
  const disconnect = useCallback(
    async (name: string) => {
      if (!api.disconnectIntegration) throw new Error("This host cannot remove accounts");
      const status = await api.disconnectIntegration(name);
      setListed((current) => replaceStatus(current, status));
    },
    [api],
  );

  const error = listed?.api === api ? listed.error : undefined;
  const view = useMemo<IntegrationsView>(() => {
    if (!api.listIntegrations) return UNSUPPORTED;
    const byName = new Map((statuses ?? []).map((status) => [status.name, status]));
    return {
      supported: true,
      loaded: statuses !== undefined,
      statuses: statuses ?? [],
      error,
      status: (name) => byName.get(name),
      iconUrl: (name) => {
        const status = byName.get(name);
        return status?.declaration.kind === "catalog"
          ? (status.icon_url ?? undefined)
          : status
            ? icons[iconKey(status)]
            : undefined;
      },
      connect: api.connectIntegration ? connect : undefined,
      login: api.startIntegrationLogin && api.getIntegrationLogin ? login : undefined,
      disconnect: api.disconnectIntegration ? disconnect : undefined,
      openIntegrations,
    };
  }, [api, connect, disconnect, error, icons, login, openIntegrations, statuses]);

  return <IntegrationsContext.Provider value={view}>{children}</IntegrationsContext.Provider>;
}

export type StepIntegrationsResult =
  { declarations: IntegrationDeclaration[] } | { error: string };

/** A map item (`step[3]`) declares what its step declares. */
function declaringNodeId(nodeId: string): string {
  return nodeId.replace(/\[\d+\]$/, "");
}

/** The integrations one step of the workflow declares; undefined when it declares none. */
export function stepIntegrations(
  workflow: FlowInfoMsg | undefined,
  nodeId: string | undefined,
): StepIntegrationsResult | undefined {
  if (!workflow || nodeId === undefined) return undefined;
  const raw = workflow.integrationDeclarationsJson[declaringNodeId(nodeId)];
  if (raw === undefined) return undefined;
  try {
    return { declarations: parseStepIntegrations(raw) };
  } catch {
    return { error: "Integration declaration unavailable" };
  }
}

const StepIntegrationsContext = createContext<readonly IntegrationDeclaration[]>([]);

/** The integrations of the step being inspected, for the views inside its inspector. */
export const StepIntegrationsProvider = StepIntegrationsContext.Provider;

export function useStepIntegrations(): readonly IntegrationDeclaration[] {
  return useContext(StepIntegrationsContext);
}
