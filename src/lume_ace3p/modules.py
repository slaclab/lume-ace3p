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
/ ``provides {td_solution}``) needed no rule change, and a runnable Track3P
solver will likewise slot in as ``requires {em_solution}`` / ``provides
{track3p_particles}``.

The old skip-flags (``skip_cubit``/``skip_solver``/``skip_acdtool``) and the
``geant4_particle_file`` bypass have no place in this layer: in a declarative
module list, "skip X" is simply "do not list module X", and a prebuilt artifact
is expressed as a *source module*
(:class:`MeshSourceModule` / :class:`Track3PSourceModule` /
:class:`ParticleSourceModule`). The one exception is meshconvert, which is a
sub-step *inside* :class:`CubitModule` and stays a per-module ``meshconvert``
bool.
"""

import os
import shutil
import warnings

import numpy as np

from lume_ace3p.cubit import Cubit
from lume_ace3p.ace3p import Omega3P, S3P, T3P
from lume_ace3p.acdtool import (
    Acdtool, COMMANDS, CURVE, GRID, MODE_TABLE, RFPOST, SECTIONS, SURFACE,
    field_sections, mode_table_arrays, resolve_command, table_mode_ids,
    wired_commands,
)
from lume_ace3p.geant4 import Geant4
from lume_ace3p.particles import Particles
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
TRACK3P_PARTICLES = 'track3p_particles'  # raw Track3P dump (produced EXTERNALLY today)
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
    """

    def __init__(self, workdir, inputs=None, artifacts=None, outputs=None,
                 dry_run=False, paths=None, stage_mode='copy'):
        self.workdir = workdir
        self.inputs = inputs if inputs is not None else WorkflowInputs()
        self.artifacts = dict(artifacts) if artifacts else {}
        self.outputs = dict(outputs) if outputs else {}
        self.dry_run = dry_run
        self.paths = dict(paths) if paths else {}
        self.stage_mode = stage_mode
        self.job_names = {}
        self.reparse = {}

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

    def run(self, ctx):
        raise NotImplementedError

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


class MeshSourceModule(Module):
    """Provide a ``mesh`` from a prebuilt mesh file — the declarative
    replacement for ``skip_cubit`` + a supplied mesh."""

    type = 'mesh'
    provides = frozenset({MESH})

    def run(self, ctx):
        src = self.config.get('file')
        if src is None:
            raise ValueError("mesh source module requires a 'file'.")
        ctx.artifacts[MESH] = _stage_file(ctx, src)


class Track3PSourceModule(Module):
    """Provide ``track3p_particles`` from an externally-produced Track3P dump.

    This is a *source* module — there is no in-pipeline Track3P solver in this
    refactor. When a runnable Track3P/T3P solver is built later it will
    ``require {em_solution}`` and ``provide {track3p_particles}``; nothing here
    needs to change for that to slot in."""

    type = 'track3p_source'
    provides = frozenset({TRACK3P_PARTICLES})

    def run(self, ctx):
        src = self.config.get('file')
        if src is None:
            raise ValueError("track3p source module requires a 'file'.")
        ctx.artifacts[TRACK3P_PARTICLES] = _stage_file(ctx, src)


class ParticleSourceModule(Module):
    """Provide a ``particle_source`` directly from a Geant4-format particle
    file — the declarative way to supply a prebuilt Geant4 source file directly,
    bypassing the Particles weighting step."""

    type = 'particle_source'
    provides = frozenset({PARTICLE_SOURCE})

    def run(self, ctx):
        src = self.config.get('file')
        if src is None:
            raise ValueError("particle source module requires a 'file'.")
        ctx.artifacts[PARTICLE_SOURCE] = _stage_file(ctx, src)


# --------------------------------------------------------------------------- #
# Cubit
# --------------------------------------------------------------------------- #


class CubitModule(Module):
    """Provide a ``mesh`` from a Cubit ``journal``. Runs meshconvert unless
    ``meshconvert: false``."""

    type = 'cubit'
    provides = frozenset({MESH})

    def __init__(self, config=None, name=None):
        super().__init__(config, name)
        self.journal = self.config.get('journal') or self.config.get('cubit_input')
        self.meshconvert = self.config.get('meshconvert', True)

    def run(self, ctx):
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
        ctx.ensure_workdir()
        cubit = Cubit(self.journal, workdir=ctx.workdir,
                      ace3p_path=ctx.paths.get('ace3p', ''),
                      cubit_path=ctx.paths.get('cubit', ''),
                      mpi_caller=ctx.paths.get('mpi', ''))
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
        self._solver = None

    def run(self, ctx):
        if MESH not in ctx.artifacts:
            raise ValueError(f"module '{self.type}' requires a mesh artifact.")
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
            return
        ctx.ensure_workdir()
        solver = self._wrapper(self.input_file,
                               ace3p_tasks=self.tasks,
                               ace3p_cores=self.cores,
                               ace3p_opts=self.opts,
                               results_dir=self.results_dir,
                               workdir=ctx.workdir,
                               ace3p_path=ctx.paths.get('ace3p', ''),
                               mpi_caller=ctx.paths.get('mpi', ''))
        solver.set_value(ctx.inputs.ace3p)
        solver.run()
        self._solver = solver
        ctx.artifacts[self._artifact] = ctx.workdir
        ctx.job_names[self._artifact] = solver.job_name()
        # Let a consumer that rewrites this solver's output in place ask for a
        # re-read (the acdtool wake commands overwrite wakefield.out).
        ctx.reparse[self._artifact] = solver.output_parser


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
                "if the run used a different job name.")
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

    def extract(self, ctx, spec):
        """Return an S-parameter quantity from the S3P solution.

        ``spec`` may be:
          * a string ``'S(0,0)'`` — the full frequency-indexed array,
          * a single-element list ``['S(0,0)']`` — same, first element used,
          * a mapping ``{'quantity': 'S(0,0)', 'at': {'frequency': f}}`` — the
            scalar value at frequency ``f`` (the objective form the Xopt driver
            needs).
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
        if frequency is None:
            return values
        freqs = list(data['Frequency'])
        try:
            idx = freqs.index(float(frequency))
        except ValueError:
            print('Inputted frequency to be optimized is not in frequency sweep.')
            return float('nan')
        return values[idx]

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
        """Return the full S3P spectrum (``{IndexMap, Frequency, S(m,n)...}``)
        for the just-run evaluation, or ``None`` under dry-run.

        This is the structured field artifact for a single point. In a sweep,
        S3P goes long-format (its :meth:`field_index` puts one row per
        frequency), so this is used only when a caller wants to persist the raw
        spectrum for a row rather than explode it."""
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
    """

    type = 't3p'
    provides = frozenset({TD_SOLUTION})
    _wrapper = T3P
    _label = 'T3P'
    _artifact = TD_SOLUTION

    # Bare quantity names this module answers to, used both by ``extract`` and
    # by the output-spec router in workflow_graph.
    QUANTITIES = frozenset({'loss_factor', 'kick_factor', 'W', 'I_bunch', 's'})

    # Extractable scalars -> the key ``parse_wakefield`` stores them under.
    _SCALARS = {'loss_factor': 'LossFactor', 'kick_factor': 'KickFactor'}

    def extract(self, ctx, spec):
        """Return a wakefield quantity from the T3P solution.

        ``spec`` may be:
          * ``'loss_factor'`` / ``'kick_factor'`` — the scalar figure of merit,
          * ``'W'`` / ``'I_bunch'`` / ``'s'`` — the full ``s``-indexed array,
          * a single-element list wrapping either of the above,
          * a mapping ``{'quantity': 'W', 'at': {'s': 0.05}}`` — the value at the
            wake position nearest ``s`` (the objective form an Xopt run needs).
        """
        solver = self._solver
        if solver is None:
            # Dry-run / no solver: same NaN sentinel S3PModule returns.
            return np.array([float('nan')])
        quantity, position = self._parse_spec(spec)
        data = solver.output_data
        if not data:
            raise ValueError(
                f"no T3P wakefield results to extract '{quantity}' from. T3P "
                "writes them only when the input file declares a WakeField "
                "monitor, e.g.\n"
                "  Monitor: { Type: WakeField  Name: wakefield ... }\n"
                f"Expected file: {os.path.join(solver.results_dir(), 'wakefield.out')} "
                f"under {ctx.workdir}.")

        if quantity in self._SCALARS:
            key = self._SCALARS[quantity]
            if key not in data:
                # Longitudinal runs report a loss factor, transverse ones a kick
                # factor. Name the one that IS present rather than return NaN.
                present = [name for name, k in self._SCALARS.items() if k in data]
                raise ValueError(
                    f"this is a {data.get('WakeType', 'unknown')} T3P run, which "
                    f"reports {present} — not '{quantity}'. The wake type follows "
                    "from the WakeField monitor's contour and the beam offset in "
                    "the input file.")
            return data[key]

        if quantity not in data:
            raise ValueError("Unknown quantity '" + str(quantity)
                             + "' in T3P output dict. Known quantities: "
                             + str(sorted(self.QUANTITIES)) + ".")
        values = data[quantity]
        if position is None:
            return values
        # Unlike S3P's frequency scan, the s grid is a solver-chosen consequence
        # of the timestep, so an exact match is not something a user can specify.
        # Take the nearest sample instead.
        grid = np.asarray(data['s'])
        if not grid.size:
            return float('nan')
        return values[int(np.argmin(np.abs(grid - float(position))))]

    @staticmethod
    def _parse_spec(spec):
        if isinstance(spec, dict):
            at = spec.get('at') or {}
            return spec.get('quantity'), at.get('s')
        if isinstance(spec, list):
            return spec[0], None
        return spec, None

    def field_index(self, ctx):
        """T3P field outputs are indexed by the wake coordinate ``s``. Returns
        ``('s', array)``; under dry-run (no solver) a single-row ``[0.0]``
        sentinel, so a swept long-format table still has one row per grid
        point — mirroring :meth:`S3PModule.field_index`."""
        solver = self._solver
        if solver is None:
            return 's', np.array([0.0])
        data = solver.output_data
        if not data:
            return 's', np.array([0.0])
        return 's', np.asarray(data['s'])

    def field(self, ctx):
        """Return the full wakefield result (``{s, W, I_bunch, LossFactor|
        KickFactor, WakeType, ...}``) for the just-run evaluation, or ``None``
        under dry-run / when the run declared no WakeField monitor."""
        solver = self._solver
        if solver is None or not solver.output_data:
            return None
        return dict(solver.output_data)


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
    acdtool ran. See the Phase-2 decision in ``docs/acdtool_rework_plan.md``.
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

    def run(self, ctx):
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
                          mpi_caller=ctx.paths.get('mpi', ''))
        acdtool.run()
        self._acdtool = acdtool
        ctx.artifacts[RF_POST] = ctx.workdir
        # This command rewrote its producer's output in place; have the producer
        # re-read it so downstream extraction sees the new result, not the one
        # parsed before acdtool ran.
        if self.spec.mutates and self.spec.mutates in ctx.reparse:
            ctx.reparse[self.spec.mutates]()

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
        ``docs/acdtool_rework_plan.md``). Surface-indexed sections resolve to
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


class ParticlesModule(Module):
    """Requires ``track3p_particles``, provides ``particle_source``.

    Owns the ``beta`` / ``beta_input`` / ``beta_inputs`` resolution. Always runs
    (the field-emission weighting is pure Python and produces real numbers), even
    under dry-run — the Geant4 binary is the only thing a dry run skips, so the
    particle source it consumes is always produced."""

    type = 'particles'
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

    def run(self, ctx):
        if TRACK3P_PARTICLES not in ctx.artifacts:
            raise ValueError("module 'particles' requires a track3p_particles "
                             "artifact.")
        src = ctx.artifacts[TRACK3P_PARTICLES]
        base = os.path.basename(src)
        _stage_file(ctx, src)
        params = dict(self._resolve_beta(ctx.inputs))
        params.setdefault('output_format', 'geant4')
        particles = Particles(base, params, output_file=self.output_file,
                              workdir=ctx.workdir)
        self._filtered = particles.run()
        ctx.artifacts[PARTICLE_SOURCE] = os.path.join(ctx.workdir,
                                                      particles.output_file)

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
        raise ValueError("Unknown particles quantity '" + str(spec) + "'.")


# --------------------------------------------------------------------------- #
# Geant4 dose/edep
# --------------------------------------------------------------------------- #


class Geant4Module(Module):
    """Requires a ``particle_source``, provides ``dose_grid`` / ``edep_grid``.

    Owns ``_geometry_files``, ``_output_files`` and ``_read_scoring_output``."""

    type = 'geant4'
    requires = frozenset({PARTICLE_SOURCE})
    provides = frozenset({DOSE_GRID, EDEP_GRID})

    def __init__(self, config=None, name=None):
        super().__init__(config, name)
        self.geant4_input = self.config.get('geant4_input')
        self.geant4_threads = self.config.get('geant4_threads')
        self.geant4_opts = self.config.get('geant4_opts', '')
        self.geant4_particle_cmd = self.config.get('geant4_particle_cmd', 'particles')
        self.geant4_geometry_files = self.config.get('geant4_geometry_files') or []
        # Output files are normally named in the Geant4 input file
        # (output_dose / output_edep); these allow an explicit YAML override.
        # 'geant4_scoring_output' stays a back-compat alias for the dose file.
        self.geant4_dose_output = (self.config.get('geant4_dose_output')
                                   or self.config.get('geant4_scoring_output'))
        self.geant4_edep_output = self.config.get('geant4_edep_output')
        self.geant4_obj = None

    def run(self, ctx):
        if PARTICLE_SOURCE not in ctx.artifacts:
            raise ValueError("module 'geant4' requires a particle_source "
                             "artifact.")
        ctx.ensure_workdir()
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
                                     geant4_app_exe=ctx.paths.get('geant4_app_exe', ''))
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
            if macro_inputs:
                self.geant4_obj.set_value(macro_inputs)

        # Geometry files: union of any '*_stl' values named in the input file
        # and the explicit geant4_geometry_files list, de-duplicated by basename.
        geom_files = self._geometry_files()
        for geom in geom_files:
            _stage_file(ctx, geom)

        if ctx.dry_run:
            _append_marker(ctx, 'Dry run mode: Geant4 step skipped.\n'
                                f'Input file: {self.geant4_input}\n'
                                f'Particle file: {particle_file_path}\n'
                                f'Geometry files: {geom_files}\n'
                                f'Output files: {self._output_files()}\n'
                                f'Threads: {self.geant4_threads}\n'
                                f'Particles: {ctx.inputs.particles}\n'
                                f'Input overrides: {macro_inputs}\n')
            if self.geant4_obj is not None:
                self.geant4_obj.write_input()
            self._record_grid_artifacts(ctx)
            return

        self.geant4_obj.run()
        self._record_grid_artifacts(ctx)

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
        """Resolve the dose / edep output filenames, preferring explicit YAML
        overrides and otherwise reading output_dose / output_edep from the
        Geant4 input file."""
        values = self.geant4_obj.get_values() if self.geant4_obj is not None else {}
        dose = self.geant4_dose_output or values.get('output_dose')
        edep = self.geant4_edep_output or values.get('output_edep')
        return {'dose': dose, 'edep': edep}

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

    def extract(self, ctx, spec):
        """Extract a scalar from the Geant4 dose/edep scoring output.

        ``spec`` is ``[section, entry]`` with section in {dose, edep, scoring}
        (``scoring`` is a back-compat alias for dose) and entry in
        {total, peak, peak_index}."""
        files = self._output_files()
        grids = {
            'dose': self._read_scoring_output(ctx, files['dose']),
            'edep': self._read_scoring_output(ctx, files['edep']),
        }
        grids['scoring'] = grids['dose']
        if not isinstance(spec, list) or len(spec) < 2:
            return float('nan')
        section, entry = spec[0], spec[1]
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

    def field(self, ctx):
        """Return the Geant4 voxel-grid field outputs for the just-run
        evaluation as ``{'dose': {indices, values}, 'edep': {...}}``, or
        ``None`` when neither scoring file is present (e.g. dry-run).

        These are the ragged 3-D grids the hybrid model keeps out of the flat
        table; the mode layer persists them per row and reloads on demand."""
        files = self._output_files()
        grids = {}
        for section in ('dose', 'edep'):
            grid = self._read_scoring_output(ctx, files[section])
            if grid is not None:
                # 'indices' is already a 2-D (M,3) array, so the field artifact
                # round-trips without pickling.
                grids[section] = {'indices': grid['indices'],
                                  'values': grid['values']}
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
    AcdtoolModule.type: AcdtoolModule,
    Track3PSourceModule.type: Track3PSourceModule,
    ParticlesModule.type: ParticlesModule,
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
