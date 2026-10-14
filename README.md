# castle-agents

**Option 3: subagent architecture.** A runtime for a group-chat investing agent
that works on many requests in parallel, still gives each chat a clear, ordered
stream of answers, and handles people changing or adding to a request while it
runs.

```
uv sync
make sim      # busy group chat, one-at-a-time vs parallel, offline (mock models)
make check    # ruff, pyright (strict), import contracts, pytest incl. property tests
```

With keys (`cp .env.example .env`, then fill it in):

```
uv run castle-agents sim busy --provider claude
uv run castle-agents chat --provider claude
```

The design rationale, the alternatives I rejected, and what I'd do next are in
[DECISIONS.md](DECISIONS.md).

## The idea in one paragraph

A small model **classifies** each message (new question, steer, rewrite, or
cancel) in arrival order per chat; it never answers. Each new question
**reserves a slot** in the chat's answer stream. Simple questions are answered
by the main runtime directly; heavy ones go to a **subagent** with market-data
tools. Work runs in parallel and finishes in any order; an **orderer** posts
slots in order. Edits never create new slots: a **steer** goes into the running
worker's inbox and is applied at its next tool boundary; a **rewrite** bumps the
request's **version** and restarts it in the same slot; a **cancel** releases
the slot. Any result whose version is no longer current is dropped before it
can be posted.

## What it looks like

The `busy` scenario, **live** (Claude + Massive): four people, 12 messages in 10
seconds, including an addition, a rewrite, a cancel, a quick question that needs
live data, and a reply-to follow-up. Answers are truncated here; the full run is
in [docs/live-run.md](docs/live-run.md).

```
  [  5.81s] desk         #2 ↩desk:m2  ETF stands for "exchange-traded fund." It's a basket of investments, like stocks…
  [  6.17s] desk     status  Still working on #1 (What's NVDA's last close and how does it compar…). 1 answer queued r…
  [  7.37s] desk        ack ↩desk:m5  Adding that to #1.
  [  7.37s] desk        ack ↩desk:m6  Updating #3: Analyze the top holdings of SPY and their concentration risk
  [  7.40s] desk         #4 ↩desk:m4  No, the U.S. stock market (NYSE and Nasdaq) is closed on Saturdays and Sundays, …
  [  9.91s] desk        ack ↩desk:m9  Okay, dropping #5.
  [ 10.29s] desk         #6 ↩desk:m8  A P/E ratio is a company's stock price divided by its earnings per share over a …
  [ 11.71s] desk         #1  **On Oct 13, 2026, NVDA closed at $228.52, AMD at $605.53 and INTC at $102.81. AMD had th…
  [ 13.05s] desk         #3  I can't pull SPY's current holdings or weights. My data tools only have basic fund detail…
  [ 14.26s] desk         #7  Microsoft (MSFT) closed at **$543.45** on Tuesday, October 13, 2026. That's the most rece…
  [ 16.96s] desk         #8  Apple's earnings look riskier than Microsoft's, mainly because it depends heavily on one …
  [ 19.00s] desk         #9  **On Oct 13, 2026, NVDA closed at $228.52. That's below AMD ($605.53) and AVGO ($357.31) …
```

- Quick answers (#2, #4, #6) post within ~1.5 s of finishing, threaded to their
  questions (`↩`), instead of waiting behind research.
- "also include INTC" reached #1 while it was running; #1's answer covers INTC.
- "actually make that SPY instead of QQQ" restarted #3 in its own slot; the QQQ
  answer was never posted.
- #5 was cancelled; its slot was released so nothing waited on it.
- #7 ("latest price") went straight to research with live data.
- #9 replied to an answer already posted, so it became a follow-up carrying #1's scope.

## Results

**Live** (Claude + Massive), the `busy` scenario, same code; the baseline answers
one request at a time per chat, in arrival order:

| | one at a time | parallel |
|---|---:|---:|
| mean latency | 18.93 s | **6.29 s** |
| p95 latency | 32.28 s | **9.67 s** |
| makespan | 43.09 s | **17.01 s** |
| order violations / stale / duplicate / missing posts / lost steers | 0 | 0 |
| CPU share of wall time | 0.7% | 1.5% |

Where a parallel request's time goes (means): routing 1.69 s, queued 0.31 s,
working 3.72 s, held for order 0.56 s. The full live transcript, with every
answer, is in [docs/live-run.md](docs/live-run.md). The CPU share is the threading argument
measured: about 98% of every request is waiting on the network
([DECISIONS.md §10](DECISIONS.md)).

**Offline** (mock models with realistic delays), same scenario, parallel, with
the latency features off vs on:

| | strict, no batching, no speculation | bounded + batching + speculation |
|---|---:|---:|
| mean latency | 5.46 s | **4.24 s** |
| held for order | 1.66 s | **0.75 s** |

`multi` runs three chats at once (36 messages) with the same zero-violation result.

## Latency

Measured first, then fixed, in order of impact ([DECISIONS.md §13](DECISIONS.md)):

- **Research fast mode**: Opus fast mode took a research answer from 8.4–12.8 s
  to 4.5–5.5 s (2x the per-token price, about 2 cents per answer here).
  Lowering effort barely helped: the time is the tool round-trip and the output.
- **Shorter answers**: research answers are capped at ~120 words with no
  tables. Output tokens fell 22%, and the answers suit a group chat better.
- **Bounded ordering** (default): an answer waits for earlier ones for at most
  1.5 s, then posts as a reply to its question. In order when that's cheap,
  never stuck behind a slow one.
- **Speculative quick answers**: the cheap answer starts when the message
  arrives, in parallel with routing; if the router says "heavy" or "edit", it's
  cancelled. Saves about 1 s per simple question for a fraction of a cent.
- **Batched routing**: messages that arrive while the router is busy share one
  call (3 messages: 1.35 s instead of about 4 s). The router no longer echoes
  each question back, which cut its output tokens roughly in half.
- **Prompt caching and warm-up**: cache breakpoints on every system prompt plus
  automatic caching of the research tool loop, and one warm-up router call at
  startup so the first user doesn't pay the schema-compile cost (~1 s).

What didn't matter: serialization. A profile under 200 concurrent chats shows
the runtime's own overhead is 0.2 ms per message, against 1–5 s per model
call. `orjson`, buffered log writes, `uvloop` and the SDK's aiohttp backend are
in because they're free CPU at higher load, not because they change latency.

## Router quality

`castle-agents eval-router [--provider claude]` scores the router on 12 labelled cases (13 decisions)
covering additive scope, rewrites, cancels among several requests, two users on
the same ticker, an ambiguous "actually", follow-ups after delivery, an unrelated
question in a reply thread, and a rewrite of a message in the same batch.

| router | action | target | complexity |
|---|---:|---:|---:|
| live (`claude-haiku-5-5`), two runs | 13/13 | 6/6 | 6/6 |
| mock (keywords) | 10/13 | 6/6 | 5/6 |

The mock misses exactly the reply-thread and after-delivery cases, which is why
it's a test double and not a router. The live router's one earlier miss
(a follow-up to a price question marked simple) was fixed in its prompt.

## Ordering guarantees

"Zero order violations" means the mode's own rule was kept, and the modes
promise different things:

| mode | guarantee | `busy`, mock, mean / p95 |
|---|---|---:|
| `strict` | Answers post in exactly the order questions were asked, always. | 5.50 s / 6.70 s |
| `bounded` (default) | In order, except an answer that has been ready for `max_hold_s` (1.5 s) posts early as a reply to its question. Every early post is marked and counted. | 4.42 s / 6.70 s |
| `reply` | No ordering across answers; each posts when ready, threaded to its question. | 3.67 s / 6.69 s |

To demo strict ordering on its own: `uv run castle-agents sim busy --ordering strict`.

## How correctness is checked

Every run writes an append-only JSONL event log to `runs/`. The metrics read
only the log, and five invariants must be zero on every run:

| invariant | meaning |
|---|---|
| order violations | in strict mode, a chat's slots post as exactly 1, 2, 3, … |
| stale posts | no answer is posted unless the version that produced it is current |
| duplicate posts | no slot posts twice |
| missing posts | every admitted request's slot is resolved |
| lost steers | every accepted addition is applied, folded into a rewrite, or explicitly dropped |

The tests (75, about 13 s) were audited against a test-audit checklist
([DECISIONS.md §14](DECISIONS.md)): every assertion is about what was posted,
which port did the work, or how long it took. None checks internal log events
or call shapes, and no production code exists only for tests.

- **Runtime** (`tests/test_runtime.py`): a scripted quick answerer and researcher
  with distinguishable outputs wait until the test releases them, so each test
  picks the exact completion order. Ordering in all three modes, rewrite, steer,
  edit before start, cancel, follow-up, escalation, router failure, a
  hallucinated target, status posts, chat isolation, a worker that ignores
  cancellation, and clean shutdown. Batching and speculation are proved by
  latency, each with a negative control (the feature switched off must be slower).
- **Property tests** (`tests/test_invariants.py`, hypothesis): random busy
  timelines across two chats in strict, bounded, reply and serial modes; all
  five invariants on every run.
- **Claude loop** (`tests/test_claude.py`): the real research loop against a fake
  API client. Tool results and steering go back in one user turn, an addition
  during the final call triggers one more turn, tool errors and timeouts go back
  to the model, and the escalation sentinel works.
- **The checker itself** (`tests/test_metrics.py`): hand-built logs each broken in
  one way (an answer from a superseded version, a cancelled request answered, two
  identical additions with one lost, an addition applied only to a discarded
  generation, a missing slot, a duplicate, out-of-order posts), so the invariants
  are proved independently of the runtime that produces the logs.
- **Config and CLI** (`tests/test_settings.py`, `tests/test_cli.py`): the shipped
  config loads and renders, and bad placeholders are rejected with the intended
  error. The real `castle-agents` binary is driven in a subprocess: chat input
  syntax, and a compressed sim still releasing held answers (the regression test
  for a scaling bug).
- **Mutation check**: seven injected bugs (no version check, no final steering
  drain, no ordering, bounded never releasing, same-batch targets unresolved,
  head starts unused, no batching) each make the suite fail, and fail fast.
  Every wait has a timeout, so a broken invariant can't hang CI.

## Layout

```
config/                          every tunable; edit these, not the code
  settings.toml                  limits, user-facing phrases, models, token caps, mock + sim settings
  prompts/*.md                   router, quick-answer and research system prompts
  tools.toml                     research tool definitions
  scenarios/*.toml               simulation scripts and the random-chat pools
src/castle_agents/
  domain/                        plain data: Inbound, Outbound, Request, Route, enums
  runtime/                       the engine + the ports it depends on (Router, Researcher, ...)
  adapters/claude|mock|massive   implementations of those ports
  observability/                 append-only event log, metrics and invariants
  simulation/                    scenario loading, random timelines, the replay driver
  settings/                      loads and validates config/ into one typed Settings object
  cli/                           `castle-agents sim | chat | scenarios`
tests/                           runtime, property-based, Claude-loop, config and CLI tests
DECISIONS.md                     design decisions, rejected alternatives, next steps
```

Boundaries are enforced, not just conventional (`make imports`, also in CI):

- **Layers:** `cli → settings → adapters | simulation | observability → runtime → domain`.
  Each layer imports only from layers below it, and siblings never import each other.
- **Adapters are independent:** Claude, mock and Massive never import one another.
- **Internals are private:** every package exposes its API in `__init__.py`, and its
  `_modules` can only be imported from inside the package.

The runtime knows only its ports, so Claude and the mocks are interchangeable,
which is what lets the same engine run offline in tests and live against the API.

## Configuration

Everything that isn't logic is in `config/`, validated at startup by pydantic
models that live next to the code that uses them (`runtime.Limits`,
`runtime.Phrases`, `adapters.claude.ClaudeConfig`, `adapters.mock.MockConfig`).
A typo fails fast: a phrase with an unknown `{placeholder}`, a prompt with an
unknown `$placeholder`, a non-positive limit or a bad regex is a load error, not
a mid-run surprise. Prompts use `string.Template` (`$steer_prefix`), so braces in
prompt text never collide with formatting. Secrets stay in `.env`; point
`CASTLE_CONFIG_DIR` at a copy of `config/` to run with different settings.

## Models

| role | default | why |
|---|---|---|
| router | `claude-haiku-5-5`, thinking off, structured output | on the critical path for every message; classification only |
| fast path | `claude-haiku-5-5`, no tools | one-to-three-sentence answers; replies `ESCALATE` when it needs data |
| heavy subagent | `claude-opus-5-5`, effort `medium`, Massive tools | real analysis; server-side refusal fallback enabled |

All of these are set in `config/settings.toml` under `[claude]`.
System prompts contain no timestamps or ids and the tool list is name-sorted, so
parallel subagents share a cacheable prompt prefix.

## Status

- Offline and live paths both run end to end. The live path is verified
  against Claude and Massive (transcript above). A live run with no Massive key
  refuses to start; `--mock-market-data` opts into fake figures explicitly.
- Every request has a deadline (90 s) and every router call a deadline (15 s).
  Past it, the request is answered with an apology and its slot is released,
  so an accepted slot always resolves. Failures and timeouts are reported as
  their own outcomes, never counted as answers.
- A request accepts at most 5 additions of at most 400 characters each; past
  that, the edit is refused with a receipt asking for a new question.
- The finance is deliberately thin (previous close and company details): this
  option is about the runtime.

---

## Workflow

`main` only moves by merge. Each change is a branch (`feat/…`, `fix/…`,
`docs/…`) cut from up-to-date `main`, must pass `make check`, and is merged
with `--no-ff` so every feature stays one merge commit in the history.

## Initial Thoughts / My Musings

six options, most of them are finance-y (scenario analysis, etf matching, options, research briefs). interesting but doing any of them properly in a few hours needs a lot of domain depth, and i'd rather show how i build systems
option 3 is literally their problem anyway; the group chat agent backs up when people fire off 5 questions at once

i've basically built this before in my harness (rust coding agent, has an agent bus for child agents) so a lot carries over
- spawn gives you a handle, never the answer; nothing ever awaits a child so no deadlocks
- steering table: running → queued till the next tool boundary, idle → new turn, always get a receipt
- push not poll. learned that one the hard way, a parent polling for a child's result burnt 32 model calls and $0.08 to find out the answer was "5". after the fix it was 4 calls / $0.005
- append only log as the source of truth

not porting the rust tho, python + asyncio is way faster to write and easier to read for reviewers

threads? codex runs a multi thread tokio runtime but a subagent is a task on it, not its own thread. a subagent is basically waiting on a socket the whole time so asyncio is the python version of the same thing
free threading doesnt buy anything when its io bound, and the version check / orderer are only safe bc nothing runs between two lines that dont await. with real threads every one of those needs a lock
if one core ever runs out, shard chats across processes by chat_id hash, each w its own loop; ordering stays lock free
logging cpu time vs wall time per run so this is measured, not argued

the brief gives two shapes (router + parallel runtimes vs one runtime w subagents). why not both ig
small model (haiku) only classifies, never answers, so its cheap enough to sit in front of every message
simple stuff gets answered straight away, heavy stuff goes to a subagent w tools
if the fast path realises it actually needs data it just escalates, same slot

ordering: each question reserves a slot when it comes in, work finishes whenever, slots post in order
so a quick answer can get stuck behind a slow one (head of line blocking), hence the "still working on #1" status msg
reply mode is there too if the platform does threads properly; lower latency but the order is carried by the reply instead

edits are the actually hard part
"also include INTC" → steer, goes into the running worker's inbox, keeps the work so far
"actually make that SPY" → restart, bump a version, same slot
"nevermind" → cancel, slot gets released so it doesnt block everything behind it
the version counter is what stops stale answers; cancellation is just there to save tokens. tested w a worker that ignores cancel entirely and it still cant post

edit after the answer is already posted → new follow up. never rewrite something people already read

routing is serial per chat, otherwise two msgs could both try to edit the same request. costs one haiku call of queueing per msg, fine

steer vs restart is a judgement call so the router decides; if unsure restart (wastes some tokens) > wrong steer (muddled answer)

everything thats not logic lives in config/ (prompts as .md, settings as toml), validated on load so a typo fails at startup
layering is enforced w import-linter, not just vibes; reaching into another package's _internals fails the build

tooling: uv, ruff, pyright strict. looked at ty / pyrefly but ty is still beta and catches less, not worth defending on a call
hypothesis for fuzzing random busy chats, checks no out of order / stale / dupes / missing / lost steers on every run
mutation checked it too; removed the version check and nothing failed at first (cancel was hiding it), so added the stubborn worker test

finance kept thin on purpose (prev close + company details from massive), this one's about the runtime

latency was bad so measured before touching anything. split every answer into routing / queued / working / held for order
- holding answers for order was 37% of the wait, so default is now bounded: in order unless an answer has been waiting 1.5s, then it posts as a reply
- quick answers start the moment a msg lands, in parallel w the router; heavy → just cancel it. haiku is cheap enough that the wasted calls dont matter
- msgs that pile up while the router is busy go in one call (3 msgs 1.35s vs ~4s)
- opus fast mode halved research (~10s → ~5s), effort low did basically nothing. the time is the tool round trip + output, so also capped answers at ~120 words which honestly reads better in a group chat anyway
- thought serialization was it tbh, profiled it: 0.2ms per msg of our own overhead vs seconds per model call lol. orjson / uvloop are in but theyre not why its faster

ran the tests through a test-audit checklist too. the fake returned the same text for quick vs research so tests couldnt even tell which path answered hmmm. fixed that, swapped log-event asserts for actual behaviour, and every latency test has a "feature off must be slower" control
one mutation made the suite hang instead of fail, so everything waits under a timeout now

had someone review it cold, they found a real one: an addition the worker already applied could vanish if the answer got restarted later (it only lived in the inbox). additions get an id now and live on the request, every restart starts from base question + all of them
the checker had holes too (compared against the last "done" not the current version, keyed steers by text) so it gets its own tests w deliberately broken logs
router eval on 12 hand labelled cases: live haiku 13/13 after one prompt line. the regex mock gets 10/13, which is kinda the point of it being a mock
fun one: load_dotenv() looks for .env from where the code lives, not where you run it, so my "no key" test quietly ran live lol

random gotchas
- first massive call took 59s, the rest were ~0.2s. added a 15s tool timeout so one stalled call cant hold a subagent hostage
- the claude key isnt scoped to a workspace so every request needs the workspace id header hmmm

if i had more time
- restarts should probably jump the queue, right now they go to the back
- need real chat data to eval the router, thats where quality actually gets lost
- crash recovery from the log
- per chat token budgets
