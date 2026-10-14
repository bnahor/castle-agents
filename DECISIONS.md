# Decisions

Short records of the choices that shape this design, what I rejected, and why.

## 1. Hybrid: a router lane per chat, plus a main runtime that delegates heavy work

The brief offered two shapes: a router that sends every request to its own
runtime, or one runtime that answers simple questions and spawns subagents for
heavy ones. I combined them.

- A small model (Haiku) **classifies** each message, in arrival order, per chat.
  It never answers. That keeps it fast enough to sit on the critical path.
- **Simple** questions are answered by the main runtime directly (one call, no
  tools). **Heavy** ones go to a subagent with a tool loop.
- The simple path can **escalate**: if the small model decides it needs live
  data, the request re-runs as heavy *in the same slot*. A router that
  under-classifies costs one cheap call, not a wrong answer.

Rejected: pure router-and-parallel-runtimes (every one-line question pays for a
full runtime), and pure single-runtime (a heavy question blocks the runtime's
loop unless it delegates, which is the hybrid anyway).

## 2. Ordering is a reservation, not a queue

Each request reserves a **slot** (`seq`) when it is admitted. Work runs in
parallel and finishes in any order; the orderer posts slots in order. This
separates *when work runs* from *when answers appear*, and it is why the
parallel run beats the serial baseline while still posting in order.

Three modes, set in `config/settings.toml`:

- **strict**: answers appear in the order questions were asked. The cost is
  head-of-line blocking: a fast answer waits behind a slow one. A **status
  post** fires when answers have been queued behind a slow head for more than
  `status_after_s`, so the chat is never silent without a reason.
- **reply**: answers post as soon as they are ready, each as a reply to the
  message that asked it. Lowest latency; order is carried by threading.
- **bounded** (default): in order, but a finished answer waits at most
  `max_hold_s` (1.5 s) for earlier ones, then posts as a reply to its question.

I started with strict because the brief asks for "a clear, ordered stream of
answers". Measuring changed my mind: answers held for order were 37% of mean
latency, mostly quick answers stuck behind research. Bounded keeps the stream
in order whenever that's cheap and caps the cost when it isn't, and the early
answer is threaded to its own question, so it still reads clearly. A timer
releases overdue answers, so the cap holds even when nothing else happens.

## 3. Routing is serial within a chat, batched, and parallel across chats

Slots must be assigned in arrival order, and an edit must be routed against an
up-to-date view of what is running. Routing each chat's messages in order
guarantees both. Different chats never wait on each other.

The cost was one router call (~1.3 s live) of queueing per message in a burst.
**Batching** removes most of it: messages that arrive while the router is busy
are routed together in one call (3 messages: 1.35 s instead of about 4 s). A
later message in a batch can target an earlier one by its `msg_id` ("make that
SPY" right after the QQQ question), resolved as each route is applied in order.

Rejected: routing in parallel and sorting afterwards. Two messages routed
concurrently could both claim to amend the same request, or an edit could be
routed before the request it edits was admitted. **Pipelining** (start the next
batch's call before the previous one is applied, then commit in order) would
save an estimated 0.4 s of mean latency more; I left it as a next step because
it needs the router to reason about messages whose own routing isn't known yet.

## 4. Three ways to change a running request, chosen by the router

| Edit | Example | What happens |
|---|---|---|
| **steer** | "also include INTC" | Pushed to the worker's inbox; applied at its next tool boundary. Work so far is kept. |
| **restart** | "actually make that SPY instead of QQQ" | Version bump; the worker is cancelled and restarted with the rewritten question, in the same slot. |
| **cancel** | "nevermind the TSLA thing" | Version bump; worker cancelled; an immediate "Okay, dropping #5" receipt, and the slot is released silently so later answers are not blocked. |

Rules that make these safe:

- **The version check is the single source of truth for staleness.** Every
  result carries the version it was produced for, and anything not current is
  dropped before posting. Cancellation is an optimisation that saves tokens;
  correctness does not depend on it (see the test with a worker that ignores
  cancellation).
- **An edit to a request that has not started yet** just changes its text. No
  restart, so it keeps its place in line.
- **An edit to a request that is done but not yet posted** (waiting behind a
  slow head) restarts it and retracts the unposted answer.
- **An edit to a request already posted** becomes a new follow-up request.
  Once an answer is in the chat it is never rewritten.
- **An accepted addition is never lost.** Each addition has an identity (the
  id of the message that added it) and is stored on the request, separately from
  the worker's inbox. Every generation (each restart is a new one) starts from
  the base question plus every addition accepted so far, and its inbox carries
  only additions accepted after it started, so each addition is applied exactly
  once per generation. The worker drains its inbox after every tool batch and
  once more before returning; one that arrives during the final model call
  triggers one more turn.
- **A rewrite supersedes earlier additions.** The router sees each request with
  its additions and returns the full restated question, so it decides what
  carries over. Without this, a rewrite of "QQQ, also include INTC" appended
  INTC a second time (a test caught it). Superseded additions are logged, and
  only a cancel drops additions otherwise.
- **The checker verifies this per generation.** For every delivered answer, each
  accepted addition must be in that generation's starting scope or applied
  within it. An addition applied only to a discarded generation counts as lost.

Steer vs restart is a judgement call, so the router makes it. When in doubt it
should restart: a restart wastes some tokens, a wrong steer gives a muddled
answer. Anyone in the chat may amend anyone's request. That's a group-chat
product choice: the receipt names the slot it changed, so the asker sees it.
Replies always resolve to their request: the router's view is the recent window
plus every open request plus any request a new message replies to.

## 5. The router can be wrong without corrupting state

The runtime validates every decision: an unknown target becomes a new request,
and a router error defaults to a heavy new request. Router mistakes can cost
quality, never consistency.

## 6. Bounded concurrency at two levels

A per-chat lane (default 3 heavy workers) stops one busy chat from starving
the rest; a global pool (default 8) bounds total spend and rate-limit
exposure. Simple answers have their own pool so they never queue behind heavy
work. Waiting is FIFO.

Known gap: a restarted request re-queues behind work that arrived after it.
Giving restarts priority is a one-line change to the lane, but I would want
data on how often it matters first.

## 7. An append-only event log is the record; metrics come from the log

Every inbound message, decision, state change and post is appended to a JSONL
log and never mutated. Metrics and invariant checks read only the log, so they
measure what happened, not what the runtime believes happened. This is also
what crash recovery and replay would be built on.

## 8. Testing: deterministic tests, property tests, and a baseline

- **Deterministic tests** use a provider whose answers block until the test
  releases them, so each test picks the exact completion order. No sleeps, no
  flakes.
- **Property tests** (hypothesis) generate random busy chats across two chats
  and check five invariants on every run: no out-of-order posts, no stale
  posts, no duplicates, no missing posts, no lost steers.
- **A serial baseline** (same code, one request at a time per chat) shows what
  parallelism buys.
- The suite was **mutation-checked**: removing the version check, the final
  steering drain, or strict ordering each makes tests fail.

## 9. Borrowed from my own agent harness

This design reuses patterns from a Rust coding-agent harness I built
(daemon, append-only sessions, an agent bus for child agents):

- **Spawn returns a handle, never the answer**, and nothing awaits a child, so
  the main runtime cannot deadlock on a subagent.
- **The steering table**: running → queued until the next tool boundary;
  idle → applied as a new turn; every addition gets a receipt.
- **Push, don't poll.** In the harness, a parent that had to poll for a child's
  result spent 32 model calls and $0.08 to learn the answer "5"; delivering
  results as events after every tool batch cut that to 4 calls and $0.005. Here,
  workers push to the orderer and the orderer pushes to the chat.
- **Append-only logs as canonical state**, and **cache-stable prompts**: system
  prompts contain no timestamps or ids, and tool definitions are name-sorted, so
  parallel subagents share a cached prefix.

What changed for this problem: many users and chats instead of one, ordered
output, version-based staleness, and a router deciding steer vs restart vs
cancel instead of the model choosing to send a message.

## 10. Concurrency: asyncio tasks, not a thread per subagent

The natural question is whether parallel subagents want parallel threads, or
the free-threaded (no-GIL) Python build. I checked how other harnesses do it:

- **Codex** builds one multi-threaded tokio runtime
  (`Builder::new_multi_thread()` in `codex-rs/arg0`), and `spawn_agent`
  (`codex-rs/core/src/agent/control/spawn.rs`) is an `async fn` that starts a
  new conversation "thread" (a `ThreadId`) and runs it with `tokio::spawn`. A
  subagent is a task, multiplexed over a worker pool about the size of the
  CPU count; it is not an OS thread.
- **Claude Code** runs subagents as async tasks on a single JavaScript event loop.
- My own harness plan chose the same multi-threaded tokio runtime for the same reason.

The shared pattern is "many sessions and subagents as cheap tasks over a small
pool of threads", because a subagent spends nearly all its time waiting on a
model's network stream. Rust gets multi-core execution of its CPU-side work for
free; that's a bonus, not why it scales.

asyncio is the Python form of that design. I chose it over threads, and over
the free-threaded build, because:

- **The work is I/O-bound.** Each run logs process CPU time against wall time
  (`cpu_share` in the metrics), so this is measured rather than assumed.
- **Correctness depends on it.** The version check, the orderer and the
  steering hand-off are safe because nothing can interleave between two lines
  that don't `await`. With real threads each one needs a lock, and "no stale
  answer is ever posted" goes from true by construction to true if every lock
  is right.
- **Free threading costs something.** It is supported in Python 3.14 but not
  the default build; single-threaded code runs somewhat slower on it, and the
  C extensions we depend on need free-threading-compatible builds.

Blocking calls are moved off the loop: the Massive SDK is synchronous, so its
calls run in `asyncio.to_thread`. If one core ever becomes the limit, the next
step is several processes, each with its own event loop, with chats sharded by
a hash of `chat_id`. A chat stays in one process, so its ordering still needs no
locks. CPU-heavy work, if it ever appears, goes to a thread or interpreter pool
while orchestration stays on the loop.

## 11. Configuration and enforced boundaries

- **Nothing tunable lives in code.** Prompts are Markdown files, and settings,
  tool definitions and simulation scripts are TOML, all under `config/`. Each
  section is validated by a pydantic model owned by the package that uses it,
  so the runtime never imports the config loader. Bad placeholders, bad
  regexes and non-positive limits fail at startup.
- **Pydantic's `model_copy(update=...)` skips validation.** I learned this the
  hard way: `ordering="reply"` stayed a plain string, and an `is Ordering.REPLY`
  identity check silently failed. Overrides go through a validating
  `Limits.override()` instead.
- **Layering is checked by a tool, not trusted.** import-linter runs in CI with
  three kinds of contract: layers (`cli → settings → adapters | simulation |
  observability → runtime → domain`), adapter independence, and one
  "protected" contract per package so its `_modules` are private. Each was
  verified by injecting a violation and watching the build fail. Pyright alone
  did *not* flag an import of another package's `_module`, which is why the
  protected contracts exist.

## 12. Python choices

I used the standard library wherever it has the right tool, and said no where it doesn't:

| Need | Choice | Instead of | Why |
|---|---|---|---|
| Closed sets (status, action, kind, ordering) | `enum.StrEnum` + `match` | string literals | Pyright's `reportMatchNotExhaustive` makes a missing case a type error; values still serialize as plain strings |
| Config | `tomllib` | PyYAML | Standard library, no dependency; TOML keeps types |
| Prompt placeholders | `string.Template` | `str.format`, Jinja | `$name` never collides with `{}` in prompt text; `substitute` raises on unknown names |
| Phrase validation | `string.Formatter().parse` | regex | The stdlib's own parser of format fields |
| Steering inbox | `collections.deque` | `list` | It's a FIFO |
| Metric counts | `collections.Counter` | `setattr` on a dataclass | What Counter is for |
| Slot and message numbering | `itertools.count` | `+= 1` | No mutable counter to get wrong |
| Chat history | `typing.NamedTuple` (`Exchange`) | `tuple[str, str]` | Named fields, still a tuple |
| Value types | `dataclass(frozen=True, slots=True)` | plain classes | Immutable, smaller |
| Parallel tool calls | `asyncio.TaskGroup` | `asyncio.gather` | Structured concurrency: no orphaned tasks |
| Tool timeout | `asyncio.timeout()` | `asyncio.wait_for` | The 3.11 idiom, composes with `async with` |
| Percentiles | `statistics.quantiles(method="inclusive")` | hand-rolled | The default "exclusive" method extrapolates past the data (it reported a p95 above the max) |
| Diagnostics | `logging` | `print` to stderr | Levels and handlers for free |
| Caching the loaded settings | `functools.cache` | a module global | Same result, no global state |

Deliberate non-choices:

- **No `heapq` in the orderer.** Slots are dense consecutive integers, so a
  dict keyed by slot number gives O(1) lookups and a heap adds nothing.
- **`typing.Protocol` over `abc.ABC` for ports.** Structural typing keeps test
  fakes trivial: a class with the right method is a valid `Researcher`.
- **pydantic over stdlib dataclasses for config.** It's already a dependency
  (structured outputs), and it validates for free.

## 13. Latency: measure, then fix the biggest stage

Every answer's latency is split into four stages in the metrics (routing,
queued, working, held for order), so "where did the time go" is a standard
output, not a one-off script. Measured live first, then fixed in order:

| Lever | Measured effect |
|---|---|
| Opus **fast mode** for research | 8.4–12.8 s → 4.5–5.5 s per answer, at 2x per-token price |
| Research **effort low** | no meaningful change, so not adopted: the time is the tool round-trip and output |
| **Shorter answers** (≤120 words, no tables) | output tokens −22%; better for a group chat anyway |
| **Bounded ordering** | held-for-order 1.66 s → 0.75 s (mock) |
| **Speculative quick answers** | about 1 s saved per simple question; costs a Haiku call on every message, a fraction of a cent |
| **Batched routing** + shorter router output | 3 messages in 1.35 s instead of about 4 s |
| **Prompt caching** + router **warm-up** | cached prefix on every research step; no ~1 s schema compile on the first user message |

Net live result on `busy`: mean 18.93 s one at a time → 7.83 s parallel →
**6.29 s** after the latency work, with p95 down to 9.67 s.

Serialization was the suspected bottleneck and wasn't: a profile under 200
concurrent chats puts the runtime's own overhead at 0.2 ms per message. Within
that, JSON encoding and per-line flushes were 30% of CPU, so the event log now
uses `orjson` with buffered writes, the loop uses `uvloop` when available
(`asyncio.Runner(loop_factory=...)`), and the SDK uses its aiohttp backend.
Those are free CPU wins at higher load, not latency fixes, and I'd say so on the call.

Two measurement bugs turned up along the way, both fixed with tests:
`inbound` was logged when a message was dequeued rather than when it arrived
(hiding router queueing), and compressed sims scaled one time limit but not
`max_hold_s` (hiding bounded mode's effect offline).

## 14. Tests: audited against a checklist

I ran the suite through a test-audit checklist (reject implementation-coupled
proof, mocks that blur distinct APIs, test-only seams, negative controls that
pass on the wrong guard). What changed:

- The scripted fake returned the same text from the quick answerer and the
  researcher, so no test could tell which path answered. It now returns
  `quick:` or `research:` and records calls and cancellations.
- About ten assertions on internal log events (`restart`, `escalate`,
  `head_start_used`, router call counts) became assertions on observable
  behavior: what was posted, which port did the work, how fast.
- Batching and speculation are proved by latency, each paired with a negative
  control that turns the feature off and must be slower.
- Two production seams that existed only for tests (`ChatRuntime.routed()`,
  `cli.parse_line`) are gone. The CLI is tested by running the real binary.
- A bad-prompt test that would pass on any validation error now matches the
  intended one.
- A mutation that broke batching made the suite **hang** instead of fail. Every
  wait now runs under `asyncio.timeout`, and all seven mutations fail fast.

## 15. Ring buffers, and why not more of them

The runtime is lock-free because it is single-threaded, not because of a data
structure: nothing can mutate state between two lines that don't `await`. A
lock-free ring buffer (the Disruptor pattern) solves concurrent producers and
consumers across threads, a boundary we don't have. Where a bounded
overwrite-oldest buffer is the right shape, it's `collections.deque(maxlen=N)`:
per-chat history, and the event log's in-memory window for long-running chats.
The orderer stays a dict keyed by slot number: a fixed ring would need a hard
cap on in-flight requests and overflow handling, for no speed gain at this size.

## 16. External review: findings and what changed

An independent review of an earlier snapshot scored it 84/100 and listed eight
findings. I verified each against the current code before changing anything:

| Finding | Verdict | Resolution |
|---|---|---|
| An accepted addition can vanish across a restart | **Real** | Additions are durable and identified, generations start from full scope (§4); regression test fails with the fix reverted |
| Checker false negatives (stale vs last `done`, additions keyed by text) | **Real** | Checker tracks current version and additions by id per generation; hand-built corrupted logs prove each check (`tests/test_metrics.py`) |
| Latency omits time waiting to be routed | Already fixed in this session | Acceptance test: 3 messages, 1-message batches, latency ≈ 3 router calls |
| Old requests fall out of the router's view | **Real** | Open requests and reply targets are always shown; test with a window of 1 |
| Live path unverified; silent fallback to fake market data | Partly fixed already | Live runs and transcript (`docs/live-run.md`); live runs now refuse to start without a Massive key unless `--mock-market-data` |
| No router evaluation; random replies point at wrong chats | **Real** | `eval-router` with 12 labelled cases (live 13/13, 6/6, 6/6); generator replies only to earlier messages in the same chat |
| Silent stalls; `to_thread` cancel can't stop a call | **Real** | Request and router deadlines with an apology that releases the slot; one progress post for a lone long request; Massive transport timeouts and retries in config |
| Restarts re-queue behind newer work | Agreed, P3 | Left as future work: a scheduling-policy change that needs a fairness benchmark first |

Fixing these surfaced two more bugs of mine, both now tested: the
progress-post change made every new head-of-line request post a status
immediately (now timed from becoming the head), and `load_dotenv()` found the
project's `.env` from the code's location rather than the working directory,
so a "no key" test actually ran live.

## What I would do next

- **Pipelined routing** (§3), worth about 0.4 s of mean latency.
- **Scheduling priority**: restarts and the head-of-line request jump the lane,
  with aging so no chat starves. Needs a burst/restart benchmark to justify it.
- **Router evals** on real chat logs: the live router classified "and what
  about AVGO?" differently across two runs, and steer vs restart vs new is
  where quality will be lost.

- **Persist and recover**: rebuild open requests from the log after a crash and
  resume or restart them.
- **Partial answers**: let a long heavy request post a first-pass answer and
  edit in the rest, if the chat platform supports message edits.
- **Cost controls**: a per-chat token budget, and cancelling heavy work that no
  one is waiting for.
