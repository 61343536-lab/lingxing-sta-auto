# Lingxing MCP Python Migration Design

Date: 2026-10-23

## Decision Problem

The existing Lingxing packing MCP service is implemented in TypeScript and
Node.js, while its maintainer works primarily in Python. The migration must
make the service maintainable in Python without changing its external MCP
contract, Lingxing API behavior, security controls, or business decisions.

Success means:

- the repository no longer requires Node.js, TypeScript, or pnpm for this
  service;
- the four existing MCP tools keep the same names, inputs, outputs, and error
  semantics;
- both local stdio and remote Streamable HTTP transports remain available;
- the existing domain, API-client, security, and end-to-end tests have Python
  equivalents and pass;
- setup, operation, and MCP client documentation describe only the Python
  implementation.

## Scope

### Included

- Replace the TypeScript implementation with Python.
- Preserve all existing environment variable names.
- Preserve the existing read-only Lingxing API scope.
- Preserve tool annotations, validation constraints, business rules, response
  fields, and stable error codes.
- Preserve `/health`, `/mcp`, bearer authentication, Host validation, Origin
  validation, and stateless JSON HTTP responses.
- Replace Node.js tests with pytest tests covering the same behavior.
- Use `uv`, `pyproject.toml`, and `uv.lock` for dependency management.
- Update README and example configuration.

### Excluded

- Adding new Lingxing APIs or write operations.
- Changing the monthly packing workflow.
- Changing store authorization rules.
- Deploying the service or adding real credentials.
- Reworking the service into a larger multi-service platform.

## Chosen Approach

Use a module-for-module Python migration. The current responsibility boundaries
are useful and already covered by focused tests, so the Python implementation
will retain them rather than combining the service into a few large files or
redesigning the domain model during the language change.

The service will use:

- the official Python MCP SDK for tool registration and stdio/Streamable HTTP
  protocol handling;
- `httpx` for asynchronous Lingxing HTTP requests;
- Pydantic for typed tool inputs and task data;
- `cryptography` for AES-ECB signing;
- Starlette-compatible middleware/routes for the existing HTTP boundary;
- pytest and pytest-asyncio for automated tests;
- Ruff and mypy for static checks.

## Project Structure

```text
services/lingxing-mcp/
├── pyproject.toml
├── uv.lock
├── README.md
├── .env.example
├── mcp.example.json
├── skills/
│   └── lingxing-monthly-packing/
│       └── SKILL.md
├── src/
│   └── lingxing_mcp/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py
│       ├── dates.py
│       ├── errors.py
│       ├── signature.py
│       ├── lingxing.py
│       ├── packing.py
│       ├── server.py
│       └── http.py
└── tests/
    ├── test_domain.py
    ├── test_lingxing.py
    └── test_mcp.py
```

The TypeScript files, Node.js package metadata, pnpm lockfile, and generated
JavaScript output will be removed.

## Components

### Configuration

`config.py` will read the existing environment variables into an immutable
typed configuration object. It will retain validation for timezone, date
field, transport, timeout, port, store IDs, and minimum HTTP bearer-token
length.

### Dates

`dates.py` will calculate the inclusive first-day-through-today range in the
configured business timezone. It will validate real `YYYY-MM-DD` calendar
dates and ordered ranges.

### Errors

`errors.py` will provide a service exception carrying `code`, `retryable`, and
optional `upstreamCode`. Public serialization will expose only approved error
data. Unexpected HTTP/client exceptions will not expose URLs, tokens, response
bodies, or credentials.

### Signature

`signature.py` will preserve ASCII key ordering, empty/undefined omission,
compact JSON encoding for compound values, MD5 uppercase digest generation,
and AES-ECB plus PKCS7 encryption using the AppID as the key.

### Lingxing Client

`lingxing.py` will:

- support only the packing list and packing detail endpoints;
- acquire and refresh OAuth tokens with multipart form data;
- share one in-flight token renewal across concurrent requests;
- serialize requests independently per supported API path;
- apply the existing timeout and redirect policy;
- limit response size and validate response envelopes;
- preserve large numeric IDs using Python's arbitrary-precision integers,
  converting identifiers to strings during task normalization;
- convert upstream failures into stable service errors.

The client will accept an injected async transport and clock so tests remain
deterministic.

### Packing Domain

`packing.py` will retain the current normalization and decision order:

1. exclude non-completed tasks;
2. exclude tasks with any non-empty shipment ID;
3. exclude non-SP sources;
4. require review for malformed shipment fields;
5. require review when shipment relations are unknown;
6. require review for invalid PL/SP/store/source data;
7. otherwise mark the task as a candidate.

It will preserve authorized-store checks, pagination consistency checks,
conflicting snapshot detection, and grouping by store plus SP while retaining
all source PL tasks.

### MCP Server

`server.py` will register the existing tools:

- `get_current_month_range`
- `list_packing_tasks`
- `get_packing_task`
- `summarize_sp_candidates`

Tool descriptions, read-only annotations, defaults, field constraints,
structured content, and error results will remain compatible. The summary
tool will recompute eligibility from source facts rather than trusting labels
provided by the caller.

### HTTP and Entry Point

`http.py` will expose:

- `GET /health`;
- authenticated `POST /mcp`;
- 404 for other paths;
- 405 for unsupported `/mcp` methods;
- Host and Origin allow-list checks;
- constant-time bearer-token comparison.

The MCP HTTP transport will be stateless and return JSON responses.

`__main__.py` will load `.env` when present, start stdio or HTTP according to
`MCP_TRANSPORT`, write diagnostics to stderr, and handle termination signals.

## Data Flow

1. The MCP client requests the current month range.
2. The Agent reuses that fixed range across every list page.
3. The service validates the request and store scope.
4. The Lingxing client obtains or refreshes a token and signs the request.
5. One upstream page is read and validated.
6. Tasks are normalized, authorization-filtered, and classified.
7. Unknown task relations may be resolved through the detail tool.
8. The caller submits all page summaries to the deterministic summary tool.
9. The service revalidates source facts and returns grouped SP candidates.

No step writes to Lingxing.

## Error Handling and Security

- Missing credentials and missing store scope remain explicit configuration
  errors, not empty successful results.
- Retryable status remains limited to network/timeouts, rate limits, selected
  upstream failures, and inconsistent transient pagination.
- Upstream messages are not copied into public errors.
- App secrets and OAuth tokens are never logged or returned.
- HTTP mode requires a separate bearer token of at least 32 characters.
- Unknown hosts and origins are rejected before MCP handling.
- Real credentials remain excluded by `.gitignore`.

## Test Strategy

`tests/test_domain.py` will cover:

- business timezone and month/year boundaries;
- valid and invalid calendar dates;
- canonical signature input and AES output;
- large Lingxing IDs;
- every task classification branch;
- authorization scope;
- pagination invariants;
- SP grouping and snapshot conflicts.

`tests/test_lingxing.py` will cover:

- multipart credential handling;
- signed business requests;
- one in-flight token acquisition;
- refresh-token rotation;
- per-path request serialization;
- unsupported endpoints;
- network, malformed-response, and Lingxing API failures;
- public-error secret redaction;
- missing credentials.

`tests/test_mcp.py` will cover:

- HTTP bearer, Host, and Origin enforcement;
- all four registered tool names;
- the date-to-pages-to-detail-to-summary workflow;
- invalid input as MCP tool errors;
- stdio startup without credentials;
- missing-credential behavior on business calls.

## Verification

The implementation is accepted when these commands succeed:

```bash
uv sync
uv run ruff check .
uv run mypy src
uv run pytest
```

Additional smoke checks will start both transports:

```bash
MCP_TRANSPORT=stdio uv run python -m lingxing_mcp
MCP_TRANSPORT=http uv run python -m lingxing_mcp
```

The final review will confirm that no tracked TypeScript, JavaScript build
output, Node.js package metadata, or pnpm dependency remains in the service.

## Documentation and Compatibility

The README will describe Python 3.11+ and `uv` setup, the unchanged environment
variables, validation commands, and updated stdio client command. The remote
MCP example remains compatible because the URL, headers, and tool contract do
not change.

The business Skill will retain its workflow and tool names. Only implementation
or startup references that mention Node.js will be updated.
