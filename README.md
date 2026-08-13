# n8n to Azure Logic Apps Automation templates

This repository contains a CLI-first compiler that converts n8n workflow JSON
into Azure Logic Apps Automation `AutoTemplate v1` manifests.

## Status

This is an early preview. The converter processes the current 196-workflow
research corpus without pipeline failures and converts:

- 58.29% of all nodes.
- 43.32% of executable nodes.
- Two workflows at publishable Grade A/B quality.

Unsupported behavior is never silently discarded. Best-effort conversion inserts
visible placeholders and diagnostics; strict conversion fails instead.

## Repository layout

```text
.
|-- templates/       Reviewed Grade A/B generated templates
|-- tool/            Python CLI, converter library, fixtures, and tests
|-- n8n-compete.html Competitive product analysis
`-- n8n-json.html    n8n and LAA template JSON comparison
```

## Use the converter

```powershell
python -m pip install -e .\tool
python -m n8n_to_laa --help
python -m n8n_to_laa convert workflow.json --output manifest.json --summary
```

See [`tool/README.md`](tool/README.md) for catalog ingestion, inventory, batch
conversion, quality grades, and repository generation.

## Publishing policy

Only Grade A and Grade B conversions are added to `templates/`. Grade C outputs
remain review artifacts until unsupported placeholders are resolved. Original
n8n source JSON, attribution, and conversion diagnostics are retained beside each
published template.

## License

The converter source is available under the MIT License. Individual workflow
templates retain their original attribution and may have separate source terms.
