# Design: vllm-service decides the router's models in every app (`make env`)

Date: 2026-11-23
Status: approved

## Problem

The router (`vllm-service/docker/litellm.config.yaml`) accepts in a request's
`model` field exactly the ids in vllm-service's `.env`: `TEXT_MODEL`,
`EMBED_MODEL`, `RERANK_MODEL`, `WHISPER_MODEL`, with no alias. Every app keeps
its own copy of these ids, and of the chat context, and nothing tied them
together: docint defaulted to a chat model the router does not serve, translator
to another, Nextext had none, and Ghostwriter's 40000-token context sat on a
router whose default was 8192. `env-check` passed while every call of such an
app failed.

## Decision

`env-sync.conf` gets two flags, and five groups use them.

- **`lead`** marks vllm-service's row. It is a source with three more rules:
  its value is its active line or else its example's one `# KEY=value` line
  (the compose default, by the example format); it replaces whatever another
  row reads (`linked`, never a conflict); nothing flows back into it, and the
  group waits until vllm-service is on the host.
- **`via=KEYS`** gates an app's row on that app's endpoint: the first of the
  keys with a value, the example's default included, must name the host
  `vllm-router`. Otherwise the row is left out of its group (`not linked`).

An app row is written only when what the app reads differs from the lead: its
active line, even an empty one, else its own example's default, with `${KEY}`
references resolved. An app already on the router's value keeps its file as it
is.

Groups: `text-model` (also docint's `VISION_MODEL`), `embed-model` (also
docint's `SPARSE_MODEL`), `rerank-model`, `whisper-model`, and `chat-context`
(`CHAT_MAX_MODEL_LEN` into docint's `OPENAI_CTX_WINDOW` and Ghostwriter's
`GHOSTWRITER_CONTEXT_TOKENS`). The `router-key` group's translator and
Ghostwriter rows get `via=OPENAI_API_BASE` too; docint's and Nextext's do not,
because their key also signs other router-bound endpoints.

With it, vllm-service's chat context default rises from 8192 to 40000:
Ghostwriter refuses less than 22000, and the lead's default now reaches it.
docint's vllm chat defaults and translator's compose default become
vllm-service's chat model. docint's `NER_MODEL`, read by nothing, is removed.

## Why these choices

- **Write, not just check.** Members release independently: an app's own
  default cannot track vllm-service's. Only the value on the host can.
- **Replace, not conflict.** A different model id or a larger context cannot
  work over the router, so there is no operator choice to protect. This is the
  one exception to "the planner never changes a value the operator set".
- **The endpoint decides.** The dev host sends the apps' chat to Ollama, with
  a model per app, while their Whisper and embeddings use the router. A rule
  per app and capability keeps both working without configuration.
- **Considered and rejected:** a per-host switch in `federation.env` (the
  operator must know and maintain it) and no exception at all (it would break
  the Ollama host).

## Consequences

- Switching a model or the context means editing vllm-service's `.env`, then
  `make env`; `env-check` fails `make up` until then.
- X2 (`[required]` agrees within a group) skips lead groups: Nextext requires
  `TEXT_MODEL`, which vllm-service always supplies.
- The lead's default is its example's, so the example must keep equal to the
  container-side `$${KEY:-v}` defaults and `docker/router/defaults.env`, which
  X3 does not read.
- Rollout order: vllm-service (the 40000 default) first, then the apps' tags,
  then deploy. With deploy first, a host still on the 8192 default would write
  8192 into Ghostwriter, which would not start.
