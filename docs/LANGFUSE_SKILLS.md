# Langfuse Skills for Coding Agents

Make AI coding agents (Claude Code, Cursor, etc.) Langfuse-aware so they can help you instrument, observe, and manage LLM applications.

---

## Available Skills

| Skill | What It Enables |
|-------|----------------|
| `langfuse` | General Langfuse knowledge — SDK usage, trace structure, prompt management |
| `langfuse-observability` | Observability patterns — instrumentation, dashboards, alerting |
| `langfuse-prompt-migration` | Migrate prompts from code to Langfuse prompt management |

---

## Installation

### Claude Code

Skills are installed via `npx`:

```bash
npx skills add langfuse/skills --skill "langfuse"
npx skills add langfuse/skills --skill "langfuse-observability"
npx skills add langfuse/skills --skill "langfuse-prompt-migration"
```

The skills are stored in a `.skills/` directory (gitignored).

### Cursor

Cursor supports the same skills format. Install via the terminal in your project:

```bash
npx skills add langfuse/skills --skill "langfuse"
```

---

## What Skills Enable

With Langfuse skills installed, your coding agent can:

- **Instrument code** with the correct Langfuse SDK patterns (v3 API)
- **Debug traces** by understanding the trace/span/generation hierarchy
- **Manage prompts** — migrate hardcoded prompts to Langfuse prompt management
- **Set up evaluators** — understand LLM-as-a-Judge patterns and configuration
- **Query traces** via the Langfuse API or CLI

---

## Project Context

This project also includes a `CLAUDE.md` file at the root, which Claude Code reads automatically. It provides:

- Architecture overview and deployment modes
- Key commands for setup, seeding, and demos
- Code conventions (Langfuse SDK patterns, Docker profiles, env patterns)
- Service URLs and project layout

No additional configuration is needed — Claude Code picks up `CLAUDE.md` on every session.

---

## Learn More

- [Langfuse Documentation](https://langfuse.com/docs)
- [Langfuse CLI](./LANGFUSE_CLI.md)
- [Main README](../README.md)
