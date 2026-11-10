# Cubit Multi-File Journals — Implementation Plan

**Status: IN PROGRESS** — written 2026-11-10 on S3DF against `dev` at `1ef749f`
(0.9.2). Phases 1 and 2 done 2026-11-10.

Users split their Cubit work across several files: one declares the APREPRO
variables, one imports STEP/STL/SAT geometry, one meshes and exports. A
`cubit` module today takes exactly one all-in-one journal. This plan lets one
`cubit` module take an **ordered list of journals (and static Python scripts)**
plus a list of **files that are only staged** (geometry the journals import),
and run them all in **one Cubit session**.

Decisions taken with the user (2026-11-10):

1. One module, ordered list, one session (not a chain of `cubit` modules, not
   following `playback`/`include` out of a master journal).
2. Variables: edit the first declaration across the list. Error if a `cubit:`
   input is declared nowhere. Warn if it is declared in more than one file.
3. Mesh name = the last `export` across the list.
4. Strip `quit`/`exit` from every journal but the last.
5. The hash covers every listed file's contents (journals and staged files).
6. Two listed files with the same basename are refused with an error.
7. Fix the existing substring bug in the `name=` search.
8. Python scripts run as static files: never edited.

---

## 1. Cubit behavior (verified)

From the bundled Cubit 16.12 help
(`/sdf/group/rfar/software/Cubit-16.12/bin/help/environment_control/session_control/execution_command_syntax.htm`):
`cubit [options and args] [journalFile(s)]` — "Input files to be read and
executed by CUBIT. Files are processed in the order listed, and afterwards
interactive command input can be entered (unless the -batch option is used)."
Our `cubit` launcher starts `claro` unless `-nogui` is passed, and claro
accepts Python files in the same list.

Probe job `44177451` (milano, Rocky 9.6, Cubit 16.12; script and outputs in
`/sdf/scratch/users/d/dbizzoze/cubit_multifile_probe/`, not in the repo):

| Case | Result |
|---|---|
| `cubit … vars.jou geom.jou mesh.jou` | works; APREPRO variables persist across files |
| `quit` at the end of the first file | session ends there, **rc 0, no mesh** |
| last file has no `quit`, stdin `/dev/null` | exits normally |
| last file has no `quit`, stdin an open pipe (≈ interactive terminal) | **hangs** after writing the mesh |
| same with `-batch` | exits normally |
| `.py` between journals (`-batch`) | runs; reads `r` from an earlier journal; `cubit.cmd('#{r = 12}')` redefines it for later files |
| driver journal of `playback "…"` lines | identical to the command-line list |
| relative `import step "part.step"` in the cwd | works |

Consequences: pass the files on the command line (no generated driver
journal) and **always add `-batch`**, which also covers a single journal that
lacks `quit`. A mid-list `quit` would fail silently, so stripping it is
required, not cosmetic.

## 2. Where the code stands

- [`Cubit`](../src/lume_ace3p/cubit.py) wraps one `input_file`: copies it
  into the workdir, `input_parser` flags non-`##` lines, `set_value` edits the
  first line where `key + '='` appears **as a substring** (so `radius`
  matches `cav_radius=`; a single-`#` comment such as `# r=99` also matches),
  `get_export`/`set_export` read and write the last/first `export`,
  `run` executes `cubit -nographics -nojournal -noecho <file>` (no `-batch`),
  then meshconvert. An input that matches nothing is silently ignored (the
  warning is commented out).
- [`CubitModule`](../src/lume_ace3p/modules.py#L780): `journal:` (alias
  `cubit_input:`), `meshconvert:`. `input_files()` returns `[journal]`, so the
  hash covers that file. [`_journal_export`](../src/lume_ace3p/modules.py#L757)
  names the mesh for `run` (resume), `clean`, `_mesh_path`, `verify`, and
  [`_validate_disk_collisions`](../src/lume_ace3p/workflow_graph.py#L443).
- Imported geometry is never staged: a relative `import step` path only works
  if the user copies the file into the workdir.
- The `cubit:` bucket is **shared**: unscoped `cubit:` inputs go to every
  `cubit` module, Particles/FieldEmission read legacy β from it, and an
  optimizer variable that routes nowhere lands there
  ([workflow_graph.py:1051](../src/lume_ace3p/workflow_graph.py#L1051)).
- Every example journal declares variables as one `#{name = value}` per line;
  commented-out declarations use `##{…}` / `###{…}`.

## 3. Design

### 3.1 Config

```yaml
- module : cubit
  journal : ['vars.jou', 'geometry.jou', 'tweak.py', 'mesh.jou']  # run in order
  files   : ['cavity.step', 'coupler.stl']                         # staged only
  meshconvert : true
```

- `journal:` accepts a string (today's form, unchanged) or a list. `.py`
  entries are Python scripts: staged and run, never edited or stripped.
- `files:` (string or list) — same key and meaning as on the solvers: staged
  into the workdir by basename, never edited, never run. Missing files fail
  before the first point (`required_files`).
- Absolute paths inside journals keep working. Relative paths resolve in the
  workdir, which is what `files:` provides.

### 3.2 Variable editing (the bug fix included)

A declaration is a non-comment line matching, for an exact identifier `name`:

- APREPRO: `^\s*#\{\s*name\s*=(?!=)` … `}` (the current `#{…}` form), or
- plain: `^\s*name\s*=(?!=)` (kept for parity with the current `else`
  branch).

A line is a comment if it starts with `#` but not `#{` (this covers `##{…}`
and single-`#` text). For each input, search the journals in list order and
edit the **first** declaration. Python files are not searched.

- Declared in more than one journal → warning naming the files (the first is
  edited; a later re-declaration would override it at run time, which is the
  user's journal logic, not ours). Same for a later Python file that redefines
  it: we can't see it, so it's documented, not detected.
- Multi-assignment lines (`#{a = 1, b = 2}`) are not supported. Such a name
  reads as undeclared and errors (3.5), with a message saying one declaration
  per line.

`get_value` gets the same matcher.

### 3.3 Running

For each journal in order: parse, apply variables, and on every journal but
the last **comment out** `quit`/`exit` lines (first token, case-insensitive),
prefixing them with `## lume-ace3p: ` so the written copy shows what changed.
Write each copy into the workdir (keep today's `_copy` suffix rule when the
workdir is the source dir). Python files are staged as-is. Then one
invocation:

```
cubit -batch -nographics -nojournal -noecho a.jou b.jou c.py d.jou
```

`-batch` is added for the single-journal case too (the command line is not
hashed, and the probe shows it only matters when the session would otherwise
wait for input).

### 3.4 Mesh name

`_journal_export` becomes `_journals_export(journals)`: the last `export` of
the last journal that has one (Python files skipped). Every current caller
(`run`, `clean`, `_mesh_path`, `verify`, `_validate_disk_collisions`, dry-run
naming) switches to it. A mesh exported only from a Python script is not
found. That falls back to today's no-export behavior (see open question 1).

### 3.5 Validation (before the first point)

- **Basename collisions:** within one module, two entries across `journal:` +
  `files:` with the same basename → `WorkflowValidationError` naming both
  paths. Across `cubit` modules sharing a workdir, the same basename from
  *different* source paths → error too (that one is a latent bug today for
  single journals).
- **Undeclared variables**, a check across the workflow since the bucket is
  shared:
  - a scoped input (`cubit:<name>/var`) must be declared in that module's
    journals;
  - an unscoped `cubit:` input must be declared in at least one `cubit`
    module's journals, **or** be read by a module that falls back to the
    `cubit` bucket (Particles/FieldEmission β).

  Otherwise `WorkflowValidationError`, listing the names and the journals
  searched. This changes behavior for configs that carry a stray `cubit:`
  name today, so Phase 4 surveys every example and test config first.

### 3.6 Hash compatibility

`module_hash` covers the config entry plus `input_files()` contents. A string
`journal:` with no `files:` produces the same entry and the same
`input_files()` → **existing hashes do not move** (asserted by a test pinning
a current hash). A list hashes every journal's contents. `input_files()` also
returns `files:` (decision 5), so editing a STEP file re-runs the step.

## 4. Phases

1. **Matcher fix** in `cubit.py` (3.2, single journal): exact-identifier
   matching, single-`#` comments ignored. Tests: `radius` vs `cav_radius`,
   `# r=99` comment, `##{…}`, `get_value`. Example journals must produce
   byte-identical rewritten copies (dry-run baselines unchanged).
   **DONE:** `Cubit.find_declaration`, `tests/test_cubit.py`; 230 example
   journal rewrites (every declared name plus every example `cubit:` input)
   are byte-identical to 0.9.2.
2. **Multi-journal `Cubit` wrapper** (3.3, 3.4): list input, per-file
   rewrite, `quit` stripping, `-batch`, export across the list. Fake-binary
   tests assert the command line, the rewritten copies, and file order.
   **DONE:** `Cubit` takes a path or a list (`_Journal` per file); copies are
   named by basename (an absolute path outside the workdir no longer writes
   `<path>_copy` beside the source); basename clash and Python-only lists
   raise `ValueError`; a name declared in several journals warns;
   `set_export` now edits the last export, the one `get_export` reads.
   Checked against real Cubit 16.12 on milano (`cubit_phase2_real/` in scratch).
3. **`CubitModule` config** (3.1, 3.4, 3.6): `journal:` list, `files:`
   staging, `input_files`/`required_files`, all `_journal_export` callers,
   hash-pinning test for a string `journal:`.
4. **Validation** (3.5): collision and undeclared-variable checks. Survey
   `examples/` and `tests/` configs for stray `cubit:` names first. If any
   real example trips it, stop and ask before choosing warning vs error.
5. **Example, docs, S3DF run:** split `examples/omega3p_sweep`'s journal into
   a new example (vars / geometry / mesh, plus a STEP file in `files:`).
   Update `docs/yaml_reference.md` (cubit row) and the Cubit section of
   `docs/workflow_inputs.md`. Run it on milano and compare against the
   all-in-one `omega3p_sweep` table (same Rocky 9 Cubit, so it should be
   identical).

## 5. Open questions

1. A mesh exported from a Python script can't be read from the files. Add an
   optional `mesh:` key naming the export (overrides 3.4), or require the
   export in a journal? Proposal: add `mesh:` only when a user needs it.
2. Should `files:` also accept a directory (a folder of STEP parts)?
   Proposal: no, files only, until asked.
