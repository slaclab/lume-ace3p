"""Phase-1 module-layer tests (see docs/workflow_module_refactor_plan.md).

Two things are verified for every module:

1. **Isolation dry-run** — each module runs from a hand-built ``RunContext``,
   consuming the artifact keys it ``requires`` and producing the keys it
   ``provides``. The source modules and the pure-Python ``ParticlesModule`` run
   for real; the solver/geant4 modules run their dry-run path.
2. **extract equivalence** — for the modules that expose scalars
   (``S3PModule``, ``AcdtoolModule``, ``Geant4Module``), ``extract`` is checked
   to reproduce the exact values the legacy ``S3PWorkflow`` / ``Omega3PWorkflow``
   / ``Geant4Workflow`` ``evaluate`` methods return, fed the same synthetic
   solver-output fixtures. ``ParticlesModule`` is checked against a direct
   ``Particles`` invocation (the wrapper it adapts).

No ACE3P / Geant4 binary is needed: solver objects are constructed pointing at
synthetic output files and their ``output_parser`` is driven directly, or the
scoring files are pre-placed in the workdir.
"""

import os

import numpy as np
import pytest

from lume_ace3p.modules import (
    RunContext, build_module, MODULE_REGISTRY,
    CubitModule, MeshSourceModule, Omega3PModule, S3PModule, AcdtoolModule,
    Track3PSourceModule, ParticlesModule, ParticleSourceModule, Geant4Module,
    JOURNAL, MESH, EM_SOLUTION, RF_POST, TRACK3P_PARTICLES, PARTICLE_SOURCE,
    DOSE_GRID, EDEP_GRID,
)
from lume_ace3p.ace3p import S3P, Section
from lume_ace3p.acdtool import Acdtool
from lume_ace3p.geant4 import Geant4
from lume_ace3p.particles import Particles, TRACK3P_COLUMNS
from lume_ace3p.inputs import WorkflowInputs
from lume_ace3p.workflow import S3PWorkflow, Omega3PWorkflow, Geant4Workflow


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
RFPOST_OUTPUT = """\
[RoverQ]
Results for RoverQ:
ModeID Frequency Qext V_r V_i absV RoQ
0 1.300000e9 1000.0 0.5, 0.1 0.6 250.0
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


def _make_s3p_solver(workdir):
    """Construct an S3P wrapper pointed at a synthetic Reflection.out and parse
    it directly (bypassing the subprocess)."""
    os.makedirs(os.path.join(workdir, 's3p_results'), exist_ok=True)
    _write(os.path.join(workdir, 's3p_results', 'Reflection.out'), S3P_REFLECTION)
    dummy_input = os.path.join(workdir, 'dummy.s3p')
    _write(dummy_input, '')
    s3p = S3P(dummy_input, workdir=workdir)
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


@pytest.mark.parametrize('module_cls,label', [(Omega3PModule, 'Omega3P'),
                                              (S3PModule, 'S3P')])
def test_solver_module_dry_run(tmp_path, module_cls, label):
    ctx = RunContext(str(tmp_path / 'wd'),
                     inputs=WorkflowInputs(cubit={'x': 1.0}),
                     artifacts={MESH: str(tmp_path / 'wd' / 'm.genesis')},
                     dry_run=True)
    module_cls({'input': 'in.file'}).run(ctx)
    assert EM_SOLUTION in ctx.artifacts
    marker = open(os.path.join(ctx.workdir, 'DRY_RUN.txt')).read()
    assert f'{label} step skipped' in marker


def test_solver_module_requires_mesh(tmp_path):
    ctx = RunContext(str(tmp_path / 'wd'), dry_run=True)
    with pytest.raises(ValueError):
        S3PModule({'input': 'in.s3p'}).run(ctx)


def test_s3p_extract_matches_legacy_evaluate(tmp_path):
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    s3p = _make_s3p_solver(wd)

    ctx = RunContext(wd, paths=_paths())
    module = S3PModule({'input': 'dummy.s3p'})
    module._solver = s3p  # inject the parsed solver (no binary run)

    # Full frequency-indexed array (string spec / list spec).
    wf = S3PWorkflow({}, WorkflowInputs())
    wf.s3p_obj = s3p
    legacy = wf.evaluate({'refl': 'S(0,0)'})
    assert np.allclose(module.extract(ctx, 'S(0,0)'), legacy['refl'])
    assert np.allclose(module.extract(ctx, ['S(0,0)']), legacy['refl'])

    # Scalar at a frequency (the Xopt objective form).
    assert module.extract(ctx, {'quantity': 'S(1,1)',
                                'at': {'frequency': 12.5e9}}) == pytest.approx(0.80)


def test_s3p_extract_dry_run_is_nan(tmp_path):
    ctx = RunContext(str(tmp_path / 'wd'))
    module = S3PModule({'input': 'x.s3p'})
    module._solver = None
    assert np.isnan(module.extract(ctx, 'S(0,0)')).all()


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


def test_acdtool_extract_matches_legacy_evaluate(tmp_path):
    wd = str(tmp_path / 'wd')
    os.makedirs(wd, exist_ok=True)
    acd = _make_acdtool(wd)

    module = AcdtoolModule({'input': 'test.rfpost'})
    module._acdtool = acd
    ctx = RunContext(wd)

    wf = Omega3PWorkflow({}, WorkflowInputs())
    wf.acdtool_obj = acd

    output_dict = {
        'RoQ': ['RoverQ', '0', 'RoQ'],
        'mode_freq': ['RoverQ', '0', 'Frequency'],
        'kick_Ks': ['kickFactor', '0', 'Ks'],
        'E_max': ['maxFieldsOnSurface', '6', 'Emax'],
        'loc_x': ['maxFieldsOnSurface', '6', 'Emax_location', 'x'],
        'loc_z': ['maxFieldsOnSurface', '6', 'Emax_location', 'z'],
    }
    legacy = wf.evaluate(output_dict)
    for name, spec in output_dict.items():
        assert module.extract(ctx, spec) == legacy[name], name


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
    import shutil
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
    """beta_input broadcasts one input-space variable to all bins (the
    Geant4Workflow._resolve_beta behavior moved into the module)."""
    dump = tmp_path / 'dump.txt'
    _make_track3p_dump(str(dump))
    wd = str(tmp_path / 'wd')
    ctx = RunContext(wd, inputs=WorkflowInputs(cubit={'beta': 50.0}),
                     artifacts={TRACK3P_PARTICLES: str(dump)})
    module = ParticlesModule({'impact_order': 1, 'impact_face_id': 6,
                              'work_function': 4.5, 'dt': 1.0e-10, 'num_bins': 8,
                              'beta_input': 'beta', 'output': 'particles.data'})
    resolved = module._resolve_beta(ctx.inputs)
    assert resolved['beta'] == [50.0] * 8


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


def test_geant4_extract_matches_legacy_evaluate(tmp_path):
    output_dict = {
        'total_dose': ['dose', 'total'],
        'peak_dose': ['dose', 'peak'],
        'peak_idx': ['dose', 'peak_index'],
        'total_edep': ['edep', 'total'],
        'scoring_total': ['scoring', 'total'],   # back-compat alias for dose
    }

    # Legacy path: Geant4Workflow dry-run in a workdir with pre-placed scoring.
    legacy_wd = str(tmp_path / 'legacy')
    l_input, l_psrc = _stage_geant4(legacy_wd)
    wf = Geant4Workflow(
        {'geant4_input': l_input, 'geant4_particle_file': l_psrc,
         'workdir': legacy_wd, 'workdir_mode': 'manual', 'dry_run': True},
        WorkflowInputs(), output_dict)
    legacy = wf.run(output_dict=output_dict)

    # Module path: dry-run in its own workdir, then extract.
    wd = str(tmp_path / 'mod')
    m_input, m_psrc = _stage_geant4(wd)
    ctx = RunContext(wd, inputs=WorkflowInputs(),
                     artifacts={PARTICLE_SOURCE: m_psrc}, dry_run=True,
                     paths=_paths())
    module = Geant4Module({'geant4_input': m_input})
    module.run(ctx)

    for name, spec in output_dict.items():
        assert module.extract(ctx, spec) == legacy[name], name


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
