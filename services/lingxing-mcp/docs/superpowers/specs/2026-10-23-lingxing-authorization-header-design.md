# Lingxing Authorization Header Migration

## Decision

Migrate Lingxing business API calls from the legacy URL Query
`access_token` transport to the current HTTP request header:

```http
Authorization: Bearer <access_token>
```

Token acquisition and refresh endpoints remain unchanged. The internal company
token proxy shown separately is outside this change because its complete
business API and signing contract is not available.

## Request Contract

For business requests:

- Send `Authorization: Bearer <access_token>` as a single request header.
- Keep `app_key`, `timestamp`, and `sign` in URL Query.
- Do not include `access_token` in URL Query.
- Include `access_token` exactly once in the canonical parameters used to
  calculate `sign`.
- Keep the JSON request body unchanged.

For token acquisition and refresh requests:

- Keep the existing multipart form requests.
- Do not add the business `Authorization` header.

## Code Changes

- Extend the shared HTTP request helper to accept explicit request headers.
- Build signature input independently from URL Query parameters.
- Pass the bearer header only from the business request path.
- Keep token caching, refresh, serialization, and error handling unchanged.
- Restore `.env.example` to credential-free placeholders. Real credentials
  belong only in the ignored local `.env` file and must never be committed.

## Verification

Automated tests must prove:

- Business requests contain exactly one valid bearer authorization header.
- Business request URLs do not contain `access_token`.
- Signature calculation still includes `access_token`.
- Token acquisition and refresh request behavior is unchanged.
- Existing pagination, MCP workflow, and error behavior remain green.

Run:

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

## Documentation

Update the README to describe the new business API authentication flow and to
distinguish the outbound Lingxing access token from the inbound MCP bearer
token.

## Out Of Scope

- Switching to the internal `api.data.holdwellgroup.com` gateway.
- Adding a legacy/query-token compatibility switch.
- Running a live enterprise query with exposed credentials.
- Changing packing-task filtering or Skill workflow behavior.
