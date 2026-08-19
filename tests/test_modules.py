"""Module-layer tests (see docs/workflow_module_refactor_plan.md).

Two things are verified for every module:

1. **Isolation dry-run** — each module runs from a hand-built ``RunContext``,
   consuming the artifact keys it ``requires`` and producing the keys it
   ``provides``. The source modules and the pure-Python ``ParticlesModule`` run
   for real; the solver/geant4 modules run their dry-run path.
2. **extract** — for the modules that expose scalars (``S3PModule``,
   ``AcdtoolModule``, ``Geant4Module``), ``extract`` is checked to pull the
   expected values out of synthetic solver-output fixtures. ``ParticlesModule``
   is checked against a direct ``Particles`` invocation (the wrapper it adapts).

No ACE3P / Geant4 binary is needed: solver objects are constructed pointing at
synthetic output files and their ``output_parser`` is driven directly, or the
scoring files are pre-placed in the workdir.
"""

import os
import shutil
import warnings

import numpy as np
import pytest

from lume_ace3p.results import load_field, save_field
from lume_ace3p.modules import (
    RunContext, build_module, MODULE_REGISTRY,
    CubitModule, MeshSourceModule, Omega3PModule, S3PModule, T3PModule,
    AcdtoolModule,
    Track3PSourceModule, ParticlesModule, ParticleSourceModule, Geant4Module,
    JOURNAL, MESH, EM_SOLUTION, TD_SOLUTION, RF_POST, TRACK3P_PARTICLES,
    PARTICLE_SOURCE, DOSE_GRID, EDEP_GRID,
    _stage_file, STAGE_MODES,
)
from lume_ace3p.ace3p import (
    Omega3P, S3P, S3POutputWarning, T3P, T3POutputWarning, Section,
)
from lume_ace3p.workflow_graph import _infer_output_module
from lume_ace3p.acdtool import (
    Acdtool, wired_commands, EM_SOLUTION as ACD_EM_SOLUTION,
    TD_SOLUTION as ACD_TD_SOLUTION,
    TRACK3P_PARTICLES as ACD_TRACK3P_PARTICLES,
)
from lume_ace3p.geant4 import Geant4
from lume_ace3p.particles import Particles, TRACK3P_COLUMNS
from lume_ace3p.inputs import WorkflowInputs


# --------------------------------------------------------------------------- #
# Synthetic fixtures
# --------------------------------------------------------------------------- #

# 2-port S3P Reflection.out: 3 frequencies, 4 S-parameters per row (ids 0,1).
S3P_REFLECTION = """\
#Index information
#0  Port 1, Mode 0, Type: (TE) cutoff: 1.000000e9 Hz
#1  Port 2, Mode 0, Type: (TE) cutoff: 1.000000e9 Hz
#Frequency  S(0,0) S(0,1) S(1,0) S(1,1)
12.0e9 0.10 0.20 0.30 0.40
12.5e9 0.50 0.60 0.70 0.80
13.0e9 0.90 1.00 1.10 1.20
"""

# The same matrix as '( real,  imag )' pairs (SParameter.out). Every cell is a
# 3-4-5 triple scaled to its magnitude above, so abs(complex) reproduces
# S3P_REFLECTION exactly rather than approximately.
S3P_SPARAMETER = """\
#Index information
#0  Port 1, Mode 0, Type: (TE) cutoff: 1.000000e9 Hz
#1  Port 2, Mode 0, Type: (TE) cutoff: 1.000000e9 Hz
#Frequency  S(0,0) S(0,1) S(1,0) S(1,1)
12.0e9 ( 0.06,  0.08) ( 0.12,  0.16) ( 0.18,  0.24) ( 0.24,  0.32)
12.5e9 ( 0.30,  0.40) ( 0.36,  0.48) ( 0.42,  0.56) ( 0.48,  0.64)
13.0e9 ( 0.54,  0.72) ( 0.60,  0.80) ( 0.66,  0.88) ( 0.72,  0.96)
"""

# A port mode field profile (PortRef<n>_<m>.out): indexed by position, not by
# frequency, and commented with '%' rather than '#'.
S3P_PORT_PROFILE = """\
%          x          y            Ex            Ey            Hx            Hy
0.0 0.0 1.0 0.0 0.0 2.0
1.0e-3 2.0e-3 3.0 4.0 5.0 6.0
2.0e-3 4.0e-3 7.0 8.0 9.0 10.0
"""

# rfpost input with all three postprocess sections flagged on.
RFPOST_INPUT = """\
RoverQ
{
   ionoff = 1
}

kickFactor
{
   ionoff = 1
}

maxFieldsOnSurface
{
   ionoff = 1
}
"""

# rfpost.out with parseable RoverQ / kickFactor / maxFieldsOnSurface blocks.
# [RoverQ] carries TWO modes so a mapping spec with no 'at:' has an axis longer
# than one row; [kickFactor] carries one, as a run whose modeID range differs
# would (the blocks are narrowed independently).
RFPOST_OUTPUT = """\
[RoverQ]
Results for RoverQ:
ModeID Frequency Qext V_r V_i absV RoQ
0 1.300000e9 1000.0 0.5, 0.1 0.6 250.0
1 2.400000e9 900.0 0.2, 0.05 0.3 40.0
}

[kickFactor]
Results for kickFactor:
ModeID Frequency Qext Ks V_r V_i absV
0 1.300000e9 1000.0 3.3 0.5 0.1 0.6
}

[maxFieldsOnSurface]
surfaceID : 6
header line
Emax = 1.500000e6 at (0.1, 0.2, 0.3)
Hmax = 2.500000e3 at (0.4, 0.5, 0.6)
}
"""

# Geant4 key=value input naming output files so extract can resolve them.
GEANT4_INPUT = """\
# synthetic geant4 input
particles = particles.data
solid_stl = missing_solid.stl
cavity_stl = missing_cavity.stl
nthreads = 4
output_dose = dose.out
output_edep = edep.out
"""

# Scoring grids: ix iy iz value.
DOSE_OUT = "0 0 0 1.0\n0 0 1 2.0\n1 0 0 5.0\n"
EDEP_OUT = "0 0 0 0.5\n0 0 1 1.5\n1 0 0 3.0\n"


def _write(path, text):
    with open(path, 'w') as f:
        f.write(text)


def _make_track3p_dump(path, n=24, impact_order=1, impact_face_id=6):
    """Write a small synthetic Track3P dump matching TRACK3P_COLUMNS, with
    particles spread in Initial_z so the 8-bin assignment is populated."""
    rng = np.random.RandomState(0)
    z = np.linspace(-0.05, 0.05, n)
    lines = ['#' + ' '.join(TRACK3P_COLUMNS)]
    for i in range(n):
        row = {
            'InitialID': 1000 + i,
            'ImpactOrder': impact_order,
            'Initial_x': 0.01, 'Initial_y': 0.005, 'Initial_z': z[i],
            'Impact_x': 0.01, 'Impact_y': 0.005, 'Impact_z': z[i],
            'InitialPhaseinRFcycle': 0.0, 'ImpactPhaseinRFcycle': 0.0,
            'ImpactEnergy': 42.0 + rng.rand(),
            'momentum_x': -1e-24, 'momentum_y': -1e-24, 'momentum_z': 6e-25,
            'ImpactFaceID': impact_face_id,
            'InitialNormalField': 3.8e7 + 1e6 * rng.rand(),
            'InitialFaceArea': 1.8e-7,
        }
        lines.append(' '.join(str(row[c]) for c in TRACK3P_COLUMNS))
    _write(path, '\n'.join(lines) + '\n')


def _paths():
    """Empty tool paths — no binary is invoked in these tests."""
    return {'ace3p': '', 'cubit': '', 'mpi': '', 'geant4_app_path': '',
            'geant4_app_exe': ''}


def _make_s3p_solver(workdir, complete=False):
    """Construct an S3P wrapper pointed at a synthetic Reflection.out and parse
    it directly (bypassing the subprocess).

    By default only ``Reflection.out`` is written, which is the older-ACE3P-build
    path: the parser warns that S-parameter phase is unavailable and returns the
    magnitudes alone. `complete` adds the two files Phase 5 gave readers to —
    ``SParameter.out`` and one ``PortRef<n>_<m>.out``."""
    results = os.path.join(workdir, 's3p_results')
    os.makedirs(results, exist_ok=True)
    _write(os.path.join(results, 'Reflection.out'), S3P_REFLECTION)
    if complete:
        _write(os.path.join(results, 'SParameter.out'), S3P_SPARAMETER)
        _write(os.path.join(results, 'PortRef1_0.out'), S3P_PORT_PROFILE)
    dummy_input = os.path.join(workdir, 'dummy.s3p')
    _write(dummy_input, '')
    s3p = S3P(dummy_input, workdir=workdir)
    with warnings.catch_warnings():
        # The incomplete case warns by design; the tests that care about the
        # warning assert it against the real fixtures in test_acdtool_fixtures.
        warnings.simplefilter('ignore', S3POutputWarning)
        s3p.output_parser()
    return s3p


def _make_acdtool(workdir):
    """Construct an Acdtool wrapper over synthetic rfpost input/output and load
    its parsed output_data."""
    input_path = os.path.join(workdir, 'test.rfpost')
    _write(input_path, RFPOST_INPUT)
    _write(os.path.join(workdir, 'rfpost.out'), RFPOST_OUTPUT)
    acd = Acdtool(input_path, workdir=workdir)
    acd.output_file = 'rfpost.out'
    acd.load_output()
    return acd


# --------------------------------------------------------------------------- #
# Registry / edges
# --------------------------------------------------------------------------- #


def test_registry_edges_match_plan():
    expected = {
        'cubit': (set(), {MESH}),
        'mesh': (set(), {MESH}),
        'omega3p': ({MESH}, {EM_SOLUTION}),
        's3p': ({MESH}, {EM_SOLUTION}),
        # T3P provides a DISTINCT artifact kind: acdtool requires em_solution, so
        # this is what makes 'cubit -> t3p -> acdtool' a validation error rather
        # than RF postprocessing pointed at time-domain output.
        't3p': ({MESH}, {TD_SOLUTION}),
        'acdtool': ({EM_SOLUTION}, {RF_POST}),
        'track3p_source': (set(), {TRACK3P_PARTICLES}),
        'particles': ({TRACK3P_PARTICLES}, {PARTICLE_SOURCE}),
        'particle_source': (set(), {PARTICLE_SOURCE}),
        'geant4': ({PARTICLE_SOURCE}, {DOSE_GRID, EDEP_GRID}),
    }
    assert set(MODULE_REGISTRY) == set(expected)
    for type_str, (req, prov) in expected.items():
        m = build_module(type_str)
        assert set(m.requires) == req, type_str
        assert set(m.provides) == prov, type_str


def test_build_module_unknown_type():
    with pytest.raises(ValueError):
        build_module('nonexistent')


# --------------------------------------------------------------------------- #
# Source modules — provide a prebuilt artifact from a supplied file
# --------------------------------------------------------------------------- #


def test_mesh_source_module(tmp_path):
    src = tmp_path / 'prebuilt.ncdf'
    _write(str(src), 'mesh-bytes')
    wd = tmp_path / 'wd'
    ctx = RunContext(str(wd))
    MeshSourceModule({'file': str(src)}).run(ctx)
    assert MESH in ctx.artifacts
    assert os.path.isfile(ctx.artifacts[MESH])


def test_track3p_source_module(tmp_path):
    src = tmp_path / 'dump.txt'
    _make_track3p_dump(str(src))
    ctx = RunContext(str(tmp_path / 'wd'))
    Track3PSourceModule({'file': str(src)}).run(ctx)
    assert os.path.isfile(ctx.artifacts[TRACK3P_PARTICLES])


def test_particle_source_module(tmp_path):
    src = tmp_path / 'particles.data'
    _write(str(src), '0.0 0.0 0.0 0.0 1.0 1 0 0 1 6\n')
    ctx = RunContext(str(tmp_path / 'wd'))
    ParticleSourceModule({'file': str(src)}).run(ctx)
    assert os.path.isfile(ctx.artifacts[PARTICLE_SOURCE])


def test_source_module_requires_file(tmp_path):
    ctx = RunContext(str(tmp_path / 'wd'))
    with pytest.raises(ValueError):
        MeshSourceModule().run(ctx)


# --------------------------------------------------------------------------- #
# Cubit
# --------------------------------------------------------------------------- #


def test_cubit_module_dry_run(tmp_path):
    ctx = RunContext(str(tmp_path / 'wd'),
                     inputs=WorkflowInputs(cubit={'cornercut': 15.0}),
                     dry_run=True)
    CubitModule({'journal': 'bend-90degree.jou'}).run(ctx)
    assert MESH in ctx.artifacts
    marker = os.path.join(ctx.workdir, 'DRY_RUN.txt')
    assert os.path.isfile(marker)
    assert 'Cubit step skipped' in open(marker).read()


def test_cubit_module_requires_journal(tmp_path):
    ctx = RunContext(str(tmp_path / 'wd'), dry_run=True)
    with pytest.raises(ValueError):
        CubitModule().run(ctx)


# --------------------------------------------------------------------------- #
# EM solvers (dry-run + require mesh)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('module_cls,label,artifact', [
    (Omega3PModule, 'Omega3P', EM_SOLUTION),
    (S3PModule, 'S3P', EM_SOLUTION),
    (T3PModule, 'T3P', TD_SOLUTION),
])
def test_solver_module_dry_run(tmp_path, module_cls, label, artifact):
    ctx = RunContext(str(tmp_path / 'wd'),
                     inputs=WorkflowInputs(cubit={'x': 1.0}),
                     artifacts={MESH: str(tmp_path / 'wd' / 'm.genesis')},
                     dry_run=True)
    module_cls({'input': 'in.file'}).run(ctx)
    assert artifact in ctx.artifacts
    marker = open(os.path.join(ctx.workdir, 'DRY_RUN.txt')).read()
    assert f'{label} step skipped' in marker


@pytest.mark.parametrize('module_cls', [S3PModule, T3PModule])
def test_solver_module_requires_mesh(tmp_path, module_cls):
    ctx = RunContext(str(tmp_path / 'wd'), dry_run=True)
    with pytest.raises(ValueError):
        module_cls({'input': 'in.file'}).run(ctx)


def test_s3p_extract(tmp_path):
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    s3p = _make_s3p_solver(wd)

    ctx = RunContext(wd, paths=_paths())
    module = S3PModule({'input': 'dummy.s3p'})
    module._solver = s3p  # inject the parsed solver (no binary run)

    # Full frequency-indexed array (string spec / list spec) — the three
    # S(0,0) values from S3P_REFLECTION.
    expected = np.array([0.10, 0.50, 0.90])
    assert np.allclose(module.extract(ctx, 'S(0,0)'), expected)
    assert np.allclose(module.extract(ctx, ['S(0,0)']), expected)

    # Scalar at a frequency (the Xopt objective form): S(1,1) @ 12.5e9 = 0.80.
    assert module.extract(ctx, {'quantity': 'S(1,1)',
                                'at': {'frequency': 12.5e9}}) == pytest.approx(0.80)


def test_s3p_extract_dry_run_is_nan(tmp_path):
    ctx = RunContext(str(tmp_path / 'wd'))
    module = S3PModule({'input': 'x.s3p'})
    module._solver = None
    assert np.isnan(module.extract(ctx, 'S(0,0)')).all()


def _s3p_module(tmp_path, complete=True):
    """An S3PModule with a parsed solver injected (no binary run), plus its
    RunContext."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module = S3PModule({'input': 'dummy.s3p'})
    module._solver = _make_s3p_solver(wd, complete=complete)
    return module, RunContext(wd, paths=_paths())


def test_s3p_extract_complex_quantities(tmp_path):
    """PHASE 5: the complex S-parameter is extractable the same way the magnitude
    is — a frequency-indexed array, or an ``at:``-narrowed scalar. The magnitude
    keeps its own key, so both are available at once."""
    module, ctx = _s3p_module(tmp_path)

    assert np.allclose(module.extract(ctx, 'S(0,0)'), [0.10, 0.50, 0.90])
    assert np.allclose(module.extract(ctx, 'S(0,0)_real'), [0.06, 0.30, 0.54])
    assert np.allclose(module.extract(ctx, 'S(0,0)_imag'), [0.08, 0.40, 0.72])
    # 3-4-5: atan2(4, 3) = 53.130102 deg, the same at every frequency here.
    assert np.allclose(module.extract(ctx, 'S(0,0)_phase_deg'), 53.13010235)

    scalar = module.extract(ctx, {'quantity': 'S(1,1)_imag',
                                  'at': {'frequency': 12.5e9}})
    assert scalar == pytest.approx(0.64)


def test_s3p_extract_rejects_a_port_profile(tmp_path):
    """PHASE 5: a port mode profile is indexed by position, not frequency, so it
    is not a result-table column. Asking for one names the field-artifact route
    rather than returning a dict into a DataFrame — the same treatment
    AcdtoolModule gives its curve blocks."""
    module, ctx = _s3p_module(tmp_path)

    with pytest.raises(ValueError, match=r"module's field\(\)"):
        module.extract(ctx, 'PortRef1_0')
    # Same guard covers the index map, which is a dict for the same reason.
    with pytest.raises(ValueError, match=r"module's field\(\)"):
        module.extract(ctx, {'quantity': 'IndexMap'})


def test_s3p_field_carries_the_port_profiles(tmp_path):
    """PHASE 5: ``field()`` is where the port profiles surface, alongside the
    spectrum, and they survive the ``.npz`` round-trip as nested dicts the way
    ``IndexMap`` already did."""
    module, ctx = _s3p_module(tmp_path)
    field = module.field(ctx)

    assert field['PortRef1_0']['Ex'][-1] == pytest.approx(7.0)
    assert list(field['PortRef1_0']) == ['x', 'y', 'Ex', 'Ey', 'Hx', 'Hy']
    assert np.allclose(field['S(0,1)_phase_deg'], 53.13010235)

    handle = save_field(field, os.path.join(str(tmp_path), 'field_0'))
    loaded = load_field(handle)
    assert np.allclose(loaded['PortRef1_0']['Hy'], field['PortRef1_0']['Hy'])
    assert np.allclose(loaded['S(0,1)_imag'], field['S(0,1)_imag'])


def test_s3p_field_index_is_unaffected_by_the_new_keys(tmp_path):
    """The table axis is still S3P's frequency scan: the complex columns align to
    it and the port profiles are not on the table at all, so nothing here moves
    (which is what keeps the s3p baselines byte-identical)."""
    complete, ctx = _s3p_module(tmp_path)
    axis, values = complete.field_index(ctx)
    assert axis == 'Frequency'
    assert np.allclose(values, [12.0e9, 12.5e9, 13.0e9])
    for name in ('S(0,0)', 'S(0,0)_real', 'S(0,0)_phase_deg'):
        assert len(complete.extract(ctx, name)) == len(values)


def test_s3p_older_build_has_magnitudes_only(tmp_path):
    """With no ``SParameter.out`` — an older ACE3P build — the module behaves
    exactly as it did before Phase 5: magnitudes present, complex keys absent
    with an error that says which quantities the run did produce."""
    module, ctx = _s3p_module(tmp_path, complete=False)

    assert np.allclose(module.extract(ctx, 'S(0,0)'), [0.10, 0.50, 0.90])
    with pytest.raises(ValueError, match='S\\(0,0\\)_real'):
        module.extract(ctx, 'S(0,0)_real')


# --------------------------------------------------------------------------- #
# Omega3P (eigenmodes read from the solver's own output)
# --------------------------------------------------------------------------- #

# Minimal Omega3P input. Nothing here names the results directory: no shipped
# tutorial input sets 'JobName', so the per-solver default is what a real run
# writes to.
OMEGA3P_INPUT = """\
ModelInfo: {
  File: ./pillbox.ncdf
}
"""

# Synthetic omega3p.out (KVC syntax, like the real thing): 2 modes, real
# eigenvalues. The real frozen fixtures — banner, differing section order,
# complex eigenvalues — are exercised in tests/test_ace3p.py.
OMEGA3P_OUTPUT = """\
        Mode : {
            TotalEnergy : 4.0e-12
            QualityFactor : 24000.0
            File : omega3p_results/mode.l0.m0000.mod
            PowerLoss : 1.0e-06
            Frequency : 1.1e9
        }
        Mode : {
            TotalEnergy : 4.0e-12
            QualityFactor : 21000.0
            File : omega3p_results/mode.l0.m0001.mod
            PowerLoss : 2.0e-06
            Frequency : 2.2e9
        }
"""

# The lossy/port variant: one mode, complex eigenvalue, ExternalQ present.
OMEGA3P_OUTPUT_COMPLEX = """\
        Mode : {
            QualityFactor : 28000.0
            Frequency : 1.3e9 , 641.0
            PowerLoss : 1.2e-06
            TotalEnergy : 4.0e-12 , 0.0
            ExternalQ : 1024235.0
            File : omega3p_results/omega3p.l0.m0000.mod
        }
"""


def _make_omega3p_solver(workdir, output=OMEGA3P_OUTPUT,
                         results_dir='omega3p_results'):
    """Construct an Omega3P wrapper over a synthetic omega3p.out and drive its
    output_parser directly — no binary run. Mirrors _make_s3p_solver."""
    dummy_input = os.path.join(workdir, 'dummy.omega3p')
    _write(dummy_input, OMEGA3P_INPUT)
    kwargs = {} if results_dir == 'omega3p_results' else {'results_dir': results_dir}
    omega3p = Omega3P(dummy_input, workdir=workdir, **kwargs)
    if output is not None:
        results = os.path.join(workdir, results_dir)
        os.makedirs(results, exist_ok=True)
        _write(os.path.join(results, 'omega3p.out'), output)
    omega3p.output_parser()
    return omega3p


def test_omega3p_extract(tmp_path):
    """Mode quantities come straight from the eigensolver's own output — no
    acdtool RoverQ step needed to get a frequency or a Q."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module = Omega3PModule({'input': 'dummy.omega3p'})
    module._solver = _make_omega3p_solver(wd)
    ctx = RunContext(wd, paths=_paths())

    # Full mode-indexed arrays (string spec / list spec).
    assert np.allclose(module.extract(ctx, 'Frequency'), [1.1e9, 2.2e9])
    assert np.allclose(module.extract(ctx, ['Frequency']), [1.1e9, 2.2e9])
    assert np.allclose(module.extract(ctx, 'QualityFactor'), [24000.0, 21000.0])

    # Scalar for one mode — the same 'at:' narrowing S3P and T3P use.
    assert module.extract(ctx, {'quantity': 'Frequency', 'at': {'mode': 0}}) \
        == pytest.approx(1.1e9)
    assert module.extract(ctx, {'quantity': 'Frequency', 'at': {'mode': 1}}) \
        == pytest.approx(2.2e9)
    # A mapping with no 'at:' is the full array.
    assert np.allclose(module.extract(ctx, {'quantity': 'PowerLoss'}),
                       [1.0e-06, 2.0e-06])


def test_omega3p_extract_complex_eigenvalue(tmp_path):
    """A lossy/port run: Frequency keeps the real part (so it stays a plottable
    table column) and the imaginary half is its own quantity."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module = Omega3PModule({'input': 'dummy.omega3p'})
    module._solver = _make_omega3p_solver(wd, output=OMEGA3P_OUTPUT_COMPLEX)
    ctx = RunContext(wd, paths=_paths())

    at_mode_0 = {'at': {'mode': 0}}
    assert module.extract(ctx, {'quantity': 'Frequency', **at_mode_0}) \
        == pytest.approx(1.3e9)
    assert module.extract(ctx, {'quantity': 'Frequency_imag', **at_mode_0}) \
        == pytest.approx(641.0)
    assert module.extract(ctx, {'quantity': 'ExternalQ', **at_mode_0}) \
        == pytest.approx(1024235.0)


def test_omega3p_extract_honors_results_dir_config(tmp_path):
    """'results_dir' on the module reaches the wrapper, so a run whose batch
    job name was not the default is still readable."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module = Omega3PModule({'input': 'dummy.omega3p', 'results_dir': 'run17'})
    assert module.results_dir == 'run17'
    module._solver = _make_omega3p_solver(wd, results_dir='run17')
    assert np.allclose(module.extract(RunContext(wd), 'Frequency'),
                       [1.1e9, 2.2e9])


def test_omega3p_extract_unknown_quantity(tmp_path):
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module = Omega3PModule({'input': 'dummy.omega3p'})
    module._solver = _make_omega3p_solver(wd)
    with pytest.raises(ValueError, match='Unknown quantity'):
        module.extract(RunContext(wd), 'RoQ')
    # 'Modes' is the readable per-mode list, not an extractable column.
    with pytest.raises(ValueError, match='Unknown quantity'):
        module.extract(RunContext(wd), 'Modes')


def test_omega3p_extract_unknown_mode_names_what_exists(tmp_path):
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module = Omega3PModule({'input': 'dummy.omega3p'})
    module._solver = _make_omega3p_solver(wd)
    with pytest.raises(ValueError, match='no mode 5'):
        module.extract(RunContext(wd),
                       {'quantity': 'Frequency', 'at': {'mode': 5}})


def test_omega3p_extract_without_output_file_explains_why(tmp_path):
    """A failed/interrupted run writes no omega3p.out. Asking for a quantity
    must name the path looked for and the config key that changes it, rather
    than returning a bare NaN."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module = Omega3PModule({'input': 'dummy.omega3p'})
    module._solver = _make_omega3p_solver(wd, output=None)
    with pytest.raises(ValueError, match='omega3p_results'):
        module.extract(RunContext(wd), 'Frequency')


def test_omega3p_extract_dry_run_is_nan(tmp_path):
    """A scalar NaN, not S3P's array([nan]): Omega3P exposes no dry-run index
    axis, so the value lands in a wide table cell as-is."""
    ctx = RunContext(str(tmp_path / 'wd'))
    module = Omega3PModule({'input': 'x.omega3p'})
    module._solver = None
    assert np.isnan(module.extract(ctx, 'Frequency'))


def test_omega3p_field_index_and_field(tmp_path):
    """Omega3P results are indexed by mode, the way S3P's are by Frequency."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module = Omega3PModule({'input': 'dummy.omega3p'})
    module._solver = _make_omega3p_solver(wd)
    ctx = RunContext(wd)

    label, values = module.field_index(ctx)
    assert label == 'ModeID'
    assert np.array_equal(values, [0, 1])

    field = module.field(ctx)
    assert np.allclose(field['Frequency'], [1.1e9, 2.2e9])
    # The per-mode dict list is dropped: it cannot ride inside a field-artifact
    # .npz without pickling, and adds nothing the arrays lack.
    assert 'Modes' not in field


def test_omega3p_field_index_is_none_without_modes(tmp_path):
    """Deliberately NOT the single-row sentinel S3P/T3P return under dry-run.
    Omega3P's mode count is a result of the eigensolve, not something the input
    file declares, and a sentinel axis would silently reshape the existing wide
    omega3p -> acdtool sweep tables."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    ctx = RunContext(wd)

    module = Omega3PModule({'input': 'x.omega3p'})
    module._solver = None
    assert module.field_index(ctx) is None
    assert module.field(ctx) is None

    # Same when the run produced no output file at all.
    module._solver = _make_omega3p_solver(wd, output=None)
    assert module.field_index(ctx) is None
    assert module.field(ctx) is None


# --------------------------------------------------------------------------- #
# T3P (time-domain wakefields)
# --------------------------------------------------------------------------- #

# Minimal T3P input declaring a WakeField monitor, so the wrapper can resolve
# where its output lives. Braces on their own line — the T3P tutorial style.
T3P_INPUT = """\
ModelInfo:
{
  File: ./mesh.ncdf
}

Monitor:
{
  Type: WakeField
  Name: wakefield
  Smax: 1.4
}
"""

# Longitudinal wakefield.out: 3 s-samples. Values from the ACE3P tutorial's
# t3p/cavity-quarter run.
T3P_WAKEFIELD = """\
# T3P wakefield results at transverse point:
#(0.00000000000000e+00,0.00000000000000e+00)
# Loss factor = -3.88576373282202e-01 V/pC
#          s[m]        W_long(s)[V/pC]     I_bunch(s)[C/m]
0.00000000000000e+00 -1.00000000000000e-07 0.00000000000000e+00
1.00000000000000e-01 -2.00000000000000e-07 2.00000000000000e-16
2.00000000000000e-01 -3.00000000000000e-07 4.00000000000000e-16
"""

# Transverse variant — reports a kick factor instead of a loss factor.
T3P_WAKEFIELD_TRANSVERSE = """\
# T3P transverse wakefield result using transverse points:
# (0.00000000000000e+00,0.00000000000000e+00) and
# (0.00000000000000e+00,1.25000000000000e-02)
# with offset 1.25000000000000e-02 m
# Kick factor = 9.64058337896157e-02 V/pC
#          s[m]        W_trans(s)[V/pC]     I_bunch(s)[C/m]
0.00000000000000e+00 1.00000000000000e-09 0.00000000000000e+00
1.00000000000000e-01 2.00000000000000e-09 2.00000000000000e-16
"""


def _make_t3p_solver(workdir, wakefield=T3P_WAKEFIELD):
    """Build a T3P wrapper over a synthetic input + output file and drive its
    output_parser directly — no binary run. Mirrors _make_s3p_solver."""
    source = os.path.join(workdir, 'model.t3p')
    _write(source, T3P_INPUT)
    t3p = T3P(source, workdir=workdir)
    results = os.path.join(workdir, 't3p_results', 'OUTPUT')
    os.makedirs(results, exist_ok=True)
    if wakefield is not None:
        _write(os.path.join(results, 'wakefield.out'), wakefield)
    t3p.output_parser()
    return t3p


def test_t3p_extract(tmp_path):
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module = T3PModule({'input': 'model.t3p'})
    module._solver = _make_t3p_solver(wd)
    ctx = RunContext(wd, paths=_paths())

    # The per-run scalar figure of merit.
    assert module.extract(ctx, 'loss_factor') == pytest.approx(-3.88576373282202e-01)
    assert module.extract(ctx, ['loss_factor']) == pytest.approx(-3.88576373282202e-01)

    # Full s-indexed arrays.
    assert np.allclose(module.extract(ctx, 'W'), [-1e-07, -2e-07, -3e-07])
    assert np.allclose(module.extract(ctx, 's'), [0.0, 0.1, 0.2])
    assert np.allclose(module.extract(ctx, 'I_bunch'), [0.0, 2e-16, 4e-16])

    # Scalar at a wake position (the Xopt objective form). The s grid follows
    # from the solver's timestep, so the nearest sample is taken — 0.09 -> 0.1.
    assert module.extract(ctx, {'quantity': 'W', 'at': {'s': 0.1}}) \
        == pytest.approx(-2e-07)
    assert module.extract(ctx, {'quantity': 'W', 'at': {'s': 0.09}}) \
        == pytest.approx(-2e-07)


def test_t3p_extract_transverse_reports_kick_factor(tmp_path):
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module = T3PModule({'input': 'model.t3p'})
    module._solver = _make_t3p_solver(wd, wakefield=T3P_WAKEFIELD_TRANSVERSE)
    ctx = RunContext(wd, paths=_paths())

    assert module.extract(ctx, 'kick_factor') == pytest.approx(9.64058337896157e-02)

    # Asking for the longitudinal scalar on a transverse run names what IS
    # available rather than silently returning NaN.
    with pytest.raises(ValueError, match='kick_factor'):
        module.extract(ctx, 'loss_factor')


def test_t3p_extract_unknown_quantity(tmp_path):
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module = T3PModule({'input': 'model.t3p'})
    module._solver = _make_t3p_solver(wd)
    with pytest.raises(ValueError, match='Unknown quantity'):
        module.extract(RunContext(wd), 'impedance')


def test_t3p_extract_without_wake_monitor_explains_why(tmp_path):
    """A run with no WakeField monitor parses fine but has nothing to extract;
    asking for a quantity must say so rather than return a bare NaN."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module = T3PModule({'input': 'model.t3p'})
    module._solver = _make_t3p_solver(wd, wakefield=None)
    with pytest.raises(ValueError, match='WakeField monitor'):
        module.extract(RunContext(wd), 'loss_factor')


def test_t3p_extract_dry_run_is_nan(tmp_path):
    ctx = RunContext(str(tmp_path / 'wd'))
    module = T3PModule({'input': 'x.t3p'})
    module._solver = None
    assert np.isnan(module.extract(ctx, 'loss_factor')).all()


def test_t3p_field_index_and_field(tmp_path):
    """T3P is indexed by the wake coordinate s, the way S3P is by Frequency —
    this is what makes a T3P sweep table go long-format."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module = T3PModule({'input': 'model.t3p'})
    module._solver = _make_t3p_solver(wd)
    ctx = RunContext(wd)

    label, values = module.field_index(ctx)
    assert label == 's'
    assert np.allclose(values, [0.0, 0.1, 0.2])

    field = module.field(ctx)
    assert field['WakeType'] == 'longitudinal'
    assert np.allclose(field['W'], [-1e-07, -2e-07, -3e-07])


def test_t3p_field_index_dry_run_sentinel(tmp_path):
    """Under dry-run the index is a single-row sentinel so a swept long-format
    table still gets one row per grid point (same contract as S3P)."""
    module = T3PModule({'input': 'x.t3p'})
    module._solver = None
    label, values = module.field_index(RunContext(str(tmp_path / 'wd')))
    assert label == 's'
    assert np.allclose(values, [0.0])
    assert module.field(RunContext(str(tmp_path / 'wd'))) is None


# --------------------------------------------------------------------------- #
# T3P multi-monitor extraction (t3p_monitor_plan.md, Phase 2)
# --------------------------------------------------------------------------- #
#
# Driven against the *real* CW23 monitor fixtures frozen in that plan's Phase 0
# rather than synthetic text, for the same reason the Omega3P and S3P sections
# use real output: the series monitors are headerless, so a synthetic file would
# be a copy of an assumption. Provenance is in
# tests/fixtures/acdtool/SOURCES.md.

T3P_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'fixtures', 'acdtool', 't3p_outputs')

# The BPM run: five monitor types on one run, with a wake. Fixture -> the name
# T3P wrote. coaxpoint.out is skipped (same shape as point.out) and the netCDF
# Volume dumps are created empty, since nothing parses them.
BPM_STAGED = {'BPM.point.out': 'point.out',
              'BPM.port.out': 'port.out',
              'BPM.modecoeff.out': 'modecoeff.out',
              'BPM.Bunch0.out': 'Bunch0.out'}
BPM_VOLUME = ['volumets_t000000000020ps.out', 'volumets_t000000000020ps.out.mod']

# The SIBC run: three Power monitors and NO WakeField monitor -- the multi-
# instance case, and the one that forces the 't' axis.
SIBC_STAGED = [('SIBC.inputPower.out', 'inputPower.out'),
               ('SIBC.wallossPower.out', 'wallossPower.out'),
               # outputPower.out has the same two-column shape as its siblings,
               # which is why SOURCES.md records it as deliberately not copied.
               ('SIBC.wallossPower.out', 'outputPower.out')]
SIBC_VOLUME = ['fieldts_t000000000500ps.out', 'fieldts_t000000000500ps.out.mod']


def _monitor_module(tmp_path, input_fixture, staged=(), touch=(), wake=None):
    """A :class:`T3PModule` whose solver has parsed a staged results directory
    built from the real monitor fixtures. Returns ``(module, ctx)``."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    source = os.path.join(wd, input_fixture)
    shutil.copy(os.path.join(T3P_FIXTURES, input_fixture), source)
    results = os.path.join(wd, 't3p_results', 'OUTPUT')
    os.makedirs(results, exist_ok=True)
    for fixture, name in (staged.items() if hasattr(staged, 'items') else staged):
        shutil.copy(os.path.join(T3P_FIXTURES, fixture),
                    os.path.join(results, name))
    for name in touch:
        _write(os.path.join(results, name), '')
    if wake is not None:
        _write(os.path.join(results, 'wakefield.out'), wake)

    solver = T3P(source, workdir=wd)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', T3POutputWarning)
        solver.output_parser()
    module = T3PModule({'input': input_fixture})
    module._solver = solver
    return module, RunContext(wd, paths=_paths())


def test_t3p_extracts_three_power_monitors_by_name(tmp_path):
    """The workflow this package could not express: in / out / wall-loss power on
    one run, addressed by ``Name``. ``Type`` cannot address them — all three are
    ``Power`` monitors."""
    module, ctx = _monitor_module(tmp_path, 'SIBC.t3p', staged=SIBC_STAGED,
                                  touch=SIBC_VOLUME)

    powers = {name: module.extract(ctx, {'monitor': name, 'quantity': 'P'})
              for name in ['inputPower', 'outputPower', 'wallossPower']}
    assert set(powers) == {'inputPower', 'outputPower', 'wallossPower'}
    for values in powers.values():
        assert len(values) == 20
    assert powers['inputPower'][0] == pytest.approx(-4.664513771111e-08)
    # inputPower and wallossPower are genuinely different columns.
    assert not np.allclose(powers['inputPower'], powers['wallossPower'])

    # A scalar at one instant, the objective form an Xopt run needs. The time grid
    # is a consequence of TimeStepping: DT, so the nearest sample is taken.
    at_50ps = module.extract(ctx, {'monitor': 'inputPower', 'quantity': 'P',
                                   'at': {'t': 5.0e-11}})
    assert at_50ps == pytest.approx(powers['inputPower'][4])
    assert module.extract(ctx, {'monitor': 'inputPower', 'quantity': 'P',
                                'at': {'t': 4.7e-11}}) == pytest.approx(at_50ps)


def test_t3p_bare_ambiguous_quantity_lists_the_candidates(tmp_path):
    """Three monitors provide ``P``, so a bare ``quantity: P`` cannot be resolved.
    It raises naming all three rather than picking the first."""
    module, ctx = _monitor_module(tmp_path, 'SIBC.t3p', staged=SIBC_STAGED,
                                  touch=SIBC_VOLUME)

    with pytest.raises(ValueError) as excinfo:
        module.extract(ctx, {'quantity': 'P'})
    message = str(excinfo.value)
    assert "monitor: <name>" in message
    for name in ['inputPower', 'outputPower', 'wallossPower']:
        assert name in message


def test_t3p_bare_quantity_resolves_when_only_one_monitor_provides_it(tmp_path):
    """``monitor:`` is omittable when exactly one monitor answers — which is what
    keeps every wakefield spec ever written working, and also covers a run with a
    single power monitor."""
    module, ctx = _monitor_module(
        tmp_path, 'SIBC.t3p',
        staged=[('SIBC.inputPower.out', 'inputPower.out')], touch=SIBC_VOLUME)

    assert module.extract(ctx, {'quantity': 'P'})[0] == pytest.approx(
        -4.664513771111e-08)
    # ...and the module is still reachable by name.
    assert np.allclose(module.extract(ctx, {'monitor': 'inputPower',
                                           'quantity': 'P'}),
                       module.extract(ctx, {'quantity': 'P'}))


def test_t3p_axis_is_t_when_the_run_has_no_wake(tmp_path):
    """A run with no ``WakeField`` monitor is indexed by time, so its power
    columns are table columns rather than needing an ``at:``."""
    module, ctx = _monitor_module(tmp_path, 'SIBC.t3p', staged=SIBC_STAGED,
                                  touch=SIBC_VOLUME)

    label, values = module.field_index(ctx)
    assert label == 't'
    assert len(values) == 20
    assert values[0] == pytest.approx(1.0e-11)
    # SIBC's DT is 10 ps.
    assert np.allclose(np.diff(values), 1.0e-11)
    # The full array aligns with the index, so no narrowing is required.
    assert len(module.extract(ctx, {'monitor': 'inputPower',
                                    'quantity': 'P'})) == len(values)


def test_t3p_axis_is_s_when_a_wake_is_present(tmp_path):
    """Design decision 2: ``s`` wins over ``t``. The BPM run has a wake *and*
    Point/Power/ModeVoltage monitors on a 4001-step time grid; the table stays
    ``s``-indexed, which is the tiebreak that keeps every baseline where it is."""
    module, ctx = _monitor_module(tmp_path, 'BPM.t3p', staged=BPM_STAGED,
                                  touch=BPM_VOLUME, wake=T3P_WAKEFIELD)

    label, values = module.field_index(ctx)
    assert label == 's'
    assert np.allclose(values, [0.0, 0.1, 0.2])
    # The legacy specs resolve to the wake monitor with no 'monitor:' key.
    assert module.extract(ctx, 'loss_factor') == pytest.approx(
        -3.88576373282202e-01)
    assert np.allclose(module.extract(ctx, 'W'), [-1e-07, -2e-07, -3e-07])
    # ...and so does naming the wake monitor explicitly.
    assert module.extract(ctx, {'monitor': 'wakefield',
                                'quantity': 'loss_factor'}) == pytest.approx(
        -3.88576373282202e-01)


def test_t3p_off_axis_monitor_must_be_narrowed_to_a_scalar(tmp_path):
    """A ``Point`` monitor's 20 timesteps cannot be columns of a 3-row
    ``s``-indexed table, so the array form raises naming both axes and the array
    is reachable at one instant instead. Same rule ``AcdtoolModule`` applies to a
    surface-indexed section on a ``ModeID``-indexed table."""
    module, ctx = _monitor_module(tmp_path, 'BPM.t3p', staged=BPM_STAGED,
                                  touch=BPM_VOLUME, wake=T3P_WAKEFIELD)

    with pytest.raises(ValueError) as excinfo:
        module.extract(ctx, {'monitor': 'point', 'quantity': 'Ez'})
    message = str(excinfo.value)
    assert "at: {t: ...}" in message
    assert "indexed by 's'" in message

    # Narrowed, it is a perfectly good table column.
    value = module.extract(ctx, {'monitor': 'point', 'quantity': 'Ez',
                                 'at': {'t': 5.0e-13}})
    assert value == pytest.approx(-1.087379539e-28)
    assert np.ndim(value) == 0


def test_t3p_volume_monitor_provides_no_quantity(tmp_path):
    """Design decision 7: a ``Volume`` monitor dumps netCDF field snapshots, so
    asking it for a quantity raises naming the reason — and names the files, which
    is what it does carry."""
    module, ctx = _monitor_module(tmp_path, 'BPM.t3p', staged=BPM_STAGED,
                                  touch=BPM_VOLUME, wake=T3P_WAKEFIELD)

    with pytest.raises(ValueError) as excinfo:
        module.extract(ctx, {'monitor': 'volume', 'quantity': 'Ez'})
    message = str(excinfo.value)
    assert 'netCDF' in message
    assert 'volumets_t000000000020ps.out' in message


def test_t3p_unknown_monitor_names_what_is_readable(tmp_path):
    """Naming a monitor that did not write lists both what *is* readable and what
    the input declared, so a typo and a failed monitor look different."""
    module, ctx = _monitor_module(tmp_path, 'BPM.t3p', staged=BPM_STAGED,
                                  touch=BPM_VOLUME, wake=T3P_WAKEFIELD)

    with pytest.raises(ValueError) as excinfo:
        module.extract(ctx, {'monitor': 'coaxpoint', 'quantity': 'Ez'})
    message = str(excinfo.value)
    assert 'coaxpoint' in message          # declared in BPM.t3p, wrote nothing
    assert 'modecoeff' in message          # ...unlike this one


def test_t3p_wrong_quantity_on_a_named_monitor_lists_its_columns(tmp_path):
    """Reporting from the data rather than from a hardcoded set — the same style
    ``AcdtoolModule._value`` uses, and necessary for the same reason: a
    ``Power`` monitor and a ``Point`` monitor answer to different names."""
    module, ctx = _monitor_module(tmp_path, 'SIBC.t3p', staged=SIBC_STAGED,
                                  touch=SIBC_VOLUME)

    with pytest.raises(ValueError, match=r"reported \['P', 't'\]"):
        module.extract(ctx, {'monitor': 'inputPower', 'quantity': 'Ez'})


def test_t3p_stray_and_wrong_axis_at_keys_are_rejected(tmp_path):
    """``at:`` narrows on ``s`` or ``t`` and nothing else, and a monitor takes only
    its own axis — an ``at: {s: ...}`` on a power monitor is a spec error, not a
    silently ignored key."""
    module, ctx = _monitor_module(tmp_path, 'SIBC.t3p', staged=SIBC_STAGED,
                                  touch=SIBC_VOLUME)

    with pytest.raises(ValueError, match="narrows on"):
        module.extract(ctx, {'monitor': 'inputPower', 'quantity': 'P',
                             'at': {'mode': 0}})
    with pytest.raises(ValueError, match=r"takes 'at: \{t: \.\.\.\}'"):
        module.extract(ctx, {'monitor': 'inputPower', 'quantity': 'P',
                             'at': {'s': 0.1}})


def test_t3p_bunch0_is_addressable(tmp_path):
    """``Bunch0.out`` is written by every run and declared by no monitor, so it has
    no ``Type`` — but it is a ``t``-indexed series like any other and can be asked
    for by name."""
    module, ctx = _monitor_module(
        tmp_path, 'SIBC.t3p', staged=[('BPM.Bunch0.out', 'Bunch0.out')],
        touch=SIBC_VOLUME)

    current = module.extract(ctx, {'monitor': 'Bunch0', 'quantity': 'I'})
    assert len(current) == 20
    assert current[0] == pytest.approx(1.03510387e-07)
    # It is also the axis of last resort when no declared monitor wrote.
    label, values = module.field_index(ctx)
    assert label == 't'
    assert values[0] == pytest.approx(5.0e-13)


def test_t3p_field_carries_every_off_axis_series(tmp_path):
    """The other half of design decision 2: nothing is discarded. The wake keys,
    the three series monitors and the Volume filenames all ride in ``field()``,
    and the nested dict round-trips through ``save_field`` the way S3P's
    ``IndexMap`` does."""
    module, ctx = _monitor_module(tmp_path, 'BPM.t3p', staged=BPM_STAGED,
                                  touch=BPM_VOLUME, wake=T3P_WAKEFIELD)
    field = module.field(ctx)

    assert field['WakeType'] == 'longitudinal'
    assert np.allclose(field['W'], [-1e-07, -2e-07, -3e-07])
    assert sorted(field['Monitors']) == ['modecoeff', 'point', 'port', 'volume']
    assert list(field['Bunch0']) == ['t', 'I']

    handle = save_field(field, str(tmp_path / 'row0'))
    loaded = load_field(handle)
    assert loaded['WakeType'] == 'longitudinal'
    assert np.allclose(loaded['s'], field['s'])
    assert np.allclose(loaded['Monitors']['point']['Ez'],
                       field['Monitors']['point']['Ez'])
    assert loaded['Monitors']['volume']['Type'] == 'Volume'
    assert list(loaded['Monitors']['volume']['files']) == sorted(BPM_VOLUME)
    assert np.allclose(loaded['Bunch0']['I'], field['Bunch0']['I'])


def test_t3p_field_index_is_none_when_nothing_was_read(tmp_path):
    """A real run that produced no readable monitor output has no index axis at
    all, so the table goes wide rather than getting a fabricated one-row ``s``.
    (The dry-run sentinel is a different case — see
    ``test_t3p_field_index_dry_run_sentinel``.)"""
    module, ctx = _monitor_module(tmp_path, 'SIBC.t3p')
    assert module._solver.output_data == {}
    assert module.field_index(ctx) is None
    assert module.field(ctx) is None


def test_t3p_monitor_key_routes_without_naming_the_module():
    """A ``monitor:`` key routes to ``t3p`` on its own, the way a ``section:`` key
    routes to acdtool — so ``module: t3p`` need not be repeated. The monitor
    *quantities* stay unroutable bare on purpose: ``'P'`` / ``'V'`` / ``'t'`` are
    short and generic, so ``QUANTITIES`` is not widened."""
    assert _infer_output_module({'monitor': 'inputPower', 'quantity': 'P'}) == 't3p'
    assert _infer_output_module({'quantity': 'loss_factor'}) == 't3p'
    assert _infer_output_module('kick_factor') == 't3p'
    # Unchanged: a bare monitor quantity is not a T3P routing signal.
    assert 'P' not in T3PModule.QUANTITIES
    assert 't' not in T3PModule.QUANTITIES
    assert _infer_output_module({'quantity': 'P'}) == 's3p'


# --------------------------------------------------------------------------- #
# Acdtool
# --------------------------------------------------------------------------- #


def test_acdtool_module_dry_run(tmp_path):
    ctx = RunContext(str(tmp_path / 'wd'),
                     artifacts={EM_SOLUTION: str(tmp_path / 'wd')},
                     dry_run=True)
    AcdtoolModule({'input': 'x.rfpost'}).run(ctx)
    assert RF_POST in ctx.artifacts
    assert 'Acdtool step skipped' in open(
        os.path.join(ctx.workdir, 'DRY_RUN.txt')).read()


def test_acdtool_module_requires_em_solution(tmp_path):
    ctx = RunContext(str(tmp_path / 'wd'), dry_run=True)
    with pytest.raises(ValueError):
        AcdtoolModule({'input': 'x.rfpost'}).run(ctx)


def test_acdtool_extract(tmp_path):
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    acd = _make_acdtool(wd)

    module = AcdtoolModule({'input': 'test.rfpost'})
    module._acdtool = acd
    ctx = RunContext(wd)

    # Values parsed from RFPOST_OUTPUT.
    expected = {
        ('RoverQ', '0', 'RoQ'): 250.0,
        ('RoverQ', '0', 'Frequency'): 1.3e9,
        ('kickFactor', '0', 'Ks'): 3.3,
        ('maxFieldsOnSurface', '6', 'Emax'): 1.5e6,
        ('maxFieldsOnSurface', '6', 'Emax_location', 'x'): 0.1,
        ('maxFieldsOnSurface', '6', 'Emax_location', 'z'): 0.3,
    }
    for spec, value in expected.items():
        assert module.extract(ctx, list(spec)) == pytest.approx(value), spec


# --------------------------------------------------------------------------- #
# Acdtool output-spec migration (Phase 4)
# --------------------------------------------------------------------------- #


# A run with the two shapes that have no index axis: FieldAtPoint evaluates only
# RFField's ModeID, and '[scaling]' is emitted by every run and declared by no
# block.
POINT_RFPOST_INPUT = """\
FieldAtPoint
{
   ionoff = 1
}
"""

POINT_RFPOST_OUTPUT = """\
[FieldAtPoint]
Ez = 1.250000e6
Hphi = 3.400000e3
}

[scaling]
Field scaled at: x0 = 0.00000  y0 = 0.00000  z0 = 0.00000
Ez from O3P = ( 2.55910e+00, 0.00000e+00)
Ez scaled to = 2.00000e+07
m_factor = ( 7.81528e+06, 0.00000e+00)  amplitude/phase_deg = ( 7.81528e+06, 0.00000)
}
"""


def _acdtool_over(workdir, input_text, output_text, name='point.rfpost'):
    """An ``Acdtool`` over an arbitrary synthetic input/output pair, parsed."""
    _write(os.path.join(workdir, name), input_text)
    _write(os.path.join(workdir, 'rfpost.out'), output_text)
    acd = Acdtool(os.path.join(workdir, name), workdir=workdir)
    acd.output_file = 'rfpost.out'
    acd.load_output()
    return acd


def _acdtool_module(workdir):
    module = AcdtoolModule({'input': 'test.rfpost'})
    module._acdtool = _make_acdtool(workdir)
    return module, RunContext(workdir)


def test_acdtool_mapping_and_list_forms_agree(tmp_path):
    """The deprecated positional form is an *alias*: both spellings of the same
    quantity come out of the same fixture identical, including the location
    component the list form spells as a fourth element."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module, ctx = _acdtool_module(wd)

    pairs = [
        (['RoverQ', '0', 'RoQ'],
         {'section': 'RoverQ', 'quantity': 'RoQ', 'at': {'mode': 0}}),
        (['RoverQ', '1', 'Frequency'],
         {'section': 'RoverQ', 'quantity': 'Frequency', 'at': {'mode': 1}}),
        (['kickFactor', '0', 'Ks'],
         {'section': 'kickFactor', 'quantity': 'Ks', 'at': {'mode': 0}}),
        (['maxFieldsOnSurface', '6', 'Emax'],
         {'section': 'maxFieldsOnSurface', 'quantity': 'Emax',
          'at': {'surface': 6}}),
        (['maxFieldsOnSurface', '6', 'Emax_location', 'y'],
         {'section': 'maxFieldsOnSurface', 'quantity': 'Emax_location',
          'component': 'y', 'at': {'surface': 6}}),
    ]
    for old, new in pairs:
        with pytest.warns(DeprecationWarning):
            legacy = module.extract(ctx, old)
        assert module.extract(ctx, new) == pytest.approx(legacy), new


def test_acdtool_list_form_warns_naming_its_mapping_replacement(tmp_path):
    """The deprecation names the exact replacement, and warns once per spec — a
    100-point sweep calls ``extract`` 100 times and must not print 100 copies."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module, ctx = _acdtool_module(wd)

    with pytest.warns(DeprecationWarning) as record:
        module.extract(ctx, ['RoverQ', '0', 'RoQ'])
    message = str(record[0].message)
    assert '{module: acdtool, section: RoverQ, quantity: RoQ, at: {mode: 0}}' \
        in message

    with warnings.catch_warnings(record=True) as again:
        warnings.simplefilter('always')
        module.extract(ctx, ['RoverQ', '0', 'RoQ'])
        module.extract(ctx, ['RoverQ', '0', 'Frequency'])
    assert [w for w in again if w.category is DeprecationWarning]
    assert len([w for w in again
                if w.category is DeprecationWarning]) == 1     # only the new spec

    # The mapping form never warns.
    with warnings.catch_warnings():
        warnings.simplefilter('error', DeprecationWarning)
        module.extract(ctx, {'section': 'RoverQ', 'quantity': 'RoQ',
                             'at': {'mode': 0}})


def test_acdtool_mode_section_without_at_returns_the_whole_axis(tmp_path):
    """The middle element of the list form was an index *axis*, not a selector:
    dropping the ``at:`` asks for every mode, which is what a dispersion curve or
    an HOM catalog wants and what the list form could not express."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module, ctx = _acdtool_module(wd)

    values = module.extract(ctx, {'section': 'RoverQ', 'quantity': 'RoQ'})
    assert isinstance(values, np.ndarray)
    assert values == pytest.approx([250.0, 40.0])
    freqs = module.extract(ctx, {'section': 'RoverQ', 'quantity': 'Frequency'})
    assert freqs == pytest.approx([1.3e9, 2.4e9])

    # ...and the axis those arrays are aligned to, so a sweep table gets one row
    # per mode.
    label, ids = module.field_index(ctx)
    assert label == 'ModeID'
    assert list(ids) == [0, 1]
    # Narrowing to one mode still gives the scalar, from either spelling of 0.
    assert module.extract(ctx, {'section': 'RoverQ', 'quantity': 'RoQ',
                                'at': {'mode': 0}}) == pytest.approx(250.0)
    assert module.extract(ctx, {'section': 'RoverQ', 'quantity': 'RoQ',
                                'at': {'mode': '0'}}) == pytest.approx(250.0)


def test_acdtool_surface_section_without_at_names_the_surface_ids(tmp_path):
    """``ModeID`` is acdtool's only table axis (design decision 2), so a
    surface-indexed section must be narrowed to one surface — and the error says
    which surfaces the run actually reported rather than just refusing."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module, ctx = _acdtool_module(wd)

    with pytest.raises(ValueError, match=r"at: \{surface: n\}.*\['6'\]"):
        module.extract(ctx, {'section': 'maxFieldsOnSurface',
                             'quantity': 'Emax'})
    with pytest.raises(ValueError, match=r"no surface 7.*\['6'\]"):
        module.extract(ctx, {'section': 'maxFieldsOnSurface',
                             'quantity': 'Emax', 'at': {'surface': 7}})
    # An 'at:' on the wrong axis is a clear error, not a silent whole-axis read.
    with pytest.raises(ValueError, match="takes 'at: \\{surface: n\\}'"):
        module.extract(ctx, {'section': 'maxFieldsOnSurface',
                             'quantity': 'Emax', 'at': {'mode': 0}})


def test_acdtool_unindexed_sections_resolve_to_scalars(tmp_path):
    """``FieldAtPoint`` has no index axis (it evaluates only ``RFField``'s
    ``ModeID``) and ``[scaling]`` is run-level, so both take no ``at:`` — and
    neither puts a ``ModeID`` axis on the table."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module = AcdtoolModule({'input': 'point.rfpost'})
    module._acdtool = _acdtool_over(wd, POINT_RFPOST_INPUT, POINT_RFPOST_OUTPUT)
    ctx = RunContext(wd)

    assert module.extract(ctx, {'section': 'FieldAtPoint',
                                'quantity': 'Ez'}) == pytest.approx(1.25e6)
    # '[scaling]' carries m_factor, the normalized-to-physical conversion.
    assert module.extract(ctx, {'section': 'scaling',
                                'quantity': 'm_factor'}) == pytest.approx(7.81528e6)
    assert module.field_index(ctx) is None
    with pytest.raises(ValueError, match='no at: narrowing'):
        module.extract(ctx, {'section': 'FieldAtPoint', 'quantity': 'Ez',
                             'at': {'mode': 0}})


def test_acdtool_extract_errors_name_what_the_run_reported(tmp_path):
    """Column names come from the output file, so an unknown one is answered with
    the columns that are there rather than with a hardcoded set."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module, ctx = _acdtool_module(wd)

    with pytest.raises(ValueError, match="no 'Ks'.*'RoQ'"):
        module.extract(ctx, {'section': 'RoverQ', 'quantity': 'Ks'})
    with pytest.raises(ValueError, match="needs a 'quantity'"):
        module.extract(ctx, {'section': 'RoverQ'})
    # A location vector without a component, and a component on a scalar.
    with pytest.raises(ValueError, match="is a vector.*component: x"):
        module.extract(ctx, {'section': 'maxFieldsOnSurface',
                             'quantity': 'Emax_location', 'at': {'surface': 6}})
    with pytest.raises(ValueError, match='is a scalar'):
        module.extract(ctx, {'section': 'maxFieldsOnSurface',
                             'quantity': 'Emax', 'component': 'x',
                             'at': {'surface': 6}})
    # A block the run did not report (its 'ionoff' was off) vs. no block at all.
    with pytest.raises(ValueError, match="no 'VFFT' section.*ionoff"):
        module.extract(ctx, {'section': 'VFFT', 'quantity': 'RoQ'})
    with pytest.raises(ValueError, match='names no known .rfpost block'):
        module.extract(ctx, {'section': 'NoSuchBlock', 'quantity': 'x'})
    # A curve block is a field artifact, not a table column.
    with pytest.raises(ValueError, match='field artifact'):
        module.extract(ctx, {'section': 'ALLFieldOnLine', 'quantity': 'Ez'})


def test_acdtool_field_index_is_none_without_a_mode_section(tmp_path):
    """No wrapper (dry-run) and a run with no mode-indexed block both yield no
    axis — Omega3P's asymmetry, for its reason: the mode count is a result of the
    solve, so a sentinel axis would reshape the dry-run sweep tables."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    ctx = RunContext(wd)

    module = AcdtoolModule({'input': 'test.rfpost'})
    assert module.field_index(ctx) is None       # dry-run: no wrapper
    assert module.extract(ctx, {'section': 'RoverQ', 'quantity': 'RoQ'}) != \
        module.extract(ctx, {'section': 'RoverQ', 'quantity': 'RoQ'})  # NaN

    surface_only = AcdtoolModule({'input': 'surface.rfpost'})
    surface_only._acdtool = _acdtool_over(
        wd, 'maxFieldsOnSurface\n{\n   ionoff = 1\n}\n',
        '[maxFieldsOnSurface]\nsurfaceID : 6\nEmax = 1.0e6 at (0.1, 0.2, 0.3)\n}\n',
        name='surface.rfpost')
    assert surface_only.field_index(ctx) is None


def test_acdtool_field_carries_the_mode_arrays(tmp_path):
    """When another module owns the table axis (``s3p -> acdtool``), a per-mode
    array cannot be a column of a frequency-indexed table, so design decision 2
    routes it to the field artifact. It is the same data ``extract`` returns."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    module, ctx = _acdtool_module(wd)

    field = module.field(ctx)
    assert set(field) == {'RoverQ', 'kickFactor'}
    assert list(field['RoverQ']['ModeID']) == [0, 1]
    assert field['RoverQ']['RoQ'] == pytest.approx(
        module.extract(ctx, {'section': 'RoverQ', 'quantity': 'RoQ'}))
    # It round-trips through the field-artifact store (no pickling).
    handle = save_field(field, os.path.join(wd, 'field'))
    assert load_field(handle)['RoverQ']['RoQ'] == pytest.approx([250.0, 40.0])


def test_acdtool_field_returns_curves_not_table_columns(tmp_path):
    """Phase 3: the ``filename`` blocks write their own column tables, which are
    per-position arrays and so ride as a field artifact rather than as table
    columns (design decision 4). ``None`` when the command wrote none."""
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    ctx = RunContext(wd)

    module = AcdtoolModule({'input': 'test.rfpost'})
    module._acdtool = _make_acdtool(wd)
    # RFPOST_OUTPUT declares no curve files, so the only thing riding here is
    # the Phase-4 mode-table view (see
    # test_acdtool_field_carries_the_mode_arrays); the surface block does not,
    # since it always resolves to an ``at:``-narrowed scalar column.
    assert set(module.field(ctx)) == {'RoverQ', 'kickFactor'}

    _write(os.path.join(wd, 'curve.rfpost'),
           'ALLFieldOnLine\n{\n   ionoff = 1\n   filename = field1\n}\n')
    _write(os.path.join(wd, 'field1_0'),
           '#  x           y           Ez\n'
           ' 0.0000e+00  1.0000e-03  2.5591e+00\n'
           ' 0.0000e+00  1.0000e-03 -8.9748e-01\n')
    _write(os.path.join(wd, 'rfpost.out'), '')
    acd = Acdtool(os.path.join(wd, 'curve.rfpost'), workdir=wd)
    acd.output_file = 'rfpost.out'
    acd.load_output()
    module._acdtool = acd

    field = module.field(ctx)
    assert list(field) == ['ALLFieldOnLine']
    curve = field['ALLFieldOnLine']['field1_0']
    assert list(curve) == ['x', 'y', 'Ez']
    assert curve['Ez'][1] == pytest.approx(-8.9748e-01)

    # Dry-run: no wrapper, no field.
    module._acdtool = None
    assert module.field(ctx) is None


# --------------------------------------------------------------------------- #
# Acdtool command dispatch (Phase 2)
# --------------------------------------------------------------------------- #


def test_acdtool_artifact_vocabulary_is_shared():
    """The command table repeats the artifact-kind strings rather than importing
    them (modules.py imports acdtool.py, not the reverse). Pin that they match,
    since a silent drift would make every command's ``requires`` unsatisfiable."""
    assert ACD_EM_SOLUTION == EM_SOLUTION
    assert ACD_TD_SOLUTION == TD_SOLUTION
    assert ACD_TRACK3P_PARTICLES == TRACK3P_PARTICLES


def test_acdtool_requires_follows_the_command():
    """``requires`` is set on the *instance* from the command table, which is all
    the DAG needs — ``_resolve_order`` reads the edges off instances."""
    cases = {
        None: EM_SOLUTION,                        # inferred 'postprocess rf'
        'postprocess rf': EM_SOLUTION,
        'postprocess transwake': TD_SOLUTION,
        'postprocess coaxsignal': TD_SOLUTION,
        'postprocess volmontomode': TD_SOLUTION,
    }
    for command, artifact in cases.items():
        config = {'input': 'x.rfpost'} if command is None else {'command': command}
        module = AcdtoolModule(config)
        assert module.requires == frozenset({artifact}), command
        assert module.provides == frozenset({RF_POST}), command


def test_acdtool_unknown_command_lists_known_ones():
    with pytest.raises(ValueError, match='postprocess transwake'):
        AcdtoolModule({'command': 'postprocess wiggle'})


def test_acdtool_unwired_command_names_why():
    """A command in the table but with no module home must say so, and say why —
    the reason lives in the table rather than only in the plan."""
    with pytest.raises(ValueError, match='mesh producer'):
        AcdtoolModule({'command': 'mesh deform'})
    with pytest.raises(ValueError, match='KVC'):
        AcdtoolModule({'command': 'postprocess track3p'})
    # ...and points at the wrapper for the ones that are still invocable.
    with pytest.raises(ValueError, match='invoked directly'):
        AcdtoolModule({'command': 'postprocess track3p'})


def test_acdtool_non_rfpost_input_without_a_command_is_rejected():
    """Extension inference covers only ``.rfpost``; anything else must declare
    its command rather than be guessed at."""
    with pytest.raises(ValueError, match='cannot infer a command'):
        AcdtoolModule({'input': 'Pillbox.acdtool'})


def test_acdtool_module_dry_run_records_the_command(tmp_path):
    ctx = RunContext(str(tmp_path / 'wd'),
                     artifacts={TD_SOLUTION: str(tmp_path / 'wd')},
                     dry_run=True)
    module = AcdtoolModule({'command': 'postprocess transwake',
                            'args': [0.0, 0.0, 0.0, 0.0125]})
    module.run(ctx)

    marker = open(os.path.join(ctx.workdir, 'DRY_RUN.txt')).read()
    assert 'postprocess transwake' in marker
    assert '0.0125' in marker
    assert 'Acdtool jobname: t3p_results' in marker
    assert RF_POST in ctx.artifacts


def test_acdtool_reports_the_artifact_its_command_needs(tmp_path):
    ctx = RunContext(str(tmp_path / 'wd'), dry_run=True)
    with pytest.raises(ValueError, match='td_solution'):
        AcdtoolModule({'command': 'postprocess transwake',
                       'args': [0.0, 0.0, 0.0, 0.0125]}).run(ctx)


def test_acdtool_injects_the_producers_jobname(tmp_path, monkeypatch):
    """The jobname is the *producing solver's* resolved results directory, not a
    value the user repeats. A T3P module with ``results_dir: custom_results``
    therefore moves acdtool's argument with it."""
    commands = []
    monkeypatch.setattr('subprocess.run',
                        lambda cmd, **kw: commands.append(cmd))
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    _write(os.path.join(wd, 'model.t3p'), T3P_INPUT)
    ctx = RunContext(wd, artifacts={MESH: wd},
                    paths={'ace3p': '/ace3p/', 'mpi': 'srun'})

    t3p = T3PModule({'input': os.path.join(wd, 'model.t3p'),
                     'results_dir': 'custom_results'})
    t3p.run(ctx)
    assert ctx.job_names[TD_SOLUTION] == 'custom_results'

    AcdtoolModule({'command': 'postprocess coaxsignal'}).run(ctx)
    assert commands[-1].endswith('postprocess coaxsignal custom_results')

    # An explicit 'jobname:' still wins.
    AcdtoolModule({'command': 'postprocess coaxsignal',
                   'jobname': 'elsewhere'}).run(ctx)
    assert commands[-1].endswith('postprocess coaxsignal elsewhere')


def test_transwake_reparses_the_producer(tmp_path, monkeypatch):
    """DEFECT 7, the ordering hazard, and the test most likely to pass by
    accident — so it asserts the **wake type**, not just that a number came out.

    ``transwake`` overwrites ``<jobname>/OUTPUT/wakefield.out``, which T3P has
    already parsed. Phase 2's decision is (a): the mutating consumer calls the
    producer's re-parse hook, so ``T3PModule`` stays the single owner of every
    wakefield quantity. Without it the chain silently reports the *longitudinal*
    loss factor from before acdtool ran.
    """
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    _write(os.path.join(wd, 'model.t3p'), T3P_INPUT)
    wake = os.path.join(wd, 't3p_results', 'OUTPUT', 'wakefield.out')

    def fake_run(cmd, **kwargs):
        # The t3p invocation writes the longitudinal wake; the acdtool transwake
        # invocation overwrites it with the transverse one, as the real tool does.
        # Match the full command, not the bare word: pytest's tmp_path is named
        # after this test, so 'transwake' appears in t3p's command line too.
        os.makedirs(os.path.dirname(wake), exist_ok=True)
        _write(wake, T3P_WAKEFIELD_TRANSVERSE
               if 'acdtool postprocess transwake' in cmd else T3P_WAKEFIELD)
    monkeypatch.setattr('subprocess.run', fake_run)

    ctx = RunContext(wd, artifacts={MESH: wd},
                     paths={'ace3p': '/ace3p/', 'mpi': 'srun'})
    t3p = T3PModule({'input': os.path.join(wd, 'model.t3p')})
    t3p.run(ctx)

    # Before acdtool: the longitudinal result T3P's own monitor produced.
    assert t3p._solver.output_data['WakeType'] == 'longitudinal'
    assert t3p.extract(ctx, 'loss_factor') == pytest.approx(-3.88576373282202e-01)

    acdtool = AcdtoolModule({'command': 'postprocess transwake',
                             'args': [0.0, 0.0, 0.0, 0.0125]})
    acdtool.run(ctx)

    # After acdtool: the transverse result, read by T3PModule -- not by acdtool.
    assert t3p._solver.output_data['WakeType'] == 'transverse'
    assert t3p.extract(ctx, 'kick_factor') == pytest.approx(9.64058337896157e-02)
    assert acdtool._acdtool.output_data == {}    # acdtool parses nothing here
    assert acdtool._acdtool.output_file == 't3p_results/OUTPUT/wakefield.out'


def test_coaxsignal_does_not_reparse_the_producer(tmp_path, monkeypatch):
    """``coaxsignal`` writes a *new* file (``signal.out``), so there is no
    ordering hazard and no re-parse. Pins that the hook is driven by the table's
    ``mutates`` field rather than fired for every td_solution consumer."""
    calls = []
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    monkeypatch.setattr('subprocess.run', lambda cmd, **kw: None)
    ctx = RunContext(wd, artifacts={TD_SOLUTION: wd},
                     paths={'ace3p': '/ace3p/', 'mpi': 'srun'})
    ctx.reparse[TD_SOLUTION] = lambda: calls.append('reparsed')

    AcdtoolModule({'command': 'postprocess coaxsignal'}).run(ctx)
    assert calls == []

    AcdtoolModule({'command': 'postprocess transwake',
                   'args': [0.0, 0.0, 0.0, 0.0125]}).run(ctx)
    assert calls == ['reparsed']


def test_acdtool_extract_from_a_non_rf_command_points_at_the_right_module(tmp_path):
    """Only ``postprocess rf`` writes indexable ``rfpost.out`` sections. Asking a
    transwake step for a quantity is an output-spec mistake, and the error says
    which module owns the value instead of returning NaN or a KeyError."""
    module = AcdtoolModule({'command': 'postprocess transwake',
                            'args': [0.0, 0.0, 0.0, 0.0125]})
    with pytest.raises(ValueError, match='module: t3p'):
        module.extract(RunContext(str(tmp_path)), ['RoverQ', '0', 'RoQ'])

    module = AcdtoolModule({'command': 'postprocess volmontomode'})
    with pytest.raises(ValueError, match="Only 'postprocess rf'"):
        module.extract(RunContext(str(tmp_path)), ['RoverQ', '0', 'RoQ'])

    # coaxsignal's output IS read (Phase 3), but as a column table — a field
    # artifact, not an indexable rfpost.out section.
    module = AcdtoolModule({'command': 'postprocess coaxsignal'})
    with pytest.raises(ValueError, match='field artifact'):
        module.extract(RunContext(str(tmp_path)), ['signal', 'V'])


def test_wired_commands_are_the_cw23_exercised_ones():
    """Phase 2's implement tier: the four commands the tutorial exercises that
    map onto artifacts this package already has."""
    assert set(wired_commands()) == {
        'postprocess rf', 'postprocess transwake', 'postprocess coaxsignal',
        'postprocess volmontomode'}


# --------------------------------------------------------------------------- #
# Particles (real field-emission weighting)
# --------------------------------------------------------------------------- #


def test_particles_module_runs_and_extracts(tmp_path):
    dump = tmp_path / 'dump.txt'
    _make_track3p_dump(str(dump), impact_order=1, impact_face_id=6)
    wd = str(tmp_path / 'wd')
    ctx = RunContext(wd, artifacts={TRACK3P_PARTICLES: str(dump)})
    params = {'impact_order': 1, 'impact_face_id': 6, 'work_function': 4.5,
              'dt': 1.0e-10, 'num_bins': 8, 'beta': [50, 55, 60, 65, 65, 60, 55, 50],
              'output': 'particles.data'}
    module = ParticlesModule(params)
    module.run(ctx)

    assert PARTICLE_SOURCE in ctx.artifacts
    assert os.path.isfile(ctx.artifacts[PARTICLE_SOURCE])
    count = module.extract(ctx, 'count')
    assert count == 24  # every particle matches the filter
    assert np.isfinite(module.extract(ctx, 'total_weight'))


def test_particles_module_matches_direct_wrapper(tmp_path):
    """The module is a thin adapter — its geant4 source file must be identical
    to a direct Particles() invocation with the same params."""
    dump = tmp_path / 'dump.txt'
    _make_track3p_dump(str(dump))
    params = {'impact_order': 1, 'impact_face_id': 6, 'work_function': 4.5,
              'dt': 1.0e-10, 'num_bins': 8,
              'beta': [50, 55, 60, 65, 65, 60, 55, 50], 'output_format': 'geant4'}

    # Ground-truth: direct wrapper.
    ref_wd = str(tmp_path / 'ref')
    os.makedirs(ref_wd)
    shutil.copy(str(dump), ref_wd)
    ref = Particles('dump.txt', dict(params), output_file='particles.data',
                    workdir=ref_wd)
    ref.run()
    ref_arr = np.loadtxt(os.path.join(ref_wd, 'particles.data'))

    # Module path.
    wd = str(tmp_path / 'wd')
    ctx = RunContext(wd, artifacts={TRACK3P_PARTICLES: str(dump)})
    ParticlesModule(dict(params, output='particles.data')).run(ctx)
    mod_arr = np.loadtxt(ctx.artifacts[PARTICLE_SOURCE])

    assert np.allclose(ref_arr, mod_arr)


def test_particles_module_beta_input_broadcast(tmp_path):
    """beta_input broadcasts one input-space variable to all bins, read from the
    particles bucket (the field-enhancement scaling belongs to this step)."""
    dump = tmp_path / 'dump.txt'
    _make_track3p_dump(str(dump))
    wd = str(tmp_path / 'wd')
    ctx = RunContext(wd, inputs=WorkflowInputs(particles={'beta': 50.0}),
                     artifacts={TRACK3P_PARTICLES: str(dump)})
    module = ParticlesModule({'impact_order': 1, 'impact_face_id': 6,
                              'work_function': 4.5, 'dt': 1.0e-10, 'num_bins': 8,
                              'beta_input': 'beta', 'output': 'particles.data'})
    resolved = module._resolve_beta(ctx.inputs)
    assert resolved['beta'] == [50.0] * 8


def test_particles_module_beta_falls_back_to_cubit(tmp_path):
    """Legacy configs that declared β under the cubit bucket still resolve."""
    dump = tmp_path / 'dump.txt'
    _make_track3p_dump(str(dump))
    wd = str(tmp_path / 'wd')
    ctx = RunContext(wd, inputs=WorkflowInputs(cubit={'beta': 42.0}),
                     artifacts={TRACK3P_PARTICLES: str(dump)})
    module = ParticlesModule({'impact_order': 1, 'impact_face_id': 6,
                              'work_function': 4.5, 'dt': 1.0e-10, 'num_bins': 8,
                              'beta_input': 'beta', 'output': 'particles.data'})
    resolved = module._resolve_beta(ctx.inputs)
    assert resolved['beta'] == [42.0] * 8


def test_particles_module_requires_track3p(tmp_path):
    ctx = RunContext(str(tmp_path / 'wd'))
    with pytest.raises(ValueError):
        ParticlesModule({'num_bins': 1, 'beta': [1.0], 'work_function': 4.5,
                         'dt': 1e-10}).run(ctx)


# --------------------------------------------------------------------------- #
# Geant4
# --------------------------------------------------------------------------- #


def _stage_geant4(workdir):
    """Place a geant4 input + scoring outputs + particle source in workdir."""
    os.makedirs(workdir, exist_ok=True)
    _write(os.path.join(workdir, 'input.geant4'), GEANT4_INPUT)
    _write(os.path.join(workdir, 'dose.out'), DOSE_OUT)
    _write(os.path.join(workdir, 'edep.out'), EDEP_OUT)
    psrc = os.path.join(workdir, 'particles.data')
    _write(psrc, '0.0 0.0 0.0 0.0 1.0 1 0 0 1 6\n0.0 0.0 0.0 0.0 1.0 1 0 0 1 6\n')
    return os.path.join(workdir, 'input.geant4'), psrc


def test_geant4_module_dry_run(tmp_path):
    wd = str(tmp_path / 'wd')
    input_path, psrc = _stage_geant4(wd)
    ctx = RunContext(wd, inputs=WorkflowInputs(),
                     artifacts={PARTICLE_SOURCE: psrc}, dry_run=True,
                     paths=_paths())
    Geant4Module({'geant4_input': input_path}).run(ctx)
    # Output files are named in the input -> grid artifacts recorded.
    assert DOSE_GRID in ctx.artifacts
    assert EDEP_GRID in ctx.artifacts
    assert 'Geant4 step skipped' in open(os.path.join(wd, 'DRY_RUN.txt')).read()


def test_geant4_module_requires_particle_source(tmp_path):
    wd = str(tmp_path / 'wd')
    input_path, _ = _stage_geant4(wd)
    ctx = RunContext(wd, dry_run=True, paths=_paths())
    with pytest.raises(ValueError):
        Geant4Module({'geant4_input': input_path}).run(ctx)


def test_geant4_extract(tmp_path):
    # Module path: dry-run in its own workdir with pre-placed scoring, then
    # extract. Values come from DOSE_OUT (1,2,5) and EDEP_OUT (0.5,1.5,3).
    wd = str(tmp_path / 'mod')
    m_input, m_psrc = _stage_geant4(wd)
    ctx = RunContext(wd, inputs=WorkflowInputs(),
                     artifacts={PARTICLE_SOURCE: m_psrc}, dry_run=True,
                     paths=_paths())
    module = Geant4Module({'geant4_input': m_input})
    module.run(ctx)

    assert module.extract(ctx, ['dose', 'total']) == pytest.approx(8.0)
    assert module.extract(ctx, ['dose', 'peak']) == pytest.approx(5.0)
    assert module.extract(ctx, ['dose', 'peak_index']) == (1, 0, 0)
    assert module.extract(ctx, ['edep', 'total']) == pytest.approx(5.0)
    # 'scoring' is a back-compat alias for the dose grid.
    assert module.extract(ctx, ['scoring', 'total']) == pytest.approx(8.0)


def test_geant4_mapping_and_list_forms_agree(tmp_path):
    """The mapping form the shipped examples now use returns exactly what the
    positional list returned, for every (section, entry) pair.

    Unlike acdtool's list form this one is not deprecated — a Geant4 spec is a
    (grid, reduction) pair with no index axis, so the list expresses everything
    the mapping does — and the test asserts that neither form warns."""
    wd = str(tmp_path / 'mod')
    m_input, m_psrc = _stage_geant4(wd)
    ctx = RunContext(wd, inputs=WorkflowInputs(),
                     artifacts={PARTICLE_SOURCE: m_psrc}, dry_run=True,
                     paths=_paths())
    module = Geant4Module({'geant4_input': m_input})
    module.run(ctx)

    pairs = [(s, e) for s in ('dose', 'edep', 'scoring')
             for e in ('total', 'peak', 'peak_index')]
    for section, entry in pairs:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            listed = module.extract(ctx, [section, entry])
            mapped = module.extract(
                ctx, {'section': section, 'quantity': entry})
        assert mapped == listed, (section, entry)
        assert not [w for w in caught
                    if issubclass(w.category, DeprecationWarning)]
    assert len(pairs) == 9


def test_geant4_mapping_form_without_a_section_names_the_sections(tmp_path):
    """A mapping missing its 'section:' raises naming the scoring grids, rather
    than falling through to the NaN a malformed *list* still returns for
    back-compat."""
    wd = str(tmp_path / 'mod')
    m_input, m_psrc = _stage_geant4(wd)
    ctx = RunContext(wd, inputs=WorkflowInputs(),
                     artifacts={PARTICLE_SOURCE: m_psrc}, dry_run=True,
                     paths=_paths())
    module = Geant4Module({'geant4_input': m_input})
    module.run(ctx)

    with pytest.raises(ValueError, match="'dose'"):
        module.extract(ctx, {'quantity': 'total'})
    assert np.isnan(module.extract(ctx, ['dose']))     # unchanged list behavior


# --------------------------------------------------------------------------- #
# Staging modes (copy / symlink / hardlink) — storage-efficient staging.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('mode', sorted(STAGE_MODES))
def test_stage_file_modes_land_at_basename(tmp_path, mode):
    """All three modes land the file at ``workdir/basename`` (the co-location
    contract), and each produces the expected link kind."""
    src = tmp_path / 'big.stl'
    _write(str(src), 'x' * 1024)
    wd = tmp_path / 'wd'
    ctx = RunContext(str(wd), stage_mode=mode)

    dest = _stage_file(ctx, str(src))

    assert dest == os.path.join(str(wd), 'big.stl')
    assert os.path.isfile(dest)
    if mode == 'symlink':
        assert os.path.islink(dest)
        # Absolute target so it resolves under the tool's cwd=workdir.
        assert os.path.isabs(os.readlink(dest))
    elif mode == 'hardlink':
        assert not os.path.islink(dest)
        assert os.stat(src).st_ino == os.stat(dest).st_ino
    else:
        assert not os.path.islink(dest)
        assert os.stat(src).st_ino != os.stat(dest).st_ino


@pytest.mark.parametrize('mode', sorted(STAGE_MODES))
def test_stage_file_is_idempotent(tmp_path, mode):
    """A pre-existing dest (real file or stale symlink) makes a re-run a no-op —
    no exception, dest still resolves to the source bytes."""
    src = tmp_path / 'dump.txt'
    _write(str(src), 'payload')
    ctx = RunContext(str(tmp_path / 'wd'), stage_mode=mode)

    dest = _stage_file(ctx, str(src))
    dest2 = _stage_file(ctx, str(src))          # must not raise

    assert dest == dest2
    with open(dest) as f:
        assert f.read() == 'payload'


def test_stage_file_hardlink_falls_back_to_copy(tmp_path, monkeypatch):
    """Cross-device / unsupported hardlink (OSError from os.link) falls back to a
    real copy rather than propagating."""
    src = tmp_path / 'dump.txt'
    _write(str(src), 'payload')
    ctx = RunContext(str(tmp_path / 'wd'), stage_mode='hardlink')

    def _boom(*a, **k):
        raise OSError('Invalid cross-device link')
    monkeypatch.setattr('lume_ace3p.modules.os.link', _boom)

    dest = _stage_file(ctx, str(src))

    assert os.path.isfile(dest)
    assert not os.path.islink(dest)
    assert os.stat(src).st_ino != os.stat(dest).st_ino   # a copy, not a link
    with open(dest) as f:
        assert f.read() == 'payload'


def test_stage_file_default_mode_is_copy(tmp_path):
    """A RunContext built without stage_mode copies (unchanged legacy behavior)."""
    src = tmp_path / 'mesh.ncdf'
    _write(str(src), 'mesh')
    ctx = RunContext(str(tmp_path / 'wd'))       # no stage_mode -> 'copy'

    dest = _stage_file(ctx, str(src))

    assert os.path.isfile(dest) and not os.path.islink(dest)


def test_symlinked_source_module_resolves_at_workdir(tmp_path):
    """Regression for the co-location contract: a symlink-staged source artifact
    still resolves to ``workdir/basename`` (what every downstream tool reads)."""
    src = tmp_path / 'dump.txt'
    _make_track3p_dump(str(src))
    wd = tmp_path / 'wd'
    ctx = RunContext(str(wd), stage_mode='symlink')

    Track3PSourceModule({'file': str(src)}).run(ctx)

    staged = ctx.artifacts[TRACK3P_PARTICLES]
    assert staged == os.path.join(str(wd), 'dump.txt')
    assert os.path.islink(staged) and os.path.isfile(staged)


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
