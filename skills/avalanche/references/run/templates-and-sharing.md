# Templates and share pages

A template is a published copy of a deployed Avalanche Cloud project that anyone can import
into their own organization and try. A share page is the template's public web page. Both
exist only on Avalanche Cloud ([cloud.md](cloud.md)); the local operator has neither.

## When to use it

- A user gives you a share link (`/t/<slug>` on the console's host) and wants that workflow:
  they import it in the console, then you work on the imported project.
- A user asks you to "make a template" or "share this workflow publicly": you cannot. Only
  the template publisher organization can publish. Other organizations get 403
  `template_publisher_only`.
- To give a colleague in the same organization access to a workflow, deploy it to a project
  they can open ([cloud.md](cloud.md)). No template is involved.
- To hand someone the code, share the repository. `delta clone` returns a deployed
  project's source to someone who has access to that project.

## Interface

### What a template is

A template is one slug that pins one deployment of the publisher's project, plus a public
definition written at publish time.

| Field of the public definition | Meaning |
| --- | --- |
| `slug` | Lowercase letters, digits and inner hyphens, at most 64 characters. Part of the share URL. |
| `title`, `description`, `body` | Title (at most 120 characters), short description (at most 280), write-up in Markdown (at most 20,000). |
| `goals` | Up to 20 short "what it does" lines. |
| `flows[]` | Each flow of the pinned deployment: `flow_slug`, `workflow_name`, step names, graph, display names, step types, the structure of its run input, and the variable names it needs. |
| `main_flow` | The flow whose graph the page shows first. |
| `required_env` | Names of the variables the flows declare. |
| `trial_supplied_env` | The subset the publisher provides during trial runs. |
| `requirements[]` | Other things the user needs (free text), each marked provided for the trial or not. |
| `trial_runs_per_user` | 3. |
| `author`, `author_image`, `video` | Optional author name, role, links, picture, and a demo video. |

### What a share page exposes

The share page at `/t/<slug>` is public: no sign-in. It shows the definition above and a
**Use this workflow** button.

| Exposed | Not exposed |
| --- | --- |
| Title, description, write-up, goals | Source code |
| Workflow graph: step names, step types, edges | Step docstrings |
| The shape of the run input (types, required fields, enums, bounds) | Descriptions and other annotations written on the input model |
| Names of required variables, and which ones the trial provides | Values of any variable or secret |
| Requirements, author, demo video | Organization, project, deployment and flow IDs |
| | Runs, results, logs, traces |

An unknown or unpublished slug gives one uniform "Workflow not found" page.

### How someone starts from a template

1. Open the share link and choose **Use this workflow** (`/t/<slug>/use`). This needs a
   signed-in user, an organization enabled on Avalanche Cloud, and deploy rights in it.
2. The console imports the template: a new project in the user's organization with a copy of
   the pinned deployment as its current deployment. The copy is one snapshot, without the
   publisher's history, variables or build logs.
3. The flow opens, ready to run. The project is a **trial**.

While the project is a trial:

| Rule | Detail |
| --- | --- |
| Runs | Started manually by a signed-in user only. An organization API key gets 403 `template_trial_user_required`. |
| Quota | 3 trial runs per user per template, across every import of it. A run counts once it starts, whatever its outcome. Then 409 `template_trial_exhausted`. |
| Trial-supplied variables | Provided by the publisher for trial runs; the user cannot read or change them. |
| Other variables | The user sets them with `delta env set` or in the console. |
| Frozen | `delta deploy`, `delta clone`, promote, rollback, turning a schedule on or off, and editing a trial-supplied variable fail with 409 `template_trial_frozen`. |
| Triggers | Code-declared schedules and webhooks are off. |

**Convert** ends the trial. It is one way.

- In the console: the **Convert** button on the project or flow page (needs the deployment
  set-live permission).
- The project becomes a normal project: deploys, clone, schedules, webhooks and variables
  work; declared schedules and webhooks turn on as after a deploy.
- Trial-supplied variables stop being provided. A run that needs one the user has not set
  fails with `env_unavailable`.
- Runs no longer count against the trial quota. The project keeps its runs and history.

### How a workflow becomes a template

Publishing is console-only and restricted to the publisher organization.

1. The publisher deploys the project. All its flows must run on one deployment
   (`template_flows_not_on_one_deployment` otherwise; `template_project_not_deployed`
   without a deployment).
2. In the project's **Template** settings section, the publisher fills in slug, title,
   description, write-up, goals, main workflow, author, requirements and the variables the
   trial supplies (each must be a variable the flows declare).
3. Publish pins the deployment the project currently serves. Later deploys do not change
   the template until **Republish**.
4. **Unpublish** takes the share page down. Imports that already exist keep working.

The publisher also sees counts per template: share page views, imports, trial runs, trials
used up, conversions.

## Example

A user imported the template `invoice-triage` and asks you to change it.

```bash
# 1. The user converts the project in the console (Convert), then:
uv run delta projects list                 # find the imported project's ID
uv run delta clone proj_... invoice-triage # one commit, delta.toml already bound
cd invoice-triage && uv sync

# 2. Set the variables the trial used to provide
printf '%s' "$OPENAI_API_KEY" | uv run delta env set OPENAI_API_KEY --secret

# 3. Edit, check locally, deploy
uv run ava dev
uv run delta deploy
```

Before the convert, step 1 fails with:
`... is a template trial, which cannot be cloned (template_trial_frozen). Convert it first.`

## Rules

What a coding agent can do:

- Run and inspect a trial project's flows with `delta runs ...`, if the user is signed in
  (`delta login`). Each start uses one of the user's 3 trial runs.
- Set the variables the trial does not supply: `delta env set NAME --secret`.
- After the convert: `delta clone`, edit, `delta deploy`, everything in [cloud.md](cloud.md).

What it cannot do:

- Publish, republish or unpublish a template: there is no `delta` command, and the API
  refuses every organization but the publisher.
- Import a template: the `delta` CLI has no import command. The user does it in the console
  with **Use this workflow**.
- Convert a trial from the CLI: there is no `delta` command. The user clicks **Convert**.
- Read a template's source before it is imported and converted. The share page shows the
  graph and the input shape, not the code.
- Start trial runs with `DELTA_TOKEN` set to an organization API key.

Writing a workflow that may become a template:

- Step names, the graph and the input model's field names, types and constraints are public
  on the share page. Name steps `verb_object` and keep internal names out of them.
- Put every key and endpoint in declared variables (`[tool.delta.env]`), never in code: the
  importer receives the source after converting.
- Give the input model defaults that make a first run work; a trial user has 3 runs.
- Keep everything inside the project folder: the copy contains only what `delta deploy`
  uploads (no ignored files, no `.env` files, nothing outside the folder).
- Do not rely on a schedule or webhook for the first experience: both are off during a trial.

## In the UI

- Share page: title, description, video or main workflow graph, "What it does", requirements,
  required environment variables (each marked "Provided for your trial" or "You add your
  own"), the write-up, the included workflows, and **Use this workflow**.
- Imported project: a trial banner on the project and flow pages with the runs left, the
  variables the trial provides, the ones to add, and **Convert**.
- Publisher only: a **Template** section in the project's settings.

## See also

- [cloud.md](cloud.md): projects, deployments, variables, runs, `delta clone`.
- [local.md](local.md): run the cloned project locally.
- [../building-blocks/inputs-and-files.md](../building-blocks/inputs-and-files.md): the input model shown on the share page.
- [../reference/delta-cli.md](../reference/delta-cli.md).
