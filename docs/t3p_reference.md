# T3P reference

T3P is ACE3P's time-domain solver. Most of its interesting output does not come
from the solver's own log but from **monitors** — blocks in the `.t3p` input file
that ask for a particular measurement, each writing its own file named after the
monitor's `Name`. There are **six** monitor types, and LUME-ACE3P used to read
one.

This page is the map of that surface: what each type writes, which of them have
real output behind them, and what else lands in a T3P results directory that
nothing reads. For how to *use* it from a YAML config, see
[](yaml_reference.md#t3p-module); for a worked example of the multi-monitor case,
`examples/t3p_power_balance`.

:::{note}
**Sources.** `references/t3p-commands.pdf` is the authoritative document, and like
every ACE3P solver reference it specifies **inputs thoroughly and outputs barely**.
So every output format below comes from real runs frozen as fixtures under
`tests/fixtures/acdtool/t3p_outputs/`, whose provenance is in that directory's
`SOURCES.md` and whose per-type coverage is in its `COVERAGE.md`. Where a type has
no real output behind it, this page says so.

The table here is a digest of `lume_ace3p.ace3p.MONITORS`, which is what actually
drives the reading. If the two disagree, the code is right.
:::

## The six `Monitor` types

Every monitor block carries a `Type` and a `Name`. **`Name` is the selector**: a
run may declare several monitors of one type — CW23's `SIBC` case declares three
`Power` monitors — so the type cannot address one of them. `Name` is also the
output filename stem.

| `Type` | Measures | Output file | Columns | Real output? |
|---|---|---|---|---|
| `WakeField` | wake potential vs. the wake coordinate | `<Name>.out` | `s`, `W`, `I_bunch` under a header carrying the loss/kick factor | **yes** |
| `Point` | fields at one location vs. time | `<Name>.out` | `t Hx Hy Hz Ex Ey Ez`, SI | **yes** |
| `Power` | power through a boundary port | `<Name>.out` | `t`, `P` — s and W | **yes** (3 files) |
| `ModeVoltage` | generalized voltage of a waveguide mode at a port | `<Name>.out` | `t`, `V` — s and V | **yes** |
| `SurfacePowerLoss` | loss on a lossy surface | `<Name>.out` | `t`, `P` — s and W | **no fixture at all** |
| `Volume` | volumetric field snapshots for ParaView | `<Name>ts_t*ps.out` + `.out.mod` | **netCDF** — not parsed | input only |

Plus one file no monitor declares:

| File | Measures | Columns | Real output? |
|---|---|---|---|
| `Bunch0.out` | the bunch current profile T3P loaded | `t`, `I` — s and A | **yes** |

`Bunch0.out` is written by **every** run and is the structural twin of acdtool's
`[scaling]` section: read outside the per-monitor loop, and addressable as
`monitor: Bunch0`.

## Things the files do that the reference does not say

Each of these has bitten a reader of the tutorial output.

| | |
|---|---|
| **Every series monitor is headerless** | `point.out`, `port.out`, `modecoeff.out` and the power files carry no header line of any kind — not commented, not uncommented. Their column names come from the reference and nowhere else, which is exactly the case `parse_column_file(path, columns=…)` already existed for. **No new parsing code was needed to read them.** |
| **`Volume` output is netCDF despite the `.out` extension** | `volumets_t000000000020ps.out` begins with the bytes `CDF\x02`, and `file(1)` reports *NetCDF Data Format data*; its `.out.mod` sibling is netCDF too. A text reader pointed at that glob reads binary garbage, so the filenames are recorded and never parsed — and a `Volume` monitor therefore provides no extractable quantity. |
| **`Bunch0.out`'s header names carry units** | Its `##` header reads `t[sec]    I[A]`, which cannot double as the `t` index axis — and its *other* comment line (`## Bunch distribution`) happens to be the same two tokens wide, so header inference would be fragile even if the units were absent. The column names are supplied explicitly. |
| **T3P normalizes input keys in its own echo** | `BPM.t3p` writes `Start contour: -0.0055` **with a space**; the `Input :` echo inside `t3p.out` reports `Startcontour`, and the reference spells it `StartContour`. All three are the same key. Read monitors from the input file — it exists before the run and under dry-run — and treat the echo as a cross-check on `(Type, Name)` pairs only. |
| **A run may have no `WakeField` monitor at all** | `SIBC` has none. That was always tolerated; it is now the case that produces a `t`-indexed result table rather than nothing. |
| **The wake file's units disagree with the document** | The reference gives `W(s)` in V/C and `I(s)` in A/m; the real file header says **V/pC** and **C/m**. The file header is authoritative and is what is read. |
| **`t3p.out` swallows text into key names** | The leading `/* … */` block comment and the trailing license banner are absorbed into key names, because the KVC tokenizer strips only `//` comments — the same harmless garbage `omega3p.out` produces. `Input` is still reachable, which is all that matters. |

## The two gaps

**`SurfacePowerLoss` has no fixture in either direction** — not even an input
example. No CW23 run declares one: `SIBC` measures loss on its coated wire with a
`Power` monitor on the impedance surface instead, which is what
`examples/t3p_power_balance` does too. The reference gives `SurfacePowerLoss` the
same two-column time/power output `Power` has, and the reader is the shared one, so
the exposure is narrow — the file's own width is what the reader follows — but it
is marked `validated=False` and its "monitor wrote nothing" warning says so. **If
you run one, the output format is worth reporting back.**

Worth noting that the reference is internally inconsistent here: its `Monitor`
overview says *"namely, Point, Volume, WakeField, Power and ModeVoltage"* — five —
while `SurfacePowerLoss` has its own `Type` bullet and its own numbered subsection.
Six is the right number; the overview sentence is stale. (The acdtool guide has the
same class of defect: `RoverQRoverQT` is documented in its body and absent from its
own functionality list.)

**A `WakeField` monitor's `Grid:` sub-block is undemonstrated.** The reference
documents `Grid: {Method: Circle, Radius, NPoints, StartDense, EndDense,
Fraction}` for **direct** wakefield integration on collimator-type structures,
paired with `acdtool postprocess wake_direct`. No CW23 input declares one. It
changes nothing about the output shape — a wake file is a wake file — so it passes
through like any other input block, but it is an input surface with no example
behind it.

## What else is in a T3P results directory

Everything lands under `<jobname>/OUTPUT/` (`t3p_results` by default; see
[](yaml_reference.md#omega3p-module) for how the jobname resolves).

T3P is the one solver whose jobname `lume-ace3p` cannot *select*. Every other
solver takes the results directory as a second positional argument, and the
`omega3p`/`s3p`/`track3p` modules pass their `results_dir:` through as one. No T3P
invocation in CW23 does that — all eight rely on the default — and no reference
documents an ACE3P command line either way, so `t3p` is not given the argument
rather than being guessed at. The practical consequence: `results_dir:` on a `t3p`
module moves only where `lume-ace3p` reads, so use it to *follow* a `JobName` leaf
in the `.t3p` file, never to impose one. Given a single CW23-style invocation
proving `t3p` accepts the argument, flipping `T3P.accepts_results_dir_arg` to
`True` is the whole change.

Besides the monitor files above:

| File | What it is | Status |
|---|---|---|
| `t3p.out` | the run log — KVC syntax, with an `Input :` echo of the whole resolved input including every `Monitor` block and the `JobName` the solver used | read as the monitor cross-check |
| `postprocess.in` | the same echo, written for acdtool's benefit | not read; it is the evidence behind the jobname resolution order |
| `CHECKPOINT/` | restart state, when a `CheckPoint` block asks for it | passed through; **restarts are not orchestrated** — see below |
| `wakefield.bnd`, `wakefield.z*.dat`, `wakefield.z.all.dat` | `acdtool postprocess transwake`'s other outputs: the mesh-point list for its Laplace solve, and the per-coordinate longitudinal wakes the Panofsky-Wenzel derivative is taken from (2-column tables under a `#(x, y)` first line) | **not read.** Small and well-shaped; they belong to the acdtool command table rather than the monitor table, so they are the natural follow-on |
| `signal.out` | `acdtool postprocess coaxsignal`'s output, three headerless columns `t V I` | read by the `acdtool` module — see [](acdtool_reference.md) |
| `port_<n>_Pin.out`, `port_<n>_Vin.out` | two headerless columns each, appearing with a waveguide port and starting at a later `t` than the run does | **undocumented, not read.** No monitor declares them |
| `dipole.dat` | **0 bytes** in every CW23 run inspected | unknown, not read |
| `BBL1/` | an **empty directory** in every CW23 run inspected | unknown, not read |

## Two things LUME-ACE3P does not do for you

**Disk.** A `Volume` monitor writes a full field dump per sampled timestep — tens
to hundreds of MB per run, multiplied by every point in a sweep (the tutorial's
`BPM` case is ~470 MB for a single run). LUME-ACE3P writes whatever monitors your
input file declares and does not prune or rewrite them. Widen the monitor's
`TimeStep` or delete the block.

**Checkpoint/restart.** A `CheckPoint` block is passed through like any other
input section and T3P will write `t3p_results/CHECKPOINT`, but LUME-ACE3P will not
detect an existing checkpoint or set `Action: restart` for you. A sweep point that
exceeds its wall time restarts from scratch on re-run, so size the allocation for a
full run.

## Related

- [](yaml_reference.md#t3p-module) — the `monitor:` key, the per-type quantity
  names, the axis rule, and `at:` narrowing.
- [](acdtool_reference.md) — the three time-domain `postprocess` commands that
  chain after T3P, one of which **overwrites** the wake monitor's output.
- `plans/t3p_monitor_plan.md` — how the multi-monitor support was built, and every
  place the implementation deviated from its plan.
- `tests/fixtures/acdtool/COVERAGE.md` — the machine-checked version of the "real
  output?" column above.
