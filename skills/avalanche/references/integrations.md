# Integrations

An integration is an outside service a step uses. A step lists its integrations
with `integrations=[...]`, and each integration's account (an API key, or a
login done in the browser) is connected on the host that runs the workflow,
never written in code.

There are three kinds:

- A **Connector** is a Python class you write for an HTTP API. Its tools are
  methods that call the API through a client that is already logged in.
- An **MCP server** is declared with `ava.MCP(...)`. An agent step that
  declares it gets the tools the server offers.

## Use a built-in

```python
import avalanche as ava
from avalanche.catalog import asana

@ava.agent_step(Research, integrations=[asana])
async def research(topic: str, *, agent: ava.Agent) -> list[str]:
    return (await agent(topic=topic)).outputs.findings
```

`ava.catalog.asana` is the same import. `ava.Catalog("asana", allow=["search"])`
selects tools explicitly, or names an entry newer than the installed import list.
Built-ins are for agent steps only. Display names and image addresses come from
an installed provider, not workflow code. Importing or describing this workflow
loads no provider and contacts no service. Connect and run it under the local
operator, after installing and signing in to a provider package:

```sh
ava connect asana                  # open its login and wait for Connected
ava connect asana --no-browser     # print the address instead
ava connect --json                 # declared entries and account states
ava disconnect asana               # also works for Connectors and custom MCP
```

With no provider installed, setup guidance names Avalanche Cloud (Trampoline
Delta) as an example: `uv tool install trampoline-delta`. Then sign in and use
`ava connect <name>`. This is setup text only: the SDK does not import Delta or
select a provider for you. Connecting an account does not promise that a provider
supports tool execution on every host.

A provider package registers a zero-argument factory in the Python entry-point
group `avalanche.integration_providers`. With more than one installed, set
`AVALANCHE_INTEGRATION_PROVIDER` to the entry name; the SDK never silently picks
one. Implement the public `IntegrationProvider` protocol from
`avalanche.integrations.providers`: `entries()`, `connections()`,
`start_login(name)`, `login_status(login_id)`, `disconnect(name)` and
`open_tool_session(step, names)`. These synchronous methods return the typed
catalog models from the same module. The provider owns persistent connections
and sign-in guidance. No provider implementation ships in the SDK.

The SDK opens one tool session per step start for a legacy provider, or one
bound session per declared connection for a named provider. Named catalog
declarations require a named-capable local operator, not standalone `Workflow.run()`.
It reuses MCP tool naming, allow selection, argument checks and teardown, and
keeps each temporary bearer pass
in memory only. An expired pass fails the next call as retryable unavailable;
it is never renewed in place. Missing connections and revoked logins fail
before the body without retries. Missing provider, provider not signed in,
missing integration and unreachable provider have separate messages. Custom
Connectors and MCP servers need no catalog provider.

The checked-in `catalog_entries.json` drives `scripts/generate_catalog.py`.
Regenerate offline with `uv run python scripts/generate_catalog.py`; the
release gate uses `--check` to reject stale imports.

## Named built-in connections

A provider implementing `NamedIntegrationProvider` can keep several accounts for
one built-in. With that provider installed and a workflow declaring Slack:

```sh
ava connect slack --slug acme-hq
ava connect slack --slug partner-ws
ava connect slack --slug my-slack --per-user
ava connect
ava connect --json
ava disconnect slack --slug partner-ws
```

`--slug` defaults to the integration name. A slug uses lowercase letters and
digits joined by single hyphens, is unique across the operator's catalog
connections, and cannot be renamed. Reconnecting the same slug and kind keeps
its identity and assignment. Its integration and kind cannot be changed.
Disconnecting a slug removes the connection, all its accounts and its operator
assignment; it does not remove other connections of that integration.
`--slug` and `--per-user` are for built-ins, not Connectors or custom MCP servers.
A legacy provider refuses these options and named run admission with
`This provider does not support named connections.`

Name both connections on a step to use two accounts:

```python
@ava.agent_step(Research, integrations=[
    ava.catalog.slack(connection="acme-hq"),
    ava.catalog.slack(connection="partner-ws"),
])
async def research(topic: str, *, agent: ava.Agent) -> list[str]:
    return (await agent(topic=topic)).outputs.findings
```

`ava.Catalog("slack", connection="acme-hq", allow=["get-team-info"])` is the
explicit form. A named declaration gets tools such as `acme_hq__get_team_info`:
replace slug hyphens with underscores, then append `__` and the tool name.
Without `connection=`, tools keep names such as `slack__get_team_info`.
Two declarations of the same integration on a step must both name different
slugs. Mixing named and unnamed declarations is refused; prefix collisions
are refused too. `allow` still takes the provider's original tool names.

Every local connection is assigned to the operator; there are no local project
or flow assignment editors. The CLI's text and JSON lists and the UI's
**Integrations** connection tables show slug, shared/per-user kind, the starting
human's state and operator assignment. JSON and the UI also show instance counts. Each named step declaration has its
own slug-labelled badge, account state and tool attribution. A refused personal
run offers **Connect account**, which opens the connection view. **New connection** asks
for slug and kind; a connection's **Reconnect** (or **Connect your account**)
keeps them fixed. **Disconnect** names the assignment it removes before asking
for confirmation. The provider persists catalog records; the operator persists
run history in SQLite. Both remain available after an operator restart.

With a named provider, resolution happens before a run is created, with the
same pure resolver exported as `avalanche.integrations.resolve`. An unnamed
built-in binds only when exactly one connection of that integration is
assigned; more than one is ambiguous. A missing named connection, missing
account or revoked account refuses admission. Step start checks again as a
backstop. Legacy providers keep their single-account, step-start behavior.

A shared connection uses its shared account. A per-user connection uses only
the provider's starting human (its signed-in user or local OS user). Caller
input/context and an API token's owner are never identity fallbacks. For flow
`personal` and slug `my-slack`, a missing human account is refused verbatim:

```text
Connect your slack account ('my-slack') to run flow 'personal', then start the run again.
```

A per-user login without a starting human is refused verbatim:

```text
A per-user connection requires a starting human. Sign in, then connect again.
```

A scheduled flow cannot bind a per-user connection, even if the current human
is connected. Registration, enabling and run start refuse it. The shared
resolver also refuses webhook-triggered resolution. For flow `scheduled` and
slug `schedule-slot`, the message is:

```text
Flow 'scheduled' has a schedule or webhook, and its slack connection 'schedule-slot' is per user. A triggered run has no user. Assign a shared connection.
```

The public refusal classes are available from `avalanche` and
`avalanche.integrations`: `ConnectionNotResolvedError`,
`ConnectionAmbiguousError`, `ConnectionNotAssignedError`,
`ConnectionNotFoundError`, `ConnectionPerUserTriggeredError` and
`ConnectionUserNotConnectedError`. All are `NonRetryableError` subclasses.

Providers opt in by registering their factory in both
`avalanche.integration_providers` and `avalanche.named_integration_providers`,
under the same entry name. Implement `NamedIntegrationProvider` from
`avalanche.integrations.providers`: `starting_user()`, `named_connections()`,
`start_login(name, *, slug=None, kind="shared")` and
`open_bound_tool_session(step, bindings)`, as well as the original protocol.
`disconnect(slug)` removes the whole named connection. Named records include
missing per-user slots and provider-owned instance IDs, never credentials.
The provider must enforce immutable, operator-wide unique slugs atomically,
persist connections and instances, and keep their IDs on reauthorization.
Session bindings select instance IDs, not user input. No provider implementation
ships in Avalanche; provider-specific hosted support is separate.

## Write a Connector

```python
import avalanche as ava
from pydantic import BaseModel


class Customer(BaseModel):
    email: str
    plan: str


class AcmeCrm(ava.Connector):
    name = "acme-crm"
    display_name = "Acme CRM"
    icon = "assets/acme.svg"
    auth = ava.ApiKey(header="X-Api-Key")
    base_url = "https://api.acme.com"

    @ava.tool
    def find_customer(self, email: str) -> Customer:
        """Find the customer with this email address."""
        response = self.client.get("/customers", params={"email": email})
        response.raise_for_status()
        return Customer.model_validate(response.json())


crm = AcmeCrm()
```

| Attribute | Meaning |
| --- | --- |
| `name` | Required. Lowercase letters and digits joined by single `-` or `_`. It names the integration and its account. |
| `display_name`, `icon` | Optional label and the path of an image file in the project. |
| `base_url` | Required. Paths passed to `self.client` are relative to it. |
| `auth` | `ava.ApiKey(header=...)`, `ava.OAuth2(...)` for a [browser login](#log-in-through-the-browser), or `None` for a service that needs no account. `ava.ApiKey(header="Authorization", prefix="Bearer ")` sends the key after a prefix. |

`self.client` is an `ava.Client`, an [`httpx.Client`](https://www.python-httpx.org/)
bound to `base_url` that sends the account's key in the declared header. It
calls that origin only: an absolute URL, or a redirect, to another scheme, host
or port raises `IntegrationError` before anything is sent, so the key cannot
reach another service even when an agent chooses the path. Redirects are not
followed unless a call asks for it, and proxy environment variables are not
applied. The client exists only while a step that declares the Connector is
running; anywhere else, reading it raises `IntegrationError`.

A method marked `@ava.tool` is a tool. Its docstring is the description an agent
sees, and its parameters need type annotations.

Building a Connector, importing the workflow and listing integrations call no
network and read no account.

## Use it in steps

A plain step calls the tools as methods:

```python
@ava.step(integrations=[crm])
def plan_of(email: str) -> str:
    return crm.find_customer(email).plan
```

An agent step offers them to its agent. On every runtime (PredictRLM,
ClaudeAgent, OpenAIAgent) a tool is named `<integration>__<tool>`, with `-` in
the integration name written as `_`: here `acme_crm__find_customer`. The call
appears in the run's trace like any tool call, and a returned model reaches the
agent as JSON data.

```python
@ava.agent_step(Triage, integrations=[crm], tools=[format_ticket])
async def triage(ticket: str, *, agent: ava.Agent) -> str:
    return (await agent(ticket=ticket)).outputs.answer
```

`tools=` and `skills=` work next to `integrations=`. The agent gets callable
tools only: it never receives the Connector, its client or the key.

These mistakes are errors when the workflow is defined, before anything runs:

- two integrations on one step with the same name (except built-ins naming distinct
  connections), or names that differ only by
  `-` and `_`;
- an integration tool named like one of the step's own `tools=`;
- one name (and, for a built-in, the same connection) declared in two different
  ways in one workflow;
- an `ava.MCP` on a plain step (MCP tools are for agent steps only).

What an MCP server offers is known only when a step connects to it, so a clash
with one of its tool names is an error when that step starts.

### Two accounts for one Connector

Each instance is one account, named by its name. Pass a name to make a second:

```python
crm = AcmeCrm()                   # account "acme-crm"
crm_eu = AcmeCrm("acme-crm-eu")   # a separate account

@ava.step(integrations=[crm, crm_eu])
def plans(email: str) -> tuple[str, str]:
    return crm.find_customer(email).plan, crm_eu.find_customer(email).plan
```

## Connect an account

Connector and custom MCP accounts live in the local operator, one per integration
name. Start the operator (`ava operator` or `ava dev`), then:

```bash
ava connect             # list declared integrations and whether each is connected
ava connect acme-crm    # asks for the API key and stores it
```

`ava connect NAME` reads the key from a hidden prompt, or from standard input
when that is not a terminal (`ava connect acme-crm < key.txt`). The key is never
a command-line argument. Connecting again replaces the account. Only a name
that a discovered workflow declares can be connected.

When a step starts, Avalanche looks up the account of each integration it
declares. If one is missing, the step fails before its code runs, is not
retried, and its error names the command to run:

```text
step 'plan_of' uses integration 'acme-crm' with no connected account.
Connect with `ava connect acme-crm`, then start the run again.
```

An account connected while a run is in progress is used by the steps of that
run that start afterwards.

Accounts are kept in the operator's state database and survive a restart. That
file is local and not encrypted, like the rest of the operator's state: keep it
on a local disk and out of version control. `ava state clear` does not remove
accounts.

## Log in through the browser

Some services give no API key: a person logs in on the service's own page and
agrees to give access (OAuth 2). `ava connect` does that login in your browser
and the operator stores the tokens it returns.

```bash
ava connect acme-calendar   # a Connector that declares ava.OAuth2(...)
ava connect kb              # a remote MCP server: the login the server describes
```

The command prints the address of the service's consent page and opens it in
your browser. After you agree, the service sends the browser back to the
operator at `http://127.0.0.1:7436/callback`, the operator exchanges the
result for tokens and stores them, and the command prints `Connected`. The
browser page says whether it worked. Connecting again replaces the account.

### A Connector

Declare the login on the class. The client ID and secret are not in code:

```python
class AcmeCalendar(ava.Connector):
    name = "acme-calendar"
    base_url = "https://api.acme.com"
    auth = ava.OAuth2(
        authorize_url="https://acme.com/oauth/authorize",
        token_url="https://acme.com/oauth/token",
        scopes=["calendar.read"],
    )

    @ava.tool
    def next_event(self) -> str:
        """The title of the next event."""
        return self.client.get("/events/next").json()["title"]
```

`self.client` then sends the access token as `Authorization: Bearer`.

| Argument | Meaning |
| --- | --- |
| `authorize_url` | The service's consent page. |
| `token_url` | Where the service exchanges a code, and later a refresh token, for tokens. |
| `scopes` | What the login asks for. |
| `authorize_params` | Extra query parameters for the consent page. Some services hand out a refresh token only when asked: Google wants `{"access_type": "offline", "prompt": "consent"}`. |
| `client_auth` | Where the service reads the client secret in a token request: `"basic"` (the default, the `Authorization` header) or `"post"` (the form body). |

Before connecting, register an app with the service (often called an "OAuth
app" or "OAuth client") and note what it gives you:

| What the service asks or gives | What to use |
| --- | --- |
| Redirect URI (callback URL) | `http://127.0.0.1:7436/callback`, exactly. With `--login-port N` on the operator, the same address with port `N`. |
| App type | "Desktop", "native" or "installed" when the service asks. A "web" app also works when it accepts the redirect URI above. |
| Scopes | The ones in `scopes=[...]`. |
| Client ID and client secret | Typed into `ava connect`, never written in code. A public app has no secret: leave it empty. |

`ava connect acme-calendar` asks for the client ID, then the client secret
without showing it. When standard input is not a terminal it reads them as two
lines. They are stored with the tokens, because a refresh needs them.

### An MCP server

Nothing is declared: the login is discovered from the server, as the MCP
standard defines. `ava connect kb` asks the server where its login service is,
registers Avalanche as a client there, and opens the consent page.

A login service that does not let a client register itself answers the
registration with an error, and `ava connect` says so. Register a client with
the service yourself, with the redirect URI above, and name it in the
declaration:

```python
kb = ava.MCP("kb", url="https://mcp.acme.com", client_id="your-client-id")
```

If that client has a secret, `ava connect kb --client-secret` asks for it.

A server that takes a plain key instead is connected with
`ava connect kb --api-key`; see [accounts for MCP servers](#accounts-for-mcp-servers).

### Expired and revoked logins

An access token lives for a limited time. When a step starts, the operator
checks the stored login. If the access token has expired, or will within a
minute, the operator asks the service for a new one with the refresh token and
stores the new tokens, before the step gets its account. A step that runs
longer than its token gets a fresh one the same way. Nobody has to do anything.

When the login cannot be renewed (the service refuses the refresh token, the
login was revoked, or the service gave no refresh token and the access token
expired), the step fails before its code runs, is not retried, and says to
connect again:

```text
step 'next_event': the login of integration 'acme-calendar' is no longer valid
(the service refused to renew it: invalid_grant). Connect it again with
`ava connect acme-calendar`, then start the run again.
```

A login revoked at the service while its access token still looks valid is
found out by the service's answer. An MCP server answers when the step
connects to it, so the step fails before its code runs. A Connector's service
answers the step's first call with HTTP 401: that call raises
`IntegrationReconnectError` with the same message and the step is not
retried. A service that cannot be reached for a refresh is a different case:
the step fails with `AccountUnavailableError` and its retries apply.

### The listener and what it accepts

The operator listens for the browser's return only while a login is waiting,
on `127.0.0.1` only, and only on the port given by `--login-port` (7436 by
default). It accepts a return only for a login it started: each login carries a
random `state` value, and a request with another one is refused. Each login
also uses PKCE, so a code is useless without a value only the operator holds.
A login nobody finishes ends after 10 minutes.

The browser must run on the machine the operator runs on, because the service
sends it to `127.0.0.1`. For an operator on another machine, forward the port
first (`ssh -L 7436:127.0.0.1:7436 that-machine`), then open the address
`ava connect` prints. `ava connect NAME --no-browser` only prints the address.

### What is stored and where it can appear

The operator stores the access token, the refresh token, the client ID and
secret, and the token address, in the same account table as API keys. A step
is handed the access token and its expiry only; the refresh token and the
client secret never leave the operator's process. The tokens, the client
secret and the one-time code are not printed by `ava connect`, not shown by the
UI, and not put in logs, traces, prompts or tool arguments. The consent page's
address, which `ava connect` prints, holds no secret.

### In the operator UI

The operator's browser UI shows the same accounts:

- Each step's card carries a badge per integration it declares, with its icon
  and display name. A step whose required account is missing has its badge
  marked.
- A step's details list its integrations, their kind (Connector or MCP), and a
  Connector's tools.
- **Integrations**, at the bottom of the sidebar, lists every integration the
  loaded workflows declare, each connected or not. **Connect** asks for the API
  key and stores it, or starts a [browser login](#log-in-through-the-browser):
  for a Connector that declares one it asks for the app's client ID and secret,
  then opens the consent page in a new tab; a remote MCP server offers both
  the browser login and a key. **Disconnect** removes the account, and steps that use it
  then fail from their next start until it is connected again. The list
  follows the store: an account connected with `ava connect` shows up without
  a reload. An MCP server started with `command=` takes no account, so it has
  no **Connect**.
- A step that failed for a missing account says so in its details, with a link
  that opens the connect form. Tool calls in an agent's trace name the
  integration they belong to.

The UI never shows a stored key, in full or in part: it shows only whether an
account exists and since when. Opening the UI and listing integrations starts
no tool process and makes no tool call; provider metadata and account states may
be fetched from the provider.

`icon` is a path inside the folder the operator scans (the folder you gave
`ava operator`, or the folder of the file you gave it). The file must be an
SVG, PNG, JPEG, WebP or GIF image of at most 256 KiB. It is drawn as an image
and never inserted as markup, so an SVG icon cannot run script in the UI. An
integration with no icon, or whose file cannot be served, gets a generic one. A
run shows the integrations its steps declare now, not at the time it ran.

## Where the key can and cannot appear

The key is sent to the service in the declared header and stored in the account
table. It is not put in prompts, tool arguments, traces, logs, step previews or
the workflow definition, and `repr()` of a stored credential leaves it out. Your
own Connector code can still leak it, for example by returning or logging the
headers of a request it sent; do not.

## Declare an MCP server

```python
kb = ava.MCP("kb", url="https://mcp.acme.com", allow=["search"])
files = ava.MCP("local-files", command=["uvx", "acme-files-mcp"])

@ava.agent_step(Triage, integrations=[crm, kb, files])
async def triage(ticket: str, *, agent: ava.Agent) -> str: ...
```

An `ava.MCP` takes a `name` and exactly one of `url` (a remote server, reached
over Streamable HTTP) or `command` (a local process, reached over its standard
input and output), plus optional `allow`, `display_name` and `icon`. MCP tools
are for agent steps: the agent of a step that declares the server can call its
tools on PredictRLM, ClaudeAgent and OpenAIAgent alike.

Declaring, importing and listing a workflow starts no process and calls no
server. When the step starts, before its own code runs, Avalanche:

1. connects to each declared server: an HTTP connection for `url`, a started
   process for `command`;
2. asks the server for its tools and keeps those in `allow`, or all of them
   without `allow`;
3. offers each one to the agent as `<integration>__<tool>`, with the server's
   description and argument schema. Characters other than letters, digits and
   `_` in either name are written `_`: tool `list-pages` of server `acme-kb` is
   `acme_kb__list_pages`.

A call from the agent is checked against the tool's argument schema, sent to
the server, and its result returned to the model. Calls and failures are in
the run trace like any other tool call. The connection lives for the step.
When the step ends, whether it succeeded, failed or was cancelled, the
connection is closed and the process stopped; a local server is also stopped
if the run's own process is killed.

### Accounts for MCP servers

A remote server that wants a login is connected with `ava connect kb`, a
[browser login](#an-mcp-server). One that takes a key instead gets it from the
account stored for its name:

```bash
ava connect kb --api-key             # sent as "Authorization: Bearer <key>"
ava connect kb --header X-Api-Key    # sent as it is in that header
```

A server needs no account unless it asks for one. When a remote server answers
that it wants a login (HTTP 401) and no account is stored, the step fails
before its code runs, without retries, and names `ava connect kb`. The key is
or the access token is sent only to the server's own origin, and never appears
in prompts, tool arguments, traces or logs.

A local process has no request header to carry a key, so `ava connect` refuses
one. It runs with the environment of the process that runs the step, the
operator's under `ava operator` and `ava dev`: give it credentials there.

### When a server cannot be used

| What happens | Result |
| --- | --- |
| A remote server cannot be reached, or does not answer within 30 seconds | The step fails with `MCPServerUnavailableError`, naming the server and the reason. The step's retries apply. |
| The `command` is not found, or the process stops or does not speak MCP | The step fails with `IntegrationError` and the end of the process's error output. Not retried. |
| `allow` names a tool the server does not offer | The step fails with `IntegrationError` listing the tools the server offers. Not retried. |
| Two tools would get the same name, among the server's or with the step's `tools=` | The step fails with `IntegrationError`. Not retried. |
| The model passes arguments the tool's schema refuses | The call fails, and the model reads which argument is wrong and the schema. |
| The tool fails, the server stops, or it does not answer a call within 5 minutes | The call fails, and the model reads the reason. The step goes on. |

Text results and structured results reach the model. Other content, such as
images, is named in the result but not passed on.

ClaudeAgent's own `mcp_servers` option still works and is separate: those
servers are passed to the Claude CLI as they are, for that runtime only, with
no account and no `allow`.

## Run workflows from your own program

`Workflow.run()` outside an operator has no account store. Give it one for the
runs it starts:

```python
import os

from avalanche.integrations import ApiKeyCredential, StaticAccounts, use_accounts

accounts = StaticAccounts({"acme-crm": ApiKeyCredential(key=os.environ["ACME_KEY"])})
with use_accounts(accounts):
    result = flow().run(executor=ava.LocalExecutor()).result()
```

A host that stores accounts itself implements `AccountSource`: one method that
returns the stored accounts among the names a step asks for. For a browser
login it returns an `AccessTokenCredential` that is valid now. The login and
the refresh rule are in `avalanche.integrations.oauth` for every host:
`connector_login` and `mcp_login` take a person through the consent page and
return the `OAuthCredential` to store, and `fresh` returns that credential
with a renewed access token when the old one has expired. A source raises
`AccountLoginExpiredError` for a login that cannot be renewed.
