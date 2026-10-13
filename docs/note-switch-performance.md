# Note switching measurements

The 50 ms budget is **not met**. This change removes the seconds-long stalls, but
must remain a draft until both first and repeated openings pass the budget.

## Measurement

```sh
uv run scripts/benchmark_note_switch.py /path/to/library.sqlite3 --output report.json
```

The script opens the supplied SQLite file read-only, backs it up into a temporary
library and uses isolated XDG directories. It selects each `test*` note through the
actual sidebar signal and waits for `after-paint`, the complete logical transcript
model and fully measured visible row heights. Each note's first opening is measured
without having previously loaded or rendered that note; subsequent cycles switch
between all three notes. Application startup is excluded. Reading the database,
constructing the model, binding controls, layout and painting are included.

It fails if **any** sample exceeds 50 ms. There is no transcript cache or hidden
preopening of the measured notes. The normal window initializes two reusable full
row widgets; reading widgets and cells are built on demand and then recycled.

## Results

Environment: Python 3.14.8, GTK 4.20, libadwaita 1.8, Linux with a two-vCPU quota,
Xvfb and the Cairo renderer. The app uses its default 1180 × 780 window.
Baseline UI: `d3510b7` (unchanged in `4905586`). The same sidebar/frame harness was
used for both implementations, with an equivalent row-height readiness check in
the baseline. The baseline has three repeat cycles; the change has ten.

The user-provided database contains these test notes; no transcript text, original
audio or database file is included in the repository.

| Note | Segments | Before: first | Before: repeat median | After: first | After: repeat median | After: repeat maximum |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| test1 | 494 | 2684.4 ms | 1616.7 ms | 143.6 ms | 106.7 ms | 275.4 ms |
| test2 | 210 | 2556.9 ms | 1736.9 ms | 121.0 ms | 57.2 ms | 86.1 ms |
| test3 | 1 | 155.8 ms | 89.5 ms | 107.3 ms | 41.4 ms | 89.3 ms |

Earlier phase profiling measured SQLite reads at approximately 2–9 ms. Most of the
baseline cost came from GTK constructing about 205 complete transcript rows, each
with a text view, timestamp popover and header controls, even though only a fraction
were visible. Resolving copies of every segment and eagerly constructing a GObject
for every row also made the work scale with transcript length.

The change uses a metadata index, lazily wrapped rows, cheap estimates for offscreen
cells and lightweight reading widgets. Full editors are allocated for interaction,
recording headers and live rows. Shared cached row identities and widget pools reduce
reconstruction on switching; the first 16 reading rows are prepared before layout
so common wrapped first-screen content gets its real height promptly. Other rows
are prepared when allocated near the viewport. A post-allocation resize replaces
estimates, including single-segment notes with long wrapped text.

Remaining work: reduce the first-frame text/control layout cost and tail latency.
The software-rendered measurements do not establish performance on a user's GPU,
but they also do not justify claiming either 50 ms requirement has passed.
