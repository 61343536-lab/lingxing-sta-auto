# Lingxing All-Stores Query Mode

## Decision

Add an explicit all-stores mode controlled by:

```dotenv
LINGXING_ALL_STORES=true
```

This mode is required for the instruction:

> 请帮我处理这个月的所有店铺的建单业务

An empty `LINGXING_ALLOWED_SIDS` value must not implicitly grant enterprise-wide
access. Existing allow-list behavior remains the default.

## Configuration Contract

- `LINGXING_ALL_STORES=false` or unset:
  - `LINGXING_ALLOWED_SIDS` must contain one or more numeric store IDs before
    business tools can run.
  - Tasks remain restricted to the configured store IDs.
- `LINGXING_ALL_STORES=true`:
  - Business tools may query and return tasks from every store visible to the
    Lingxing application.
  - `LINGXING_ALLOWED_SIDS` is ignored for authorization decisions.
- Invalid boolean values fail configuration loading.

The date tool reports the active scope so the Agent cannot confuse a scoped
query with an enterprise-wide query.

## Data Flow

1. Resolve the current month in the configured business timezone.
2. Page through the Lingxing packing-task list without a store filter.
3. In all-stores mode, retain every returned task for normalization and
   classification.
4. Continue to require a single resolved store ID before a task can become a
   candidate.
5. Tasks with missing or conflicting store identity remain `needs_review`.
6. Resolve reviewable tasks through the detail endpoint.
7. Group valid candidates by store ID and SP, preserving every source PL.

All-stores mode removes authorization filtering. It does not weaken task data
quality checks.

## MCP And Skill Changes

- Add `all_stores` to runtime configuration.
- Make scope checks aware of explicit all-stores mode.
- Return `storeScope` from `get_current_month_range`.
- Keep `allowedSids` for diagnostics; it is empty or ignored in all-stores mode.
- Update tool descriptions and notes to distinguish hidden store records from
  unresolved store identity.
- Add the all-stores instruction phrase and workflow expectations to the
  project Skill.
- Document the new environment variable and its enterprise-wide visibility.

## Verification

Automated tests must cover:

- Explicit boolean parsing and invalid values.
- Allow-list mode remains fail-closed.
- All-stores mode accepts tasks from multiple stores.
- Missing or mixed store identity cannot become a candidate.
- MCP date output reports `storeScope=all`.
- Existing filtering, detail resolution, pagination, grouping, and
  authentication tests remain green.

Live read-only validation for 2026-10-01 through 2026-10-23 must report:

- Complete pagination evidence.
- Per-store and total task counts.
- Completed, shipment-present, candidate, and review counts.
- Detail-resolution success and failure counts.
- Final SP and PL candidate counts with masked examples.
- No write endpoint calls.

## Security And Operations

- The switch is an explicit enterprise-wide opt-in.
- Credentials remain only in the ignored `.env` file.
- Reports do not print access tokens, AppSecret values, or unmasked business
  identifiers.
- The workflow remains read-only and does not create STA records.

## Out Of Scope

- Per-user authorization inside one MCP service instance.
- Persisting monthly scan results.
- Changing the date field from `create` to `done`.
- Implementing downstream SP status checks or STA creation.
