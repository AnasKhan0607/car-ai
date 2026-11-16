# Running Claude headless on the owner's subscription: what the terms allow

Checked 2026-11-16 for #9, before building the Claude brain (#7). Re-check
before relying on anything here; Anthropic changed these rules several times
in 2026.

## What Anthropic's own docs say

From the Claude Code [Legal and compliance][legal] page:

- Pro/Max use falls under the **Consumer Terms**. "Advertised usage limits for
  Pro, Max, and Ultra plans assume **ordinary, individual usage** of Claude
  Code and the Agent SDK."
- Subscription (OAuth) login "is intended exclusively for purchasers of …
  subscription plans and is designed to support ordinary use of Claude Code."
- The restriction is on **third-party developers**: they may not "offer
  Claude.ai login into their own applications, or … route requests through
  Free, Pro, Max, or Ultra plan credentials **on behalf of their users**", nor
  collect or store Claude.ai credentials or tokens. Developers building
  products should use API keys.
- It explicitly does not prevent "an end user from signing in to the
  **unmodified Claude Code binary** with their own Claude subscription."

From the [headless docs][headless]:

- `claude -p` is the Agent SDK's CLI. `--output-format json` returns the
  answer in `result`, with an estimated cost in `total_cost_usd`.
- **`--bare` never reads the subscription login**; it needs
  `ANTHROPIC_API_KEY`. The docs say `--bare` "will become the default for `-p`
  in a future release."
- Without `--bare`, `-p` loads the working directory's `CLAUDE.md`, hooks,
  skills and MCP servers, with no trust prompt.

## What that means for Car AI

| Use | Verdict |
|---|---|
| The owner, on their own Pi, running the official `claude` binary logged in to their own Pro/Max account, asking about their own car | **Allowed.** That's an end user using unmodified Claude Code with their own subscription. |
| Polling Claude on a timer or for every reading | **Not "ordinary individual usage."** The design already avoids it: Claude is called only on a question, an alert, a trip end, or a report (`docs/PHASES.md`). |
| Sharing the project so other people's cars use the owner's login, or selling it | **Not allowed** on a subscription. Each user must sign in with their own account, or the project must use an API key. |
| Copying the OAuth token out of Claude Code into our own HTTP client | **Not allowed.** Always call the real `claude` binary. |

## Design constraints for #7

1. **Call the unmodified `claude` binary**, never the token directly.
2. **Run it from a clean, empty working directory**, not `~/car-ai`.
   Otherwise every call loads this repo's `CLAUDE.md`, hooks and skills, which
   is slower, costs tokens, and runs the pre-PR hook on the Pi.
3. **Support both auth paths from day one.** Subscription today. But because
   `--bare` (API key only) is slated to become the `-p` default, the brain
   must also accept `ANTHROPIC_API_KEY`, and must fall through to Ollama
   cleanly when Claude reports an auth error rather than hanging or retrying.
4. **Keep call volume human-scale.** Event-driven only; back off on
   usage-limit errors (already in the plan).

## Not verified

Several secondary sources (2026) report that `claude -p` and Agent SDK usage
on Pro/Max moved to a **separate monthly credit allowance** rather than the
interactive limits. Anthropic's pages above don't state this. If it's true,
the car's Claude budget is smaller than the owner's chat usage would suggest.
Check the usage page in the Claude account after the first week of real use.

[legal]: https://code.claude.com/docs/en/legal-and-compliance
[headless]: https://code.claude.com/docs/en/headless
