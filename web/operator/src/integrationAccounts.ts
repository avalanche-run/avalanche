// The integrations a step declares and whether each has an account. The field
// names follow the operator's JSON documents (`StepIntegrations`,
// `IntegrationStatus`), so a host passes its documents through unchanged.
import { z } from "zod";

const apiKeyAuth = z.strictObject({
  kind: z.literal("api_key"),
  header: z.string(),
  prefix: z.string(),
});

const oauth2Auth = z.strictObject({
  kind: z.literal("oauth2"),
  authorize_url: z.string(),
  token_url: z.string(),
  scopes: z.array(z.string()),
  authorize_params: z.record(z.string(), z.string()),
  client_auth: z.enum(["basic", "post"]),
});

const integrationTool = z.strictObject({
  /** The name an agent calls the tool by: `<integration>__<method>`. */
  name: z.string(),
  method: z.string(),
  description: z.string(),
});

const connectorDeclaration = z.strictObject({
  kind: z.literal("connector"),
  name: z.string(),
  display_name: z.string().nullable(),
  icon: z.string().nullable(),
  base_url: z.string(),
  // An API key in a header, or a browser login (OAuth 2).
  auth: z.discriminatedUnion("kind", [apiKeyAuth, oauth2Auth]).nullable(),
  tools: z.array(integrationTool),
});

const mcpDeclaration = z.strictObject({
  kind: z.literal("mcp"),
  name: z.string(),
  display_name: z.string().nullable(),
  icon: z.string().nullable(),
  url: z.string().nullable(),
  command: z.array(z.string()).nullable(),
  allow: z.array(z.string()).nullable(),
  /** The client a browser login uses with a server that registers none itself. */
  client_id: z.string().nullable(),
});

const catalogDeclaration = z.strictObject({
  kind: z.literal("catalog"),
  name: z.string(),
  allow: z.array(z.string()).nullable(),
});

const integrationDeclaration = z.discriminatedUnion("kind", [
  connectorDeclaration,
  mcpDeclaration,
  catalogDeclaration,
]);

const accountState = z.enum(["connected", "missing", "none", "reconnect_needed"]);

const stepIntegrations = z.strictObject({ integrations: z.array(integrationDeclaration) });

const integrationStatus = z.strictObject({
  name: z.string(),
  declaration: integrationDeclaration,
  /** False for a Connector without auth and for an MCP server, which may need none. */
  requires_account: z.boolean(),
  /** The ways to connect it, the default first; empty when it takes no account. */
  login: z.array(z.enum(["api_key", "browser"])),
  connected: z.boolean(),
  // Older hosts omit these status fields; declarations still render there.
  display_name: z.string().nullable().optional(),
  icon_url: z.string().url().nullable().optional(),
  account_state: accountState.optional(),
  provider_error: z.string().nullable().optional(),
  /** Epoch seconds. */
  connected_at: z.number().nullable(),
  used_by: z.array(
    z.strictObject({ workflow_id: z.string(), workflow: z.string(), node_id: z.string() }),
  ),
});

const integrationStatusList = z.strictObject({ integrations: z.array(integrationStatus) });

const integrationLogin = z.strictObject({
  login_id: z.string(),
  name: z.string(),
  /** The consent page, to open in a browser on the host's machine. */
  authorization_url: z.string(),
  status: z.enum(["waiting", "connected", "failed"]),
  error: z.string().nullable(),
});

export type ConnectorDeclaration = z.infer<typeof connectorDeclaration>;
export type McpDeclaration = z.infer<typeof mcpDeclaration>;
export type IntegrationDeclaration = z.infer<typeof integrationDeclaration>;
export type IntegrationToolDeclaration = z.infer<typeof integrationTool>;
/** A declared integration and whether it has an account. It never holds a credential. */
export type IntegrationStatus = z.infer<typeof integrationStatus>;

/** One browser login and how far it is. It never holds a token, a code or a secret. */
export type IntegrationLogin = z.infer<typeof integrationLogin>;
export type IntegrationLoginKind = IntegrationStatus["login"][number];

/**
 * What a person supplies to start a browser login: the client ID and secret of
 * the app registered with a Connector's service, or the secret of the client an
 * MCP server's declaration names. Sent once and never read back.
 */
export interface IntegrationLoginClient {
  clientId?: string;
  clientSecret?: string;
}

/** What a person supplies to connect an account. It is sent once and never read back. */
export interface IntegrationCredential {
  kind: "api_key";
  key: string;
  /** Only for an MCP server, which does not declare the header its key goes in. */
  header?: string;
}

/** Parse one step's `StepIntegrations` document. */
export function parseStepIntegrations(raw: string): IntegrationDeclaration[] {
  return stepIntegrations.parse(JSON.parse(raw)).integrations;
}

/** Parse an `IntegrationStatusList` document. */
export function parseIntegrationStatusList(raw: string): IntegrationStatus[] {
  return integrationStatusList.parse(JSON.parse(raw)).integrations;
}

/** Parse one `IntegrationLogin` document. */
export function parseIntegrationLogin(raw: string): IntegrationLogin {
  return integrationLogin.parse(JSON.parse(raw));
}

/** Parse one `IntegrationStatus` document. */
export function parseIntegrationStatus(raw: string): IntegrationStatus {
  return integrationStatus.parse(JSON.parse(raw));
}

export function integrationLabel(
  declaration: IntegrationDeclaration,
  status?: IntegrationStatus,
): string {
  return (
    status?.display_name ??
    (declaration.kind === "catalog"
      ? declaration.name
      : (declaration.display_name ?? declaration.name))
  );
}

export function integrationKindLabel(declaration: IntegrationDeclaration): string {
  return declaration.kind === "catalog"
    ? "Built-in"
    : declaration.kind === "connector"
      ? "Connector"
      : "MCP";
}

/**
 * Whether a person can store an account for the integration. An MCP server
 * started as a local process takes none: it has no request to carry a key.
 */
export function integrationTakesAccount(declaration: IntegrationDeclaration): boolean {
  if (declaration.kind === "catalog") return true;
  return declaration.kind === "mcp" ? declaration.url !== null : declaration.auth !== null;
}

export type IntegrationAccountState = z.infer<typeof accountState>;

/** `none`: a Connector without auth or a local-process MCP server; there is no account to connect. */
export function integrationAccountState(status: IntegrationStatus): IntegrationAccountState {
  if (status.account_state !== undefined) return status.account_state;
  if (status.connected) return "connected";
  return integrationTakesAccount(status.declaration) ? "missing" : "none";
}

/**
 * The integration a tool call belongs to, among those its step declares.
 *
 * Integration tools are named `<integration>__<tool>`, with `-` in the
 * integration name written `_`. A Connector lists its tools; an MCP server's
 * tools are known only by that prefix.
 */
export function integrationForTool(
  toolName: string,
  declarations: readonly IntegrationDeclaration[],
): IntegrationDeclaration | undefined {
  return declarations.find((declaration) =>
    declaration.kind === "connector"
      ? declaration.tools.some((tool) => tool.name === toolName)
      : toolName.startsWith(`${declaration.name.replaceAll("-", "_")}__`),
  );
}

// The SDK's sentence for a step that started without an account, which every
// host raises: "... uses integration 'a' with no connected account. ..."
const USES = /uses integrations? /;
const NOT_CONNECTED = "with no connected account";
// Its sentence for a browser login that expired or was revoked:
// "... the login of integration 'a' is no longer valid ..."
const LOGIN_GONE = /the login of integration '([^']+)' is no longer valid/;

/**
 * Integrations whose current account blocks the step. Structured account state
 * wins over historical errors; error text is a fallback for older hosts and
 * for optional MCP accounts whose requirement was learned during a run.
 */
export function integrationsMissingAccount(
  error: string | undefined,
  declarations: readonly IntegrationDeclaration[],
  statuses: readonly IntegrationStatus[] = [],
): IntegrationDeclaration[] {
  const known = new Map(statuses.map((status) => [status.name, status]));
  const inferred = legacyMissingAccount(error, declarations);
  return declarations.filter((declaration) => {
    const status = known.get(declaration.name);
    if (status?.account_state === undefined) return inferred.includes(declaration);
    return (
      status.account_state === "reconnect_needed" ||
      (status.account_state === "missing" &&
        (status.requires_account || inferred.includes(declaration)))
    );
  });
}

function legacyMissingAccount(
  error: string | undefined,
  declarations: readonly IntegrationDeclaration[],
): IntegrationDeclaration[] {
  const gone = error?.match(LOGIN_GONE)?.[1];
  if (gone !== undefined) {
    return declarations.filter((declaration) => declaration.name === gone);
  }
  const end = error?.indexOf(NOT_CONNECTED) ?? -1;
  const start = error?.slice(0, Math.max(end, 0)).search(USES) ?? -1;
  if (error === undefined || end < 0 || start < 0) return [];
  const named = error.slice(start, end);
  return declarations.filter((declaration) => named.includes(`'${declaration.name}'`));
}
