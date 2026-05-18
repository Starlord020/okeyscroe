---
name: whop
description: Guidelines for using Whop to integrate commerce, payment plans, checkout, and subscription management
---

## Introduction

Replit offers a native integration with Whop that allows users to implement payment plans, checkout configurations, and subscription management in their applications.

## Prerequisites

1. The Whop integration must be connected to the repl. You can do this by proposing the integration. Reference the `integrations` skill if necessary.
    The connection is provisioned automatically — there is no user login or OAuth authorization step. Once connected, a Whop store (company) is created for the repl and credentials (company ID and API key) are managed automatically. Users can optionally log into Whop with their email to manage their store and receive payouts, but this is not required to use the integration.

2. Verify the connection is active by calling `listConnections('whop')` in the `code_execution` sandbox. The returned connection settings contain `company_id` (biz_xxx) and `api_key` (apik_xxx).

3. Immediately store `company_id` as a Replit Configuration named `WHOP_COMPANY_ID`. Server-side code reads it from `process.env.WHOP_COMPANY_ID`.

You are required to ensure these prerequisites are met before setting up or using Whop.

## How It Works

- **Agent interactions (preferred):** Call Whop MCP tools via the OpenInt connector proxy (e.g. create products, plans, list memberships). The proxy handles authentication and `company_id` injection automatically. Run via `shell_exec` (NOT `code_execution` — `process.env` is unavailable there). See "Calling Whop MCP Tools" below.
- **Server-side code:** Use `getWhopClient()` from `whopClient.ts` (see code-templates reference) to get a typed Whop SDK client. Credentials are fetched lazily from the Replit connection API. `company_id` (biz_xxx) is not secret — store it in a Replit Configuration and pass in request params or body as needed. `whopClient.ts` is server-only.
- **Frontend code:** Call your server routes (e.g. `/api/whop/products`). Never import `whopClient.ts` in client code.

### Calling Whop MCP Tools

Write `whop-mcp.mjs` once, then call it via `shell_exec`. Do NOT use `code_execution` — `process.env` is unavailable there.

Filename: whop-mcp.mjs (project root)

```js
const hostname = process.env.REPLIT_CONNECTORS_HOSTNAME;
const token = process.env.REPL_IDENTITY ? "repl " + process.env.REPL_IDENTITY
  : process.env.WEB_REPL_RENEWAL ? "depl " + process.env.WEB_REPL_RENEWAL : null;

async function mcpCall(method, params = {}) {
  const resp = await fetch(`https://${hostname}/api/v2/proxy/mcp`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      "X-Replit-Token": token,
      "Connector-Name": "whop",
    },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  const text = await resp.text();
  const lines = text.split('\n').filter(l => l.startsWith('data:'));
  const envelope = JSON.parse(lines.at(-1).replace(/^data:\s*/, ''));
  const result = envelope.result;
  const textContent = result?.content?.[0]?.text;
  return textContent ? JSON.parse(textContent) : result;
}

const [cmd, argsJson] = process.argv.slice(2);
if (cmd === "--list-tools") {
  const { tools } = await mcpCall("tools/list");
  tools.forEach(t => console.log(t.name + " — " + t.description));
} else if (cmd === "--schema") {
  const { tools } = await mcpCall("tools/list");
  const tool = tools.find(t => t.name === argsJson);
  console.log(tool ? JSON.stringify(tool.inputSchema, null, 2) : `Tool "${argsJson}" not found`);
} else if (cmd) {
  const result = await mcpCall("tools/call", {
    name: cmd,
    arguments: JSON.parse(argsJson || "{}"),
  });
  console.log(JSON.stringify(result, null, 2));
} else {
  console.log("Usage: node whop-mcp.mjs --list-tools | --schema <tool> | <tool> '{...}'");
}
```

Usage via `shell_exec`:
```bash
node whop-mcp.mjs --list-tools                              # list available tools
node whop-mcp.mjs --schema create_plan                      # check params before calling
node whop-mcp.mjs create_product '{"title":"Pro Access"}'   # call a tool
```

**Always run `--schema <tool>` before calling an unfamiliar tool** — don't guess parameters.

The proxy injects `api_key` and `company_id` automatically — no credentials needed.

## Project Structure

- **API server directory**: `server/`
- **Client app directory**: `client/`


## Making API Calls

Whop uses different API versions per endpoint — check https://dev.whop.com for the correct version.

Read the code-templates reference for `whopClient.ts` and checkout flow.

### API gotchas

- Most SDK methods require `company_id` — read it from `process.env.WHOP_COMPANY_ID` (Replit Configuration).
- Plans reference their product via a nested `product: { id }` object, NOT a flat `product_id` field. Use `plan.product.id` to match plans to products.
- List endpoints return `{ data: [...] }` — the array is in the `data` field.
- Products use `title` (not `name`) for the display name.
- Whop returns prices in **cents** (e.g. `100` = $1.00), like Stripe. Store prices as cents in the database to avoid conversion bugs. If you display prices, divide by 100 only at the UI layer.
- The SDK method is `whop.checkoutConfigurations.create({ plan_id })`, NOT `checkoutSessions`. Do NOT pass `company_id` — it causes an error unless creating on behalf of a connected account.
- Whop is the system of record for purchases. Two strategies to verify ownership:
  1. **Always poll** — query Whop memberships by email on each access check. Simple, no local state to sync.
  2. **Store in DB + webhook** — store purchase state in the database, update via Whop webhooks. Better performance but more setup.

## Whop Concepts

- **Companies:** Top-level entity. Each API key is scoped to a company (`biz_xxx`).
- **Products:** Items or access tiers users can purchase (e.g., "Pro Membership").
- **Plans:** Pricing configurations for products (one-time, recurring, free trial, etc.).
- **Memberships:** A user's active access to a product, created when they purchase a plan.
- **Checkout Sessions:** Payment flows for purchasing plans.

### Typical Flow

1. **Build a real signup/login system first** (registration, login page, sessions/cookies). This is required before any checkout flow. Users must be able to create an account, log in, and stay logged in across pages. A simple "enter email" form at checkout is NOT sufficient — the app needs persistent sessions so it can verify purchase ownership, restore purchases, and gate content. Do NOT use Whop for authentication; Whop is the payment processor, not the identity provider.
2. **Create a Product** via `mcpCall` in shell (e.g. `create_product` with a title). The API key has full owner access — no manual Whop dashboard login is needed.
3. **Create one or more Plans** via `mcpCall` (pricing, billing interval, one-time or recurring)
4. **Store the plan ID** in a Replit Configuration (e.g. `WHOP_PLAN_ID`) so checkout can reference it
5. **Create a Checkout Session** with the plan — render it on a dedicated checkout page using `WhopCheckoutEmbed`. Prefill the logged-in user's email and disable the email field so the purchase is tied to their account.
6. **On `onComplete`**, verify the membership via the Whop API using the user's email and store email + membership ID in your database.
7. **Check access** by querying your database for the user's membership, or call Whop's API to validate.

Apps must have a database to store purchases (user email + Whop membership ID).

**Important:** The agent can create products, plans, and manage subscriptions directly via MCP tools or the API. Users do NOT need to log into the Whop dashboard.

## Storing Data

- Do NOT duplicate product or plan catalogs in your database — query Whop for those
- DO store purchase records: user email + Whop membership ID. This is required for users to restore purchases.
- Store Whop entity IDs (e.g., `whop_membership_id TEXT`, `user_email TEXT`) in your application tables

## Key Rules

**DO:**
- Use `getWhopClient()` from `whopClient.ts` for all server-side API calls
- Check https://dev.whop.com for the correct API version per endpoint
- Store `company_id` (biz_xxx) in a Replit Configuration (e.g. `WHOP_COMPANY_ID`)
- Store purchase records (user email + Whop membership ID) in your database
- Implement user login so email is known before checkout

**DO NOT:**
- Hardcode or cache `api_key` (apik_xxx) in source code — always fetch via `getWhopClient()`
- Import `whopClient.ts` in frontend/client code — it is server-only
- Duplicate Whop product/plan catalogs in your own database

## References

- ./references/code-templates.md -- Code templates for setting up Whop integration including proxy client, routes, and checkout flow
