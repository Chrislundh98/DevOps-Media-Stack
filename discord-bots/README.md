# Discord Bots

Two Discord bots — one for managing a Clash of Clans clan's chat and war scheduling, one for catching PhD positions the moment a listing goes live.

## clan-war-bot

A clan-management bot that does three things in parallel:

- **War + CWL automation.** Polls the Supercell API for the current regular war and CWL round, persists war state and per-player attack data in SQLite, and DMs / pings members who haven't used their attacks within a configurable window.
- **Sassy chat persona.** A `/chat-ai` command routes turns through a local Ollama (llama3.2:3b on CPU). Persona is sandboxed: prompt-injection refusal, strict CONTEXT-only stat grounding (never invents player stats), and a post-processor that strips quote-wraps, stage directions, and stray ping syntax.
- **Store monitor.** Tracks the Supercell store for free offers and community-event rewards via the same authenticated session, posts an alert when something free shows up.

Layout:

```
bot.py             Discord client + slash commands (~2400 LOC).
coc_api.py         Thin HTTP wrapper around the official Clash of Clans API.
database.py        SQLite schema + access layer (war state, attacks, chat history, store offers).
llm.py             Ollama client + persona + post-processor.
tasks/
  war_monitor.py     Periodic war-state polling, attack-reminder scheduling, CWL roll-over.
  store_monitor.py   Periodic store/free-offer polling.
explore_store.py   One-shot helper for discovering store API endpoints from a saved cookie file.
docker-compose.yml Bot + Ollama, with Ollama pinned to P-cores and given scheduler priority.
```

Architecturally the bot runs `bot.py` and a sidecar Ollama container on the same docker network. Ollama is CPU-pinned to P-cores 0–11 with `cpu_shares=4096` so a 3B model has consistent ~20–30s response time even when the host's media transcoder is busy on the E-cores.

Restart policy is `on-failure:5` rather than `unless-stopped` — Discord rate-limits (HTTP 429) can otherwise snowball into a restart loop that triggers token-identity lock-out.

## phd-monitor

Polls the Cybercampus Sweden graduate-listings page every 15 minutes, parses out per-university entries, and watches for state transitions (`no link → active link → filled`). When a listing flips to active it posts a Discord notification with the university and link. State is persisted locally so a restart doesn't re-fire old alerts.

Built because PhD listings at certain Swedish universities open with no public notice and fill within hours.
