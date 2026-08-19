# T3P Multi-Monitor Support — Implementation Plan

**Status: Phases 0 and 1 COMPLETE** (2026-08-19); Phases 2 and 3 planned. See
"Phases 0 and 1 as landed" at the bottom for what was built and where it
deviated. Written 2026-08-18. Follows `docs/acdtool_rework_plan.md`
(COMPLETE) and reuses its machinery deliberately: the declarative shape table,
the header-driven column reader, the "one index axis per module" rule, and the
warn-naming-itself failure mode. Nothing in this plan invents a new pattern.

T3P writes **six** kinds of monitor output. LUME-ACE3P reads **one**. This plan
reads the rest.

Every format claim below was verified against real CW23 output, not inferred
from the reference. Where the reference and the shipped build disagree, the
build wins and the disagreement is recorded.

---

## Motivation

### The six monitor types

`references/t3p-commands.pdf` documents `Monitor` with six `Type` values:

| `Type` | What it measures | Output per the reference |
|---|---|---|
| `Point` | fields at one location vs. time | ascii `(t Hx Hy Hz Ex Ey Ez)`, SI |
| `Volume` | volumetric field snapshots | netCDF, for ParaView |
| `WakeField` | wake potential vs. `s` | ascii `(s W(s) I(s))` |
| `Power` | power through a boundary port | ascii, time [s] and power [W] |
| `SurfacePowerLoss` | loss on a lossy surface | ascii, time [s] and power [W] |
| `ModeVoltage` | waveguide-mode voltage at a port | ascii, time [s] and voltage [V] |

### What the code reads today

[`T3P.wake_monitor_name()`](../src/lume_ace3p/ace3p.py) finds the *first*
`Monitor` whose `Type` is `WakeField` and returns its `Name`;
`T3P.output_parser` reads `<job_name>/OUTPUT/<name>.out` through
`parse_wakefield` and stops. `T3PModule.QUANTITIES` is the closed set
`{loss_factor, kick_factor, W, I_bunch, s}`, and `T3PModule.field_index`
hardcodes `('s', array)`.

Consequence: a run declaring `Power` and `ModeVoltage` monitors produces those
files, LUME-ACE3P ignores them, and there is no error — the same silent-hole
failure mode Phase 3 of the acdtool rework replaced with
`AcdtoolOutputWarning`.

### Real fixtures exist (this is why the plan is low-risk)

Two CW23 runs cover four of the six types with **real output on disk**:

`CW23/examples/t3p/BPM/t3p_results/OUTPUT/` — a run with two `Point`
monitors, one `Power`, one `ModeVoltage`, one `Volume`, one `WakeField`:

| File | Monitor | Verified format |
|---|---|---|
| `point.out` | `Point` (`Name: point`) | 7 cols, **no header**, 4001 rows |
| `coaxpoint.out` | `Point` (`Name: coaxpoint`) | same |
| `port.out` | `Power` (`Name: port`) | 2 cols, **no header**, 4001 rows |
| `modecoeff.out` | `ModeVoltage` (`Name: modecoeff`) | 2 cols, **no header**, 4001 rows |
| `Bunch0.out` | none — always written | `##`-commented `t[sec] I[A]`, 2 cols |
| `volumets_t*ps.out` + `.out.mod` | `Volume` (`Name: volume`) | **netCDF binary** |
| `signal.out` | `acdtool postprocess coaxsignal` | already read (Phase 2) |
| `t3p.out` | T3P run log | KVC; see below |
| `dipole.dat` | unknown | **0 bytes** in every run inspected |
| `BBL1/` | unknown | empty directory |

`CW23/examples/t3p/SIBC/t3p_results/OUTPUT/` — three `Power` monitors, which is
the multi-instance case:

| File | Monitor | Verified format |
|---|---|---|
| `inputPower.out` | `Power` `ReferenceNumber: 4` | 2 cols, no header |
| `outputPower.out` | `Power` `ReferenceNumber: 5` | 2 cols, no header |
| `wallossPower.out` | `Power` `ReferenceNumber: 3` | 2 cols, no header |
| `port_4_Pin.out` | **undocumented** | 2 cols, no header; starts at a later `t` |
| `port_4_Vin.out` | **undocumented** | 2 cols, no header |
| `fieldts_t*ps.out` + `.out.mod` | `Volume` (`Name: field`) | netCDF binary |

`SurfacePowerLoss` is documented but has **no fixture** in CW23 — SIBC uses
`Power` on a lossy surface instead. Treat it exactly as Phase 3 treated the
unvalidated `.rfpost` blocks: implement from the reference, mark
`validated=False`, and let the reader be driven by the file.

### Confirmed facts, verified against CW23

1. **The series monitors are headerless.** `point.out`, `port.out`,
   `modecoeff.out` and the SIBC power files carry no header line of any kind.
   Column names therefore come from the reference, not the file — which is
   precisely the case `parse_column_file(path, columns=...)` and
   `acdtool.SIGNAL_COLUMNS` already exist for. **No new parsing code is
   needed.**

2. **`Volume` output is netCDF despite the `.out` extension.**
   `volumets_t000000000020ps.out` begins with the bytes `CDF\x02`; `file(1)`
   reports *NetCDF Data Format data*. Its `.out.mod` sibling is netCDF too. A
   text reader pointed at this glob reads binary garbage. **Record filenames,
   never parse** — the same treatment `SECTIONS` gives the `GRID` shape.

3. **`Bunch0.out` is emitted by every run and declared by no monitor.** It is
   the structural twin of acdtool's `[scaling]` section, and Phase 3's handling
   applies: read it unconditionally, outside the per-monitor loop.

4. **`t3p.out` carries the resolved monitor list.** Its `Input :` section is a
   normalized KVC echo of the whole input, including every `Monitor` block —
   readable by `parse_ace3p` with no changes. This matters because T3P
   *normalizes keys*: `BPM.t3p` writes `Start contour: -0.0055` (with a space)
   and the echo reports `Startcontour`. The input file is what a workflow can
   validate before the run; the echo is what the run actually used.

5. **Monitor names are not unique per type.** SIBC has three `Power` monitors.
   So `Type` alone cannot address a monitor — `Name` is the selector, and it is
   also the output filename stem.

6. **A run may have no `WakeField` monitor at all.** SIBC has none. This is
   already handled (`output_parser` returns empty rather than raising), but it
   is now the *common* case rather than an edge case, and it is what forces
   design decision 2.

7. **`acdtool postprocess transwake` writes more than `wakefield.out`.**
   `CW23/examples/t3p/cavity-half/t3p_results/OUTPUT/` also holds
   `wakefield.bnd` (a mesh-point list for the Laplace solve),
   `wakefield.z0.dat`, `wakefield.z1.dat` and `wakefield.z.all.dat` — 2-column
   tables whose `#(x, y)` first line records the transverse coordinate. These
   are the per-coordinate longitudinal wakes the Panofsky-Wenzel derivative is
   taken from, currently unread. Recorded here; see "Out of scope".

---

## Target design

### The `MONITORS` table

One row per `Type`, mirroring `acdtool.SECTIONS` field-for-field so the two read
the same way. Lives in `lume_ace3p.ace3p` beside `parse_wakefield` (the
postprocessor may depend on the solver layer, not the reverse — the Phase 5
rule).

```python
# Output shapes.
WAKE   = 'wake'     # (s, W, I_bunch) + header scalars -> parse_wakefield
SERIES = 'series'   # headerless (t, ...) column table -> parse_column_file
GRID   = 'grid'     # netCDF field dumps; recorded, never parsed

POINT_COLUMNS = ('t', 'Hx', 'Hy', 'Hz', 'Ex', 'Ey', 'Ez')

MONITORS = {
    'WakeField': Monitor(WAKE, files=('{name}.out',), axis='s',
                         validated=True),
    'Point': Monitor(SERIES, files=('{name}.out',), columns=POINT_COLUMNS,
                     axis='t', validated=True),
    'Power': Monitor(SERIES, files=('{name}.out',), columns=('t', 'P'),
                     axis='t', validated=True,
                     note='power through a boundary port [W]; a run may declare '
                          'several, addressed by Name'),
    'ModeVoltage': Monitor(SERIES, files=('{name}.out',), columns=('t', 'V'),
                           axis='t', validated=True),
    'SurfacePowerLoss': Monitor(SERIES, files=('{name}.out',),
                                columns=('t', 'P'), axis='t',
                                note='documented but UNVALIDATED — no CW23 run '
                                     'declares one; SIBC uses Power on a lossy '
                                     'surface instead'),
    'Volume': Monitor(GRID, files=('{name}ts_t*ps.out', '{name}ts_t*ps.out.mod'),
                      note='netCDF despite the .out extension (verified: leading '
                           'bytes are CDF\\x02) — recorded, never parsed'),
}

# Emitted by every run, declared by no monitor. The structural twin of
# acdtool's '[scaling]': read outside the per-monitor loop.
ALWAYS = {'Bunch0': Monitor(SERIES, files=('Bunch0.out',), axis='t',
                            validated=True)}
```

`Bunch0.out` does carry a `##` header naming its columns, and
`parse_column_file` already picks the last comment line whose token count
matches the data width, so it needs no explicit `columns`.

### Design decisions

1. **`Name` is the selector; `Type` supplies the shape.** An output spec names
   the monitor, and the table supplies its columns and axis:

   ```yaml
   'P_in'   : {module: t3p, monitor: inputPower,  quantity: P}
   'P_wall' : {module: t3p, monitor: wallossPower, quantity: P}
   'Ez_gap' : {module: t3p, monitor: point, quantity: Ez, at: {t: 1.0e-9}}
   ```

   `monitor:` may be omitted when the run declares exactly one monitor whose
   type provides the named quantity; ambiguity raises and lists the candidates,
   the way `WorkflowInputs._route_registry` handles a colliding bare name.

2. **One index axis per module, `s` winning over `t`.** A run with both a
   `WakeField` and a `Point` monitor has two incompatible axes.
   `T3PModule.field_index` returns `('s', ...)` when a `WakeField` monitor
   produced output and `('t', ...)` otherwise; everything not on the chosen axis
   rides in `field()`. This is acdtool design decision 2 applied verbatim, and
   the `s`-wins tiebreak is what keeps every existing baseline where it is.

3. **`at:` narrows to the nearest sample.** `at: {t: 1e-9}` and the existing
   `at: {s: 1.0}` both take the nearest grid point, because the time grid is a
   consequence of `TimeStepping: DT` rather than something a user can name
   exactly. Same reasoning already recorded in `T3PModule.extract`.

4. **Existing specs keep working, unchanged and unwarned.**
   `{module: t3p, quantity: kick_factor}` and bare `'W'` / `'s'` / `'I_bunch'`
   resolve to the `WakeField` monitor with no `monitor:` key. Unlike acdtool's
   positional list form, these are **not deprecated** — they are unambiguous
   whenever there is one wake monitor, which is every shipped example. **No
   baseline may move.**

5. **A declared monitor whose output is missing warns, naming itself.** New
   `T3POutputWarning`, alongside `S3POutputWarning` and
   `AcdtoolOutputWarning`. A whole run must not die because one monitor did not
   write, but neither may it vanish silently.

6. **The input file is the monitor list; `t3p.out` is the cross-check.** Read
   monitors from `_input_tree()` (available before the run, so a validation pass
   can use it) and, when `t3p.out` is present, warn if the resolved echo
   disagrees. Do not make the echo the primary source — it does not exist under
   dry-run.

7. **`Volume` provides no extractable quantity.** Asking for one raises naming
   the reason, exactly as a `CURVE`/`GRID` acdtool section does. Its filenames
   ride in `field()` so a plotting script can find them.

---

# Phase 0 — Freeze real T3P monitor fixtures

No `src/` changes. Everything downstream is validated against these files, so
they come first.

### Approach

Copy from CW23 into `tests/fixtures/acdtool/t3p_outputs/`, following the
existing naming (`<case>.<file>`) and the **20-data-row truncation** convention
already used for `BPM.signal.out` and `cavity-half.wakefield.out`:

| Target | Source (under `CW23/examples/t3p/`) | Treatment |
|---|---|---|
| `BPM.point.out` | `BPM/.../OUTPUT/point.out` | `head -n 20` of 4001 |
| `BPM.port.out` | `BPM/.../OUTPUT/port.out` | `head -n 20` of 4001 |
| `BPM.modecoeff.out` | `BPM/.../OUTPUT/modecoeff.out` | `head -n 20` of 4001 |
| `BPM.Bunch0.out` | `BPM/.../OUTPUT/Bunch0.out` | `head -n 22` (2 header + 20) |
| `SIBC.inputPower.out` | `SIBC/.../OUTPUT/inputPower.out` | `head -n 20` |
| `SIBC.wallossPower.out` | `SIBC/.../OUTPUT/wallossPower.out` | `head -n 20` |
| `BPM.t3p` | `BPM/BPM.t3p` | full — the 6-monitor input |
| `SIBC.t3p` | `SIBC/SIBC.t3p` | full — the 3-`Power` input |
| `BPM.t3p.out` | `BPM/.../OUTPUT/t3p.out` | full 168 lines (the `Input :` echo) |

Skip `coaxpoint.out` (same shape as `point.out`), `outputPower.out` (same shape
as its siblings), and every netCDF file — a `Volume` monitor is exercised by
`BPM.t3p`'s *input* block plus a synthetic empty file per glob, since the plan
never parses one.

Record provenance in `SOURCES.md` with a row per file, noting truncation, and
add a `COVERAGE.md` section keyed by monitor `Type` — the machine-readable
mirror being the `validated` flag, asserted the way
`test_section_table_covers_the_documented_block_surface` already asserts
`SECTIONS`.

### Note on the size budget

`SOURCES.md` records 141,735 B of fixture data against a **100–150 KB** budget.
The nine files above add roughly 12 KB, landing near 154 KB. Either truncate
`BPM.t3p.out` to its `Input :` section only, or revise the stated budget to
~160 KB in the same commit. Do not silently exceed it.

### Verification (Phase 0 done when)

- Characterization tests in a new `tests/test_t3p_monitor_fixtures.py` pin the
  column count and first/last row of every series fixture.
- A test asserts `parse_column_file('BPM.point.out', columns=POINT_COLUMNS)`
  yields 7 arrays of equal length — i.e. the existing reader already handles
  these files, *before* any table exists.
- A test asserts `parse_ace3p` on `BPM.t3p` finds 6 `Monitor` sections with the
  expected `(Type, Name)` pairs, including the space-in-key
  `Start contour`.
- `SOURCES.md` and `COVERAGE.md` updated; size gate re-measured and stated.

### Deliverables

Fixtures, `SOURCES.md`, `COVERAGE.md`, `tests/test_t3p_monitor_fixtures.py`. No
`src/` change, no baseline change.

---

# Phase 1 — `MONITORS` table and solver-layer reading

### Approach

In `lume_ace3p/ace3p.py`:

1. Add the `Monitor` class and `MONITORS` / `ALWAYS` tables above, plus
   `POINT_COLUMNS` and `T3POutputWarning`.
2. Add `T3P.monitors()` returning `[(type, name)]` for every `Monitor` section
   in `_input_tree()`, in file order. Keep `wake_monitor_name()` as a thin
   wrapper over it — it is public-ish and its docstring is referenced from
   `T3PModule`.
3. Rewrite `T3P.output_parser` to loop the table. Target shape for
   `output_data`:

   ```python
   {
     # unchanged: the wake monitor's keys stay at top level
     's': ..., 'W': ..., 'I_bunch': ..., 'KickFactor': ..., 'WakeType': ...,
     # new: one entry per non-wake monitor, keyed by Name
     'Monitors': {
        'point':      {'Type': 'Point',  't': ..., 'Ex': ..., ...},
        'inputPower': {'Type': 'Power',  't': ..., 'P': ...},
        'volume':     {'Type': 'Volume', 'files': [...]},
     },
     'Bunch0': {'t': ..., 'I': ...},
   }
   ```

   **Keeping the wake keys at top level is load-bearing** — it is what makes
   decision 4 (no baseline moves) true by construction rather than by a
   compatibility shim.

4. Warn (`T3POutputWarning`) per declared monitor with no output file, naming
   the monitor, the expected path, and its `validated` flag — the message shape
   `read_mode_table` already uses.

### Verification (Phase 1 done when)

- Every Phase-0 fixture parses to the documented shape, checked against the
  frozen values.
- `SIBC.t3p` (no `WakeField`) parses to a populated `Monitors` with three
  `Power` entries and **no** `s`/`W` keys, and does not warn.
- `cavity-half.wakefield.out` parses **byte-identically to today** — same keys,
  same values. Pin this with an explicit equality test, not just a baseline run.
- A declared-but-missing monitor raises `T3POutputWarning` naming itself.
- `pytest` green; **no baseline moves.** Phase 1 touches no module and no mode,
  so any baseline movement here is a bug, not an update.

### Deliverables

`src/lume_ace3p/ace3p.py`, `tests/test_ace3p.py` additions. No module-layer
change yet, no example change, no baseline change.

---

# Phase 2 — Module layer: axis, `extract`, `field`

### Approach

In `lume_ace3p/modules.py`, `T3PModule` only:

1. **`extract`** — extend `_parse_spec` to read `monitor:` and `at: {t: ...}`.
   Resolution order: an explicit `monitor:`; else the wake monitor when the
   quantity is one of the legacy five; else the unique monitor whose type
   provides that quantity; else raise listing the candidates. Reuse
   `AcdtoolModule._value`'s error style — report what the monitor *did*
   produce.
2. **`field_index`** — decision 2. `('s', ...)` when a wake result is present,
   `('t', ...)` from the first `t`-axis monitor otherwise, `None` when neither.
   Keep the dry-run single-row `[0.0]` sentinel for whichever axis is chosen,
   since T3P's axes are both declared by the input file (unlike Omega3P's mode
   count) — the reasoning already in the docstring.
3. **`field`** — return the wake result plus `Monitors` and `Bunch0`. Confirm the
   nested dicts survive `results.save_field` round-tripping; they are the same
   shape as S3P's `PortRef<n>_<m>` entries, which already do.
4. **`QUANTITIES`** — this frozenset is read by
   `workflow_graph._infer_output_module` to route bare specs. Widen it with
   care: `'P'`, `'V'`, `'t'` are short and generic, and `'t'` in particular
   risks colliding with future bare specs. **Recommendation: do not add them.**
   Require `module: t3p` (or `monitor:`) for the new quantities and leave bare
   routing exactly as it is. This keeps `_infer_output_module` untouched.

### Verification (Phase 2 done when)

- `examples/t3p_sweep` and `examples/t3p_transwake` produce **identical**
  tables to their frozen baselines.
- A synthetic workflow over the `SIBC` fixtures extracts all three powers by
  name and errors informatively on a bare `quantity: P` (three candidates).
- A workflow over `BPM` returns `('s', ...)` as its field index while carrying
  the `Point`/`Power`/`ModeVoltage` series in `field()`.
- Asking a `Volume` monitor for a quantity raises naming the reason.
- `pytest` green; **no baseline moves.**

### Deliverables

`src/lume_ace3p/modules.py`, `tests/test_modules.py` additions.

---

# Phase 3 — Docs and an example

### Approach

1. `docs/yaml_reference.md` — extend the `t3p` module section with `monitor:`,
   the per-type quantity table, the axis rule, and the "no `monitor:` when
   unambiguous" shorthand.
2. `docs/t3p_reference.md` (new, modelled on `docs/acdtool_reference.md`) —
   the six types, what each writes, which are validated, and the recorded
   unknowns (`dipole.dat`, `BBL1/`, `port_4_*.out`).
3. **One new example**, `examples/t3p_power_balance/`, from the SIBC case:
   three `Power` monitors giving in / out / wall-loss on one run, with the
   balance as a derived column. This is the workflow the package could not
   express before, and it is the honest demonstration — a `Point` monitor
   example would only re-plot something the field artifact already carried.
4. Update `docs/acdtool_reference.md` where it says a transwake result is the
   only thing T3P exposes.

### Verification (Phase 3 done when)

- The new example runs to a frozen baseline (dry-run in CI).
- `pytest` green, docs build clean.

---

## Out of scope

Recorded so the gaps are visible rather than forgotten:

- **`transwake`'s extra files** (`wakefield.z0.dat`, `z1.dat`, `z.all.dat`,
  `wakefield.bnd`) — confirmed fact 7. Small and well-shaped (a `#(x, y)`
  header over two columns), but they belong to the acdtool command table, not
  the monitor table. Natural follow-on.
- **`Volume` monitor netCDF parsing.** `netCDF4` is not a core dependency and
  nothing in the package consumes a field snapshot. Filenames only.
- **`port_4_Pin.out` / `port_4_Vin.out`** — undocumented, appear with a
  waveguide port. Record in `t3p_reference.md`, do not read.
- **`CheckPoint` / restart orchestration.** A T3P sweep point cut off by the
  wall clock still restarts from scratch; the `t3p_sweep` batch script says so.
  Unrelated to monitors.

## Adjacent finding — do not fold into this plan

While verifying the fixtures: **every ACE3P solver takes the results directory
as an optional second positional argument.** From CW23 batch scripts —
`omega3p SRFCell.omega3p omega3p_results`, `tem3p SRFCell.tem3p tem3p_results`,
`track3p Pillbox2.3MV.track3p 2.3MV`, `s3p FPC-Vacuum.s3p s3p_results` (14
invocations in total use it; the rest rely on the default).

`ACE3P.run()` does not pass it. So a module-level `results_dir:` currently tells
LUME-ACE3P where to **look** without telling the solver where to **write** —
which means setting it to anything other than the solver's default should
mislead the reader. This is stronger evidence than the `JobName` echo that
`SOURCES.md` records, and it supersedes the resolution order documented in
`ACE3P.job_name()`.

Fix it in its own change, with its own test, before or after this plan. Folding
a job-name change into a monitor change would put two independent reasons for a
baseline to move into one commit.

---

# Phases 0 and 1 as landed (2026-08-19)

## What was built

**Phase 0** — nine fixtures in `tests/fixtures/acdtool/t3p_outputs/`
(`BPM.{point,port,modecoeff,Bunch0}.out`, `SIBC.{inputPower,wallossPower}.out`,
`BPM.t3p`, `SIBC.t3p`, `BPM.t3p.out`), a `t3p_outputs` provenance section and a
per-`Type` coverage section in `SOURCES.md` / `COVERAGE.md`, and 24
characterization tests in `tests/test_t3p_monitor_fixtures.py`. No `src/` change.

**Phase 1** — in `src/lume_ace3p/ace3p.py`: `T3POutputWarning`, the `Monitor`
class, `MONITORS` / `ALWAYS`, `POINT_COLUMNS` / `BUNCH_COLUMNS`, the module
functions `monitor_identity` and `read_monitor`, and on `T3P` the new
`monitors()` / `echoed_monitors()` plus a rewritten `output_parser`.
`wake_monitor_name()` is now a wrapper over `monitors()`. 29 tests added to
`tests/test_ace3p.py`. `pytest` green; **no baseline moved** — the shipped
examples' baselines are dry-run, where `_solver is None` and `output_parser` is
never reached at all, and the wake-only parse is pinned equal to
`parse_wakefield`'s own return by
`test_t3p_wake_only_run_parses_byte_identically_to_before`.

## Deviations from the plan above

1. **`Bunch0.out` gets explicit column names after all.** The plan says it "needs
   no explicit `columns`" because it carries a `##` header. It does carry one, and
   the header-driven reader reads it correctly — as `t[sec]` and `I[A]`, **with
   units**, which cannot double as the module layer's `t` index axis. Its other
   comment line (`## Bunch distribution`) is also two tokens wide, so
   "last-match-wins" is the only thing keeping *that* from becoming the column
   names. `BUNCH_COLUMNS = ('t', 'I')` is passed explicitly and both facts are
   pinned (`test_bunch0_header_names_carry_units`).

2. **The size gate moved to 100–160 KB, and `BPM.t3p.out` was kept whole.** The
   plan offered truncation *or* a revised budget. Measured: the nine files add
   14,927 B for a data total of 156,662 B, and truncating `BPM.t3p.out` to its
   `Input :` section recovers only ~2.6 KB — so neither file list reaches 150 KB
   and the gate was the thing that had to move. The banner is also the part that
   proves `parse_ace3p` reads a *T3P* log unmodified, which the `omega3p.out`
   fixtures prove only for Omega3P. Recorded in `SOURCES.md`, not silently.

3. **Design decision 6 (the `t3p.out` cross-check) landed in Phase 1**, though the
   Phase-1 approach list does not enumerate it. It is solver-layer work, and
   Phase 0 freezes `BPM.t3p.out` specifically for it — leaving it out would have
   frozen an unused fixture. `echoed_monitors()` returns `None` with no log, so
   dry-run is not a disagreement.

4. **Two one-line module-layer guards, despite "Phase 1 touches no module."**
   Phase 1 makes `output_data` non-empty for a wake-less run that *did* write
   monitor output, and `T3PModule.field_index` / `extract` both tested for that
   case with `if not data`. `field_index` is called on every sweep, so a
   SIBC-shaped run would have hit a bare `KeyError('s')` in the window between
   Phase 1 and Phase 2. Both now test `if 's' not in data`, which is behavior-
   identical for every case reachable before Phase 1 and is superseded wholesale
   by Phase 2. Marked as such in both docstrings.

5. **Three warnings the plan did not enumerate**, alongside the missing-output one
   it did: an unknown `Monitor` `Type` (a newer build's seventh type), a monitor
   with no `Name` (nothing to look for, since `Name` is the filename stem), and
   two monitors sharing a `Name` (they write the same file, so the second is
   unaddressable). All three are silent holes of exactly the kind decision 5
   exists to close.

## Two findings for Phase 3's `t3p_reference.md`

* **The T3P reference contradicts itself on the type count.** Its `Monitor`
  overview says "namely, Point, Volume, WakeField, Power and ModeVoltage" — five —
  while `SurfacePowerLoss` has its own `Type` bullet *and* its own numbered
  subsection (5). Six is the right number; the overview sentence is stale. This is
  the same class of defect as `RoverQRoverQT` being absent from the acdtool guide's
  own functionality list.
* **A `WakeField` monitor may carry a `Grid:` sub-block** (`Method`, `Radius`,
  `NPoints`, `StartDense`, `EndDense`, `Fraction`) for direct integration on
  collimator-type structures. No CW23 input uses one, and it changes nothing about
  the output shape, but it is a documented input surface with no fixture — worth
  listing beside `SurfacePowerLoss` in what a real run is still owed.
