# Live run: busy scenario

Recorded 14 October 2026 with `uv run castle-agents sim busy --provider claude --mode parallel`.

- **Models:** router and quick answers `claude-haiku-5-5`; research `claude-opus-5-5`, effort `medium`, fast mode.
- **Market data:** live, from Massive (previous close and company details only). Figures are for the
  last trading session before the run, 13 October 2026.
- **Settings:** `config/settings.toml` as shipped at the time, with **bounded** ordering (now opt-in via
  `--ordering bounded`; the default is strict), batched routing and speculative quick answers.
- **The script** (`config/scenarios/busy.toml`): four people, 12 messages in 10 seconds, including an
  addition (m5 adds INTC to #1), a rewrite (m6 switches #3 from QQQ to SPY), a cancel (m9 drops #5),
  a quick question that needs live data (#7), and a reply-to follow-up (m12 → #9).
  Some questions ask for data the two tools don't have (ETF holdings, earnings dates). The answers
  say so instead of guessing, which is the intended behaviour.

`#n` lines fill answer slots in order; `↩` marks a post threaded as a reply (receipts, and answers
released early by bounded ordering).

```
WARNING urllib3.connectionpool: Connection pool is full, discarding connection: api.massive.com. Connection pool size: 1
WARNING urllib3.connectionpool: Connection pool is full, discarding connection: api.massive.com. Connection pool size: 1
WARNING urllib3.connectionpool: Connection pool is full, discarding connection: api.massive.com. Connection pool size: 1
WARNING urllib3.connectionpool: Connection pool is full, discarding connection: api.massive.com. Connection pool size: 1
WARNING urllib3.connectionpool: Connection pool is full, discarding connection: api.massive.com. Connection pool size: 1
WARNING urllib3.connectionpool: Connection pool is full, discarding connection: api.massive.com. Connection pool size: 1
WARNING urllib3.connectionpool: Connection pool is full, discarding connection: api.massive.com. Connection pool size: 1
WARNING urllib3.connectionpool: Connection pool is full, discarding connection: api.massive.com. Connection pool size: 1
WARNING urllib3.connectionpool: Connection pool is full, discarding connection: api.massive.com. Connection pool size: 1
scenario busy: One group chat: 12 messages in 10 s, with a steer, a rewrite, a cancel, an escalation and a reply-to follow-up.
provider claude, ordering bounded, logs in runs/

── parallel transcript ──────────────────────────────────────────────────
  [  5.81s] desk         #2 ↩desk:m2  ETF stands for "exchange-traded fund." It's a basket of investments, like stocks or bonds, that trades on a stock exchange throughout the day just like an individual stock.
  [  6.17s] desk     status  Still working on #1 (What's NVDA's last close and how does it compar…). 1 answer queued right behind it.
  [  7.37s] desk        ack ↩desk:m5  Adding that to #1.
  [  7.37s] desk        ack ↩desk:m6  Updating #3: Analyze the top holdings of SPY and their concentration risk
  [  7.40s] desk         #4 ↩desk:m4  No, the U.S. stock market (NYSE and Nasdaq) is closed on Saturdays and Sundays, so it's open Monday through Friday, roughly 9:30 a.m. to 4:00 p.m. Eastern Time, excluding exchange holidays. Some other markets and crypto exchanges trade on weekends, but regular stock trading does not happen on Saturdays.
  [  9.91s] desk        ack ↩desk:m9  Okay, dropping #5.
  [ 10.29s] desk         #6 ↩desk:m8  A P/E ratio is a company's stock price divided by its earnings per share over a set period, usually the last twelve months. It shows how much investors are paying for each dollar of the company's profit, so a higher number generally means investors expect more growth or are paying a premium.
  [ 11.71s] desk         #1  **On Oct 13, 2026, NVDA closed at $228.52, AMD at $605.53 and INTC at $102.81. AMD had the strongest session, then INTC, then NVDA.**

- **AMD:** opened at $577.50 and closed up about 4.9% from the open, near its high. Volume was about 17.9M shares.
- **INTC:** opened at $99.59 and closed up about 3.2% from the open, near its high of $102.88. Volume was about 88.5M shares, the most of the three.
- **NVDA:** opened at $224.34 and closed up about 1.9% from the open, near its high. Volume was about 84.0M shares.

All three ended the day near their highs. These are open-to-close moves, because I don't have the previous day's closes. Share prices alone don't show which company is bigger, but I can pull market caps if that helps.
  [ 13.05s] desk         #3  I can't pull SPY's current holdings or weights. My data tools only have basic fund details and prices, so I can't give an exact, up-to-date concentration analysis.

**What I could check:** SPY (State Street SPDR S&P 500 ETF Trust) closed at **$778.01 on Oct 13, 2026**.

**From general knowledge, not live data:**
- SPY tracks the S&P 500, and each company's weight depends on its market value.
- In recent years the top 10 holdings have made up roughly a third or more of the fund. They've mostly been large tech-related companies such as Nvidia, Microsoft, Apple, Amazon, Alphabet, Meta and Broadcom.
- **The risk:** a selloff in a few tech names could hit SPY much harder than "500 stocks" suggests.

For current weights, check State Street's SPY holdings page.
  [ 14.26s] desk         #7  Microsoft (MSFT) closed at **$543.45** on Tuesday, October 13, 2026. That's the most recent closing price I can get, so it may have changed since.

That day it opened at $544.64, traded between $543.06 and $551.15, and about 23.7 million shares changed hands. It closed a little below where it opened and near the day's low.
  [ 16.96s] desk         #8  Apple's earnings look riskier than Microsoft's, mainly because it depends heavily on one product. I couldn't get earnings dates, estimates or past earnings-day price moves, so this is based on how each business makes money, not on figures.

- **AAPL** (closed at $341.27 on Oct 13, 2026; market cap about $4.98T): iPhone is most of its sales. That ties results to upgrade cycles, consumer spending, China and outside manufacturers like Foxconn and TSMC. A weak iPhone quarter can move the whole report.
- **MSFT** (closed at $543.45 on Oct 13, 2026; market cap about $4.04T): sales come from three roughly equal segments, much of it recurring business software and cloud. That usually means steadier results. Its main risk is whether Azure cloud growth and AI spending meet expectations.
  [ 19.00s] desk         #9  **On Oct 13, 2026, NVDA closed at $228.52. That's below AMD ($605.53) and AVGO ($357.31) but above INTC ($102.81). Measured from each stock's open, AMD did best, then INTC, AVGO and NVDA.**

- **AMD:** $577.50 → $605.53, up about 4.9%, on 17.9M shares
- **INTC:** $99.59 → $102.81, up about 3.2%, on 88.5M shares, the most traded of the four
- **AVGO:** $349.60 → $357.31, up about 2.2%, on 17.3M shares
- **NVDA:** $224.34 → $228.52, up about 1.9%, on 84.0M shares

All four closed near their highs for the day. The gains are from that day's open, not the prior close. A higher share price doesn't mean a company is worth more.

metric                      parallel
────────────────────────────────────
messages                          12
requests admitted                  9
answers posted                     8
makespan (s)                   17.01
latency mean (s)                6.29
latency p50 (s)                 6.64
latency p95 (s)                 9.67
latency max (s)                 9.73
  stage: routing (s)            1.69
  stage: queued (s)             0.31
  stage: working (s)            3.72
  stage: held for order (s)        0.56
router calls                       8
quick head starts used             3
released early (bounded)           4
restarts (rewrites)                1
steers applied mid-run             1
escalations                        0
cancels                            1
status posts                       1
order violations                   0
stale posts                        0
duplicate posts                    0
missing posts                      0
lost steers                        0
input tokens                    5866
output tokens                   5009
cache read tokens              24222
cpu share of wall time        0.0154
```
