"""Module layer for the module/workflow/mode architecture.

This is the bottom layer of the three-layer design: modules (here), the
declarative :class:`~lume_ace3p.workflow_graph.Workflow` DAG that orders them,
and the workflow-agnostic modes in :mod:`lume_ace3p.modes` that drive it.

A ``Module`` is a thin adapter over one of the existing step wrappers
(``Cubit``, ``Omega3P``/``S3P``/``T3P``, ``Acdtool``, ``Particles``, ``Geant4``).
Each module declares the *artifact kinds* it ``requires`` (must exist upstream)
and ``provides`` (produces), so a future ``Workflow`` can order a declared list
of modules into a runnable DAG purely from those edges. The requires/provides
edges are deliberately additive — adding :class:`T3PModule` (``requires {mesh}``
/ ``provides {td_solution}``) needed no rule change, and :class:`Track3PModule`
slotted in the same way as ``requires {em_solution}`` / ``provides
{track3p_particles}``.

Each module also answers two questions about a *past* run, which is what makes
resume possible: :meth:`Module.verify` says whether its output is still on disk,
and ``run(ctx, skip_execution=True)`` re-reads that output instead of launching
the tool again. Both are documented on :class:`Module`.

The old skip-flags (``skip_cubit``/``skip_solver``/``skip_acdtool``) and the
``geant4_particle_file`` bypass have no place in this layer: in a declarative
module list, "skip X" is simply "do not list module X", and a prebuilt artifact
is expressed as a *source module*
(:class:`MeshSourceModule` / :class:`Track3PSourceModule` /
:class:`ParticleSourceModule`). The one exception is meshconvert, which is a
sub-step *inside* :class:`CubitModule` and stays a per-module ``meshconvert``
bool.
"""

import glob
import hashlib
import os
import shutil
import warnings

import numpy as np

from lume_ace3p.cubit import Cubit
# ALWAYS / MONITORS are the T3P monitor table; ace3p's own GRID shape constant is
# deliberately NOT imported, since acdtool exports one of the same name and value
# — T3PModule tests for a monitor's missing index axis instead.
from lume_ace3p.ace3p import (
    ALWAYS, MONITORS, Omega3P, S3P, T3P, Track3P, declared_field_levels,
    declared_monitors, impacts_summary, input_job_name, level_files,
    parse_ace3p, results_path,
)
from lume_ace3p.acdtool import (
    Acdtool, COMMANDS, CURVE, GRID, MODE_TABLE, RFPOST, SECTIONS, SURFACE,
    field_sections, mode_table_arrays, resolve_command, table_mode_ids,
    wired_commands,
)
from lume_ace3p.geant4 import Geant4, read_detector_dose, read_gamma_spectrum
from lume_ace3p.logs import log_path
from lume_ace3p.particles import Particles, default_output_name
from lume_ace3p.inputs import WorkflowInputs, _walk_ace3p


# --------------------------------------------------------------------------- #
# Artifact-kind vocabulary — the strings modules glue on. A module's
# ``requires``/``provides`` are sets drawn from this vocabulary.
# --------------------------------------------------------------------------- #

JOURNAL = 'journal'                    # Cubit journal file
MESH = 'mesh'                          # genesis/ncdf mesh (cubit+meshconvert, or provided)
EM_SOLUTION = 'em_solution'            # Omega3P/S3P frequency-domain solution
TD_SOLUTION = 'td_solution'            # T3P time-domain solution (wakefields)
RF_POST = 'rf_post'                    # acdtool postprocess results
TRACK3P_PARTICLES = 'track3p_particles'  # raw Track3P dump (track3p run, or supplied)
PARTICLE_SOURCE = 'particle_source'    # Geant4-format particle file (Particles output)
DOSE_GRID = 'dose_grid'                # Geant4 dose scoring output
EDEP_GRID = 'edep_grid'                # Geant4 energy-deposit scoring output

ARTIFACT_KINDS = frozenset({
    JOURNAL, MESH, EM_SOLUTION, TD_SOLUTION, RF_POST, TRACK3P_PARTICLES,
    PARTICLE_SOURCE, DOSE_GRID, EDEP_GRID,
})

# The acdtool command table names the artifact each command consumes, and it
# repeats these strings rather than importing them (this module imports that one,
# not the reverse). Fail at import if the two ever drift apart.
_UNKNOWN_ACDTOOL_ARTIFACTS = {spec.requires for spec in COMMANDS.values()
                              if spec.requires} - ARTIFACT_KINDS
assert not _UNKNOWN_ACDTOOL_ARTIFACTS, (
    'lume_ace3p.acdtool.COMMANDS names artifact kinds absent from this '
    f'vocabulary: {sorted(_UNKNOWN_ACDTOOL_ARTIFACTS)}')


# --------------------------------------------------------------------------- #
# RunContext — the per-evaluation state threaded through a module chain.
# --------------------------------------------------------------------------- #


class RunContext:
    """State for one evaluation of a module chain.

    ``artifacts`` maps an artifact kind (see the vocabulary above) to the path
    a module produced for it; ``outputs`` collects extracted scalar/structured
    quantities. Modules read ``inputs`` (a materialized :class:`WorkflowInputs`
    for this eval point), ``paths`` (resolved executable paths), and
    ``dry_run``.

    Two per-artifact side tables let a consumer reach back to its producer
    without either module knowing about the other:

    ``job_names``
        ``{artifact kind: results-directory name}``, recorded by each solver
        module. ``acdtool``'s positional ``postprocess`` commands take that name
        as their first argument, so this is what lets the jobname be *injected*
        rather than repeated in the YAML.
    ``reparse``
        ``{artifact kind: callable}``, also registered by each solver module. A
        consumer that **overwrites** its producer's output file in place calls the
        hook so the producer re-reads it — see :class:`AcdtoolModule` for why
        ``postprocess transwake`` needs this.
    ``field_levels``
        ``{artifact kind: field level}``, recorded by :class:`Track3PModule`
        when its ``field_level:`` key names which level of a multi-level scan a
        consumer should take. The artifact itself is always the results
        directory, so this is how the choice reaches
        :class:`FieldEmissionModule` without the artifact's shape depending on how
        many levels a run produced.

    ``config_hash``
        the ``'sha256:...'`` identity of this evaluation's resolved
        configuration (:func:`lume_ace3p.state.config_hash`) — the module chain
        plus the *materialized* input point. Recorded by
        :meth:`~lume_ace3p.workflow_graph.Workflow.evaluate`, which computes it
        for the run manifest anyway. :class:`Geant4Module` derives
        ``geant4_seed: auto`` from it: distinct per sweep point, reproducible
        across re-runs of the same point. ``None`` on a hand-built context.

    ``modules``
        the **live** module instances this evaluation runs, in resolved DAG order.
        Module instances hold per-run state (a solver's parsed results, acdtool's
        parsed output), so each evaluation gets its own freshly built chain and
        the context is what carries it. ``Workflow.modules`` is a separate,
        never-run prototype list kept for config inspection; anything that reads
        run state — ``field``, ``field_index``, ``extract`` — must source its
        module from here.
    ``capture_output``
        whether each module's subprocess output is teed to
        ``<workdir>/<module name>.log`` (:meth:`Module.log_file`). It defaults to
        ``False`` — the pre-Phase-2 inherited-streams behavior — so a hand-built
        context (a module unit test, a direct driver) is unchanged by this
        feature; :meth:`Workflow.evaluate` passes the ``capture_output``
        workflow parameter, which defaults to ``True``.
    """

    def __init__(self, workdir, inputs=None, artifacts=None, outputs=None,
                 dry_run=False, paths=None, stage_mode='copy', modules=None,
                 capture_output=False, config_hash=None):
        self.workdir = workdir
        self.config_hash = config_hash
        self.modules = list(modules) if modules else []
        self.capture_output = bool(capture_output)
        self.inputs = inputs if inputs is not None else WorkflowInputs()
        self.artifacts = dict(artifacts) if artifacts else {}
        self.outputs = dict(outputs) if outputs else {}
        self.dry_run = dry_run
        self.paths = dict(paths) if paths else {}
        self.stage_mode = stage_mode
        self.job_names = {}
        self.reparse = {}
        self.field_levels = {}

    def ensure_workdir(self):
        if self.workdir and not os.path.exists(self.workdir):
            os.makedirs(self.workdir, exist_ok=True)


def _ace3p_leaf_pairs(section):
    """Return the (path, value) leaf pairs of an ACE3P Section, matching the
    marker content the legacy per-workflow dry-run blocks wrote."""
    return [(path, value) for path, value in _walk_ace3p(section)]


def _append_marker(ctx, text):
    """Append a module's dry-run description to the workdir DRY_RUN.txt. Each
    module contributes its own block, so an assembled chain (Phase 2) yields a
    combined marker."""
    ctx.ensure_workdir()
    with open(os.path.join(ctx.workdir, 'DRY_RUN.txt'), 'a') as f:
        f.write(text)


STAGE_MODES = frozenset({'copy', 'symlink', 'hardlink'})


def _link_or_copy(mode, src, dest):
    """Materialize ``src`` at ``dest`` using the requested staging strategy.

    ``symlink`` writes an *absolute* symlink (the tool runs with ``cwd=workdir``,
    so a relative link would not resolve). ``hardlink`` falls back to a real copy
    on ``OSError`` — cross-device links (``EXDEV``) and filesystems that forbid
    hardlinks are common on WSL / network mounts — and warns so the fallback is
    not silent."""
    if mode == 'symlink':
        os.symlink(os.path.abspath(src), dest)
    elif mode == 'hardlink':
        try:
            os.link(src, dest)
        except OSError as exc:
            print(f"Warning: hardlink of {src} failed ({exc}); copying instead.")
            shutil.copy(src, dest)
    else:
        shutil.copy(src, dest)


def _stage_file(ctx, src):
    """Stage a source file into the workdir (unless already there) and return
    the in-workdir path. Used by the source modules and by any module that
    consumes an externally-supplied file.

    Honors ``ctx.stage_mode`` (``copy`` | ``symlink`` | ``hardlink``): all three
    land the file at ``workdir/basename`` so the co-location contract — every
    referenced file resolves as a bare basename under the run's ``cwd`` — is
    identical regardless of mode.

    INVARIANT: staged files are treated as read-only. Symlink and hardlink modes
    share bytes with the source, so any in-place write to a staged file would
    corrupt the original. Modules that mutate an input (Cubit/ACE3P/Geant4
    parameter merges) copy and rewrite their own input files separately; they do
    not go through this helper."""
    ctx.ensure_workdir()
    base = os.path.basename(src)
    dest = os.path.join(ctx.workdir, base)
    if not os.path.isfile(src) or os.path.abspath(src) == os.path.abspath(dest):
        return dest
    if os.path.lexists(dest):          # real file or a pre-existing/stale symlink
        return dest
    _link_or_copy(ctx.stage_mode, src, dest)
    return dest


# --------------------------------------------------------------------------- #
# Module base
# --------------------------------------------------------------------------- #


class Module:
    """Base class for a pipeline step.

    Subclasses set ``type`` (registry key), ``requires`` and ``provides``
    (artifact-kind sets), and implement :meth:`run`. :meth:`extract` pulls a
    scalar/structured quantity out of the module's own artifacts for the
    ``output_parameters`` spec; the default raises for modules with no
    extractable quantities.
    """

    type = None
    requires = frozenset()
    provides = frozenset()

    def __init__(self, config=None, name=None):
        self.config = dict(config) if config else {}
        self.name = name or self.type

    def run(self, ctx, skip_execution=False):
        """Run this step for the evaluation ``ctx`` describes.

        ``skip_execution`` is what a **resume** passes (Phase 4 of
        ``plans/evaluation_isolation_resume_plan.md``): this module already ran to
        completion in this workdir, so do everything *except* launch the external
        tool — re-read the output that is already on disk, re-record the artifacts,
        and leave the module holding the same state a fresh run would (design
        decision 1: *a resumed module re-runs its parser and skips only the
        subprocess*).

        Re-running the parser rather than skipping the module outright is what
        makes the long-format and field-artifact paths need no special case at
        all: ``field_index`` needs the parsed solver output, so a resumed S3P
        point must still know its frequency axis. It is also cheap — the
        subprocess is the hours, the parse is milliseconds.

        It is an explicit parameter rather than a flag on the context on purpose:
        a module reading ``ctx`` to decide whether to launch a subprocess is the
        kind of implicit control flow this codebase has otherwise avoided.

        A module with no subprocess to skip (the source modules,
        ``field_emission``) accepts the parameter and ignores it, since running
        again *is* the cheap path and is what re-records its artifact.
        """
        raise NotImplementedError

    def log_file(self, ctx):
        """Where this module's subprocesses tee their output for the evaluation
        ``ctx`` describes — ``<ctx.workdir>/<self.name>.log`` — or ``None`` when
        capture is off (``capture_output: false``) or there is no workdir.

        Keyed on the module's *instance name*, not its type, so two modules of
        one type in a chain (two ``acdtool`` steps, say) get separate logs while
        one module's several invocations share one — which is what makes the
        per-evaluation log answer "what did this step do", the question a
        wall-clock-killed sweep point leaves behind."""
        if not ctx.capture_output:
            return None
        return log_path(ctx.workdir, self.name)

    def verify(self, ctx):
        """Whether this module's output is still on disk in the evaluation
        ``ctx`` describes: ``True`` (it is), ``False`` (it is not), or ``None``
        (cannot tell — the default).

        This is the second half of the resume rule (design decision 2 of
        ``plans/evaluation_isolation_resume_plan.md``): the **manifest** is
        authoritative for "did this module run", and the **module** for "is its
        output still there". A workdir whose results directory was deleted or
        truncated therefore re-runs rather than being trusted, while an
        unanswerable case stays unanswered instead of guessing.

        ``None`` is a real answer, not a failure to implement one. Where a module
        cannot name its output without having run — a Geant4 step whose filenames
        live in an input file it has not read yet — or where the output file is
        *not evidence* — an acdtool command that overwrites its producer's file
        (design decision 3) — saying so is the honest report.

        Never raises: a verification that cannot read something returns ``None``.
        It is called about a workdir that may be in any state at all, including
        one written by a different machine."""
        return None

    def extract(self, ctx, spec):
        raise NotImplementedError(
            f"module '{self.type}' exposes no extractable quantities")

    def field_index(self, ctx):
        """Return ``(label, values)`` for the shared index axis this module's
        field outputs are aligned to (e.g. S3P's ``('Frequency', array)``), or
        ``None`` if the module produces no index-aligned field outputs.

        The mode layer uses this seam to emit the S3P long-format sweep table
        (one row per (grid-point, frequency)) generically, without reaching
        into any solver-specific code."""
        return None

    def field(self, ctx):
        """Return this module's structured *field* output for the just-run
        evaluation, or ``None`` if it produces none.

        A field is the ragged/nested per-run output the hybrid data model keeps
        out of the flat result table (S3P ``{Frequency, S(m,n)...}`` arrays,
        Geant4 ``{dose/edep: {indices, values}}`` voxel grids). The mode layer
        persists it per row via :func:`lume_ace3p.results.save_field` and stores
        only the returned handle in the table's field-artifact column; the arrays
        reload on demand with :func:`lume_ace3p.results.load_field`. Modules that
        expose a :meth:`field_index` (S3P) are emitted long-format instead, so
        their :meth:`field` is not used for the sweep table."""
        return None

    def __repr__(self):
        return f'<{type(self).__name__} name={self.name!r}>'


# --------------------------------------------------------------------------- #
# Source modules — provide a prebuilt artifact from a supplied file.
# --------------------------------------------------------------------------- #


class _SourceModule(Module):
    """Shared body for the three source modules: what they have in common is
    that they *stage* a supplied file rather than compute one.

    None of them launches a subprocess, so ``skip_execution`` has nothing to skip:
    each accepts it and stages anyway. Staging is a no-op when the file is already
    in the workdir (:func:`_stage_file`), and running is what re-records the
    artifact the modules downstream require — so on a resume this is both the
    cheap path and the necessary one."""

    def verify(self, ctx):
        """``True``, always: staging is idempotent.

        A source module launches no subprocess and produces nothing that can go
        missing in a way re-running would not fix in milliseconds, so there is
        nothing for a resume to check. It runs again either way (see design
        decision 1 — a resumed module skips only its subprocess, and this one has
        none), which is what re-records its artifact for the modules downstream."""
        return True


class MeshSourceModule(_SourceModule):
    """Provide a ``mesh`` from a prebuilt mesh file — the declarative
    replacement for ``skip_cubit`` + a supplied mesh."""

    type = 'mesh'
    provides = frozenset({MESH})

    def run(self, ctx, skip_execution=False):
        src = self.config.get('file')
        if src is None:
            raise ValueError("mesh source module requires a 'file'.")
        ctx.artifacts[MESH] = _stage_file(ctx, src)


class Track3PSourceModule(_SourceModule):
    """Provide ``track3p_particles`` from an externally-produced Track3P dump.

    The *source* counterpart of :class:`Track3PModule`, which runs Track3P in
    the pipeline: a workflow lists one or the other, since both provide the same
    artifact. This one is the intended head for modelling studies over pre-run
    dumps (a cryomodule-scale Track3P run costs ~50 node-minutes per field
    level), the runnable one for producing new dumps or for multipacting sweeps.
    """

    type = 'track3p_source'
    provides = frozenset({TRACK3P_PARTICLES})

    def run(self, ctx, skip_execution=False):
        src = self.config.get('file')
        if src is None:
            raise ValueError("track3p source module requires a 'file'.")
        ctx.artifacts[TRACK3P_PARTICLES] = _stage_file(ctx, src)


class ParticleSourceModule(_SourceModule):
    """Provide a ``particle_source`` directly from a Geant4-format particle
    file — the declarative way to supply a prebuilt Geant4 source file directly,
    bypassing the Particles weighting step."""

    type = 'particle_source'
    provides = frozenset({PARTICLE_SOURCE})

    def run(self, ctx, skip_execution=False):
        src = self.config.get('file')
        if src is None:
            raise ValueError("particle source module requires a 'file'.")
        ctx.artifacts[PARTICLE_SOURCE] = _stage_file(ctx, src)


# --------------------------------------------------------------------------- #
# Cubit
# --------------------------------------------------------------------------- #


def _journal_export(journal):
    """The filename the **last** ``export`` statement of a Cubit journal writes,
    or ``None`` when the journal declares none or cannot be read.

    The same statement :meth:`lume_ace3p.cubit.Cubit.get_export` finds, read
    without constructing a :class:`Cubit` — which would create the workdir and
    copy the journal into it, side effects a question about a *past* run must not
    have. Used by :meth:`CubitModule.verify` to name the mesh a completed step
    should have left behind."""
    try:
        with open(journal) as file:
            lines = file.readlines()
    except (OSError, TypeError):
        return None
    for line in reversed(lines):
        words = line.split()
        if words and words[0] == 'export':
            for word in words:
                if len(word) > 1 and word.startswith('"') and word.endswith('"'):
                    return word.strip('"')
    return None


class CubitModule(Module):
    """Provide a ``mesh`` from a Cubit ``journal``. Runs meshconvert unless
    ``meshconvert: false``."""

    type = 'cubit'
    provides = frozenset({MESH})

    def __init__(self, config=None, name=None):
        super().__init__(config, name)
        self.journal = self.config.get('journal') or self.config.get('cubit_input')
        self.meshconvert = self.config.get('meshconvert', True)

    def run(self, ctx, skip_execution=False):
        if self.journal is None:
            raise ValueError("cubit module requires a 'journal'.")
        if ctx.dry_run:
            # Legacy dry-run skipped Cubit entirely; record a nominal mesh path
            # (side-effect-free) so a downstream solver's requirement is met.
            base = os.path.splitext(os.path.basename(self.journal))[0]
            ctx.artifacts[MESH] = os.path.join(ctx.workdir, base + '.genesis')
            _append_marker(ctx, 'Dry run mode: Cubit step skipped.\n'
                                f'Cubit journal: {self.journal}\n'
                                f'Cubit inputs: {ctx.inputs.cubit}\n')
            return
        if skip_execution:
            # Resumed: the mesh this step exported is already in the workdir. Cubit
            # has no output *parser* to re-run — the mesh file is its whole output,
            # and the solver downstream reads it, not this module — so naming the
            # artifact is all a re-run would have contributed. The name comes from
            # the journal's own export statement, the same place :meth:`verify`
            # reads it and the same value ``cubit.exportfile`` would give, so
            # neither the mesh built here nor the one resumed is found differently.
            ctx.artifacts[MESH] = self._mesh_path(ctx)
            return
        ctx.ensure_workdir()
        cubit = Cubit(self.journal, workdir=ctx.workdir,
                      ace3p_path=ctx.paths.get('ace3p', ''),
                      cubit_path=ctx.paths.get('cubit', ''),
                      mpi_caller=ctx.paths.get('mpi', ''),
                      log_file=self.log_file(ctx))
        if ctx.inputs.cubit:
            cubit.set_value(ctx.inputs.cubit)
        cubit.run(mcflag=self.meshconvert)
        self._cubit = cubit
        mesh = getattr(cubit, 'exportfile', None)
        if mesh is None:
            cubit.get_export()
            mesh = getattr(cubit, 'exportfile', None)
        ctx.artifacts[MESH] = (os.path.join(ctx.workdir, mesh) if mesh
                               else ctx.workdir)

    def _mesh_path(self, ctx):
        """The mesh this step exported, named without running Cubit.

        The journal's last ``export`` statement (:func:`_journal_export`) is the
        same source :meth:`lume_ace3p.cubit.Cubit.get_export` reads, so a resumed
        step and a fresh one record the same path. Falls back to the recorded
        artifact, then to the workdir, exactly as :meth:`run` does when the journal
        names no export."""
        mesh = _journal_export(self.journal)
        if mesh:
            return os.path.join(ctx.workdir or '', mesh)
        return ctx.artifacts.get(MESH) or ctx.workdir

    def verify(self, ctx):
        """Whether the mesh file is still in the workdir.

        The mesh is named by the journal's ``export`` statement — read from the
        journal (:func:`_journal_export`) rather than from ``ctx.artifacts``, so
        the question can be asked *before* the step is re-run, which is when a
        resume needs the answer. Falls back to the recorded artifact when the
        journal names nothing.

        ``None`` under dry-run: that path deliberately skips Cubit and records a
        nominal mesh path nothing wrote, so its absence is not evidence of
        anything."""
        if ctx.dry_run:
            return None
        mesh = _journal_export(self.journal)
        path = (os.path.join(ctx.workdir or '', mesh) if mesh
                else ctx.artifacts.get(MESH))
        if not path:
            return None
        return os.path.isfile(path)


# --------------------------------------------------------------------------- #
# EM solvers
# --------------------------------------------------------------------------- #


class _SolverModule(Module):
    """Shared body for the ACE3P solver adapters — all require a ``mesh`` and
    provide a solution artifact, differing only in the wrapper they construct,
    the dry-run label, and which artifact kind they produce.

    Omega3P/S3P provide an ``em_solution`` (frequency domain); T3P provides a
    ``td_solution`` (time domain). The split is deliberate: ``acdtool`` requires
    ``em_solution``, so a T3P workflow that lists ``acdtool`` fails validation
    instead of silently running RF postprocessing on time-domain output."""

    requires = frozenset({MESH})
    provides = frozenset({EM_SOLUTION})
    _wrapper = None
    _label = ''
    _artifact = EM_SOLUTION
    # The one upstream artifact :meth:`run` insists on. A mesh for the field
    # solvers; :class:`Track3PModule` sets ``EM_SOLUTION``, since Track3P reads
    # its mesh out of the upstream solver's results directory.
    _input_artifact = MESH
    # The one output file whose presence means this solver's results are still on
    # disk (see :meth:`verify`) — the same file the solver's own
    # ``output_parser`` reads first, so "verify passes" and "the results are
    # readable" cannot drift apart. ``None`` means the check is not implemented
    # for this solver; :class:`T3PModule` answers per monitor instead.
    _results_file = None

    def __init__(self, config=None, name=None):
        super().__init__(config, name)
        self.input_file = self.config.get('input') or self.config.get('ace3p_input')
        self.tasks = self.config.get('tasks', self.config.get('ace3p_tasks'))
        self.cores = self.config.get('cores', self.config.get('ace3p_cores'))
        self.opts = self.config.get('opts', self.config.get('ace3p_opts'))
        # Overrides the solver's results directory — which is really chosen by
        # the batch job submission script's job name, not by the input file (no
        # solver reference documents a 'JobName' input container). Unset means
        # the per-solver default ('omega3p_results', 't3p_results', ...).
        self.results_dir = self.config.get('results_dir')
        # Auxiliary files the solver reads from its working directory by bare
        # name and that no artifact supplies -- a Track3P SEY table
        # ('SEYFileName1: copper.dat'), an external field map. Staged into the
        # workdir (see :func:`_stage_file`) before the run, the way the Geant4
        # module stages 'geant4_geometry_files'.
        files = self.config.get('files') or []
        self.files = [files] if isinstance(files, str) else list(files)
        self._solver = None

    def run(self, ctx, skip_execution=False):
        if self._input_artifact not in ctx.artifacts:
            raise ValueError(f"module '{self.type}' requires a "
                             f"{self._input_artifact} artifact.")
        for path in self.files:
            if not os.path.isfile(path):
                raise FileNotFoundError(
                    f"module '{self.type}' lists '{path}' under 'files:' but "
                    "there is no such file (paths resolve from the directory "
                    "run-lume-ace3p was started in).")
            _stage_file(ctx, path)
        if ctx.dry_run:
            self._solver = None
            leaves = _ace3p_leaf_pairs(ctx.inputs.ace3p)
            _append_marker(ctx, f'Dry run mode: {self._label} step skipped.\n'
                                f'Cubit: {ctx.inputs.cubit}\n'
                                f'ACE3P: {[(_, v) for _, v in leaves]}\n')
            ctx.artifacts[self._artifact] = ctx.workdir
            # No solver instance to ask, so fall back to the declared override or
            # the documented per-solver default. A dry-run acdtool step still
            # builds its command line from this.
            ctx.job_names[self._artifact] = (self.results_dir
                                             or self._wrapper.default_job_name)
            self._prepare_dry_run(ctx)
            return
        ctx.ensure_workdir()
        solver = self._wrapper(self.input_file,
                               ace3p_tasks=self.tasks,
                               ace3p_cores=self.cores,
                               ace3p_opts=self.opts,
                               results_dir=self.results_dir,
                               workdir=ctx.workdir,
                               ace3p_path=ctx.paths.get('ace3p', ''),
                               mpi_caller=ctx.paths.get('mpi', ''),
                               log_file=self.log_file(ctx))
        solver.set_value(ctx.inputs.ace3p)
        self._prepare_solver(ctx, solver)
        if skip_execution:
            # Resumed: this solve already finished in this workdir, so read its
            # results instead of repeating the hours that produced them.
            # ``output_parser`` is the very call ``run()`` makes after the
            # subprocess returns, which is what makes design decision 1 a branch
            # rather than a restructure — and why the resumed point's ``extract``,
            # ``field`` and ``field_index`` need no special case at all.
            #
            # A parser that finds nothing raises from here, which the caller
            # records as a failure of this module. That is not a hole a resume can
            # fall into unnoticed: :meth:`verify` gates the skip on the presence of
            # the very file ``output_parser`` reads first (see
            # :attr:`_results_file`), so the two cannot drift apart.
            solver.output_parser()
        else:
            solver.run()
        self._solver = solver
        ctx.artifacts[self._artifact] = ctx.workdir
        ctx.job_names[self._artifact] = solver.job_name()
        # Let a consumer that rewrites this solver's output in place ask for a
        # re-read (the acdtool wake commands overwrite wakefield.out).
        ctx.reparse[self._artifact] = solver.output_parser

    def _prepare_solver(self, ctx, solver):
        """Hook: edit the solver's input tree after the ``ace3p:`` overrides
        are merged and before it runs (or is re-read). A no-op for the field
        solvers; :class:`Track3PModule` points ``Domain.FieldDir`` at the
        upstream solver's results directory here."""

    def _prepare_dry_run(self, ctx):
        """Hook: the dry-run counterpart of :meth:`_prepare_solver`, called
        after the artifact and job name are recorded. There is no solver to
        edit, so this is where a module records what it *would* inject or
        checks what it can without one."""

    def _results_dir(self, ctx):
        """This solver's results directory, relative to the workdir, resolved
        **without needing the run** — which is what lets :meth:`verify` ask about
        a workdir written by an earlier, already-finished process.

        Mirrors :meth:`lume_ace3p.ace3p.ACE3P.job_name`'s resolution order: the
        module's ``results_dir:`` override, then a ``JobName`` leaf in the input
        file (undocumented, best-effort — see
        :func:`lume_ace3p.ace3p.input_job_name`), then the wrapper's default. A
        solver instance, when there is one, is asked directly: it is the same
        answer, from the object that actually resolved it."""
        if self._solver is not None:
            return self._solver.results_dir()
        job = self.results_dir
        if not job:
            try:
                with open(self.input_file) as file:
                    job = input_job_name(file.read())
            except (OSError, TypeError, ValueError):
                job = None
        return results_path(job or self._wrapper.default_job_name,
                            self._wrapper.results_subdir)

    def verify(self, ctx):
        """Whether this solver's results are still in the workdir — the presence
        of :attr:`_results_file` under :meth:`_results_dir`.

        ``None`` under dry-run (the solver was skipped, so nothing was meant to be
        written) and for a solver that names no results file."""
        if ctx.dry_run or not self._results_file:
            return None
        return os.path.isfile(os.path.join(ctx.workdir or '',
                                           self._results_dir(ctx),
                                           self._results_file))


class Omega3PModule(_SolverModule):
    """The ACE3P eigensolver: requires a ``mesh``, provides an ``em_solution``.

    Exposes the eigensolve's own results — mode frequency, Q, stored energy —
    read from ``omega3p.out`` by :meth:`Omega3P.output_parser`. These used to be
    reachable only by running acdtool with ``RoverQ`` enabled, which is why the
    shipped sweep example still spells frequency as ``['RoverQ', '0',
    'Frequency']``; the acdtool route keeps working and those examples migrate
    later.

    The quantity names are the ``Mode`` leaf names Omega3P itself writes
    (``Frequency``, ``QualityFactor``, ``ExternalQ``, ``TotalEnergy``,
    ``PowerLoss``, plus ``Frequency_imag`` / ``TotalEnergy_imag`` on a complex
    eigensolve), so an output spec must name this module explicitly —
    ``{module: omega3p, quantity: Frequency}``. A bare ``'Frequency'`` string
    routes to S3P by shape, which is the pre-existing behavior of
    ``_infer_output_module`` and is left alone.
    """

    type = 'omega3p'
    _wrapper = Omega3P
    _label = 'Omega3P'
    # The eigensolve results themselves; Omega3PModule.extract reads this file.
    _results_file = 'omega3p.out'

    def extract(self, ctx, spec):
        """Return an eigenmode quantity from the Omega3P solution.

        ``spec`` may be:
          * a string ``'Frequency'`` — the full mode-indexed array,
          * a single-element list ``['Frequency']`` — same,
          * a mapping ``{'quantity': 'Frequency', 'at': {'mode': 0}}`` — the
            scalar for one mode (the same ``at:`` narrowing S3P and T3P use).
        """
        solver = self._solver
        if solver is None:
            # Dry-run / no solver. A scalar NaN, not S3P's ``array([nan])``:
            # Omega3P has no dry-run index axis (see :meth:`field_index`), so
            # the value lands in a wide table cell as-is.
            return float('nan')
        quantity, mode = self._parse_spec(spec)
        data = solver.output_data
        if not data:
            raise ValueError(
                f"no Omega3P eigenmode results to extract '{quantity}' from. "
                f"Expected {os.path.join(solver.results_dir(), solver.output_file)} "
                f"under {ctx.workdir}; set 'results_dir' on the omega3p module "
                f"if the run used a different job name. {solver.exit_status_note()}".rstrip())
        if quantity == 'Modes' or quantity not in data:
            raise ValueError(
                "Unknown quantity '" + str(quantity) + "' in Omega3P output "
                "dict. Known quantities: "
                + str(sorted(k for k in data if k != 'Modes')) + ".")
        values = data[quantity]
        if mode is None:
            return values
        # Lookup by ModeID rather than by position: they coincide today, and
        # this keeps working if a future output ever numbers modes otherwise.
        ids = list(data['ModeID'])
        try:
            index = ids.index(int(mode))
        except ValueError:
            raise ValueError(
                f"Omega3P produced no mode {mode}; this run has modes "
                f"{ids}. The mode count follows from the eigensolver's "
                "NumEigenvalues, so it is not known before the run.") from None
        return values[index]

    @staticmethod
    def _parse_spec(spec):
        if isinstance(spec, dict):
            at = spec.get('at') or {}
            return spec.get('quantity'), at.get('mode')
        if isinstance(spec, list):
            return spec[0], None
        return spec, None

    def field_index(self, ctx):
        """Omega3P results are indexed by mode: returns ``('ModeID', array)``.

        Returns ``None`` — **not** the single-row sentinel :class:`S3PModule` and
        :class:`T3PModule` return — when there are no parsed modes, which covers
        dry-run and a failed run. The asymmetry is deliberate: S3P's frequency
        scan and T3P's ``s`` range are declared in the input file, so the axis is
        known to exist before the run, while Omega3P's mode count is a *result*
        of the eigensolve. Emitting a sentinel axis would also silently reshape
        the existing wide ``omega3p -> acdtool`` sweep tables under dry-run."""
        solver = self._solver
        if solver is None or not solver.output_data.get('Modes'):
            return None
        return 'ModeID', np.asarray(solver.output_data['ModeID'])

    def field(self, ctx):
        """Return the mode-indexed arrays (``{ModeID, Frequency,
        QualityFactor, ...}``) for the just-run evaluation, or ``None`` under
        dry-run / when no modes were parsed.

        Drops ``'Modes'`` — the readable list of per-mode dicts cannot ride
        inside a field-artifact ``.npz`` without pickling, and it carries no
        information the arrays do not."""
        solver = self._solver
        if solver is None or not solver.output_data.get('Modes'):
            return None
        return {key: value for key, value in solver.output_data.items()
                if key != 'Modes'}


class S3PModule(_SolverModule):
    type = 's3p'
    _wrapper = S3P
    _label = 'S3P'
    # The S-parameter magnitudes. 'SParameter.out' is deliberately not checked:
    # older ACE3P builds write none, and S3P.output_parser only warns about that
    # — a run missing it still produced results.
    _results_file = 'Reflection.out'

    def extract(self, ctx, spec):
        """Return an S-parameter quantity from the S3P solution.

        The quantity is any frequency-indexed key
        :meth:`lume_ace3p.ace3p.S3P.output_parser` produces — the magnitude
        ``'S(0,0)'``, or its complex form ``'S(0,0)_real'`` / ``'_imag'`` /
        ``'_phase_deg'``, or ``'Frequency'`` itself.

        ``spec`` may be:
          * a string ``'S(0,0)'`` — the full frequency-indexed array,
          * a single-element list ``['S(0,0)']`` — same, first element used,
          * a mapping ``{'quantity': 'S(0,0)', 'at': {'frequency': f}}`` — the
            scalar value at scan frequency ``f`` (matched to 1e-9 relative; a
            frequency that is not on the scan raises, naming the nearest scan
            points — the objective form the Xopt driver
            needs).

        The port mode profiles and the ``IndexMap`` are *not* extractable: they
        are not indexed by frequency, so they come back through :meth:`field`.
        """
        solver = self._solver
        if solver is None:
            # Dry-run / no solver: mirror the legacy evaluate NaN sentinel.
            return np.array([float('nan')])
        data = solver.output_data
        assert len(data) > 0, 'No output data found, run S3P first.'
        quantity, frequency = self._parse_spec(spec)
        if quantity not in data:
            raise ValueError("Unknown section name '" + str(quantity)
                             + "' in output dict.")
        values = data[quantity]
        if isinstance(values, dict):
            # A port mode profile (PortRef<n>_<m>.out) or the IndexMap: indexed by
            # position / by S-matrix index, not by frequency, so it cannot be a
            # column of this module's table. Name the route that does return it.
            raise ValueError(
                "'" + str(quantity) + "' is not a frequency-indexed S3P "
                "quantity (it holds " + str(sorted(values)) + "), so it is not "
                "a result-table column. It is returned as a field artifact by "
                "this module's field(), alongside the full spectrum.")
        if frequency is None:
            return values
        freqs = np.asarray(data['Frequency'], dtype=float)
        hits = np.flatnonzero(np.isclose(freqs, float(frequency),
                                         rtol=1e-9, atol=0.0))
        if len(hits) == 0:
            # Loud, not NaN: an Xopt objective on an off-grid frequency used to
            # print a line and return NaN, and the optimizer then spent its whole
            # budget on NaNs (examples/s3p_optimization asked for 12.0 GHz of a
            # 9.424 + k*0.25 GHz scan; 25 solver runs, no result). Xopt runs
            # strict by default, so raising stops the campaign at its first
            # evaluation, naming the grid the input file actually declares.
            nearest = freqs[np.argsort(np.abs(freqs - float(frequency)))[:2]]
            raise ValueError(
                'at: {frequency: ' + repr(float(frequency)) + '} is not a point '
                "of this S3P run's frequency scan (" + repr(float(freqs[0]))
                + ' to ' + repr(float(freqs[-1])) + ' Hz in ' + str(len(freqs))
                + ' steps; nearest ' + ', '.join(repr(float(f)) for f in
                                                sorted(nearest))
                + "). Pick a scan point, or change the FrequencyScan block.")
        return values[int(hits[0])]

    @staticmethod
    def _parse_spec(spec):
        if isinstance(spec, dict):
            quantity = spec.get('quantity')
            at = spec.get('at') or {}
            return quantity, at.get('frequency')
        if isinstance(spec, list):
            return spec[0], None
        return spec, None

    def field_index(self, ctx):
        """S3P field outputs are indexed by frequency. Return
        ``('Frequency', array)``; under dry-run (no solver) return a single-row
        ``[0.0]`` sentinel so a swept long-format table still has one row per
        grid point."""
        solver = self._solver
        if solver is None:
            return 'Frequency', np.array([0.0])
        return 'Frequency', np.asarray(solver.output_data['Frequency'])

    def field(self, ctx):
        """Return the full S3P spectrum for the just-run evaluation, or ``None``
        under dry-run: ``{IndexMap, Frequency, S(m,n), S(m,n)_real,
        S(m,n)_imag, S(m,n)_phase_deg, PortRef<n>_<m>: {x, y, Ex, Ey, Hx, Hy}}``.

        This is the structured field artifact for a single point. In a sweep,
        S3P goes long-format (its :meth:`field_index` puts one row per
        frequency), so this is used only when a caller wants to persist the raw
        spectrum for a row rather than explode it.

        The port mode profiles ride here and *only* here — they are indexed by
        position rather than by frequency, so they are field artifacts by the
        same rule that keeps acdtool's curve files out of the table (design
        decision 4 of ``plans/acdtool_rework_plan.md``). They survive
        :func:`lume_ace3p.results.save_field` as nested dicts, the way
        ``IndexMap`` already does."""
        solver = self._solver
        if solver is None:
            return None
        return dict(solver.output_data)


class T3PModule(_SolverModule):
    """The T3P time-domain solver: requires a ``mesh``, provides a
    ``td_solution``.

    Exposes the wakefield monitor's results the same way :class:`S3PModule`
    exposes S-parameters — a scalar figure of merit plus arrays over a shared
    index — except the index is the wake coordinate ``s`` rather than frequency.

    A wake is only one of six things a T3P run can monitor, though, and since
    ``plans/t3p_monitor_plan.md`` all of them are readable. That makes ``Name``
    the selector::

        output_parameters :
          'k_loss' : {module: t3p, quantity: loss_factor}
          'P_in'   : {module: t3p, monitor: inputPower,   quantity: P}
          'P_wall' : {module: t3p, monitor: wallossPower, quantity: P}
          'Ez_gap' : {module: t3p, monitor: point, quantity: Ez, at: {t: 1.0e-9}}

    ``monitor:`` may be omitted whenever exactly one monitor provides the named
    quantity — which is every wakefield spec ever written against this package,
    so those keep working unchanged. Where several could answer,
    :meth:`extract` raises listing them rather than picking one.

    **One index axis per module, ``s`` winning over ``t``** (the rule
    :class:`AcdtoolModule` applies to ``ModeID``). A run with both a ``WakeField``
    and a ``Point`` monitor has two incompatible axes — 20-odd wake samples
    against thousands of timesteps — so :meth:`field_index` picks one and
    everything on the other axis must be narrowed to a scalar with ``at:``. The
    full arrays are still there: they ride in :meth:`field`.
    """

    type = 't3p'
    provides = frozenset({TD_SOLUTION})
    _wrapper = T3P
    _label = 'T3P'
    _artifact = TD_SOLUTION

    # Bare quantity names this module answers to, used both by ``extract`` and by
    # the output-spec router in workflow_graph.
    #
    # **Deliberately not widened** for the monitor quantities ('P', 'V', 't',
    # 'Ex'...): they are short and generic, 't' especially, and a bare 't' routing
    # to t3p by name would be a trap for any future spec. The new quantities are
    # reachable through 'module: t3p' or a 'monitor:' key, both of which route
    # explicitly, so bare routing is left exactly as it was.
    QUANTITIES = frozenset({'loss_factor', 'kick_factor', 'W', 'I_bunch', 's'})

    # Extractable scalars -> the key ``parse_wakefield`` stores them under.
    _SCALARS = {'loss_factor': 'LossFactor', 'kick_factor': 'KickFactor'}

    # The axes a T3P ``at:`` may narrow on: the wake coordinate and time.
    _AXES = ('s', 't')

    # Keys of a monitor entry that are metadata rather than extractable values.
    _NOT_QUANTITIES = frozenset({'Type', 'files'})

    # ``(Type, Name)`` read straight from the input file, for the dry-run axis
    # decision. None = not read yet; () = unreadable, or no Monitor blocks.
    _declared = None

    def extract(self, ctx, spec):
        """Return one quantity from the T3P solution.

        ``spec`` may be:

        * ``'loss_factor'`` / ``'kick_factor'`` — the wake monitor's scalar figure
          of merit,
        * ``'W'`` / ``'I_bunch'`` / ``'s'`` — the full ``s``-indexed array,
        * a single-element list wrapping either of the above,
        * a mapping ``{'quantity': 'W', 'at': {'s': 0.05}}`` — the value at the
          wake position nearest ``s`` (the objective form an Xopt run needs),
        * a mapping naming a monitor, ``{'monitor': 'inputPower', 'quantity': 'P'}``
          or ``{'monitor': 'point', 'quantity': 'Ez', 'at': {'t': 1.0e-9}}``.

        Resolution order for the monitor: an explicit ``monitor:``; else the wake
        monitor when the quantity is one of :data:`QUANTITIES`; else the unique
        monitor whose type provides that quantity. An ambiguous bare quantity
        raises listing the candidates, and an unknown one raises listing what each
        monitor did report — the error style :meth:`AcdtoolModule._value` uses.

        ``at:`` takes the **nearest** sample on the monitor's own axis, for both
        ``s`` and ``t``: neither grid is something a user can name exactly, since
        the time grid is a consequence of ``TimeStepping: DT`` and the wake grid of
        that in turn. It is *required* for a monitor whose axis is not the one
        :meth:`field_index` chose, because an off-axis array cannot be a column of
        a table indexed on the other.
        """
        solver = self._solver
        if solver is None:
            # Dry-run / no solver: same NaN sentinel S3PModule returns.
            return np.array([float('nan')])
        quantity, monitor, at = self._parse_spec(spec)
        stray = sorted(set(at) - set(self._AXES))
        if stray:
            raise ValueError(
                "a t3p 'at:' narrows on " + str(list(self._AXES)) + ', not '
                + str(stray) + '.')
        name, entry, axis = self._resolve(ctx, solver, quantity, monitor)

        if axis == 's' and quantity in self._SCALARS:
            key = self._SCALARS[quantity]
            if key not in entry:
                # Longitudinal runs report a loss factor, transverse ones a kick
                # factor. Name the one that IS present rather than return NaN.
                present = [q for q, k in self._SCALARS.items() if k in entry]
                raise ValueError(
                    f"this is a {entry.get('WakeType', 'unknown')} T3P run, which "
                    f"reports {present} — not '{quantity}'. The wake type follows "
                    "from the WakeField monitor's contour and the beam offset in "
                    "the input file.")
            return entry[key]

        if quantity not in entry:
            raise ValueError(
                "T3P monitor '" + str(name) + "' reported no '" + str(quantity)
                + "'. It reported " + str(self._quantities(entry)) + '.')
        wrong = sorted(set(at) - {axis})
        if wrong:
            raise ValueError(
                "T3P monitor '" + str(name) + "' is indexed by '" + str(axis)
                + "', so it takes 'at: {" + str(axis) + ": ...}', not "
                + str(wrong) + '.')

        values = entry[quantity]
        position = at.get(axis)
        if np.ndim(values) == 0:
            if position is not None:
                raise ValueError(
                    "T3P monitor '" + str(name) + "'s '" + str(quantity) + "' is "
                    "a scalar, so an 'at:' narrowing does not apply to it.")
            return values
        if position is None:
            table_axis = self._field_axis(solver)
            if table_axis is not None and axis != table_axis[0]:
                raise ValueError(
                    "T3P monitor '" + str(name) + "' is indexed by '" + str(axis)
                    + "' but this run's result table is indexed by '"
                    + str(table_axis[0]) + "' (one index axis per module, 's' "
                    "winning over 't'), so '" + str(quantity) + "' must be "
                    "narrowed to a scalar: add 'at: {" + str(axis) + ": ...}'. "
                    'The whole array is still available through the run\'s field '
                    'artifact.')
            return values
        # Unlike S3P's frequency scan, both T3P grids are solver-chosen
        # consequences of the timestep, so an exact match is not something a user
        # can specify. Take the nearest sample instead.
        grid = np.asarray(entry[axis])
        if not grid.size:
            return float('nan')
        return values[int(np.argmin(np.abs(grid - float(position))))]

    @staticmethod
    def _parse_spec(spec):
        """``(quantity, monitor, at)`` for every accepted spec form."""
        if isinstance(spec, dict):
            return (spec.get('quantity'), spec.get('monitor'),
                    dict(spec.get('at') or {}))
        if isinstance(spec, (list, tuple)) and spec:
            return spec[0], None, {}
        return spec, None, {}

    @classmethod
    def _quantities(cls, entry):
        """The extractable names one monitor entry offers, metadata excluded. A
        ``Volume`` monitor offers none, which is why it cannot be extracted from
        at all."""
        return sorted(set(entry) - cls._NOT_QUANTITIES)

    @staticmethod
    def _addressable(solver):
        """Ordered ``{name: (entry, axis)}`` for every monitor result a spec may
        name — the wake monitor under its own ``Name`` (its keys live at the top
        level of ``output_data``, which is what keeps the legacy specs working),
        then each entry of ``Monitors`` in declaration order, then ``Bunch0``."""
        data = solver.output_data
        found = {}
        if 's' in data:
            found[solver.wake_monitor_name() or 'wakefield'] = (
                {key: value for key, value in data.items()
                 if key != 'Monitors' and key not in ALWAYS}, 's')
        for name, entry in (data.get('Monitors') or {}).items():
            spec = MONITORS.get(entry.get('Type'))
            found[name] = (entry, spec.axis if spec is not None else None)
        for name, spec in ALWAYS.items():
            if name in data:
                found[name] = (data[name], spec.axis)
        return found

    def _resolve(self, ctx, solver, quantity, monitor):
        """``(name, entry, axis)`` for the monitor a spec addresses."""
        found = self._addressable(solver)
        if monitor is not None:
            name = str(monitor)
            if name not in found:
                declared = [n for _, n in solver.monitors() if n]
                raise ValueError(
                    "this T3P run has no readable monitor named '" + name
                    + "'. Readable: " + str(sorted(found)) + '; declared in the '
                    'input file: ' + str(declared) + '. A declared monitor that '
                    'wrote nothing warns at parse time (T3POutputWarning).')
            entry, axis = found[name]
            if MONITORS.get(entry.get('Type')) is not None and axis is None:
                raise ValueError(
                    "T3P monitor '" + name + "' is a "
                    + str(entry.get('Type')) + " monitor, which dumps netCDF "
                    'field snapshots rather than a time series, so it provides no '
                    'extractable quantity. Its filenames ride in the run\'s field '
                    'artifact: ' + str(entry.get('files')) + '.')
            return name, entry, axis

        if quantity in self.QUANTITIES:
            # The legacy five: the wake monitor, whatever it is called.
            if 's' not in solver.output_data:
                raise ValueError(
                    f"no T3P wakefield results to extract '{quantity}' from. T3P "
                    "writes them only when the input file declares a WakeField "
                    "monitor, e.g.\n"
                    "  Monitor: { Type: WakeField  Name: wakefield ... }\n"
                    f"Expected file: {os.path.join(solver.results_dir(), 'wakefield.out')} "
                    f"under {ctx.workdir}. {solver.exit_status_note()}".rstrip())
            name = solver.wake_monitor_name() or 'wakefield'
            return name, found[name][0], 's'

        candidates = [name for name, (entry, _) in found.items()
                      if quantity in self._quantities(entry)]
        if len(candidates) == 1:
            name = candidates[0]
            return name, found[name][0], found[name][1]
        reported = {name: self._quantities(entry)
                    for name, (entry, _) in found.items()}
        if not candidates:
            raise ValueError(
                "Unknown quantity '" + str(quantity) + "' in T3P output dict. "
                'Known bare quantities: ' + str(sorted(self.QUANTITIES))
                + '; this run\'s monitors reported ' + str(reported) + '.')
        raise ValueError(
            str(len(candidates)) + " T3P monitors provide '" + str(quantity)
            + "': " + str(candidates) + ". Name one with 'monitor: <name>' — "
            'a run may declare several monitors of one type (three Power '
            'monitors give input, output and wall-loss power on one run), so '
            'Name is the only unique selector.')

    def _field_axis(self, solver):
        """``(label, values)`` for the one axis this module puts on a result
        table, or ``None`` when the run produced nothing indexable.

        ``s`` wins over ``t`` when both are present. That tiebreak is what keeps
        every existing baseline where it is — a wake run's table is ``s``-indexed
        whether or not the input also declares a ``Point`` monitor."""
        data = solver.output_data
        if 's' in data:
            return 's', np.asarray(data['s'])
        for entry in (data.get('Monitors') or {}).values():
            spec = MONITORS.get(entry.get('Type'))
            if spec is not None and spec.axis == 't' and 't' in entry:
                return 't', np.asarray(entry['t'])
        for name, spec in ALWAYS.items():
            if name in data and spec.axis == 't' and 't' in data[name]:
                return 't', np.asarray(data[name]['t'])
        return None

    def _input_monitors(self):
        """``[(Type, Name)]`` the input file declares, read once and cached.

        The stateless route to the monitor list — :meth:`T3P.monitors` reads the
        same thing off a solver instance, and this is what a caller with no solver
        (a dry run, or :meth:`verify` asking about a finished run) uses instead.
        ``()`` when the file cannot be read or declares nothing."""
        if self._declared is None:
            try:
                with open(self.input_file) as file:
                    self._declared = declared_monitors(file.read())
            except (OSError, TypeError, ValueError):
                self._declared = ()
        return self._declared

    def verify(self, ctx):
        """Whether every monitor the input file declares still has its output
        under the results directory.

        This reuses the monitor table directly (:data:`lume_ace3p.ace3p.MONITORS`
        says which files each ``Type`` writes, named after the monitor's own
        ``Name``), so it covers a run with no wake as readily as one with several
        power monitors — and a partially-deleted results directory fails it, which
        is the case worth catching.

        Every pattern is globbed rather than tested as a literal path: a
        ``Volume`` monitor writes one file per dump time and declares its files as
        a glob, and a glob-free pattern matches itself.

        ``None`` under dry-run, and for a run that declares no readable monitor at
        all — there is then nothing whose absence would mean anything."""
        if ctx.dry_run:
            return None
        declared = (self._solver.monitors() if self._solver is not None
                    else self._input_monitors())
        results = os.path.join(ctx.workdir or '', self._results_dir(ctx))
        checked = []
        for monitor_type, name in declared:
            spec = MONITORS.get(monitor_type)
            if spec is None or not name:
                # An undocumented Type has an unknown output shape and a nameless
                # monitor has no filename stem, so neither is checkable. Both
                # already warn at parse time.
                continue
            checked.append(any(glob.glob(os.path.join(results, pattern))
                               for pattern in spec.filenames(name)))
        if not checked:
            return None
        return all(checked)

    def _dry_run_axis(self):
        """The index-axis label a dry run reports.

        No solver has run, so there is nothing parsed to ask — but unlike
        Omega3P's mode count, **both** T3P axes are declared by the *input file*,
        so the answer is readable without one: a ``WakeField`` monitor means
        ``'s'``, any other time-series monitor means ``'t'``. Falls back to
        ``'s'`` when the input file cannot be read or declares nothing, which is
        what this returned unconditionally before ``examples/t3p_power_balance``
        made a wake-less dry run something a shipped example does."""
        axes = [MONITORS[kind].axis for kind, _ in self._input_monitors()
                if kind in MONITORS]
        if 's' in axes:
            return 's'
        return 't' if 't' in axes else 's'

    def field_index(self, ctx):
        """The T3P result table's index: ``('s', array)`` when the run produced a
        wake, ``('t', array)`` from the first time-series monitor otherwise, and
        ``None`` when it produced neither.

        Under dry-run (no solver) a single-row ``[0.0]`` sentinel, so a swept
        long-format table still has one row per grid point — mirroring
        :meth:`S3PModule.field_index`. Its *label* comes from the input file (see
        :meth:`_dry_run_axis`), so a dry run of a wake-less workflow reports ``t``
        rather than an ``s`` it will never have."""
        solver = self._solver
        if solver is None:
            return self._dry_run_axis(), np.array([0.0])
        return self._field_axis(solver)

    def field(self, ctx):
        """Return the whole T3P result for the just-run evaluation — the wake
        keys (``{s, W, I_bunch, LossFactor|KickFactor, WakeType, ...}``) plus
        ``Monitors`` and ``Bunch0`` — or ``None`` under dry-run / when the run
        produced nothing readable.

        The nested ``Monitors`` dict round-trips through
        :func:`lume_ace3p.results.save_field` as JSON, the same way S3P's
        ``IndexMap`` and ``PortRef<n>_<m>`` entries do. This is where every array
        that is *not* on the chosen index axis lives: a ``Point`` monitor's
        thousands of timesteps cannot be columns of an ``s``-indexed table, but
        they are not discarded either."""
        solver = self._solver
        if solver is None or not solver.output_data:
            return None
        return dict(solver.output_data)


# --------------------------------------------------------------------------- #
# Track3P
# --------------------------------------------------------------------------- #


class Track3PModule(_SolverModule):
    """The ACE3P particle tracker: requires an ``em_solution``, provides
    ``track3p_particles``.

    The runnable counterpart of :class:`Track3PSourceModule`: a workflow has one
    or the other as the producer of ``track3p_particles``, never both. It runs
    ``track3p`` on the fields the upstream Omega3P or S3P step wrote, and exposes
    the run's multipacting and dark-current results as result-table columns::

        output_parameters :
          'EC_max'   : {module: track3p, quantity: max_enhancement}
          'impacts'  : {module: track3p, quantity: total_impacts}
          'onset'    : {module: track3p, quantity: mp_onset_level, at: {threshold: 1.0}}
          'captured' : {module: track3p, quantity: captured_electrons, at: {boundary: 1}}

    **Field level is the index axis** (``FieldLevel``): every per-level quantity
    is an array aligned to it, so a ``single`` or ``parameter_sweep`` table goes
    long-format, one row per level, and ``at: {field_level: x}`` narrows one to a
    scalar (an off-grid level raises naming the grid, as S3P's ``at:
    {frequency}`` does). The levels are declared in the input's ``FieldScales``,
    so a dry run reports the right rows.

    **What the module injects.** Track3P finds its fields through
    ``Domain.FieldDir``, which in a hand-run case is a relative path the user
    typed to match a batch script. Here the upstream solver's results directory
    is known (``ctx.job_names``), so :meth:`_prepare_solver` sets
    ``FieldDir: ./<that directory>`` — unless the input already names a
    ``FieldDir`` that exists in the workdir, which is respected (a pre-staged
    ``omega3p_results`` symlink, say).

    The 17-column impact dumps are *not* read into memory here: the run records
    their paths and :meth:`extract` summarises one on demand (Lixin Ge's
    cryomodule dumps are ~137 MB each).

    **The artifact is always the results directory**, never one dump, so its
    shape does not depend on how many field levels a run produced;
    :meth:`FieldEmissionModule._resolve_dump` is where a consumer resolves the
    ``ImpactsInfo_<level>`` out of it, and ``field_level:`` says which one when a
    scan produced several. ``impacts_format: initials-impacts`` injects the
    opt-in layout selector that dump needs to carry the two field-emission
    columns the weighting reads.
    """

    type = 'track3p'
    requires = frozenset({EM_SOLUTION})
    provides = frozenset({TRACK3P_PARTICLES})
    _wrapper = Track3P
    _label = 'Track3P'
    _artifact = TRACK3P_PARTICLES
    _input_artifact = EM_SOLUTION
    # The run log, which every run writes and which carries the 'Done!' that
    # says it finished. verify() checks both.
    _results_file = 'track3p.log'

    # A Track3P table is indexed by field level even though the Omega3P step
    # upstream exposes a mode index: the modes are Track3P's *input*, and a mode
    # frequency in this chain is one scalar per run (``at: {mode: n}``), not an
    # axis anyone tabulates a multipacting result over. See
    # :meth:`Workflow.field_index`.
    index_precedence = 1

    # Bare quantity names this module answers to; the output-spec router in
    # workflow_graph sends these to 'track3p' without a 'module:' key.
    PER_LEVEL = frozenset({
        'max_enhancement', 'mean_enhancement', 'total_impacts', 'resonant_count',
        'resonant_particles', 'max_resonant_energy',
        'impact_count', 'max_impact_energy', 'lost_count',
    })
    SCALARS = frozenset({'total_emitted', 'emitting_faces', 'survived'})
    QUANTITIES = PER_LEVEL | SCALARS | {'captured_electrons', 'mp_onset_level'}

    # The axes an 'at:' may narrow on.
    _AXES = ('field_level', 'boundary', 'threshold')

    # Where each per-level table quantity comes from: (output_data key, level
    # column, reduction over that level's rows).
    _TABLE = {
        'max_enhancement': ('EnhancementCounter', 'fieldlevel',
                            lambda t, m: np.max(t['maxEnhancement'][m])),
        'mean_enhancement': ('EnhancementCounter', 'fieldlevel',
                             lambda t, m: np.mean(t['averageEnhancement'][m])),
        'total_impacts': ('EnhancementCounter', 'fieldlevel',
                          lambda t, m: np.sum(t['totalImpactNum'][m])),
        'resonant_count': ('EnhancementCounter', 'fieldlevel',
                           lambda t, m: np.count_nonzero(m)),
        'resonant_particles': ('ResonantParticles', 'Field_Level',
                               lambda t, m: len(np.unique(t['ID'][m]))),
        'max_resonant_energy': ('ResonantParticles', 'Field_Level',
                                lambda t, m: np.max(t['Energy'][m])),
    }

    def __init__(self, config=None, name=None):
        super().__init__(config, name)
        self._declared = None
        self._summaries = {}
        # Opt-in: inject the Initials-Impacts selector so the dump carries the
        # two field-emission columns the field_emission module reweights. Left
        # alone by default -- a multipacting user never needs it, and the
        # default layout is what every CW23 case writes.
        fmt = str(self.config.get('impacts_format') or 'default').lower()
        if fmt not in ('default', 'initials-impacts'):
            raise ValueError(
                f"track3p: impacts_format '{fmt}' is not recognised; use "
                f"'default' (leave the input's own layout alone) or "
                f"'initials-impacts' (inject OutputImpacts: on and "
                f"OutputImpactsInfo: {{ Type: Initials-Impacts }}, the layout "
                f"the '{FieldEmissionModule.type}' module reads).")
        self.impacts_format = fmt
        # Which level's dump is *the* track3p_particles dump when a scan
        # produced several. The artifact is always the results directory
        # (plan 3.4); this only disambiguates for the consumer, which is why it
        # travels in ctx.field_levels rather than changing the artifact's shape.
        self.field_level = self.config.get('field_level')
        if self.field_level is not None:
            self.field_level = float(self.field_level)

    # ---- input injection ---------------------------------------------------

    def _field_source(self, ctx):
        """``(producer module or None, its results directory name)`` for the
        ``em_solution`` this run reads."""
        producer = next((m for m in ctx.modules if EM_SOLUTION in m.provides),
                        None)
        return producer, ctx.job_names.get(EM_SOLUTION)

    def _prepare_solver(self, ctx, solver):
        producer, job_name = self._field_source(ctx)
        current = solver.field_dir()
        if job_name and not (current and os.path.isdir(
                os.path.join(ctx.workdir or '', current))):
            solver.set_input_leaf(('Domain', 'FieldDir'), './' + job_name)
        if self.impacts_format == 'initials-impacts':
            # Both lines: the dump is only written at all with OutputImpacts on,
            # and the container is what selects the 17-column layout that
            # carries InitialNormalField / InitialFaceArea. The scalar spelling
            # 'OutputImpactsInfo: Initials-Impacts' is silently ignored by the
            # build -- it is parsed as a container (genptab.C:543).
            solver.set_input_leaf(('OutputImpacts',), 'on')
            solver.set_input_leaf(('OutputImpactsInfo', 'Type'),
                                  'Initials-Impacts')
        self._check_s3p_scan(producer)

    def run(self, ctx, skip_execution=False):
        """The base run, plus the level a downstream consumer should prefer.

        The artifact stays the results directory in every case (plan §3.4), so
        ``field_level:`` cannot be expressed by narrowing it. It travels beside
        the job name instead, which keeps the artifact's *type* the same under
        run and dry run and leaves all resolution in the consumer."""
        super().run(ctx, skip_execution=skip_execution)
        if self.field_level is not None:
            ctx.field_levels[self._artifact] = self.field_level

    def _prepare_dry_run(self, ctx):
        producer, job_name = self._field_source(ctx)
        _append_marker(ctx, f"Track3P FieldDir: ./{job_name}\n")
        if self.impacts_format == 'initials-impacts':
            _append_marker(ctx, 'Track3P OutputImpactsInfo: '
                                '{ Type: Initials-Impacts }\n')
        self._check_s3p_scan(producer)

    @staticmethod
    def _check_s3p_scan(producer):
        """Warn when the fields come from an S3P scan with more than one
        frequency. Both CW23 S3P-driven Track3P cases (TW7Cell, Window) run S3P
        at a single frequency (``Start == End``); no input key selects a scan
        point (the build's ``InputParameters`` echo lists none), so which
        frequency a multi-point scan would track is unknown."""
        if producer is None or producer.type != 's3p':
            return
        try:
            with open(producer.input_file) as file:
                scan = parse_ace3p(file.read()).find('FrequencyScan')
            start, end = (float(scan.get_leaf(k)) for k in ('Start', 'End'))
            step = float(scan.get_leaf('Interval'))
        except (OSError, TypeError, ValueError, AttributeError):
            return
        points = int(np.floor((end - start) / step + 1e-9)) + 1 if step else 1
        if points > 1:
            warnings.warn(
                f"track3p reads fields from an S3P scan of {points} frequencies "
                f"({start:g} to {end:g} Hz); Track3P has no documented way to "
                "pick one and every CW23 S3P-driven Track3P case runs S3P at a "
                "single frequency (Start == End). Check which fields the run "
                "used.", stacklevel=3)

    # ---- the axis -----------------------------------------------------------

    def _input_levels(self):
        """Levels the input file declares, read once. ``[]`` when unreadable."""
        if self._declared is None:
            try:
                with open(self.input_file) as file:
                    self._declared = declared_field_levels(file.read())
            except (OSError, TypeError, ValueError):
                self._declared = []
        return self._declared

    def _levels(self):
        solver = self._solver
        if solver is None:
            levels = self._input_levels()
            return np.array(levels if levels else [0.0])
        return np.asarray(solver.output_data['FieldLevel'], dtype=float)

    def field_index(self, ctx):
        """``('FieldLevel', levels)`` — from the run when there is one, else
        from the input file's ``FieldScales`` (a dry run), else the ``[0.0]``
        sentinel S3P and T3P use when the input cannot be read."""
        return 'FieldLevel', self._levels()

    def field(self, ctx):
        """The run's tables and log scalars, or ``None`` under dry-run:
        ``{FieldLevel, EnhancementCounter, ResonantParticles, FaradayCups,
        EmittingFaces, TotalEmitted, Survived, Log, ImpactsFiles,
        LostParticlesFiles}``."""
        solver = self._solver
        if solver is None or not solver.output_data:
            return None
        return dict(solver.output_data)

    # ---- extraction ---------------------------------------------------------

    def extract(self, ctx, spec):
        """Return one quantity from the Track3P run.

        Per level (arrays aligned to ``FieldLevel``; ``at: {field_level: x}``
        picks one):

        * from ``OUTPUT/enhancementCounter`` — ``max_enhancement`` (largest
          ``maxEnhancement`` among the level's rows), ``mean_enhancement`` (mean
          ``averageEnhancement``), ``total_impacts`` (sum of ``totalImpactNum``),
          ``resonant_count`` (rows, i.e. particles above ``MinimumEC``);
        * from ``OUTPUT/resonantparticles`` — ``resonant_particles`` (distinct
          IDs), ``max_resonant_energy``;
        * from ``ImpactsInfo_<level>``, read on first use — ``impact_count``
          (rows with impact ordinal ≥ 1) and ``max_impact_energy``; and
          ``lost_count`` from ``LostParticles_<level>``.

        A level with no rows in a table is NaN, and a table the run did not
        write raises naming the ``Postprocess`` token that enables it.

        Scalars (one per run; they repeat down the level rows): ``total_emitted``,
        ``emitting_faces``, ``survived`` from the log — NaN when the build did
        not write the line (a secondary-emission run reports no
        ``Total Emitted Particles``).

        ``captured_electrons`` — ``sum(NumElectrons)`` of one Faraday cup;
        ``at: {boundary: id}`` is required, naming a ``BoundaryID`` of the
        input's ``FaradayCup`` block.

        ``mp_onset_level`` — the lowest level whose ``max_enhancement`` is at
        least ``at: {threshold: t}`` (default 1.0, i.e. growth), NaN when none
        reaches it. The multipacting objective an optimizer minimises or
        constrains.

        Under dry-run every quantity is a NaN array aligned to the declared
        levels, so the dry-run table has the shape the real one will.
        """
        quantity, at = self._parse_spec(spec)
        stray = sorted(set(at) - set(self._AXES))
        if stray:
            raise ValueError(
                "a track3p 'at:' narrows on " + str(list(self._AXES))
                + ', not ' + str(stray) + '.')
        if quantity not in self.QUANTITIES:
            raise ValueError(
                "Unknown track3p quantity '" + str(quantity) + "'. Known: "
                + str(sorted(self.QUANTITIES)) + '.')
        solver = self._solver
        if solver is None:
            return np.full(len(self._levels()), float('nan'))
        data = solver.output_data

        if quantity in self.SCALARS:
            value = data[{'total_emitted': 'TotalEmitted',
                          'emitting_faces': 'EmittingFaces',
                          'survived': 'Survived'}[quantity]]
            return float('nan') if value is None else value

        if quantity == 'captured_electrons':
            return self._captured(data, at)

        if quantity == 'mp_onset_level':
            threshold = float(at.get('threshold', 1.0))
            peak = self._per_level(data, 'max_enhancement')
            hits = np.flatnonzero(np.nan_to_num(peak, nan=-np.inf) >= threshold)
            return float(self._levels()[hits[0]]) if len(hits) else float('nan')

        values = self._per_level(data, quantity)
        if 'field_level' not in at:
            return values
        return values[self._level_index(float(at['field_level']))]

    @staticmethod
    def _parse_spec(spec):
        if isinstance(spec, dict):
            return spec.get('quantity'), dict(spec.get('at') or {})
        if isinstance(spec, (list, tuple)) and spec:
            return spec[0], {}
        return spec, {}

    def _level_index(self, level):
        levels = self._levels()
        hits = np.flatnonzero(np.isclose(levels, level, rtol=1e-9, atol=0.0))
        if not len(hits):
            raise ValueError(
                'at: {field_level: ' + repr(level) + '} is not a level of this '
                "Track3P run (" + ', '.join(repr(float(v)) for v in levels)
                + '). Pick one of those, or change the FieldScales block.')
        return int(hits[0])

    def _per_level(self, data, quantity):
        """The per-level array for one quantity, aligned to ``FieldLevel``."""
        levels = self._levels()
        if quantity in self._TABLE:
            key, column, reduce = self._TABLE[quantity]
            table = data.get(key)
            if not table:
                token = ('Postprocess.EnhancementCounter.Token' if key ==
                         'EnhancementCounter' else 'Postprocess.ResonantParticles.Token')
                raise ValueError(
                    f"this Track3P run wrote no OUTPUT/{key[0].lower() + key[1:]} "
                    f"table, so '{quantity}' is unavailable. It is written when "
                    f"the input sets {token}: on (and Postprocess.Toggle: on).")
            column_values = np.asarray(table[column], dtype=float)
            out = np.full(len(levels), float('nan'))
            for i, level in enumerate(levels):
                mask = np.isclose(column_values, level, rtol=1e-9, atol=0.0)
                if mask.any():
                    out[i] = float(reduce(table, mask))
            return out
        if quantity in ('impact_count', 'max_impact_energy'):
            out = np.full(len(levels), float('nan'))
            for i, level in enumerate(levels):
                summary = self._summary(data['ImpactsFiles'], level)
                if summary is not None:
                    out[i] = summary[quantity]
            return out
        if quantity == 'lost_count':
            out = np.full(len(levels), float('nan'))
            for i, level in enumerate(levels):
                summary = self._summary(data['LostParticlesFiles'], level)
                if summary is not None:
                    out[i] = summary['impact_count']
            return out
        raise ValueError(f"'{quantity}' is not a per-level track3p quantity.")

    def _summary(self, files, level):
        """:func:`impacts_summary` of the dump for `level`, cached per path;
        ``None`` when the run wrote no file for that level."""
        path = next((p for lv, p in files.items()
                     if np.isclose(lv, level, rtol=1e-9, atol=0.0)), None)
        if path is None:
            return None
        if path not in self._summaries:
            self._summaries[path] = impacts_summary(path)
        return self._summaries[path]

    def _captured(self, data, at):
        cups = data.get('FaradayCups') or {}
        if 'boundary' not in at:
            raise ValueError(
                "'captured_electrons' is per Faraday cup, so it needs "
                "'at: {boundary: <id>}'. This run wrote cups for boundaries "
                + str(sorted(cups)) + '.')
        boundary = int(at['boundary'])
        if boundary not in cups:
            raise ValueError(
                f"this Track3P run wrote no OUTPUT/faradaycup_{boundary}; it "
                f"wrote {sorted(cups)}. Cups follow the input's "
                "Postprocess.FaradayCup { Token: on  BoundaryID: ... }.")
        electrons = cups[boundary].get('NumElectrons')
        if electrons is None:
            raise ValueError(
                f"OUTPUT/faradaycup_{boundary} has no NumElectrons column; its "
                f"columns are {sorted(cups[boundary])}.")
        return float(np.sum(electrons))

    # ---- resume -------------------------------------------------------------

    def verify(self, ctx):
        """Whether the run finished: its log is in the results directory **and**
        ends with ``Done!``. A killed run leaves a log without the terminator,
        which the base class's presence check would mistake for a result."""
        if ctx.dry_run:
            return None
        path = os.path.join(ctx.workdir or '', self._results_dir(ctx),
                            self._results_file)
        if not os.path.isfile(path):
            return False
        try:
            with open(path) as file:
                return any(line.strip() == 'Done!' for line in file)
        except OSError:
            return None


# --------------------------------------------------------------------------- #
# Acdtool postprocess
# --------------------------------------------------------------------------- #

# What an ``at:`` narrows, per output shape. Mode-indexed sections are acdtool's
# table axis, so their ``at:`` is *optional* — without it the whole per-mode array
# comes back and a sweep table goes one row per mode. Surface-indexed sections are
# never a table axis, so theirs is *required* (design decision 2). The remaining
# shapes have no index axis at all and take no ``at:``.
ACDTOOL_AXIS = {MODE_TABLE: 'mode', SURFACE: 'surface'}


def _render_index(value):
    """Render a mode / surface ID for the deprecation message the way a user
    would write it in YAML — ``0`` rather than ``'0'`` when it is a number."""
    try:
        return str(int(str(value).strip()))
    except (TypeError, ValueError):
        return repr(str(value))


def _index_key(value):
    """The parsed-output dict key a mode / surface index resolves to.

    The readers key modes and surfaces by their *string* IDs, because that is
    what a positional output spec names literally (``['RoverQ', '0', 'RoQ']``),
    while the mapping form naturally writes ``at: {mode: 0}`` as a number. So
    ``0``, ``'0'`` and ``0.0`` all have to find mode ``'0'``."""
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    return str(value).strip()


def acdtool_spec(spec, warn=False):
    """Normalize an ``output_parameters`` spec for the acdtool module, or return
    ``None`` when the spec names no acdtool section.

    This is the **single translation site** between the two spec forms — both
    :func:`lume_ace3p.workflow_graph._infer_output_module` (which only asks
    whether a spec is acdtool's) and :meth:`AcdtoolModule.extract` (which asks
    what it means) come through here.

    The **mapping form** is the target::

        'R/Q'   : {module: acdtool, section: RoverQ, quantity: RoQ}
        'f0'    : {module: acdtool, section: RoverQ, quantity: Frequency,
                   at: {mode: 0}}
        'E_max' : {module: acdtool, section: maxFieldsOnSurface, quantity: Emax,
                   at: {surface: 6}}
        'loc_x' : {module: acdtool, section: maxFieldsOnSurface,
                   quantity: Emax_location, component: x, at: {surface: 6}}

    The **positional form** ``['RoverQ', '0', 'RoQ']`` is a deprecated alias
    rewritten to it. Its middle element was never a *selector* but an **index
    axis**: ``modeID2 = -1`` in the ``.rfpost`` input means "every mode the solver
    produced", so mode 0 is one narrowing of a table, not the table. Dropping the
    ``at:`` is how you ask for the whole axis, which is what a dispersion curve or
    an HOM catalog wants and what the list form cannot express.

    Returns ``{section, quantity, index, at, component, deprecated}``; ``index``
    is the ``at:`` value for the section's own axis. With `warn` set, the
    positional form emits a :class:`DeprecationWarning` naming its mapping
    replacement.
    """
    if isinstance(spec, dict):
        section = spec.get('section')
        if section is None or section not in SECTIONS:
            return None
        at = dict(spec.get('at') or {})
        axis = ACDTOOL_AXIS.get(SECTIONS[section].shape)
        return {'section': section, 'quantity': spec.get('quantity'),
                'index': at.get(axis) if axis else None, 'at': at,
                'component': spec.get('component'), 'deprecated': False}
    if isinstance(spec, (list, tuple)) and spec:
        section = spec[0]
        if section not in SECTIONS:
            return None
        axis = ACDTOOL_AXIS.get(SECTIONS[section].shape)
        rest = list(spec[1:])
        index = rest.pop(0) if (axis is not None and rest) else None
        quantity = rest.pop(0) if rest else None
        component = rest.pop(0) if rest else None
        resolved = {'section': section, 'quantity': quantity, 'index': index,
                    'at': {axis: index} if index is not None else {},
                    'component': component, 'deprecated': True}
        if warn:
            parts = ['module: acdtool', 'section: ' + str(section)]
            if quantity is not None:
                parts.append('quantity: ' + str(quantity))
            if component is not None:
                parts.append('component: ' + str(component))
            if index is not None:
                parts.append('at: {' + axis + ': ' + _render_index(index) + '}')
            warnings.warn(
                'the positional acdtool output spec ' + repr(list(spec))
                + ' is deprecated; write it as {' + ', '.join(parts) + '}. '
                'Both forms produce the same value today. The mapping form also '
                'expresses what the list cannot: dropping the '
                + ("'at:'" if axis else 'index')
                + ' asks for every mode rather than one.',
                DeprecationWarning, stacklevel=3)
        return resolved
    return None


class AcdtoolModule(Module):
    """One ``acdtool`` invocation. Provides ``rf_post``; what it *requires*
    follows from the command.

    ``acdtool`` is the postprocessing layer for all of ACE3P, not only for
    frequency-domain results, so a single ``requires = {em_solution}`` was too
    coarse: it made ``[cubit, t3p, acdtool]`` a validation error even though
    ``transwake`` / ``coaxsignal`` / ``volmontomode`` are precisely time-domain
    postprocessors. The requirement now comes from the command table
    (:data:`lume_ace3p.acdtool.COMMANDS`), set on the *instance* in
    :meth:`__init__` — which is all the DAG needs, since
    ``workflow_graph._resolve_order`` reads ``requires``/``provides`` off
    instances after they are built::

        workflow :
          - module : acdtool                      # requires em_solution
            input  : 'pillbox-rtop.rfpost'

          - module  : acdtool
            command : 'postprocess transwake'     # requires td_solution
            args    : [0.0, 0.0, 0.0, 0.0125]     # jobname is injected

    Omitting ``command`` infers ``postprocess rf`` from a ``.rfpost`` input, so
    configs written before the command surface opened up run unchanged.

    The ``<jobname>`` the positional commands take is *injected* from
    ``ctx.job_names`` — the results directory the producing solver actually
    resolved — rather than repeated in the YAML; ``jobname:`` overrides it.

    **Mutating consumers.** ``postprocess transwake`` (and ``wake_new`` /
    ``wake_direct``) write their result *over* ``<jobname>/OUTPUT/wakefield.out``,
    the file :class:`T3PModule` already parsed. In DAG order T3P parses the
    longitudinal wake, then acdtool overwrites it with the transverse one, so
    without intervention the workflow would report a wrong-but-plausible number.
    This module therefore calls the producer's re-parse hook
    (``ctx.reparse[artifact]``) after such a command, and ``T3PModule`` remains
    the single owner of every wakefield quantity — one parser
    (:func:`~lume_ace3p.ace3p.parse_wakefield`), one place to ask, whether or not
    acdtool ran. See the Phase-2 decision in ``plans/acdtool_rework_plan.md``.
    """

    type = 'acdtool'
    # Class-level default for the common case; __init__ narrows it per command.
    requires = frozenset({EM_SOLUTION})
    provides = frozenset({RF_POST})

    def __init__(self, config=None, name=None):
        super().__init__(config, name)
        self.input_file = self.config.get('input') or self.config.get('rfpost_input')
        self.args = list(self.config.get('args') or [])
        self.jobname = self.config.get('jobname')
        self.tasks = self.config.get('tasks')
        self.cores = self.config.get('cores')
        self.opts = self.config.get('opts', '')
        self.command, self.spec = self._resolve_command()
        self.requires = (frozenset({self.spec.requires}) if self.spec.requires
                         else frozenset())
        self._acdtool = None
        # Deprecated positional output specs already warned about, so a sweep of
        # N points warns once per spec rather than N times.
        self._warned = set()

    def _resolve_command(self):
        """Return ``(command, spec)`` for the declared command, or the one
        inferred from a ``.rfpost`` input when none is declared.

        Raises on an unknown command (listing the known ones) and on a known but
        unwired one (naming why it is held back), so neither fails later as a
        mangled command line."""
        command = self.config.get('command')
        if command is None:
            # No input file either: 'postprocess rf' over the generated default
            # .rfpost template, which is what a bare acdtool entry has always
            # meant.
            extension = (os.path.splitext(self.input_file)[1].lower()
                         if self.input_file else '.rfpost')
            if extension != '.rfpost':
                raise ValueError(
                    f"module 'acdtool' cannot infer a command from input file "
                    f"'{self.input_file}': only '.rfpost' implies a command "
                    f"('postprocess rf'). Set 'command' explicitly. Commands "
                    f"usable as a workflow step: {wired_commands()}.")
            command = 'postprocess rf'
        spec = resolve_command(command)          # raises, listing known commands
        if not spec.wired:
            raise ValueError(
                f"acdtool command '{command}' is not available as a workflow "
                f"step: {spec.note}. Commands usable as a workflow step: "
                f"{wired_commands()}."
                + ('' if spec.dispatch else
                   ' It can still be invoked directly through '
                   'lume_ace3p.acdtool.Acdtool.'))
        return command, spec

    def _resolve_jobname(self, ctx):
        """The results-directory name to pass to a positional command: an
        explicit ``jobname:``, else the name the producing solver resolved, else
        the documented per-solver default."""
        if not self.spec.jobname:
            return None
        return (self.jobname
                or ctx.job_names.get(self.spec.requires)
                or self.spec.default_jobname)

    def run(self, ctx, skip_execution=False):
        required = self.spec.requires
        if required and required not in ctx.artifacts:
            raise ValueError(f"module 'acdtool' ({self.command}) requires a "
                             f"{required} artifact.")
        jobname = self._resolve_jobname(ctx)
        if ctx.dry_run:
            self._acdtool = None
            marker = ('Dry run mode: Acdtool step skipped.\n'
                      f'Acdtool command: {self.command}\n'
                      f'Acdtool input: {self.input_file}\n')
            if self.args:
                marker += f'Acdtool args: {self.args}\n'
            if jobname:
                marker += f'Acdtool jobname: {jobname}\n'
            _append_marker(ctx, marker)
            ctx.artifacts[RF_POST] = ctx.workdir
            return
        ctx.ensure_workdir()
        acdtool = Acdtool(self.input_file, workdir=ctx.workdir,
                          acdtool_command=self.command,
                          acdtool_args=self.args,
                          jobname=jobname,
                          acdtool_tasks=self.tasks,
                          acdtool_cores=self.cores,
                          acdtool_opts=self.opts,
                          ace3p_path=ctx.paths.get('ace3p', ''),
                          mpi_caller=ctx.paths.get('mpi', ''),
                          log_file=self.log_file(ctx))
        if skip_execution:
            # Resumed: this command already ran in this workdir, so read the
            # output it left rather than invoking acdtool again
            # (:meth:`lume_ace3p.acdtool.Acdtool.parse_output` is ``run`` minus the
            # subprocess).
            acdtool.parse_output(self.command, jobname=jobname)
        else:
            acdtool.run()
        self._acdtool = acdtool
        ctx.artifacts[RF_POST] = ctx.workdir
        # This command rewrote its producer's output in place; have the producer
        # re-read it so downstream extraction sees the new result, not the one
        # parsed before acdtool ran.
        #
        # Called on the resumed path too, and it is not redundant there: the
        # producer parsed the file *this* run, and by then it already held the
        # previous run's acdtool result — so the value is right either way, and
        # calling it keeps "after this module, the producer's parse reflects the
        # file on disk" true unconditionally rather than only on the path that
        # launched a subprocess. Skipping the re-parse instead would make the
        # invariant depend on how the point got here.
        if self.spec.mutates and self.spec.mutates in ctx.reparse:
            ctx.reparse[self.spec.mutates]()

    def verify(self, ctx):
        """Whether this command's output file is still there — **unless the
        command overwrites its producer's file**, in which case the answer is
        ``None``, deliberately and permanently.

        ⚠️ This is design decision 3 of
        ``plans/evaluation_isolation_resume_plan.md``, and the reason the whole
        resume mechanism is a manifest rather than a file-presence check.
        ``postprocess transwake`` (and ``wake_new`` / ``wake_direct``) write their
        result *over* ``<jobname>/OUTPUT/wakefield.out`` — the file
        :class:`T3PModule` already wrote and parsed. That file is therefore
        present whether or not acdtool ever ran, so its presence says **nothing**
        about this step. Reading it as "complete" would skip the transwake step
        and report T3P's *longitudinal* wake as a kick factor: defect 7 of
        ``plans/acdtool_rework_plan.md``, reintroduced by the resume feature.

        Do not "improve" this into a presence check. Only the manifest's record
        that acdtool *ran* distinguishes the two states, which is what
        :mod:`lume_ace3p.state` exists for.

        ``None`` also for a command that writes no file this wrapper knows about,
        and under dry-run."""
        if ctx.dry_run or self.spec.mutates is not None:
            return None
        output = self.spec.resolve_output(self._resolve_jobname(ctx))
        if not output:
            return None
        return os.path.isfile(os.path.join(ctx.workdir or '', output))

    def extract(self, ctx, spec):
        """Return one quantity from ``postprocess rf``'s ``rfpost.out``.

        The spec is the mapping form ``{section, quantity, at: {mode|surface: n},
        component}`` or its deprecated positional alias
        ``['RoverQ', '0', 'RoQ']``; :func:`acdtool_spec` translates between them.
        What comes back follows the section's *shape*:

        * **mode-indexed** (``RoverQ``, ``kickFactor``, …) — the full per-mode
          array without ``at:``, the scalar for one mode with
          ``at: {mode: n}``. The array is aligned to :meth:`field_index`, so a
          sweep table goes one row per mode.
        * **surface-indexed** (``maxFieldsOnSurface``, ``powerThroughSurface``) —
          ``at: {surface: n}`` is **required**, since ``ModeID`` is acdtool's only
          table axis (design decision 2). Omitting it raises naming the surfaces
          the run reported.
        * **unindexed** (``FieldAtPoint``, ``[scaling]``) — the scalar directly.
        * **curve / grid** — not a table column at all: those are per-position
          arrays and field maps, exposed through :meth:`field`.

        ``component`` picks ``x`` / ``y`` / ``z`` out of a location vector
        (``Emax_location``).

        Only ``postprocess rf`` produces an ``rfpost.out`` with named sections to
        index. The other commands' results belong to the solver whose output they
        write into or alongside — a transwake kick factor comes from ``t3p``, not
        from here — so asking this module for a quantity says the output spec
        names the wrong module."""
        if self.spec.reader != RFPOST:
            if self.spec.mutates == TD_SOLUTION:
                detail = ("A transwake result is read by the t3p module, which "
                          "owns wakefield.out: {module: t3p, quantity: "
                          "kick_factor}.")
            elif self.spec.reader is not None:
                detail = (f"Its output is a column table, exposed per row as a "
                          f"field artifact through field(), not as a table "
                          f"column.")
            else:
                detail = "Only 'postprocess rf' writes indexable output."
            raise ValueError(
                f"the acdtool command '{self.command}' produces no indexable "
                f"rfpost.out sections, so '{spec}' cannot be extracted from it. "
                + detail)
        resolved = acdtool_spec(spec, warn=repr(spec) not in self._warned)
        if resolved is not None and resolved['deprecated']:
            self._warned.add(repr(spec))
        if resolved is None:
            raise ValueError(
                "cannot route the acdtool output spec " + repr(spec) + ": it "
                "names no known .rfpost block. A spec is either the mapping form "
                "{module: acdtool, section: <block>, quantity: <name>} or the "
                "positional ['<block>', ...]. Known blocks: "
                + str(sorted(SECTIONS)) + '.')
        section, quantity = resolved['section'], resolved['quantity']
        index, component = resolved['index'], resolved['component']
        shape = SECTIONS[section].shape
        axis = ACDTOOL_AXIS.get(shape)
        stray = sorted(set(resolved['at']) - ({axis} if axis else set()))
        if stray:
            raise ValueError(
                "acdtool section '" + section + "' takes "
                + ("'at: {" + axis + ": n}'" if axis else 'no at: narrowing')
                + ', not ' + str(stray) + '.')
        if shape in (CURVE, GRID):
            raise ValueError(
                "acdtool section '" + section + "' writes its own file, not an "
                'indexable rfpost.out section: a curve is a per-position array '
                'and a field map a grid, so both ride as a field artifact '
                'through field() rather than as a result-table column.')
        if self._acdtool is None:
            return float('nan')
        data = self._acdtool.output_data
        if section not in data:
            raise ValueError(
                "acdtool reported no '" + section + "' section. Sections read "
                'from ' + str(self._acdtool.output_file) + ': '
                + str(sorted(data)) + ". A block is reported only when its "
                ".rfpost input sets 'ionoff = 1'.")
        values = data[section]
        if shape == MODE_TABLE:
            ids = [str(i) for i in values.get('ModeIDs', [])]
            if index is None:
                # The whole axis: aligned to field_index, so a sweep table gets
                # one row per mode.
                return np.array([
                    self._value(values[key], quantity, component,
                                "acdtool section '" + section + "' mode " + key)
                    for key in ids])
            key = _index_key(index)
            if key not in values:
                raise ValueError(
                    "acdtool section '" + section + "' reported no mode "
                    + str(index) + '; this run has modes ' + str(ids) + '. The '
                    "count follows the solve — 'modeID2 = -1' in the .rfpost "
                    "input means every mode the solver produced.")
            return self._value(values[key], quantity, component,
                               "acdtool section '" + section + "' mode " + key)
        if shape == SURFACE:
            ids = [str(i) for i in values.get('SurfaceIDs', [])]
            if index is None:
                raise ValueError(
                    "acdtool section '" + section + "' is surface-indexed, and "
                    "'ModeID' is acdtool's only table axis, so it must be "
                    "narrowed to one surface: add 'at: {surface: n}'. This run "
                    'reported surface(s) ' + str(ids) + '. The .rfpost block '
                    "pins the surface it evaluates ('surfaceID = 6'), so a run "
                    'reports few of them.')
            key = _index_key(index)
            if key not in values:
                raise ValueError(
                    "acdtool section '" + section + "' reported no surface "
                    + str(index) + '; this run reported ' + str(ids) + '. The '
                    'surface IDs are the Cubit journal sideset IDs named by the '
                    "block's 'surfaceID'.")
            return self._value(values[key], quantity, component,
                               "acdtool section '" + section + "' surface " + key)
        # POINT / RUN: scalar assignments, no index axis at all.
        return self._value(values, quantity, component,
                           "acdtool section '" + section + "'")

    @staticmethod
    def _value(entry, quantity, component, where):
        """One value out of a parsed section, or an error naming what the section
        actually reported.

        The column names are whatever the output file carried (Phase 3 reads them
        from the header row / the ``name = value`` lines), so they are reported
        from the data rather than checked against a hardcoded set — which is what
        the previous ``assert entry in {...}`` per section did."""
        names = sorted(entry)
        if quantity is None:
            raise ValueError(
                where + " needs a 'quantity'; it reported " + str(names) + '.')
        if quantity not in entry:
            raise ValueError(
                where + " reported no '" + str(quantity) + "'. It reported "
                + str(names) + '.')
        value = entry[quantity]
        if component is None:
            if isinstance(value, dict):
                raise ValueError(
                    where + "'s '" + str(quantity) + "' is a vector "
                    + str(sorted(value)) + "; name one part with 'component: x'.")
            return value
        if not isinstance(value, dict):
            raise ValueError(
                where + "'s '" + str(quantity) + "' is a scalar, so "
                "'component: " + str(component) + "' does not apply.")
        if component not in value:
            raise ValueError(
                where + "'s '" + str(quantity) + "' has no component '"
                + str(component) + "'; it has " + str(sorted(value)) + '.')
        return value[component]

    def field_index(self, ctx):
        """acdtool's table axis is ``ModeID``: returns ``('ModeID', array)`` when
        the run reported a mode-indexed section, else ``None``.

        **It is the only axis acdtool ever offers** (design decision 2 of
        ``plans/acdtool_rework_plan.md``). Surface-indexed sections resolve to
        scalars through an ``at: {surface: n}``, so one acdtool module cannot put
        two axes on one table; across modules the collision falls out of DAG
        order, since :meth:`Workflow.field_index` takes the first producer — so
        ``[cubit, s3p, acdtool]`` stays indexed on S3P's ``Frequency`` (the
        ``window`` case is a frequency scan postprocessed at one ``FreqScanID``,
        so that is also the right answer) and acdtool's per-mode arrays ride as a
        field artifact instead.

        Returns ``None`` under dry-run rather than S3P/T3P's single-row sentinel,
        for the reason :meth:`Omega3PModule.field_index` records: the mode count
        is a *result* of the solve rather than something the input declares, and a
        sentinel would reshape the existing dry-run sweep tables."""
        if self._acdtool is None:
            return None
        ids = table_mode_ids(self._acdtool.output_data)
        if not ids:
            return None
        return 'ModeID', np.asarray(ids)

    def field(self, ctx):
        """Return acdtool's non-table output as a field artifact, or ``None``
        under dry-run / when the command produced none.

        Two kinds ride here:

        * **curves and grids.** The ``filename`` blocks (``ALLFieldOnLine``,
          ``FieldOnLine``, ``Multipole``, ``GBZFFT``, ``Track``, ``TrackScan``)
          each write their own ``#``-commented column table, and ``coaxsignal``
          writes a headerless one; those are per-position arrays, so they stay out
          of the flat result table (design decision 4) and appear as
          ``{section: {filename: {column: array}}}``. Grid blocks contribute
          their filenames only — see
          :meth:`lume_ace3p.acdtool.Acdtool._read_files`.
        * **the mode-indexed sections**, as ``{section: {column: array}}`` (see
          :func:`lume_ace3p.acdtool.mode_table_arrays`). These *are* table columns
          when acdtool's ``ModeID`` is the table axis, but in a chain where
          another module owns the axis — ``s3p -> acdtool``, where S3P's
          ``Frequency`` wins on DAG order — a per-mode array cannot be a column of
          a frequency-indexed table, and this is where design decision 2 sends it.

        Note :meth:`Workflow.field` takes the *first* module that returns one, so
        in an ``omega3p -> acdtool`` chain the solver's own field wins; that is a
        pre-existing one-field-per-workflow limitation of the framework, not a
        property of these outputs.
        """
        if self._acdtool is None:
            return None
        field = dict(field_sections(self._acdtool.output_data))
        field.update(mode_table_arrays(self._acdtool.output_data))
        return field or None


# --------------------------------------------------------------------------- #
# Particles (field-emission weighting)
# --------------------------------------------------------------------------- #


def _impacts_dumps(directory):
    """``{field level: path}`` for the ``ImpactsInfo_<level>`` dumps in a
    Track3P results directory. Same naming rule the wrapper's reader uses, so
    the consumer and the producer agree on what a dump is called."""
    return level_files(directory, 'ImpactsInfo_')


def _levels_note(dumps):
    """`` (levels: 2.3e+07, 2.4e+07)`` for an error message, or ``''``."""
    if not dumps:
        return ''
    return ' (levels: ' + ', '.join(f'{level:g}' for level in sorted(dumps)) + ')'


class FieldEmissionModule(Module):
    """Requires ``track3p_particles``, provides ``particle_source``.

    Owns the ``beta`` / ``beta_input`` / ``beta_inputs`` resolution. Always runs
    (the field-emission weighting is pure Python and produces real numbers), even
    under dry-run — the Geant4 binary is the only thing a dry run skips, so the
    particle source it consumes is always produced.

    Its one input artifact comes in two shapes and this module resolves both
    (see :meth:`_resolve_dump`): a **dump file** from
    :class:`Track3PSourceModule`, or a **results directory** from an
    in-pipeline :class:`Track3PModule`, in which the per-level
    ``ImpactsInfo_<level>`` dumps live. A scan that produced several dumps is
    ambiguous and raises unless ``field_level:`` on the ``track3p`` module says
    which one to take.

    The dump must be in the ``Initials-Impacts`` layout — the default layout has
    no ``InitialNormalField`` / ``InitialFaceArea`` to weight by and
    :meth:`Particles.load` silently misreads it. ``impacts_format:
    initials-impacts`` on the ``track3p`` module injects the selector, and
    :class:`~lume_ace3p.workflow_graph.Workflow` rejects a chain that is missing
    it at build time rather than after the solve."""

    type = 'field_emission'
    requires = frozenset({TRACK3P_PARTICLES})
    provides = frozenset({PARTICLE_SOURCE})

    def __init__(self, config=None, name=None):
        super().__init__(config, name)
        self.params = dict(self.config)
        self.output_file = self.params.pop('output', None) \
            or self.params.pop('particle_output', None)
        self._filtered = None

    def _resolve_beta(self, inputs):
        """Return the particle params for this run. When ``beta_input``
        (broadcast one input-space variable to all bins) or ``beta_inputs``
        (one variable per bin) is set, build the per-bin beta list from the
        ``particles`` bucket (the field-enhancement scaling belongs to this
        post-Track3P weighting step, not to Cubit). Falls back to the ``cubit``
        bucket for back-compat with legacy configs that declared β there."""
        params = self.params
        beta_input = params.get('beta_input')
        beta_inputs = params.get('beta_inputs')
        if beta_input is None and beta_inputs is None:
            return params
        if beta_input is not None and beta_inputs is not None:
            raise ValueError("Set only one of 'beta_input' or 'beta_inputs'.")
        num_bins = params.get('num_bins')
        if num_bins is None:
            raise ValueError("'num_bins' must be set when using "
                             "'beta_input' or 'beta_inputs'.")

        def beta_value(name):
            if name in inputs.particles:
                return float(inputs.particles[name])
            if name in inputs.cubit:      # back-compat: legacy β under cubit
                return float(inputs.cubit[name])
            raise KeyError(f"beta variable '{name}' not found in "
                           "'input_parameters' (expected under the "
                           "'particles:' bucket).")

        if beta_input is not None:
            effective_beta = [beta_value(beta_input)] * num_bins
        else:
            if len(beta_inputs) != num_bins:
                raise ValueError(f"len(beta_inputs)={len(beta_inputs)} must "
                                 f"equal num_bins={num_bins}.")
            effective_beta = [beta_value(n) for n in beta_inputs]

        params = dict(params)
        params['beta'] = effective_beta
        return params

    def run(self, ctx, skip_execution=False):
        """Weight and write the particle source.

        ``skip_execution`` is accepted and ignored: there is no subprocess to
        skip, and this module's own run state (``_filtered``, which
        :meth:`extract` reads) exists only as a result of doing the work. The
        weighting is pure Python over a Track3P dump, so a resumed point pays
        milliseconds to have it rather than carrying a second, file-backed way to
        reconstruct it."""
        if TRACK3P_PARTICLES not in ctx.artifacts:
            raise ValueError(f"module '{self.type}' requires a "
                             f"track3p_particles artifact.")
        dump = self._resolve_dump(ctx, stage=True)
        params = dict(self._resolve_beta(ctx.inputs))
        params.setdefault('output_format', 'geant4')
        particles = Particles(dump, params, output_file=self.output_file,
                              workdir=ctx.workdir)
        self._filtered = particles.run()
        ctx.artifacts[PARTICLE_SOURCE] = os.path.join(ctx.workdir,
                                                      particles.output_file)

    def _resolve_dump(self, ctx, stage=False):
        """The Track3P dump to reweight, as a path relative to the workdir.

        The ``track3p_particles`` artifact has two shapes and always has had
        (plan §3.4): :class:`Track3PSourceModule` provides a **file** — an
        externally produced dump, staged into the workdir as today — while
        :class:`Track3PModule` provides its **results directory**, which holds
        one ``ImpactsInfo_<level>`` per field level. All the resolution lives
        here so the artifact's type never depends on how many levels a run
        produced.

        A directory is *not* staged. The dump already sits inside the workdir,
        so the relative subpath is handed to :class:`Particles` directly;
        staging would copy a file the workdir already contains (137 MB each at
        cryomodule scale) to a second name beside it.

        ``stage`` is what separates :meth:`run` from :meth:`verify`: both need
        the same name, only the former may create anything to get it.

        **Which shape it is follows from the producer, not from the
        filesystem.** The module in ``ctx.modules`` that provides
        ``track3p_particles`` is asked, the way :meth:`_emitted_note` already
        asks it: a :class:`Track3PSourceModule` means a file, a
        :class:`Track3PModule` means a results directory. The filesystem cannot
        answer this reliably — ``verify`` runs *before* staging, so a dump file
        that is not there yet looks like neither — and dispatching on it is how
        an ``isfile`` variant briefly broke ``verify`` (job 39931202) and how a
        results directory that has since been deleted gets silently misread as an
        unstaged file, making ``verify`` return ``False`` instead of explaining.

        When no producer is in ``ctx.modules`` — a hand-built
        :class:`RunContext` with artifacts alone, which the unit tests and a
        direct driver both use — fall back to asking whether the artifact *is a
        directory*. Not whether it is an existing file: a recorded path that does
        not exist yet is a dump file whose staging has not happened, and
        :meth:`verify` must still derive a name from it."""
        src = ctx.artifacts[TRACK3P_PARTICLES]
        if not self._producer_is_solver(ctx, src):
            if stage:
                _stage_file(ctx, src)
            return os.path.basename(src)

        results = ctx.job_names.get(TRACK3P_PARTICLES)
        directory = os.path.join(src, results) if results else src
        if not os.path.isdir(directory):
            raise ValueError(
                f"module '{self.type}': {directory!r} is not a directory, so "
                f"there is no Track3P results directory to find a dump in. The "
                f"'track3p' module records its results directory name as the "
                f"job name; a run that wrote somewhere else needs "
                f"'results_dir:' set to match.")

        dumps = _impacts_dumps(directory)
        wanted = ctx.field_levels.get(TRACK3P_PARTICLES)
        if wanted is not None:
            match = next((path for level, path in dumps.items()
                          if np.isclose(level, wanted, rtol=1e-9, atol=0.0)),
                         None)
            if match is None:
                raise ValueError(
                    f"module '{self.type}': the 'track3p' module asked for "
                    f"field level {wanted:g}, but {directory} has no "
                    f"ImpactsInfo for it{_levels_note(dumps)}.")
            return os.path.relpath(match, ctx.workdir)

        if not dumps:
            raise ValueError(
                f"module '{self.type}': no ImpactsInfo_<level> dump in "
                f"{directory}. Either Track3P ran with 'OutputImpacts' off — "
                f"set 'impacts_format: initials-impacts' on the 'track3p' "
                f"module, which injects it together with the layout selector "
                f"this module needs — or the run emitted nothing"
                f"{self._emitted_note(ctx)}.")
        if len(dumps) > 1:
            raise ValueError(
                f"module '{self.type}': {directory} holds "
                f"{len(dumps)} ImpactsInfo dumps and nothing says which one to "
                f"reweight{_levels_note(dumps)}. Name one with 'field_level:' "
                f"on the 'track3p' module, or declare a single level in its "
                f"input's FieldScales.")
        return os.path.relpath(next(iter(dumps.values())), ctx.workdir)

    @staticmethod
    def _producer(ctx):
        """The module in this evaluation that provides ``track3p_particles``, or
        ``None`` when the context was built from artifacts alone."""
        return next((m for m in ctx.modules
                     if TRACK3P_PARTICLES in m.provides), None)

    @classmethod
    def _producer_is_solver(cls, ctx, src):
        """Whether the dump artifact is an in-pipeline Track3P **results
        directory** (as opposed to an externally supplied dump file).

        The producer's class is the answer when there is one; otherwise
        ``os.path.isdir`` — see :meth:`_resolve_dump` on why that order."""
        producer = cls._producer(ctx)
        if producer is None:
            return os.path.isdir(src)
        return isinstance(producer, Track3PModule)

    @classmethod
    def _emitted_note(cls, ctx):
        """`` (track3p.log reports Total Emitted Particles = N)`` when the run
        wrote a log saying so — the usual reason a field-emission dump is
        missing is that the emitter produced nothing, and the log says it
        outright."""
        producer = cls._producer(ctx)
        data = getattr(getattr(producer, '_solver', None), 'output_data', None)
        total = (data or {}).get('TotalEmitted')
        if total is None:
            return ''
        return (f" (its track3p.log reports Total Emitted Particles = "
                f"{total:g})")

    def verify(self, ctx):
        """Whether the weighted particle file is still in the workdir.

        Checked under dry-run too, unlike every other module here: this step is
        pure Python, so it *always* runs and always writes its file (the Geant4
        binary is the only thing a dry run skips).

        The filename is an explicit ``output:`` when the config gives one, else
        the name :class:`~lume_ace3p.particles.Particles` derives from the Track3P
        dump — which is why this can only answer once the upstream source module
        has recorded that artifact. Before then, ``None``."""
        name = self.output_file
        if not name:
            if TRACK3P_PARTICLES not in ctx.artifacts:
                return None
            try:
                dump = self._resolve_dump(ctx)
            except ValueError:
                # Nothing to resolve a name from yet (or an ambiguous scan):
                # run() will raise and say why. Not this method's answer to give.
                return None
            # Mirrors Particles.__init__'s default naming. Resolved from the
            # same subpath run() hands over, so a dump inside the solver's
            # results directory is looked for beside it, where it was written.
            name = default_output_name(dump)
        return os.path.isfile(os.path.join(ctx.workdir or '', name))

    def extract(self, ctx, spec):
        """Expose simple scalars off the weighted/filtered particle set.

        ``spec`` is ``'count'`` (number of filtered particles) or
        ``'total_weight'`` (sum of the field-emission ParticleWeight); a list
        wrapping either, or the target-schema mapping ``{'quantity': ...}``, is
        also accepted."""
        if isinstance(spec, dict):
            spec = spec.get('quantity')
        if isinstance(spec, list):
            spec = spec[0]
        if self._filtered is None:
            return float('nan')
        if spec == 'count':
            return int(len(self._filtered))
        if spec == 'total_weight':
            return float(self._filtered['ParticleWeight'].sum())
        raise ValueError(
            f"Unknown {self.type} quantity '{spec}'. Known: 'count' (filtered "
            "particles) and 'total_weight' (summed ParticleWeight).")


# --------------------------------------------------------------------------- #
# Geant4 dose/edep
# --------------------------------------------------------------------------- #


class Geant4Module(Module):
    """Requires a ``particle_source``, provides ``dose_grid`` / ``edep_grid``.

    Owns ``_geometry_files``, ``_output_files`` and ``_read_scoring_output``.

    **Two generations of the dose application** are in use and this one module
    drives both, because the difference is entirely in the input file's keys and
    the output files that follow from them:

    * ``/sdf/group/rfar/geant4/example/dose-npass`` — the original: STL
      geometry (``solid_stl`` / ``cavity_stl``), a voxel scoring mesh, and the
      ``dose``/``edep`` grids. Nothing below changes it.
    * Lixin Ge's LCLS-II **polycone** application — adds a ``seed``, an
      ``R(Z)``-profile cavity (its ``cavity_stl`` is a profile table, not an
      STL; ``solid_stl`` is ignored), concentric cryostat layers, and
      ``detectors = on``: 8 GM-tube detectors whose per-detector energy deposit
      and gamma spectrum land in two CSVs beside the grids.

    So beyond the two scoring grids this module exposes ``detector_edep_MeV``
    and ``detector_gammas``, indexed by ``detector``, and carries the gamma
    spectrum out through :meth:`field`. A run that scores no detectors (every
    shipped example today) is unaffected: the CSVs are only looked for when the
    input file turns detectors on, and :meth:`field_index` then reports ``None``
    so such a table stays one wide row."""

    type = 'geant4'
    requires = frozenset({PARTICLE_SOURCE})
    provides = frozenset({DOSE_GRID, EDEP_GRID})

    # A detector-scored Geant4 run is tabulated over its detectors, not over the
    # Track3P field levels upstream (``Track3PModule.index_precedence`` is 1):
    # the dose at a detector is the end product of the whole chain, while the
    # field level that produced the dump is one scalar per run. See
    # :meth:`Workflow.field_index`.
    index_precedence = 2

    def __init__(self, config=None, name=None):
        super().__init__(config, name)
        self.geant4_input = self.config.get('geant4_input')
        self.geant4_threads = self.config.get('geant4_threads')
        self.geant4_opts = self.config.get('geant4_opts', '')
        self.geant4_particle_cmd = self.config.get('geant4_particle_cmd', 'particles')
        self.geant4_geometry_files = self.config.get('geant4_geometry_files') or []
        # Output files are normally named in the Geant4 input file
        # (output_dose / output_edep, or derived from output_prefix); these allow
        # an explicit YAML override. 'geant4_scoring_output' stays a back-compat
        # alias for the dose file.
        self.geant4_dose_output = (self.config.get('geant4_dose_output')
                                   or self.config.get('geant4_scoring_output'))
        self.geant4_edep_output = self.config.get('geant4_edep_output')
        self.geant4_detector_output = self.config.get('geant4_detector_output')
        self.geant4_spectrum_output = self.config.get('geant4_spectrum_output')
        self.geant4_seed = self._parse_seed(self.config.get('geant4_seed'))
        self.geant4_obj = None
        # Detector CSVs are read on demand and cached per path: 'extract' is
        # called once per declared output and 'field_index' once per row, and
        # they would otherwise re-read the same file several times per
        # evaluation.
        self._detector_cache = {}

    @staticmethod
    def _parse_seed(value):
        """Validate ``geant4_seed:``: ``None`` (leave the input alone),
        ``'auto'``, or an integer."""
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() == 'auto':
            return 'auto'
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"geant4: geant4_seed must be 'auto' (derive a distinct, "
                f"reproducible seed per evaluation) or an integer, not "
                f"{value!r}. To sweep the seed as an input instead, declare it "
                f"under input_parameters: {{geant4: {{seed: ...}}}}.") from None

    def _seed_value(self, ctx):
        """The seed to write, or ``None`` to leave the input file's own alone.

        ``auto`` derives it from this evaluation's **config hash**, which
        ``Workflow.evaluate`` has already computed over the module chain and the
        *materialized* input point — so it differs between sweep points and
        reproduces across re-runs of the same point. Folded to a positive 31-bit
        int, which is what ``/random/setSeeds`` takes.

        Not the evaluation's position in the sweep: no point index reaches
        ``Workflow`` by design (see :meth:`Workflow.point_workdir`) — sweep
        ordering belongs to the mode layer — and an index is not stable across a
        re-ordered or partially resumed sweep, while a hash of the configuration
        is. Two *identical* points (a Nelder-Mead simplex does propose one
        twice) therefore share a seed, which is the right answer for an
        identical configuration.

        Falls back to the module name when a hand-built context carries no hash,
        so this is still deterministic off the ``Workflow`` path."""
        if self.geant4_seed is None:
            return None
        if self.geant4_seed != 'auto':
            return self.geant4_seed
        material = getattr(ctx, 'config_hash', None) or self.name
        digest = hashlib.sha256(str(material).encode()).digest()
        # Positive and inside int32: the application reads it with std::atol and
        # hands it to /random/setSeeds, and seed = 0 means 'use the clock'.
        return int.from_bytes(digest[:4], 'big') % (2 ** 31 - 1) + 1

    def run(self, ctx, skip_execution=False):
        if PARTICLE_SOURCE not in ctx.artifacts:
            raise ValueError("module 'geant4' requires a particle_source "
                             "artifact.")
        ctx.ensure_workdir()
        # A resume path may have read the detector CSV through verify() before
        # the binary re-ran, so drop what was cached then.
        self._detector_cache.clear()
        particle_file_path = ctx.artifacts[PARTICLE_SOURCE]
        macro_inputs = dict(ctx.inputs.macro) if ctx.inputs.macro else None

        # Build the Geant4 object first so we can read the input file's own
        # settings (STL geometry names, output filenames) before copying files.
        self.geant4_obj = None
        if self.geant4_input is not None:
            self.geant4_obj = Geant4(self.geant4_input,
                                     geant4_threads=self.geant4_threads or 1,
                                     geant4_opts=self.geant4_opts,
                                     workdir=ctx.workdir,
                                     mpi_caller=ctx.paths.get('mpi', ''),
                                     geant4_app_path=ctx.paths.get('geant4_app_path', ''),
                                     geant4_app_exe=ctx.paths.get('geant4_app_exe', ''),
                                     log_file=self.log_file(ctx))
            # Threads default is owned by the input file; only override when set.
            if self.geant4_threads is not None:
                self.geant4_obj.set_value({'nthreads': self.geant4_threads})
            else:
                # 'geant4_threads' drives the srun '-c' (CPUs reserved for the
                # step); when it is unset srun reserves only 1 CPU, but Geant4
                # still spawns the input file's 'nthreads' threads. If those
                # threads exceed the reserved CPU they contend for one core and
                # the run is slow. Warn when the two disagree.
                file_nthreads = self.geant4_obj.get_value('nthreads')
                try:
                    file_nthreads = int(file_nthreads)
                except (TypeError, ValueError):
                    file_nthreads = None
                # nthreads = 0 means Geant4 auto-detects all available cores,
                # which also disagrees with the single reserved CPU.
                if file_nthreads is not None and file_nthreads != 1:
                    detail = ('auto-detects all available cores'
                              if file_nthreads == 0
                              else f'spawns {file_nthreads} threads')
                    print("Warning: 'geant4_threads' is not set, so srun "
                          "reserves only 1 CPU for the Geant4 step, but the "
                          f"input file's 'nthreads = {file_nthreads}' "
                          f"{detail}. These threads will contend for a single "
                          "CPU. Set 'geant4_threads' in the geant4 module to "
                          "match 'nthreads'.")
            if particle_file_path is not None:
                self.geant4_obj.set_particle_file(
                    particle_file_path,
                    macro_value=os.path.basename(particle_file_path),
                    particle_cmd=self.geant4_particle_cmd)
            seed = self._seed_value(ctx)
            if seed is not None:
                self.geant4_obj.set_value({'seed': seed})
            # Last, so a swept 'geant4: {seed: ...}' input overrides the module
            # key rather than the other way round: an input-space variable is
            # the more specific statement of the two, and it is the per-point
            # seed mechanism the YAML reference points at.
            if macro_inputs:
                self.geant4_obj.set_value(macro_inputs)

        # Geometry files: union of any '*_stl' values named in the input file
        # and the explicit geant4_geometry_files list, de-duplicated by basename.
        geom_files = self._geometry_files()
        for geom in geom_files:
            _stage_file(ctx, geom)

        if ctx.dry_run:
            # The seed line is written only when a seed is set: a dry-run marker
            # is a frozen baseline for one example, and markers are compared by
            # the numeric tokens they contain, so an unconditional extra number
            # would move it for nothing.
            seed_note = ('' if self._seed_value(ctx) is None
                         else f'Seed: {self._seed_value(ctx)}\n')
            _append_marker(ctx, 'Dry run mode: Geant4 step skipped.\n'
                                f'Input file: {self.geant4_input}\n'
                                f'Particle file: {particle_file_path}\n'
                                f'Geometry files: {geom_files}\n'
                                f'Output files: {self._output_files()}\n'
                                f'Threads: {self.geant4_threads}\n'
                                + seed_note +
                                f'Particles: {ctx.inputs.particles}\n'
                                f'Input overrides: {macro_inputs}\n')
            if self.geant4_obj is not None:
                self.geant4_obj.write_input()
            self._record_grid_artifacts(ctx)
            return

        if skip_execution:
            # Resumed: the scoring files this step wrote are already in the
            # workdir. Building the wrapper above *is* this module's parse — it is
            # what reads the input file's ``output_dose`` / ``output_edep`` names —
            # and ``extract`` / ``field`` read the grids straight off disk from
            # there, so recording the artifacts completes the step. The input file
            # is deliberately not rewritten: nothing is going to read it.
            self._record_grid_artifacts(ctx)
            return

        self.geant4_obj.run()
        self._record_grid_artifacts(ctx)

    def verify(self, ctx):
        """Whether the scoring files this step writes are still in the workdir.

        ``None`` under dry-run (the binary was skipped), and ``None`` when the
        filenames are not known: they come from the Geant4 input file
        (``output_dose`` / ``output_edep``, or derived from ``output_prefix``),
        which is read when the module builds its
        :class:`~lume_ace3p.geant4.Geant4` object — so before this module has
        run, only an explicit ``geant4_dose_output`` / ``geant4_edep_output``
        override can name them.

        The two detector CSVs are checked **only when the run scores detectors**
        (:meth:`_detectors_on`), which is what keeps this from reporting every
        grid-only run incomplete."""
        if ctx.dry_run:
            return None
        files = [name for name in self._output_files().values() if name]
        if not files:
            return None
        return all(os.path.isfile(os.path.join(ctx.workdir or '', name))
                   for name in files)

    def _record_grid_artifacts(self, ctx):
        files = self._output_files()
        if files['dose']:
            ctx.artifacts[DOSE_GRID] = os.path.join(ctx.workdir, files['dose'])
        if files['edep']:
            ctx.artifacts[EDEP_GRID] = os.path.join(ctx.workdir, files['edep'])

    def _geometry_files(self):
        """Union of STL files named in the Geant4 input file ('*_stl' keys)
        and the explicit geant4_geometry_files list. Input-file names are
        resolved relative to the directory of geant4_input. De-duplicated by
        basename."""
        files = []
        seen = set()

        def add(path):
            base = os.path.basename(path)
            if base and base not in seen:
                seen.add(base)
                files.append(path)

        if self.geant4_obj is not None:
            input_dir = os.path.dirname(self.geant4_input)
            for key, value in self.geant4_obj.get_values().items():
                if key.endswith('_stl') and value:
                    candidate = os.path.join(input_dir, value) if input_dir else value
                    if os.path.isfile(candidate):
                        add(candidate)
                    elif os.path.isfile(value):
                        add(value)
        for geom in self.geant4_geometry_files:
            add(geom)
        return files

    def _output_files(self):
        """Resolve every output filename this module may read, as
        ``{dose, edep, detector, spectrum}`` with ``None`` for one it cannot
        name.

        The precedence, per output, mirrors the application's own
        (``sim.cc:165-177`` for the grids, ``run.cc:143-160`` for the
        detectors):

        1. the explicit YAML override (``geant4_dose_output`` &c.), then
        2. the input file's own ``output_dose`` / ``output_edep`` key, then
        3. a name derived from the input file's ``output_prefix``, then
        4. the application's bare default.

        Steps 3 and 4 are what the polycone application needs: it is driven by
        ``output_prefix`` and writes no ``output_dose`` key at all, so before
        this a prefix-only input resolved to no filenames whatsoever and both
        ``verify`` and ``extract`` came up empty.

        The two detector entries are ``None`` unless the input turns detectors
        **on** — they are the one pair of outputs a run may legitimately not
        write, and naming them unconditionally would make :meth:`verify` demand
        files from every grid-only run.

        Steps 3 and 4 need the input file to have been *read*, so they apply
        only once this module has built its wrapper. Before then an explicit
        override is still honoured and everything else stays ``None``, which is
        the property :meth:`verify` documents: asked about a workdir it has not
        opened an input file for, it cannot name the outputs and says so rather
        than reporting the application's defaults missing."""
        read_input = self.geant4_obj is not None
        values = self.geant4_obj.get_values() if read_input else {}
        prefix = (values.get('output_prefix') or values.get('output') or '').strip()

        def named(override, key, suffix, bare):
            if override:
                return override
            if not read_input:
                return None
            return values.get(key) or (prefix + suffix if prefix else bare)

        files = {
            'dose': named(self.geant4_dose_output, 'output_dose',
                          '_doseDeposit.txt', 'doseDeposit.txt'),
            'edep': named(self.geant4_edep_output, 'output_edep',
                          '_energyDeposit.txt', 'energyDeposit.txt'),
            'detector': None,
            'spectrum': None,
        }
        if self._detectors_on(values):
            files['detector'] = (self.geant4_detector_output
                                 or (prefix + '_detector_dose.csv' if prefix
                                     else 'detector_dose.csv'))
            files['spectrum'] = (self.geant4_spectrum_output
                                 or (prefix + '_detector_gamma_spectrum.csv'
                                     if prefix
                                     else 'detector_gamma_spectrum.csv'))
        return files

    def _detectors_on(self, values):
        """Whether this run scores the GM-tube detectors.

        The input file's ``detectors`` key, read with the application's own
        truthiness (``sim.cc:128-132``) so the two cannot disagree about what
        ``detectors = yes`` means. An explicit ``geant4_detector_output`` /
        ``geant4_spectrum_output`` override also counts as "on": naming the file
        is as clear a statement as setting the key, and it is the way to read a
        detector CSV written by a run this module did not launch."""
        if self.geant4_detector_output or self.geant4_spectrum_output:
            return True
        return str(values.get('detectors') or '').strip().lower() in (
            'on', 'true', '1', 'yes')

    def _read_scoring_output(self, ctx, filename):
        """Parse a whitespace ix iy iz value scoring file into
        ``{'indices': (M,3) array, 'values': (M,) array}`` (workdir from ctx).

        Delegates to :func:`lume_ace3p.surrogate_data.read_dose_file`, the single
        canonical dose parser — the surrogate/inversion path reads target dose
        files through the same code, so a target lines up bin-for-bin with the
        stored training grids."""
        if not filename:
            return None
        from lume_ace3p.surrogate_data import read_dose_file
        return read_dose_file(os.path.join(ctx.workdir, filename))

    def _detector_table(self, ctx):
        """``read_detector_dose`` of this run's detector CSV, cached per path;
        ``None`` when the run scored no detectors or has not run."""
        name = self._output_files()['detector']
        if not name:
            return None
        path = os.path.join(ctx.workdir or '', name)
        if path not in self._detector_cache:
            self._detector_cache[path] = read_detector_dose(path)
        return self._detector_cache[path]

    # The scoring grids this module can be asked for. ``scoring`` is a
    # back-compat alias for ``dose``; the router in workflow_graph keys on this
    # set, so a new grid needs adding in one place only.
    SECTIONS = frozenset({'dose', 'edep', 'scoring'})

    # Per-detector quantities, keyed by the column of the detector CSV each one
    # reads. Unlike the grids these take no ``section:`` — there is one detector
    # table, and the quantity names it — so they route on the quantity alone
    # (``workflow_graph._infer_output_module``).
    DETECTOR_QUANTITIES = {
        'detector_edep_MeV': 'edep_MeV',
        'detector_gammas': 'gamma_entries',
    }

    @staticmethod
    def _parse_spec(spec):
        """Return ``(section, entry)`` for either spec form.

        The **mapping form** is the target, matching every other module::

            'total_dose' : {module: geant4, section: dose, quantity: total}

        The **positional form** ``['dose', 'total']`` is its alias. Unlike
        acdtool's list form it is *not* deprecated: a Geant4 spec is a
        ``(grid, reduction)`` pair with no index axis, so the list expresses
        everything the mapping does and nothing is lost by keeping it. The
        shipped examples use the mapping for consistency with the rest of
        ``output_parameters``.

        Returns ``(None, None)`` for a spec of neither shape, which
        :meth:`extract` reports as ``NaN`` the way it always has."""
        if isinstance(spec, dict):
            entry = spec.get('quantity')
            if entry is None:
                entry = spec.get('entry')          # positional-alias spelling
            return spec.get('section'), entry
        if isinstance(spec, (list, tuple)) and len(spec) >= 2:
            return spec[0], spec[1]
        return None, None

    def extract(self, ctx, spec):
        """Extract a scalar from the Geant4 scoring output.

        **The scoring grids.** ``spec`` is ``{section: dose, quantity: total}``
        or its positional alias ``['dose', 'total']``, with section in
        {dose, edep, scoring} (``scoring`` is a back-compat alias for dose) and
        entry in {total, peak, peak_index}.

        **The GM-tube detectors** (polycone application, ``detectors = on``) are
        named by quantity alone, since there is one detector table::

            'det_edep'  : {module: geant4, quantity: detector_edep_MeV}
            'det_5'     : {module: geant4, quantity: detector_gammas, at: {detector: 5}}

        Without an ``at:`` the whole 8-vector comes back, aligned to
        :meth:`field_index`, so a sweep table goes one row per detector; with
        one it is that detector's scalar. A detector the run did not write
        raises naming those it did. NaN when the run scored no detectors or
        wrote no CSV (dry-run), the sentinel every module uses."""
        quantity, at = self._parse_detector_spec(spec)
        if quantity is not None:
            return self._detector_value(ctx, quantity, at)

        files = self._output_files()
        grids = {
            'dose': self._read_scoring_output(ctx, files['dose']),
            'edep': self._read_scoring_output(ctx, files['edep']),
        }
        grids['scoring'] = grids['dose']
        section, entry = self._parse_spec(spec)
        if section is None:
            if isinstance(spec, dict):
                raise ValueError(
                    "a geant4 output spec needs a 'section' naming the scoring "
                    "grid and a 'quantity' naming the reduction, e.g. "
                    "{module: geant4, section: dose, quantity: total}. Sections: "
                    + str(sorted(self.SECTIONS)) + '; per-detector quantities: '
                    + str(sorted(self.DETECTOR_QUANTITIES)) + '.')
            return float('nan')
        if section not in grids:
            raise ValueError("Unknown section name '" + str(section) + "' in output dict.")
        scoring = grids[section]
        if scoring is None:
            return float('nan')
        if entry == 'total':
            return float(np.sum(scoring['values']))
        if entry == 'peak':
            return float(np.max(scoring['values']))
        if entry == 'peak_index':
            idx = int(np.argmax(scoring['values']))
            return tuple(scoring['indices'][idx])
        raise ValueError("Unknown entry '" + str(entry) + "' in '"
                         + str(section) + "' section.")

    @classmethod
    def _parse_detector_spec(cls, spec):
        """``(quantity, at)`` when ``spec`` asks for a per-detector quantity,
        else ``(None, {})`` so :meth:`extract` carries on to the grids.

        Accepts the mapping form with or without ``module:``, the bare string,
        and the single-element list — the detector quantities name one table, so
        unlike the grids they need no ``section:``."""
        if isinstance(spec, dict):
            quantity = spec.get('quantity') or spec.get('entry')
            at = dict(spec.get('at') or {})
        elif isinstance(spec, str):
            quantity, at = spec, {}
        elif isinstance(spec, (list, tuple)) and len(spec) == 1:
            quantity, at = spec[0], {}
        else:
            return None, {}
        if quantity not in cls.DETECTOR_QUANTITIES:
            return None, {}
        stray = sorted(set(at) - {'detector'})
        if stray:
            raise ValueError(
                f"a geant4 detector 'at:' narrows on ['detector'], not "
                f"{stray}.")
        return quantity, at

    def _detector_value(self, ctx, quantity, at):
        """One per-detector quantity: the 8-vector, or a scalar under
        ``at: {detector: n}``."""
        table = self._detector_table(ctx)
        column = self.DETECTOR_QUANTITIES[quantity]
        if table is None:
            # No detector CSV: dry-run, detectors off, or a run that has not
            # happened. A declared output must still produce a cell.
            return (float('nan') if 'detector' in at
                    else np.array([float('nan')]))
        values = table[column]
        if 'detector' not in at:
            return values
        ids = table['detector_id']
        wanted = float(at['detector'])
        hits = np.flatnonzero(np.isclose(ids, wanted, rtol=0.0, atol=1e-9))
        if not len(hits):
            raise ValueError(
                'at: {detector: ' + repr(at['detector']) + '} is not a detector '
                'of this Geant4 run, which wrote '
                + str([int(i) for i in ids]) + '. The detector count and their '
                'Z positions are compiled into the application (8, one per '
                'cavity of the LCLS-II cryomodule), so this is not something '
                'the input file can change.')
        return float(values[int(hits[0])])

    def field_index(self, ctx):
        """``('detector', ids)`` when this run scored the GM-tube detectors,
        else ``None``.

        ``None``, not the single-row sentinel S3P and T3P return, for the reason
        :meth:`Omega3PModule.field_index` records: whether there is a detector
        axis at all is a *result* (did the run write the CSV?), not something the
        input declares, and emitting a sentinel axis would reshape the existing
        wide dose tables of every grid-only example.

        The mode layer drops this axis again when no declared output rides on it
        (:func:`lume_ace3p.modes._table_index`), so asking for nothing but
        scalars still gives one wide row."""
        table = self._detector_table(ctx)
        if table is None:
            return None
        return 'detector', table['detector_id'].astype(int)

    def field(self, ctx):
        """Return the Geant4 field outputs for the just-run evaluation —
        ``{'dose': {indices, values}, 'edep': {...}}`` plus ``'gamma_spectrum'``
        when the run scored detectors — or ``None`` when none is present (e.g.
        dry-run).

        The grids are the ragged 3-D arrays the hybrid model keeps out of the
        flat table; the mode layer persists them per row and reloads on demand.

        ``gamma_spectrum`` rides here rather than in :meth:`extract` because it
        is not indexed by detector: the application writes only the non-empty
        bins of each detector's 100, so the rows are ragged and
        ``detector_id`` repeats down them. It is stored as a nested dict, which
        :func:`lume_ace3p.results.save_field` serializes as JSON — the route
        S3P's ``IndexMap`` already takes.

        **Caveat, inherited rather than new:** the mode layer persists a field
        artifact only for a *wide* table
        (:func:`lume_ace3p.modes._persist_field` returns ``None`` once a table
        has an index axis, since in the long form the field values are the
        rows). So a run that asks for a whole per-detector vector — and
        therefore goes long-format — gets the detector columns but **no**
        ``gamma_spectrum`` artifact, exactly as an S3P long-format sweep gets no
        ``PortRef`` mode profiles. Declare the detector outputs with
        ``at: {detector: n}`` when the spectrum is wanted too; the CSV is in the
        workdir either way."""
        files = self._output_files()
        grids = {}
        for section in ('dose', 'edep'):
            grid = self._read_scoring_output(ctx, files[section])
            if grid is not None:
                # 'indices' is already a 2-D (M,3) array, so the field artifact
                # round-trips without pickling.
                grids[section] = {'indices': grid['indices'],
                                  'values': grid['values']}
        if files['spectrum']:
            spectrum = read_gamma_spectrum(
                os.path.join(ctx.workdir or '', files['spectrum']))
            if spectrum is not None:
                grids['gamma_spectrum'] = {name: values.tolist()
                                           for name, values in spectrum.items()}
        return grids or None


# --------------------------------------------------------------------------- #
# Registry — type string -> Module class.
# --------------------------------------------------------------------------- #

MODULE_REGISTRY = {
    CubitModule.type: CubitModule,
    MeshSourceModule.type: MeshSourceModule,
    Omega3PModule.type: Omega3PModule,
    S3PModule.type: S3PModule,
    T3PModule.type: T3PModule,
    Track3PModule.type: Track3PModule,
    AcdtoolModule.type: AcdtoolModule,
    Track3PSourceModule.type: Track3PSourceModule,
    FieldEmissionModule.type: FieldEmissionModule,
    ParticleSourceModule.type: ParticleSourceModule,
    Geant4Module.type: Geant4Module,
}


def build_module(module_type, config=None, name=None):
    """Construct a module instance from its registry type string."""
    key = str(module_type).lower()
    if key not in MODULE_REGISTRY:
        raise ValueError(f"Unknown module type '{module_type}'. Known types: "
                         f"{sorted(MODULE_REGISTRY)}.")
    return MODULE_REGISTRY[key](config=config, name=name)
