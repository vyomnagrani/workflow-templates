# n8n to Azure Logic Apps Automation converter

This directory contains a CLI-first converter for turning n8n workflow JSON into
Azure Logic Apps Automation `AutoTemplate v1` manifests.

The current release establishes the conversion pipeline and repository format.
It intentionally reports unsupported behavior rather than silently dropping it.

## Current capabilities

- Analyze one workflow and report supported and unsupported node types.
- Download and normalize workflows from the public n8n template catalog.
- Inventory node usage, mapping coverage, credential usage, graph patterns, and
  single-node-type opportunities across a template corpus.
- Convert manual/webhook and schedule triggers.
- Convert HTTP Request, Set/Edit Fields, Wait, No Operation, and Respond to Webhook nodes.
- Treat Sticky Notes as non-executable editor metadata.
- Compile guarded two-way IF regions into nested LAA `If` actions, including
  convergence back into the parent action scope.
- Compile safe multi-rule Switch regions into equivalent nested condition chains.
- Convert Google Sheets append and read operations to the managed
  `/managedApis/googlesheet` connector and generate the required connection
  manifest entry.
- Convert Gmail send-message operations through `/managedApis/gmail`.
- Convert Telegram text sends through `/managedApis/telegrambotip`, with an
  explicit secure bot-token replacement diagnostic.
- Convert compatible JavaScript Code nodes to LAA `JavaScriptCode` actions using
  an n8n item-model adapter. Python, binary data, n8n helper APIs, module loading,
  network fetch, and top-level await remain explicit placeholders.
- Translate common `$json`, `$input`, and named-node expressions, including
  `.item`, `.first()`, and `.last()` selectors.
- Translate linear n8n connections into LAA `runAfter` dependencies.
- Convert n8n pinned trigger/action data into LAA trigger fixtures and action mocks.
- Emit explicit placeholders and diagnostics for unsupported nodes.
- Run in strict or best-effort conversion mode.
- Generate a repository layout compatible with the Project Auto template pattern.
- Validate the required `AutoTemplate v1` envelope fields.

Loops, nested/overlapping branch regions, Google Sheets update/upsert semantics,
Gmail approvals/labels/drafts, Telegram media, typed AI connections, complex
expressions, incompatible Code runtimes, community nodes, credentials, and most
connector-specific nodes are detected but not yet claimed as equivalent.

## Run without installing

From `tool`:

```powershell
$env:PYTHONPATH = ".\src"
python -m n8n_to_laa supported
python -m n8n_to_laa analyze .\tests\fixtures\simple-http.json
python -m n8n_to_laa convert .\tests\fixtures\simple-http.json `
  --output .\out\simple-http.json `
  --summary
python -m n8n_to_laa validate .\out\simple-http.json
```

## Install as a CLI

```powershell
python -m pip install -e .
n8n-to-laa --help
```

## Batch conversion

```powershell
n8n-to-laa download-catalog C:\n8n-catalog --limit 200

n8n-to-laa inventory C:\templates --output .\inventory.json

n8n-to-laa batch C:\templates `
  --output C:\workflow-templates `
  --mode best-effort `
  --author "Original author preserved separately"
```

Generated layout:

```text
workflow-templates/
├── batch-report.json
└── templates/
    ├── manifest.json
    └── <template-id>/
        ├── manifest.json
        ├── source.n8n.json
        ├── conversion-report.json
        └── attribution.json
```

`manifest.json` is the LAA template. The other files preserve provenance and make
conversion quality reviewable.

## Conversion modes

### Strict

```powershell
n8n-to-laa convert workflow.json --mode strict
```

Returns a failure exit code when unsupported nodes or connection semantics are
encountered.

### Best effort

```powershell
n8n-to-laa convert workflow.json --mode best-effort
```

Produces a valid template envelope and inserts visible `Compose` placeholders for
unsupported nodes. Every placeholder is listed in the conversion report.

## Quality grades

| Grade | Meaning |
| --- | --- |
| A | All nodes converted without diagnostics |
| B | All nodes converted, but review warnings remain |
| C | A valid best-effort template containing unsupported placeholders |
| D | Strict conversion failed or the input was invalid |

## Tests

```powershell
$env:PYTHONPATH = ".\src"
python -m unittest discover -s tests -v
```

## GitHub repository

The local output is intentionally independent of GitHub. Once the batch output is
reviewed, the repository can be created and populated with:

```powershell
gh repo create vyomnagrani/workflow-templates --public `
  --description "Azure Logic Apps Automation workflow templates"
```

Repository creation is not required for converter development.

## Azure Container Apps path

The converter core has no CLI-specific global state, so a later FastAPI adapter can
call the same `analyze_workflow()` and `convert_workflow()` functions. The hosted
service should be added only after:

1. The mapping coverage target is defined.
2. Generated templates pass schema and portal-import testing.
3. Licensing and attribution policy is agreed for the source catalog.
4. Authentication, storage, workload limits, and review workflow are defined.

Azure CLI authentication is available in the current environment, so an ACA
environment and app can be provisioned later without requiring manual setup.

## Next implementation priorities

1. Merge, nested IF, and loop graph compilation.
2. Microsoft 365, additional Google Workspace, Telegram, and Azure connector mappings.
3. Credential-to-connection manifest generation.
4. A real expression parser rather than the initial safe subset.
5. Agent, model, memory, and tool graph conversion.
6. Validation against the canonical Project Auto schema and portal import tests.
7. Optional AI-assisted mapping proposals with mandatory diagnostics.
