# Integrations

An integration is an outside service a step declares it uses, with `integrations=[...]`. Its
account (API key or login) is stored by the host, never written in code, and is looked up when
the step starts.

| Kind | What it is | Works in | Availability |
|---|---|---|---|
| `ava.Connector` subclass | Your own description of an HTTP API: base URL, auth, tool methods | plain steps and agent steps | everyone |
| `ava.MCP` | An MCP server, remote (`url`) or a local process (`command`) | agent steps only | everyone |
| `ava.catalog.<name>` (`ava.Catalog`) | A built-in integration hosted by a provider, optionally bound to a named connection | agent steps only | **not available on every account yet** |

## When to use it

- A step or an agent must call an authenticated API: write a `Connector`.
- The service already has an MCP server: declare it with `ava.MCP` on the agent step.
- Slack, Notion, GitHub and the other built-ins, with no connector code: `ava.catalog.<name>`,
  if the account has built-in integrations.
- A public API with no auth called from one plain step: plain `httpx`/`requests` in the step is
  enough. A `Connector` with `auth = None` is worth it only to give agents typed tools.
- A local Python function as an agent tool, with no service or account behind it: `tools=[...]`,
  see [tools and skills](../building-blocks/tools-and-skills.md).
- Never read API keys from environment variables or input fields in workflow code when an
  integration can hold the account.

## Interface

### Connector

```python
class AcmeCrm(ava.Connector):
    name = "acme-crm"
    base_url = "https://api.acme.example"
    auth = ava.ApiKey(header="X-Api-Key")

    @ava.tool
    def find_customer(self, email: str) -> Customer:
        """Find one customer by email."""
        ...
```

| Class attribute | Type | Default | Meaning |
|---|---|---|---|
| `name` | `str` | required (or pass it to the constructor) | The integration's name and the name of its account. Lowercase letters and digits joined by single `-` or `_`. |
| `base_url` | `str` | required | `http(s)://` origin of the API. The client can call only this origin. |
| `auth` | `ava.ApiKey`, `ava.OAuth2` or `None` | `None` | How requests are authenticated. `None`: no account. |
| `display_name` | `str` | `None` | Label shown instead of the name. |
| `icon` | `str` | `None` | Path of an image file in the project. |

Constructor: `AcmeCrm(name=None, *, display_name=None, icon=None)`. Each instance is one account:
`AcmeCrm()` uses the class's `name`; `AcmeCrm("acme-crm-eu")` is a second, separate account.

| Member | Meaning |
|---|---|
| `@ava.tool` on a method | Marks the method as a tool. Its docstring is the tool description; its parameters need type annotations. |
| `self.client` | An `ava.Client` for this account. Only inside a running step that declares the connector. |
| `connector.tool_names()` | `{"acme_crm__find_customer": "find_customer"}`: agent-facing name to method. |

`ava.Client` is an `httpx.Client` bound to `base_url`: use `self.client.get("/path", params=...)`,
`.post("/path", json=...)`, `.raise_for_status()`, `.json()`. The key or token is attached as
each request leaves. Redirects are not followed.

### Auth types

`ava.ApiKey(header, prefix="")`: an API key sent in one header.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `header` | `str` | required | Header name, for example `"X-Api-Key"` or `"Authorization"`. |
| `prefix` | `str` | `""` | Text before the key, for example `"Bearer "`. |

`ava.OAuth2(*, authorize_url, token_url, scopes=(), authorize_params=None, client_auth="basic")`:
a browser login with the OAuth 2 authorization code flow.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `authorize_url` | `str` | required | The service's consent page. |
| `token_url` | `str` | required | The service's token address. |
| `scopes` | list of `str` | `()` | Scopes to ask for. |
| `authorize_params` | `dict[str, str]` | `{}` | Extra query parameters for the consent page, for example `{"access_type": "offline", "prompt": "consent"}` to get a refresh token. |
| `client_auth` | `"basic"` or `"post"` | `"basic"` | Where the client secret goes in token requests: the Authorization header or the form body. |

Requests carry `Authorization: Bearer <access token>`; an expired token is refreshed
automatically. The app's client ID and secret are asked for by `ava connect`, not written in code.

### MCP server

```python
ava.MCP(name, *, url=None, command=None, allow=None, client_id=None, display_name=None, icon=None)
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `name` | `str` | required | Integration name; also the tool prefix. |
| `url` | `str` | `None` | A remote server, reached over HTTP. Exactly one of `url` and `command`. |
| `command` | list of `str` | `None` | A local server: the program and its arguments, started when the step starts and stopped when it ends. |
| `allow` | list of `str` | `None` | The server's tools the agent may call. `None`: all of them. |
| `client_id` | `str` | `None` | For a remote server that does not let clients register themselves: the ID of the client you registered. |
| `display_name`, `icon` | `str` | `None` | Label and image. |

Needs the `integrations` extra of the package (`avalanche-ai[integrations]`). A local `command`
server takes no stored account: it reads its credentials from the environment the operator runs in.

### Built-in integrations and named connections

> Not available on every account yet. Built-in integrations and named connections need a
> provider (Avalanche Cloud) that has them enabled for the account. Custom connectors and MCP
> servers are available to everyone.

```python
from avalanche.catalog import slack

@ava.agent_step(Reply, integrations=[slack])                                   # the assigned Slack connection
@ava.agent_step(Reply, integrations=[slack(connection="support-slack")])       # one named connection
@ava.agent_step(Reply, integrations=[slack(allow=["get_team_info"])])          # only some tools
```

Built-in names in `ava.catalog`: `slack`, `notion`, `hubspot`, `linear`, `github`, `jira`,
`asana`, `gmail`, `google_drive`.

`ava.Catalog(name, *, allow=None, connection=None)`; calling an entry, `slack(connection=..., allow=...)`,
returns a copy with those set.

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `name` | `str` | required | The built-in's name. |
| `allow` | list of `str` | `None` | The tools the agent may call. `None`: all. |
| `connection` | `str` | `None` | Slug of a named connection. `None`: the one connection of that integration assigned to the flow, else to its project, else to the organization. |

A named connection is one connected account with a slug. It is `shared` (one account for
everyone) or `per_user` (each person connects their own, and a run uses the account of the person
who started it). Connections are assigned to a flow, a project or the whole organization.

Errors when a run or deployment cannot resolve a connection (none is retried):

| Error | Meaning |
|---|---|
| `ava.ConnectionNotResolvedError` | The flow uses the integration but no connection of it is assigned to the flow, its project or the organization. |
| `ava.ConnectionAmbiguousError` | Several connections of it are assigned at the same level. Remove one or name one with `connection=`. |
| `ava.ConnectionNotAssignedError` | The named connection exists but is not assigned to the flow, its project or the organization. |
| `ava.ConnectionNotFoundError` | No connection with that slug, or it belongs to another integration. |
| `ava.ConnectionPerUserTriggeredError` | The flow has a schedule or webhook and the connection is per user. A triggered run has no user: assign a shared connection. |
| `ava.ConnectionUserNotConnectedError` | The person starting the run has not connected their own account for a per-user connection. |

Without any provider installed, a step that uses a built-in fails with `ProviderNotInstalledError`.

### Connecting accounts

```bash
ava connect [--connect HOST:PORT] [--json]             # list declared integrations and their account state
ava connect NAME [options]                             # connect the account of one integration
ava disconnect NAME [--slug SLUG] [--connect HOST:PORT] [--json]
```

`NAME` must be an integration declared by a workflow the operator has discovered.

| Declaration | What `ava connect NAME` does |
|---|---|
| Connector with `ava.ApiKey` | Asks for the key (hidden), or reads one line from standard input when it is not a terminal. |
| Connector with `ava.OAuth2` | Asks for the app's client ID and secret, then opens the browser for the login. |
| Connector with `auth = None` | Refused: it needs no account. |
| Remote `ava.MCP` | Opens the browser for the login the server describes. With `--api-key` (or `--header`), stores a key instead. |
| Local `ava.MCP` (`command`) | Refused: a local process has no header to carry a key. |
| Built-in (`ava.catalog`) | Opens the provider's browser login. |

| Option | Meaning |
|---|---|
| `--api-key` | Remote MCP server: connect with an API key instead of a browser login. |
| `--header NAME` | Remote MCP server: the header its key goes in (implies `--api-key`). Not for a Connector, which declares its own. |
| `--client-secret` | Remote MCP server declared with `client_id=`: ask for that client's secret. |
| `--no-browser` | Print the login address instead of opening a browser. |
| `--slug SLUG` | Built-in only: the slug of the named connection (defaults to `NAME`). |
| `--per-user` | Built-in only: connect the starting person's own account. |
| `--connect HOST:PORT` | Operator address (default `localhost:7433`). |
| `--json` | JSON output. |

`ava connect` with no name prints `NAME  KIND  ACCOUNT  USED BY`; the account column is
`connected`, `not connected`, `reconnect needed`, `no account` (none needed or known) or `missing`.
Accounts are stored by the host, one per integration name. Keys and secrets are never passed as
command arguments.

### How steps use an integration

| Step | Declares | Uses it by |
|---|---|---|
| `@ava.step(integrations=[crm])` | Connector instances only | Calling the tool as a method: `crm.find_customer(email)` |
| `@ava.agent_step(Sig, integrations=[crm, docs, slack])` | Connectors, MCP servers, built-ins | The agent sees tools named `<integration>__<tool>` (`acme_crm__find_customer`, `acme_docs__search`), with `-` in the name written `_`. The step body can also call Connector methods directly. |
| `ava.agent.step(Sig, integrations=[...])` | same as agent step | same |

When a step that declares integrations starts, before its code runs:

- every Connector with `auth` must have a connected account, else the step fails with
  `IntegrationNotConnectedError: step 'fetch_customer' uses integration 'acme-crm' with no connected account. Connect with `ava connect acme-crm`, then start the run again.`;
- each MCP server is connected (remote) or started (local); it must answer within 30 seconds or
  the step fails with `MCPServerUnavailableError`, which is retried. One MCP tool call may take up
  to 300 seconds;
- everything is closed when the step ends.

## Example

```python
from pydantic import BaseModel

import avalanche as ava


class Customer(BaseModel):
    email: str
    plan: str


class AcmeCrm(ava.Connector):
    """The Acme CRM HTTP API."""

    name = "acme-crm"
    base_url = "https://api.acme.example"
    auth = ava.ApiKey(header="X-Api-Key")

    @ava.tool
    def find_customer(self, email: str) -> Customer:
        """Find one customer by email."""
        response = self.client.get("/customers", params={"email": email})
        response.raise_for_status()
        return Customer.model_validate(response.json())


crm = AcmeCrm()
docs = ava.MCP("acme-docs", url="https://docs.acme.example/mcp", allow=["search"])


class Lookup(ava.BaseInput):
    email: str


@ava.step(integrations=[crm])
def fetch_customer(lookup: Lookup) -> Customer:
    """Fetch the customer record from the CRM."""
    return crm.find_customer(lookup.email)


class Answer(ava.Signature):
    """Answer a question about a customer using the CRM and the docs server."""

    question: str = ava.InputField()
    answer: str = ava.OutputField()


@ava.agent_step(Answer, integrations=[crm, docs])
async def answer_question(customer: Customer, *, agent: ava.Agent) -> str:
    """Answer a question about the customer's plan."""
    result = await agent(question=f"What does the {customer.plan} plan of {customer.email} include?")
    return result.outputs.answer


@ava.workflow(input=Lookup)
def answer_customer_question():
    """Look up a customer, then let an agent answer about their plan."""
    return answer_question(fetch_customer(ava.input))
```

```bash
ava dev .
ava connect                      # acme-crm  connector  not connected  ...
ava connect acme-crm             # asks for the API key
ava run answer_customer_question --input '{"email": "ada@example.com"}'
```

In a test or an embedded program, supply accounts yourself:

```python
from avalanche.integrations import ApiKeyCredential, StaticAccounts, use_accounts

accounts = StaticAccounts({"acme-crm": ApiKeyCredential(key=crm_key)})  # crm_key: from your secret store
with use_accounts(accounts):
    result = my_workflow().run(input={"email": "ada@example.com"}).result()
```

## Rules

- Pass instances, not classes: `integrations=[AcmeCrm]` raises
  `IntegrationError: ava.step integrations takes instances: write AcmeCrm(), not AcmeCrm`.
- Create each connector instance once at module level and reuse it in every step. One name is
  one account: two different declarations under the same name in one workflow are refused.
- Call connector methods only from a step that lists the connector. Elsewhere `self.client` raises
  `IntegrationError: AcmeCrm.client is only available inside a running step that declares the Connector`.
- MCP servers and built-ins go on agent steps only. On `@ava.step`:
  `IntegrationError: ava.step cannot use MCP server 'acme-docs': MCP tools are for agent steps only`.
- Use paths relative to `base_url`. An absolute URL to another host, or a redirect to one, raises
  `IntegrationError` before anything is sent, so a key can only reach its own service.
- Never put a key, token or client secret in code, input models or environment lookups. Declare
  `auth` and connect with `ava connect`.
- Integration errors about accounts and declarations are not retried (`IntegrationError`,
  `IntegrationNotConnectedError`, `IntegrationReconnectError`). Connect, then start the run again
  or [rerun](../orchestration/retries.md#reruns) from the failed step.
- With `ava.OAuth2`, a `401` answer raises `IntegrationReconnectError` (the login was revoked).
  With `ava.ApiKey`, a `401` is an ordinary response: call `raise_for_status()`.
- An ordinary HTTP error from `raise_for_status()` is a normal exception and uses the step's
  retries. Raise `ava.NonRetryableError` yourself for answers that will not change (`404`, `422`).
- Tool names must be unique within an agent step: two integrations with the same prefix, or an
  integration tool named like one of the step's own `tools=`, are refused when the step is defined.
- Give each `@ava.tool` method a docstring and typed parameters and return: the agent reads them.
- Limit an MCP server with `allow=[...]` to the tools the task needs.
- In one step, do not mix `slack` and `slack(connection=...)`: set `connection=` on every
  declaration of that integration or on none.
- Running with integrations outside an operator and without `use_accounts` raises
  `IntegrationError: step '<name>' declares integrations [...], but nothing here stores accounts`.
- `ava connect NAME` works only after a discovered workflow declares `NAME`.

## In the UI

- The operator's web UI has an Integrations page: every declared integration with its
  `display_name` (else `name`), `icon`, account state and the steps that use it, and actions to
  connect or disconnect. It shows the same data as `ava connect`.
- An agent sees each tool under `<integration>__<tool>` with the method's docstring as its
  description; those names are what appears in the agent's tool calls.
- A step that fails for a missing or expired account shows the error text with the exact
  `ava connect` command to run.

## See also

- [Agent steps](../building-blocks/agent-steps.md) and [tools and skills](../building-blocks/tools-and-skills.md)
- [Python steps](../building-blocks/python-steps.md)
- [Triggers](triggers.md): schedules and webhooks have no starting user
- [Retries and reruns](../orchestration/retries.md)
- [Cloud](../run/cloud.md): where built-in integrations and named connections are managed
- [Errors](../reference/errors.md) and [ava CLI](../reference/ava-cli.md)
