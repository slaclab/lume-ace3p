"""The ``acdtool`` wrapper — ACE3P's shared pre/postprocessing utility.

``acdtool`` is not one command but nineteen: three top-level, five ``mesh``
subtasks and eleven ``postprocess`` subtasks (see ``references/
acdtool-commands.pdf``). They take three different argument forms and only one of
them reads an input file this module can parse, so dispatch is driven by the
declarative :data:`COMMANDS` table rather than by an ``if``/``elif`` ladder:
each row carries the argument form, whether a results-directory *jobname* is
injected, which ACE3P artifact the command consumes, and whether the command can
run on more than one rank.

Two things the table encodes that are easy to get wrong:

* **``<jobname>`` is a name, not a path.** Every positional ``postprocess``
  command takes the producing solver's job name, defaulting per solver
  (``omega3p_results``, ``s3p_results``, ``t3p_results``, ``track3p_results``).
  :class:`~lume_ace3p.modules.AcdtoolModule` injects it from the artifact rather
  than making the user repeat it.
* **Only ``postprocess rf`` and ``postprocess volmontomode`` run in parallel**;
  the other seventeen are serial. So one rank is the correct default for every
  command and only those two accept a configurable rank count.

Input dialects: ``.rfpost`` is ``key = value`` (parsed here); ``.acdtool`` — the
input to ``postprocess track3p`` — is KVC ``key : value``, the same dialect as
solver input files, read by :func:`lume_ace3p.ace3p.parse_ace3p`. The two are
**not** unified; a ``.acdtool`` input raises here rather than parsing to empty
blocks. ``mesh warpsurface``'s flat-colon ``warp.in`` is a third dialect, never
parsed — the filename is passed through as an opaque positional argument.

**Output parsing is shape-driven, not section-by-section.** The 24 ``.rfpost``
blocks collapse into six output shapes, and the :data:`SECTIONS` table maps each
block to its shape and to where its output lands (``rfpost.out`` or a separate
file, whose name follows a *per-block* scheme). One reader per shape replaces
what used to be a three-branch ``if`` ladder over hand-counted column positions:

===================== =============================================== ==============
Shape                 Blocks                                          Written to
===================== =============================================== ==============
:data:`MODE_TABLE`    the ``modeID1``/``modeID2`` blocks              ``rfpost.out``
:data:`SURFACE`       ``maxFieldsOnSurface``, ``powerThroughSurface``  ``rfpost.out``
:data:`POINT`         ``FieldAtPoint``                                ``rfpost.out``
:data:`RUN`           ``[scaling]`` (always emitted, never declared)   ``rfpost.out``
:data:`CURVE`         the column-table ``filename`` blocks             separate files
:data:`GRID`          the field-map blocks                            separate files
===================== =============================================== ==============

Grid output is *recorded but not parsed* — see :data:`SECTIONS` — and only three
shapes have a real-output fixture behind them (``RoverQ``, ``ALLFieldOnLine`` and
``[scaling]``; see ``tests/fixtures/acdtool/COVERAGE.md``). The readers are
therefore driven by what the file says — the header row for a column table, the
``key = value`` lines for a scalar block — rather than by an assumed layout, and
a section whose output cannot be read warns naming itself instead of silently
yielding nothing.
"""

import glob
import os, re, shutil
import subprocess
import warnings

import numpy as np

from lume.base import CommandWrapper

# Re-exported: the header-driven column reader that reads every curve block's
# output lives with ``parse_wakefield`` in the solver layer, since Phase 5 needs
# it for S3P's port mode profiles too and the dependency only runs one way
# (postprocessor -> solver). Imported here so ``acdtool.parse_column_file``
# keeps resolving.
from lume_ace3p.ace3p import parse_column_file


# --------------------------------------------------------------------------- #
# Command table
# --------------------------------------------------------------------------- #

# Argument forms.
INPUT = 'input'                  # acdtool <cmd> <inputfile>
POSITIONAL = 'positional'        # acdtool <cmd> <arg>...
INPUT_JOBNAME = 'input+jobname'  # acdtool <cmd> <inputfile> <jobname>

# Artifact kinds a command consumes. These are the *same strings* as the
# vocabulary in :mod:`lume_ace3p.modules`, repeated rather than imported because
# that module imports this one. ``test_modules.py`` asserts they stay in sync.
EM_SOLUTION = 'em_solution'
TD_SOLUTION = 'td_solution'
TRACK3P_PARTICLES = 'track3p_particles'

# Output readers a command's result can be read by. ``RFPOST`` is the sectioned
# ``rfpost.out`` (plus whatever separate curve/grid files its input declared);
# ``SIGNAL`` is coaxsignal's headerless three-column ``signal.out``.
RFPOST = 'rfpost'
SIGNAL = 'signal'

# coaxsignal's columns. The file carries no header row at all, so the names come
# from the reference rather than from the file — see :func:`parse_column_file`.
SIGNAL_COLUMNS = ('t', 'V', 'I')

# The template 'acdtool postprocess rf' writes when given no arguments: the
# installed build's own '.rfpost' sample, and so the authoritative source for a
# default input — see :meth:`Acdtool.make_default_input`.
SAMPLE_INPUT = 'sample.rfpost'


class Command:
    """One row of the acdtool command surface.

    Attributes
    ----------
    form : str
        ``INPUT`` / ``POSITIONAL`` / ``INPUT_JOBNAME``.
    nargs : (int, int)
        Allowed count of *explicit* positional arguments, i.e. excluding an
        injected jobname and excluding the input file.
    jobname : bool
        Whether the command's first positional argument is a solver job name,
        which the module layer injects from the consumed artifact.
    requires : str or None
        The artifact kind the command consumes, or ``None`` for the mesh/utility
        commands that consume no ACE3P solution.
    default_jobname : str or None
        The producing solver's default results directory, used when nothing
        else supplies a jobname.
    output_file : str or None
        The file the command writes, as a template over ``{jobname}``. ``None``
        for commands that write only to stdout / ``acdtool.log``, or whose output
        is a mesh or ``.mod`` file this wrapper does not read.
    reader : str or None
        Which reader :meth:`Acdtool.load_output` uses on that output —
        :data:`RFPOST`, :data:`SIGNAL`, or ``None`` when the output is not read
        here. A ``transwake`` result is deliberately ``None``: it lands in
        ``wakefield.out``, which the producing T3P module owns.
    mutates : str or None
        An artifact kind whose *already-parsed* output this command overwrites in
        place. ``postprocess transwake`` / ``wake_new`` / ``wake_direct`` rewrite
        ``<jobname>/OUTPUT/wakefield.out``, which T3P has already parsed by the
        time acdtool runs — see :class:`~lume_ace3p.modules.AcdtoolModule`.
    parallel : bool
        Whether the command may run on more than one rank.
    dispatch : bool
        Whether :meth:`Acdtool.run` can invoke it at all.
    wired : bool
        Whether :class:`~lume_ace3p.modules.AcdtoolModule` accepts it as a
        workflow step. A dispatchable-but-unwired command is invocable directly
        but has no module-layer home yet.
    note : str
        Why an undispatchable or unwired command is held back. Surfaced in the
        error message, so the reason reaches the user rather than this file only.
    """

    def __init__(self, form, nargs=(0, 0), jobname=False, requires=None,
                 default_jobname=None, output_file=None, reader=None,
                 mutates=None, parallel=False, dispatch=True, wired=False,
                 note=''):
        self.form = form
        self.nargs = nargs
        self.jobname = jobname
        self.requires = requires
        self.default_jobname = default_jobname
        self.output_file = output_file
        self.reader = reader
        self.mutates = mutates
        self.parallel = parallel
        self.dispatch = dispatch
        self.wired = wired
        self.note = note

    @property
    def parses(self):
        """Whether :meth:`Acdtool.load_output` can read this command's output."""
        return self.reader is not None

    def resolve_output(self, jobname):
        """The command's output path relative to the workdir, or ``None``."""
        if self.output_file is None:
            return None
        return self.output_file.format(jobname=jobname or '')


_MESH_PRODUCER_NOTE = (
    'producing a mesh would make acdtool a second mesh producer, which the '
    "one-producer-per-artifact rule forbids; meshconvert also already lives in "
    'lume_ace3p.cubit')

COMMANDS = {
    # ---- top level ------------------------------------------------------- #
    'meshconvert': Command(
        POSITIONAL, nargs=(1, 2), note=_MESH_PRODUCER_NOTE),
    'meshconvertdirect': Command(
        POSITIONAL, nargs=(1, 2), note=_MESH_PRODUCER_NOTE),
    'resource': Command(
        POSITIONAL, nargs=(1, 1),
        note='writes a suggested batch script to stdout / acdtool.log only'),
    # ---- mesh ------------------------------------------------------------ #
    'mesh stats': Command(
        POSITIONAL, nargs=(1, 1),
        note='run internally by meshconvert; writes to stdout / acdtool.log'),
    'mesh check': Command(
        POSITIONAL, nargs=(1, 1),
        note='run internally by meshconvert; writes to stdout / acdtool.log'),
    'mesh fix': Command(
        POSITIONAL, nargs=(2, 2), note=_MESH_PRODUCER_NOTE),
    'mesh deform': Command(
        POSITIONAL, nargs=(3, 3),
        note='invocable, but wiring it as a mesh producer needs per-instance '
             'artifact identity (two meshes in one workflow); TEM3P\'s own '
             'MeshDump: {MeshDeformScale, EMMeshInputDir} writes the deformed '
             'vacuum mesh directly and is the better route'),
    'mesh warpsurface': Command(
        POSITIONAL, nargs=(1, 1),
        note='its warp.in input is a third dialect (flat "Key: value", no '
             'braces) and its output feeds the Warp plasma code, not ACE3P'),
    # ---- postprocess ----------------------------------------------------- #
    'postprocess rf': Command(
        INPUT, requires=EM_SOLUTION, output_file='rfpost.out', reader=RFPOST,
        parallel=True, wired=True),
    'postprocess eigentomode': Command(
        POSITIONAL, jobname=True, requires=EM_SOLUTION,
        default_jobname='omega3p_results',
        note='writes ParaView .mod files, which nothing in this package reads; '
             'omega3p/s3p do the conversion themselves by default'),
    'postprocess volmontomode': Command(
        POSITIONAL, jobname=True, requires=TD_SOLUTION,
        default_jobname='t3p_results', parallel=True, wired=True),
    'postprocess wake_new': Command(
        POSITIONAL, nargs=(2, 2), jobname=True, requires=TD_SOLUTION,
        default_jobname='t3p_results',
        output_file='{jobname}/OUTPUT/wakefield.out', mutates=TD_SOLUTION,
        note='the longitudinal counterpart of transwake, and the most likely '
             'next addition; unwired only for lack of a fixture'),
    'postprocess wake_direct': Command(
        POSITIONAL, nargs=(2, 2), jobname=True, requires=TD_SOLUTION,
        default_jobname='t3p_results',
        output_file='{jobname}/OUTPUT/wakefield.out', mutates=TD_SOLUTION,
        note='as wake_new, by direct integration rather than the Laplace solve'),
    'postprocess transwake': Command(
        POSITIONAL, nargs=(4, 4), jobname=True, requires=TD_SOLUTION,
        default_jobname='t3p_results',
        output_file='{jobname}/OUTPUT/wakefield.out', mutates=TD_SOLUTION,
        wired=True),
    'postprocess coaxsignal': Command(
        POSITIONAL, jobname=True, requires=TD_SOLUTION,
        default_jobname='t3p_results',
        output_file='{jobname}/OUTPUT/signal.out', reader=SIGNAL, wired=True),
    'postprocess pic3pstats': Command(
        POSITIONAL, nargs=(2, 2),
        note='no PIC3P module exists to hang it on (out of scope)'),
    'postprocess pic3pconvert': Command(
        POSITIONAL, nargs=(1, 1),
        note='no PIC3P module exists to hang it on (out of scope)'),
    'postprocess track3p': Command(
        INPUT_JOBNAME, jobname=True, requires=TRACK3P_PARTICLES,
        default_jobname='track3p_results', output_file='{jobname}/en',
        dispatch=False,
        note="its .acdtool input is the KVC ':' dialect, which this wrapper "
             'does not parse (lume_ace3p.ace3p.parse_ace3p reads it); Track3P\'s '
             'own Postprocess: {EnhancementCounter} container already covers '
             'everything but selected-particle Trajectory extraction'),
    'postprocess project': Command(
        POSITIONAL, nargs=(1, 2),
        note='TEM3P is out of scope, and the L2 projections go to stdout / '
             'acdtool.log rather than a structured file'),
}

# Extension -> the command inferred from it when none is given explicitly. Only
# '.rfpost' is inferable; every other command names its files positionally.
_INFERRED = {'.rfpost': 'postprocess rf'}

# Extensions that identify a command we cannot dispatch, so the error can name
# it instead of just reporting "unknown".
_DIALECT_HINT = {'.acdtool': 'postprocess track3p'}


def known_commands():
    """The acdtool commands in table order — the full 19-command surface."""
    return list(COMMANDS)


def dispatchable_commands():
    """The commands :meth:`Acdtool.run` can invoke."""
    return [name for name, spec in COMMANDS.items() if spec.dispatch]


def wired_commands():
    """The commands :class:`~lume_ace3p.modules.AcdtoolModule` accepts."""
    return [name for name, spec in COMMANDS.items() if spec.wired]


def resolve_command(command):
    """Return the :class:`Command` for ``command``, raising a clear error that
    lists the known commands when it is not one of them."""
    spec = COMMANDS.get(command)
    if spec is None:
        raise ValueError(
            "Unknown acdtool command '" + str(command) + "'. Known commands: "
            + str(known_commands()) + '.')
    return spec


# --------------------------------------------------------------------------- #
# Output section table — one row per '.rfpost' block
# --------------------------------------------------------------------------- #

# The output shapes. See the module docstring for which block takes which.
MODE_TABLE = 'mode_table'    # a ModeID-headed column table inside rfpost.out
SURFACE = 'surface'          # 'surfaceID: n' followed by scalar assignments
POINT = 'point'              # scalar assignments, no index axis at all
RUN = 'run'                  # run-level scalars: '[scaling]'
CURVE = 'curve'              # a '#'-commented column table in its own file
GRID = 'grid'                # a field map in its own file(s); not parsed here
CONFIG = 'config'            # 'RFField': configuration, emits no output


class Section:
    """One ``.rfpost`` block's *output*: its shape and where it lands.

    Attributes
    ----------
    shape : str
        One of the six shape constants above.
    files : tuple of str
        Glob patterns for the files a :data:`CURVE` / :data:`GRID` block writes,
        formatted over the block's own input keys (``{filename}``,
        ``{scanfilename}``). **The schemes are block-specific and cannot be
        inferred from one another**: ``FieldOnLine`` splits E and B into
        ``<filename>.e``/``.b`` (plus complex ``.ec``/``.bc``) with no mode
        suffix, while ``ALLFieldOnLine`` writes ``<filename>_<modeID>`` carrying
        E, B and ``Sz`` together. ``FieldMap`` has no ``filename`` key at all and
        writes fixed names; ``OpenPMD_IMPACT`` writes HDF5.
    validated : bool
        Whether a real acdtool output for this block exists in
        ``tests/fixtures/acdtool/``. Where this is ``False`` the reader is driven
        entirely by the file's own header / assignments, because there is no
        ground truth to check an assumed layout against.
    note : str
        Anything a reader or a reader's caller has to know about this block.
    """

    def __init__(self, shape, files=(), validated=False, note=''):
        self.shape = shape
        self.files = tuple(files)
        self.validated = validated
        self.note = note

    def filenames(self, block):
        """Expand :attr:`files` over the input `block`'s keys.

        Patterns naming a key the block does not set are dropped rather than
        raising — an older build's block may not carry every key.
        """
        patterns = []
        for pattern in self.files:
            try:
                patterns.append(pattern.format(**block))
            except (KeyError, IndexError):
                continue
        return patterns


_UNSCALED = ('fields come straight from the eigenmode, normalized to total '
             'stored energy, not scaled to RFField gradient')

SECTIONS = {
    # ---- configuration --------------------------------------------------- #
    'RFField': Section(CONFIG, note='required first block; emits no output'),
    # ---- mode-indexed tables -> rfpost.out -------------------------------- #
    # The blocks carrying modeID1/modeID2. modeID1 = -1 means mode 0 and
    # modeID2 = -1 means every mode the solver produced, so the tutorial's
    # '-1 / -1' default already means "all modes".
    'RoverQ': Section(MODE_TABLE, validated=True),
    'RoverQT': Section(MODE_TABLE),
    'RoverQRoverQT': Section(MODE_TABLE,
                             note='documented in the reference body but absent '
                                  'from its own functionality list'),
    'kickFactor': Section(MODE_TABLE),
    'pointRoverQ': Section(MODE_TABLE),
    'dFSlater': Section(MODE_TABLE),
    'VFFT': Section(MODE_TABLE,
                    note="printGroup = nterm groups the output by multipole "
                         'component rather than by mode, which is not a '
                         'mode-indexed table; only printGroup = ModeID is read'),
    'ALLFieldAtPoint': Section(MODE_TABLE),
    'coaxPort': Section(MODE_TABLE,
                        note='absent from the reference; ships in the tutorial '
                             'template only'),
    # ---- surface-indexed scalars -> rfpost.out ---------------------------- #
    'maxFieldsOnSurface': Section(SURFACE),
    'powerThroughSurface': Section(
        SURFACE, note='the power is complex [W], the real part being the '
                      'average flow from the complex Poynting vector'),
    # ---- single-mode scalars -> rfpost.out -------------------------------- #
    'FieldAtPoint': Section(
        POINT, note='no modeID1/modeID2: evaluates only the ModeID named in '
                    'RFField, so it has no index axis'),
    # ---- column curves -> separate files ---------------------------------- #
    'FieldOnLine': Section(
        CURVE, files=('{filename}.e', '{filename}.b',
                      '{filename}.ec', '{filename}.bc'),
        note='E and B split across two files, no mode suffix; fields are '
             'scaled to RFField gradient'),
    'ALLFieldOnLine': Section(
        CURVE, files=('{filename}_*',), validated=True,
        note='one file per mode carrying E, B and Sz together, plus .ec/.bc; '
             + _UNSCALED),
    'Multipole': Section(CURVE, files=('{filename}',)),
    'GBZFFT': Section(CURVE, files=('{filename}',)),
    'Track': Section(CURVE, files=('{filename}',),
                     note='absent from the reference; tutorial template only'),
    'TrackScan': Section(CURVE, files=('{filename}', '{scanfilename}'),
                         note='absent from the reference; tutorial template '
                              'only'),
    # ---- grids -> separate files, recorded but not parsed ------------------ #
    'FieldMap': Section(GRID, files=('Efield-map.dat', 'Bfield-map.dat'),
                        note='fixed filenames; the block has no filename key'),
    'IMPACTMap': Section(GRID, files=('EBfield-map-{filename}.dat',),
                         note='IMPACT format'),
    'OpenPMD_IMPACT': Section(GRID, files=('E_Real.h5', 'E_Imag.h5',
                                           'B_Real.h5', 'B_Imag.h5'),
                              note='HDF5, not text'),
    'fieldOnSurface': Section(GRID, files=('{filename}',)),
    'fieldOn2DBoundary': Section(GRID, files=('{filename}',)),
    # ---- run level -------------------------------------------------------- #
    # Emitted by every run and declared by no input block, which is why
    # load_output reads it outside the ionoff loop.
    'scaling': Section(RUN, validated=True),
}

# The '[scaling]' section name, spelled once.
SCALING = 'scaling'


class AcdtoolOutputWarning(UserWarning):
    """Warned when an enabled block's acdtool output cannot be read.

    A whole postprocessing run should not die because one enabled block prints
    in a shape no reader knows, but neither should that block silently vanish
    from ``output_data`` — which is what the old ``print``-and-continue did.
    """


# --------------------------------------------------------------------------- #
# Shape readers. Module-level so they are testable without an Acdtool instance,
# the way lume_ace3p.ace3p.parse_wakefield is.
# --------------------------------------------------------------------------- #

_NUMBER = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
_PAREN = re.compile(r'\(([^()]*)\)')
_ASSIGNMENT = re.compile(r'([A-Za-z]\w*)\s*=\s*(\([^()]*\)|[-+]?[.\d][^\s,)]*)')
_AT = re.compile(r'\bat\b')


def _floats(text):
    """Every float-like token in `text`, in order."""
    return [float(match.group()) for match in _NUMBER.finditer(text)]


def _number(token):
    """`token` as a float, tolerating the trailing commas the column tables use
    (``-1.0029e+01,``). Non-numeric tokens come back as stripped strings."""
    text = token.strip().rstrip(',')
    try:
        return float(text)
    except ValueError:
        return text


def _truthy(value):
    """Whether an ``ionoff``-style flag is on. Replaces an ``eval()`` on solver
    input: the values are ``0``/``1``, and nothing here should execute them."""
    if value is None:
        return False
    text = str(value).strip().strip('{}').strip()
    if not text:
        return False
    try:
        return float(text) != 0.0
    except ValueError:
        return text.lower() in ('on', 'true', 'yes')


def _column_name(token):
    """Normalize a column-table header token to a stable key.

    ``'V_r,'`` -> ``V_r`` (the header punctuates a complex pair with a comma),
    ``'|V|'`` -> ``absV``, ``'RoQ(ohm/cavity)'`` -> ``RoQ`` (a parenthesized
    unit is not part of the name). These three rewrites are what make a
    header-driven reader produce the same keys the hand-indexed one did.
    """
    name = token.strip().strip(',')
    if '(' in name:
        name = name[:name.index('(')]
    if len(name) > 2 and name.startswith('|') and name.endswith('|'):
        name = 'abs' + name[1:-1]
    return name


def _is_echo_start(lines, index):
    """Whether `lines[index]` starts an *input echo* block — a bare block name
    on its own line followed by ``{``.

    One of the three bounds :func:`split_output_sections` uses, and the only one
    that terminates the unclosed ``[scaling]`` the S3P output ships.
    """
    text = lines[index].strip()
    if not text or '=' in text or ':' in text or text[0] in '{}#[*/':
        return False
    if len(text.split()) != 1:
        return False
    following = lines[index + 1].strip() if index + 1 < len(lines) else ''
    return following.startswith('{')


def split_output_sections(lines):
    """Split an ``rfpost.out`` into ``{section: body_lines}``.

    A section starts at a line beginning ``[name]`` and ends at whichever comes
    first: a ``}`` in column 0, the next ``[name]`` header, or the start of an
    input-echo block. **The last two bounds are the fix for defect 3** — in
    ``examples/s3p/window`` the ``[scaling]`` block ships with no closing brace
    at all, so ``}``-only detection ran on into the ``ALLFieldOnLine`` echo that
    follows and swallowed it.

    Indented ``[...]`` text inside a block (``  [z0 = 0.00000]`` in the RFField
    echo) is not a header: only column 0 counts.
    """
    starts = [(i, line[1:line.index(']')])
              for i, line in enumerate(lines)
              if line.startswith('[') and ']' in line]
    sections = {}
    for position, (index, name) in enumerate(starts):
        limit = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        body = []
        for i in range(index + 1, limit):
            if lines[i].startswith('}') or _is_echo_start(lines, i):
                break
            body.append(lines[i])
        sections[name] = body
    return sections


def read_mode_table(body, section='?'):
    """Read a ``ModeID``-headed column table into
    ``{mode_id: {column: value}, 'ModeIDs': [...]}``.

    Column names come from the header row rather than from a per-section list of
    hand-counted positions, which is what lets one reader cover all nine
    mode-indexed blocks — and what keeps it working if a build reorders or adds a
    column. Mode IDs stay **strings**, since they are dict keys an output spec
    names literally (``['RoverQ', '0', 'RoQ']``).
    """
    header = None
    rows = []
    for line in body:
        text = line.strip()
        if not text:
            continue
        tokens = text.split()
        if header is None:
            if tokens[0].strip(',') == 'ModeID':
                header = [_column_name(token) for token in tokens[1:]]
            continue
        try:
            int(tokens[0])
        except ValueError:
            continue        # a trailing comment, a blank-ish line, or a re-header
        rows.append(tokens)
    if header is None:
        spec = SECTIONS.get(section)
        warnings.warn(
            "acdtool section '" + section + "' has no 'ModeID' header row, so "
            'its mode-indexed columns cannot be named; nothing was read from it.'
            + ('' if spec is not None and spec.validated else
               ' No real output fixture exists for this section — see '
               'tests/fixtures/acdtool/COVERAGE.md.'),
            AcdtoolOutputWarning, stacklevel=2)
        return {}

    data = {}
    mode_ids = []
    for tokens in rows:
        names = list(header)
        values = tokens[1:]
        if len(values) != len(names):
            warnings.warn(
                "acdtool section '" + section + "' has " + str(len(names))
                + ' header column(s) but a row of ' + str(len(values))
                + '; the extra ones are named column<n> and any missing ones '
                'are dropped.', AcdtoolOutputWarning, stacklevel=2)
            names += ['column' + str(i)
                      for i in range(len(names) + 1, len(values) + 1)]
        mode_ids.append(tokens[0])
        data[tokens[0]] = {name: _number(value)
                           for name, value in zip(names, values)}
    data['ModeIDs'] = mode_ids
    return data


def _store_assignment(target, line):
    """Store one ``name = value`` line from a scalar block into `target`.

    Three value forms, all seen in acdtool output:

    * ``Emax = 1.5e6 at (0.1, 0.2, 0.3)`` -> ``Emax`` plus an ``Emax_location``
      ``{x, y, z}`` dict;
    * ``Power = ( 1.0, 2.0)`` -> ``Power`` (real) plus ``Power_imag``, the same
      split :func:`lume_ace3p.ace3p.parse_omega3p_output` gives a complex
      eigenfrequency — ``powerThroughSurface`` is complex-valued;
    * ``ga = 1.6e2`` -> a plain float.
    """
    name, _, rest = line.partition('=')
    name = name.strip()
    if not name:
        return
    rest = rest.strip()
    location = None
    at = _AT.search(rest)
    if at:
        location, rest = rest[at.end():], rest[:at.start()]
    numbers = _floats(rest)
    if '(' in rest and len(numbers) >= 2:
        target[name], target[name + '_imag'] = numbers[0], numbers[1]
    elif len(numbers) == 1:
        target[name] = numbers[0]
    elif numbers:
        target[name] = numbers
    else:
        target[name] = rest.strip() or None
    if location is not None:
        coordinates = _floats(location)
        if len(coordinates) == 3:
            target[name + '_location'] = dict(zip('xyz', coordinates))


def read_surface_scalars(body, section='?'):
    """Read a surface-indexed scalar block into
    ``{surface_id: {name: value}, 'SurfaceIDs': [...]}``.

    Surface IDs stay strings, like mode IDs. The block's own input pins one
    surface (``maxFieldsOnSurface { surfaceID = 6 }``), so a run reports few of
    them — which is why design decision 2 makes ``ModeID`` acdtool's only table
    axis and requires ``at: {surface: n}`` here.

    **Neither surface block has a real-output fixture** (see ``COVERAGE.md``), so
    this reads whatever assignments the file carries rather than assuming line
    offsets; the previous reader took ``Emax`` from a fixed two lines below the
    ``surfaceID``.
    """
    data = {}
    surface_ids = []
    current = None
    for line in body:
        text = line.strip()
        if not text:
            continue
        if text.startswith('surfaceID'):
            _, separator, rest = text.partition(':')
            if not separator:
                _, _, rest = text.partition('=')
            surface_id = rest.strip()
            if surface_id not in data:
                data[surface_id] = {}
                surface_ids.append(surface_id)
            current = data[surface_id]
            continue
        if current is None or '=' not in text or text.startswith('{'):
            continue
        _store_assignment(current, text)
    if not surface_ids:
        warnings.warn(
            "acdtool section '" + section + "' has no 'surfaceID' line, so its "
            'scalars cannot be attributed to a surface; nothing was read from '
            'it. No real output fixture exists for this section — see '
            'tests/fixtures/acdtool/COVERAGE.md.',
            AcdtoolOutputWarning, stacklevel=2)
    data['SurfaceIDs'] = surface_ids
    return data


def read_point_scalars(body, section='?'):
    """Read a block of bare scalar assignments into ``{name: value}``.

    ``FieldAtPoint``'s shape: it carries no ``modeID1``/``modeID2`` and evaluates
    only the single mode named in ``RFField``, so it has no index axis to key on
    — distinct from the mode-indexed ``ALLFieldAtPoint``.
    """
    data = {}
    for line in body:
        text = line.strip()
        if not text or text.startswith('{') or '=' not in text:
            continue
        _store_assignment(data, text)
    if not data:
        warnings.warn(
            "acdtool section '" + section + "' carried no 'name = value' lines; "
            'nothing was read from it.', AcdtoolOutputWarning, stacklevel=2)
    return data


def read_scaling(body):
    """Read the ``[scaling]`` block, which every ``postprocess rf`` run emits and
    no input block declares.

    Two variants, selected by ``RFField``'s ``gradient``:

    * **gradient-normalized** — ``V`` (complex, normalized field), ``ga``;
    * **point-scaled**, when ``gradient = -1`` means "no scaling" —
      ``Ez_from_O3P`` (complex) and ``Ez_scaled_to``.

    Both report ``m_factor``, the normalized-to-physical field conversion, which
    nothing else in ACE3P's output provides. It is also what reconciles the two
    curve-block scalings: ``FieldOnLine`` output is gradient-scaled while
    ``ALLFieldOnLine`` output carries the raw eigenmode normalization.

    ``Variant`` records which form was read.
    """
    data = {}
    for line in body:
        text = line.strip()
        if not text or text[0] in '{}':
            continue
        if text.startswith('ModeID'):
            _, _, rest = text.partition(':')
            data['ModeID'] = rest.strip() or None
            continue
        if 'm_factor' in text:
            value, _, amplitude = text.partition('amplitude/phase_deg')
            group = _PAREN.search(value.partition('=')[2])
            numbers = _floats(group.group(1)) if group else []
            if len(numbers) >= 2:
                data['m_factor'], data['m_factor_imag'] = numbers[0], numbers[1]
            group = _PAREN.search(amplitude)
            numbers = _floats(group.group(1)) if group else []
            if len(numbers) >= 2:
                data['m_factor_amplitude'] = numbers[0]
                data['m_factor_phase_deg'] = numbers[1]
            continue
        if text.startswith('Ez from O3P'):
            numbers = _floats(text.partition('=')[2])
            if numbers:
                data['Ez_from_O3P'] = numbers[0]
            if len(numbers) > 1:
                data['Ez_from_O3P_imag'] = numbers[1]
            continue
        if text.startswith('Ez scaled to'):
            numbers = _floats(text.partition('=')[2])
            if numbers:
                data['Ez_scaled_to'] = numbers[0]
            continue
        # Whatever is left is one or more 'name = value' pairs on one line: the
        # 'Integral:' bounds (x0/y0/gz1/gz2), the 'Field scaled at:' point
        # (x0/y0/z0), 'V' and 'ga'.
        for name, value in _ASSIGNMENT.findall(text):
            numbers = _floats(value)
            if not numbers:
                continue
            data[name] = numbers[0]
            if value.startswith('(') and len(numbers) > 1:
                data[name + '_imag'] = numbers[1]
    if 'ga' in data:
        data['Variant'] = 'gradient'
    elif 'Ez_scaled_to' in data:
        data['Variant'] = 'point'
    else:
        data['Variant'] = 'unknown'
    return data


def field_sections(output_data):
    """The curve/grid/signal subset of an ``output_data`` dict.

    These are the field artifacts — per-position column tables and field maps —
    which stay out of the flat result table (design decision 4).
    :meth:`lume_ace3p.modules.AcdtoolModule.field` returns these plus
    :func:`mode_table_arrays`.
    """
    fields = {}
    for key, value in output_data.items():
        section = SECTIONS.get(key)
        if key == SIGNAL or (section is not None
                             and section.shape in (CURVE, GRID)):
            fields[key] = value
    return fields


def mode_ids(section_data):
    """A mode-indexed section's IDs, as ints where they all parse.

    The parsed sections key their modes by the *string* ID (an output spec names
    it literally), but the ``ModeID`` axis a result table is indexed on is
    numeric — the same ``arange``-like column
    :func:`lume_ace3p.ace3p.parse_omega3p_output` produces for the solver's own
    mode list.
    """
    ids = list(section_data.get('ModeIDs', []))
    try:
        return [int(i) for i in ids]
    except (TypeError, ValueError):
        return ids


def table_mode_ids(output_data):
    """The ``ModeID`` axis an acdtool result is indexed by, or ``[]``.

    The first mode-indexed section present, in the order the input file declared
    its blocks. One acdtool result carries at most one mode axis (design decision
    2 of ``plans/acdtool_rework_plan.md``), and every mode-indexed block of a run
    reports the modes of the same solve, so "first" is a choice of spelling
    rather than of data.
    """
    for key, value in output_data.items():
        section = SECTIONS.get(key)
        if (section is not None and section.shape == MODE_TABLE
                and value.get('ModeIDs')):
            return mode_ids(value)
    return []


def mode_table_arrays(output_data):
    """The mode-indexed sections as index-aligned arrays,
    ``{section: {'ModeID': array, column: array, ...}}``.

    The array-per-column view of :func:`read_mode_table`'s dict-per-mode one:
    the shape :meth:`lume_ace3p.modules.AcdtoolModule.extract` returns for a
    mapping spec with no ``at:``, and the shape that rides in a field artifact
    when acdtool's mode axis is *not* the result table's axis (an
    ``s3p -> acdtool`` chain indexes on S3P's ``Frequency``, first in DAG order).

    A column missing from a row comes through as ``NaN`` rather than dropping the
    row — the mode-indexed blocks report a common column set, but a build that
    adds one is read from the header row and so may not fill it for every mode.
    """
    arrays = {}
    for key, value in output_data.items():
        section = SECTIONS.get(key)
        if section is None or section.shape != MODE_TABLE:
            continue
        ids = [str(i) for i in value.get('ModeIDs', [])]
        if not ids:
            continue
        columns = {'ModeID': np.asarray(mode_ids(value))}
        for name in value[ids[0]]:
            columns[name] = np.array([value[i].get(name, float('nan'))
                                      for i in ids])
        arrays[key] = columns
    return arrays


# --------------------------------------------------------------------------- #
# Brace-aware helpers for the '=' dialect
# --------------------------------------------------------------------------- #


def _depth(text):
    """Net brace depth of ``text`` — positive when it opens more than it closes."""
    return text.count('{') - text.count('}')


def _balance(value):
    """Return ``value`` with any unclosed braces closed and newlines flattened.

    A stored value is always brace-balanced and single-line, which is what makes
    :meth:`Acdtool.write_input` structurally safe by construction rather than by
    a check at write time. Whitespace inside an already-valid value is left
    alone, so the empty-list form the tutorial ships (``portID = {  }``)
    round-trips byte-for-byte. The repairs are only reachable from a malformed
    input file (an unterminated list at EOF) or from code that assigned
    ``input_data`` directly.
    """
    if '\n' in value or '\r' in value:
        value = ' '.join(value.split())
    missing = _depth(value)
    if missing > 0:
        value = value + ' ' + '}' * missing
    return value


class Acdtool(CommandWrapper):
    """Wrapper over one ``acdtool`` invocation.

    ``acdtool_command`` names the command; when it is omitted it is inferred from
    the input file's extension (``.rfpost`` -> ``postprocess rf``), which is what
    keeps configs written before the command surface was opened up working
    untouched. The name is prefixed because :class:`lume.base.CommandWrapper`
    already takes an unrelated ``command`` keyword.

    Commands whose argument form is positional read no input file at all, so no
    default ``.rfpost`` is fabricated for them.
    """

    def __init__(self, *args, ace3p_path=None, mpi_caller=None,
                 acdtool_command=None, acdtool_args=None, jobname=None,
                 acdtool_tasks=None, acdtool_cores=None, acdtool_opts='',
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.ACE3P_PATH = ace3p_path if ace3p_path is not None else os.environ.get('ACE3P_PATH', '')
        self.MPI_CALLER = mpi_caller if mpi_caller is not None else os.environ.get('MPI_CALLER', '')
        self.acdtool_command = acdtool_command
        self.acdtool_args = list(acdtool_args) if acdtool_args else []
        self.jobname = jobname
        self.acdtool_tasks = acdtool_tasks
        self.acdtool_cores = acdtool_cores
        self.acdtool_opts = acdtool_opts or ''
        # Always defined, on every dispatch path: a command that fails must
        # report a missing output rather than raise AttributeError from a later
        # load_output().
        self.output_file = None
        self.output_data = {}
        # The command last dispatched, so load_output knows which reader to use
        # without re-deriving it. Resolved lazily when nothing has run yet.
        self._spec = None
        # Validate an explicit command at construction — a typo should not wait
        # until the subprocess would have been launched to surface.
        spec = resolve_command(acdtool_command) if acdtool_command else None
        if self.workdir is None:
            self.workdir = os.getcwd()
        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)
        if spec is not None and spec.form == POSITIONAL:
            # Positional commands name their operands on the command line; there
            # is no input file to stage, parse or write.
            self.original_input_file = None
            self.input_data = {}
            return
        self.original_input_file = self.input_file
        if self.input_file is None:
            # make_default_input builds input_data in memory; there is no file to
            # stage or parse until write_input runs.
            self.make_default_input()
            return
        if not os.path.isfile(os.path.join(self.workdir, self.input_file)):
            shutil.copy(self.input_file, self.workdir)
        self.load_input_file()

    # ---- command resolution ---------------------------------------------- #

    def resolve(self, command=None):
        """Return ``(name, spec)`` for the command this run should invoke.

        Resolution order: the explicit argument, then the instance's
        ``acdtool_command``, then inference from the input file's extension.
        Raises :class:`ValueError` naming the command when it cannot be
        dispatched — which is the fix for a ``.acdtool`` input silently parsing
        to empty blocks and then running nothing at all.
        """
        name = command or self.acdtool_command
        if name is None:
            name = self._infer_command()
        spec = resolve_command(name)
        if not spec.dispatch:
            raise ValueError(
                "acdtool command '" + name + "' is not supported: "
                + spec.note + '.')
        return name, spec

    def _infer_command(self):
        extension = os.path.splitext(self.input_file or '')[1].lower()
        if extension in _INFERRED:
            return _INFERRED[extension]
        hint = _DIALECT_HINT.get(extension)
        detail = (" That extension is the input to '" + hint + "', which is not "
                  'supported: ' + COMMANDS[hint].note + '.') if hint else ''
        raise ValueError(
            "Cannot infer an acdtool command from input file '"
            + str(self.input_file) + "'." + detail
            + " Only " + str(sorted(_INFERRED)) + " is inferred from an "
            "extension; pass 'command' explicitly for every other acdtool "
            'command. Dispatchable commands: '
            + str(dispatchable_commands()) + '.')

    # ---- run -------------------------------------------------------------- #

    def run(self, *args, **kwargs):
        """Invoke ``acdtool``.

        ``run()`` with no arguments uses the command and arguments given at
        construction, inferring the command from the input file's extension when
        none was set. ``run('postprocess rf')`` keeps the legacy positional form
        working. ``args`` / ``jobname`` override the constructor's values.
        """
        command = kwargs.pop('command', None)
        cmd_args = kwargs.pop('args', None)
        jobname = kwargs.pop('jobname', None)
        if args:
            command = args[0]
        if kwargs:
            raise TypeError('Unexpected keyword arguments to Acdtool.run: '
                            + str(sorted(kwargs)) + '.')
        name, spec = self.resolve(command)
        cmd_args = [str(a) for a in
                    (cmd_args if cmd_args is not None else self.acdtool_args)]
        jobname = jobname or self.jobname or spec.default_jobname

        low, high = spec.nargs
        if not low <= len(cmd_args) <= high:
            expected = str(low) if low == high else f'{low}-{high}'
            raise ValueError(
                "acdtool command '" + name + "' takes " + expected
                + ' argument(s) besides '
                + ('its jobname' if spec.jobname else 'the command name')
                + ', got ' + str(len(cmd_args)) + ': ' + str(cmd_args) + '.')

        operands = []
        if spec.form in (INPUT, INPUT_JOBNAME):
            self.write_input()
            operands.append(self.input_file)
        if spec.jobname:
            operands.append(jobname)
        operands += cmd_args

        self._spec = spec
        self.output_file = spec.resolve_output(jobname)
        subprocess.run(self._command_line(name, operands, spec),
                       shell=True, cwd=self.workdir)
        if spec.parses:
            self.load_output()
        return self.output_data

    def _command_line(self, name, operands, spec):
        """Build the shell command line.

        Follows the :mod:`lume_ace3p.ace3p` convention (``-n <tasks> -c <cores>``
        plus optional caller options) rather than the srun-only
        ``--nodes=/--ntasks=`` this used to hardcode, guards ``--cpu-bind``
        against a non-srun caller the same way, and omits the rank flags
        entirely when there is no MPI caller to consume them.

        Only ``postprocess rf`` and ``postprocess volmontomode`` run in
        parallel, so every other command is pinned to one **rank** regardless of
        configuration, with a warning rather than a silent override. The CPU
        count is not pinned: the tutorial runs the serial ``transwake`` as
        ``srun -n 1 -c 256``, one rank over many threads.
        """
        tasks, cores = self.acdtool_tasks, self.acdtool_cores
        if not spec.parallel and tasks not in (None, 1):
            print("Warning: acdtool command '" + name + "' runs on a single "
                  'rank; ignoring the configured tasks=' + str(tasks) + '. Only '
                  + str([n for n, s in COMMANDS.items() if s.parallel])
                  + ' run in parallel.')
            tasks = None
        opts = self.acdtool_opts
        if opts.startswith('--cpu-bind') and self.MPI_CALLER != 'srun':
            opts = ''
        parts = []
        if self.MPI_CALLER:
            parts += [self.MPI_CALLER, '-n', str(tasks or 1), '-c',
                      str(cores or 1)]
        if opts:
            parts.append(opts)
        parts.append(self.ACE3P_PATH + 'acdtool')
        parts.append(name)
        parts += [str(o) for o in operands]
        return ' '.join(parts)

    # ---- input ------------------------------------------------------------ #

    def load_input_file(self, *args):
        if args:
            self.input_file = args[0]
        self.input_parser()

    def input_parser(self):
        """Parse a ``.rfpost`` file (the ``key = value`` dialect) into
        ``input_data`` as ``{section: {key: value}}``, values kept as strings.

        Unknown sections are carried through untouched — the block set varies by
        acdtool build (the tutorial template has three the reference dropped, the
        reference documents five the template lacks), so enumerating a fixed list
        here would silently discard a newer build's blocks.

        Values are brace-aware: ``coaxPort``'s list keys (``portID`` / ``porta``
        / ``portb``) may span several lines, and those lines are accumulated into
        one balanced single-line value rather than truncated at the opening brace.
        """
        with open(os.path.join(self.workdir, self.input_file)) as file:
            lines = file.readlines()
        self.input_data = {}  #Create base-level dict for base-level keys
        key1 = None
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if len(line) == 0:   #Skip empty lines
                i += 1
                continue
            if line.startswith('{'):   #Create dict for the base entry
                key1 = lines[i-1].strip()
                assert len(key1) > 0   #Check that base entry name in line before '{'
                self.input_data[key1] = {}
                i += 1
                continue
            if line.startswith('}'):   #Set key1 to None when not in a base entry
                key1 = None
                i += 1
                continue
            if key1 is None or '=' not in line:
                i += 1
                continue
            #Within base entry, add key-value pairs to dict
            key2, _, rest = line.partition('=')
            value = rest.split('//')[0].strip()
            i += 1
            if _depth(value) > 0:
                # A '{' opened on the value's own line and not closed there: the
                # value continues over the following lines (the multi-line list
                # form). Consume them until the braces balance.
                parts = [value]
                depth = _depth(value)
                while i < len(lines) and depth > 0:
                    chunk = lines[i].split('//')[0].strip()
                    i += 1
                    depth += _depth(chunk)
                    parts.append(chunk)
                value = ' '.join(part for part in parts if part)
            self.input_data[key1][key2.strip()] = _balance(value)

    def write_input(self, *args):
        """Write ``input_data`` back out as a ``.rfpost`` file.

        Every value is brace-balanced and single-line by construction (see
        :func:`_balance`), so the emitted file is always structurally valid —
        acdtool rejects a file whose braces do not match, and this used to emit
        a truncated ``portID = {`` verbatim.
        """
        if args:
            file = args[0]
        else:
            file = self.input_file
        if (self.original_input_file is not None
                and os.path.isfile(os.path.join(self.workdir, self.input_file))):
            if os.path.samefile(os.path.join(self.workdir, self.input_file), os.path.join(os.getcwd(), self.original_input_file)):
                file = file + '_copy'   #Used to not overwrite original input if in same directory
        self.input_file = file
        lines = []
        for key1 in self.input_data.keys():
            lines.append(key1 + '\n{\n')
            for key2, value2 in self.input_data[key1].items():
                lines.append('   ' + key2 + ' = ' + _balance(str(value2)) + '\n')
            lines.append('}\n\n')
        with open(os.path.join(self.workdir, self.input_file), 'w') as file:
            file.writelines(lines)

    # ---- output ----------------------------------------------------------- #

    def _output_spec(self):
        """The :class:`Command` whose output :meth:`load_output` should read.

        The command last run, or — when nothing has run yet, which is how the
        tests and any caller that stages an output file by hand reach the parser
        — the one the instance would dispatch. ``None`` when neither resolves.
        """
        if self._spec is not None:
            return self._spec
        try:
            return self.resolve()[1]
        except ValueError:
            return None

    def load_output(self, *args):
        """Read the last command's output into ``output_data``.

        Dispatches on the command's :attr:`Command.reader`: ``postprocess rf``
        gets the sectioned ``rfpost.out`` reader, ``postprocess coaxsignal`` the
        headerless ``signal.out`` one. The wake commands have no reader at all —
        they write ``wakefield.out``, which the producing T3P module owns.
        """
        if args:
            self.output_file = args[0]
        if self.output_file is None:
            print('No output file found to load.')
            return
        path = os.path.join(self.workdir, self.output_file)
        if not os.path.isfile(path):
            print("Expected acdtool output '" + self.output_file + "' not found "
                  'under ' + str(self.workdir) + '; the command may have failed.')
            return
        spec = self._output_spec()
        reader = spec.reader if spec is not None else RFPOST
        self.output_data = {}
        if reader == SIGNAL:
            self.output_data[SIGNAL] = parse_column_file(
                path, columns=SIGNAL_COLUMNS)
            return
        if reader != RFPOST:
            print("acdtool output '" + self.output_file + "' is not read here"
                  + (' (' + spec.note + ')' if spec is not None and spec.note
                     else '') + '.')
            return
        self._read_rfpost(path)

    def _read_rfpost(self, path):
        """Read ``rfpost.out`` plus whatever separate files the input declared.

        ``[scaling]`` is read unconditionally: every run emits it and no input
        block declares it, so the ``ionoff`` loop would never look at it — and
        with it would go ``m_factor``, the only normalized-to-physical field
        conversion acdtool reports.
        """
        with open(path) as file:
            lines = file.readlines()
        sections = split_output_sections(lines)
        if SCALING in sections:
            self.output_data[SCALING] = read_scaling(sections[SCALING])
        for key, block in (self.input_data or {}).items():
            if _truthy(block.get('ionoff')):
                self.output_parser(key, sections=sections)

    def output_parser(self, key, sections=None):
        """Read one enabled ``.rfpost`` block's output into ``output_data[key]``.

        The shape comes from :data:`SECTIONS`, so this is a table lookup plus a
        reader call rather than a branch per section. A block whose output cannot
        be read warns naming itself (:class:`AcdtoolOutputWarning`) instead of
        leaving a silent hole in ``output_data``.

        `sections` is the already-split ``rfpost.out`` when a caller has one
        (:meth:`_read_rfpost` splits once for the whole file); it is re-read from
        :attr:`output_file` when not.
        """
        section = SECTIONS.get(key)
        if section is None:
            warnings.warn(
                "Unknown acdtool section '" + str(key) + "': it is not one of "
                'the ' + str(len(SECTIONS)) + ' known blocks, so its output '
                'shape is unknown and nothing was read for it. The block set '
                'varies by acdtool build — the input round-trips regardless.',
                AcdtoolOutputWarning, stacklevel=2)
            return
        if section.shape == CONFIG:
            return
        block = (self.input_data or {}).get(key, {})
        if section.shape in (CURVE, GRID):
            self._read_files(key, section, block)
            return
        if key == 'VFFT' and str(block.get('printGroup', '')).strip() != 'ModeID':
            warnings.warn(
                "acdtool section 'VFFT' is only read with printGroup = ModeID; "
                "this input has printGroup = "
                + str(block.get('printGroup')) + ', which groups the results by '
                'multipole component instead of by mode and is not a '
                'mode-indexed table. Nothing was read for it.',
                AcdtoolOutputWarning, stacklevel=2)
            return
        if sections is None:
            with open(os.path.join(self.workdir, self.output_file)) as file:
                sections = split_output_sections(file.readlines())
        body = sections.get(key)
        if body is None:
            print('Data key \"' + key + '\" not found in output file.')
            return
        if section.shape == MODE_TABLE:
            data = read_mode_table(body, key)
        elif section.shape == SURFACE:
            data = read_surface_scalars(body, key)
        elif section.shape == POINT:
            data = read_point_scalars(body, key)
        else:                                    # RUN — '[scaling]' only
            data = read_scaling(body)
        self.output_data[key] = data

    def _read_files(self, key, section, block):
        """Collect a curve/grid block's separate output files.

        Curve files are parsed into ``{filename: {column: array}}`` by
        :func:`parse_column_file`. Grid files are **recorded, not parsed** —
        ``{'files': [...]}``: two of the five are binary or HDF5, and reading a
        field map is deferred until something in this package needs one.

        The filenames are globbed rather than predicted, because
        ``ALLFieldOnLine``'s per-mode suffix follows the mode count and
        ``modeID2 = -1`` means "every mode the solver produced".
        """
        found = {}
        for pattern in section.filenames(block):
            for path in sorted(glob.glob(os.path.join(self.workdir, pattern))):
                if os.path.isfile(path):
                    found[os.path.relpath(path, self.workdir)] = path
        if not found:
            warnings.warn(
                "acdtool section '" + str(key) + "' is enabled but wrote none of "
                'the files it names (' + str(section.filenames(block) or
                                             list(section.files))
                + ') under ' + str(self.workdir) + '; nothing was read for it.',
                AcdtoolOutputWarning, stacklevel=2)
            return
        if section.shape == GRID:
            self.output_data[key] = {'files': sorted(found)}
            return
        self.output_data[key] = {name: parse_column_file(path)
                                 for name, path in sorted(found.items())}

    def format_data(self):
        return 'Not implemented.'

    def configure(self):
        return 'Not implemented.'

    def archive(self):
        return 'Not implemented.'

    def load_archive(self):
        return 'Not implemented.'

    def plot(self):
        return 'Not implemented.'

    def make_default_input(self):
        """Fabricate the ``.rfpost`` a bare ``acdtool`` module entry implies.

        The **installed tool is the preferred source**: ``acdtool postprocess
        rf`` with no arguments writes a ``sample.rfpost`` template for that
        build, and only that copy is guaranteed to carry the block set the
        installed acdtool understands. The block set genuinely varies — the
        tutorial template ships three blocks the reference dropped and the
        reference documents five the template lacks — so a Python copy of the
        format is a copy of *one* build's format.

        The hardcoded table below is therefore a fallback for when no binary is
        reachable (no ``ACE3P_PATH``, a dry-run environment, the test suite). It
        is a hand-written **2-block subset of a 24-block format**: enough to ask
        for ``[RoverQ]`` off an Omega3P run, and it hardcodes
        ``ResultDir = omega3p_results``, so an S3P chain or any other block needs
        a real ``.rfpost`` on the module's ``input:`` key.
        """
        self.input_file = 'default_input.rfpost'    #Not written to a file until write_input is called
        if self._generate_sample_input():
            return
        self.input_data = {'RFField' : {
                                'ResultDir' : 'omega3p_results',
                                'FreqScanID' : '0',
                                'ModeID' : '0',
                                'xsymmetry' : 'none',
                                'ysymmetry' : 'none',
                                'gradient' : '2.00000e+07',
                                'cavityBeta' : '1.00000',
                                'reversePowerFlow' : '0',
                                'x0' : '0.00000',
                                'y0' : '0.00000',
                                'gz1' : '-0.05700',
                                'gz2' : '0.05700',
                                'npoint' : '300',
                                'fmnx' : '10',
                                'fmny' : '10',
                                'fmnz' : '50'},
                           'RoverQ' : {
                                'ionoff' : '1',
                                'modeID1' : '-1',
                                'modeID2' : '-1',
                                'x1' : '0.00000',
                                'x2' : '0.00100',
                                'y1' : '0.00100',
                                'y2' : '0.00100',
                                'z1' : '1.00000e+10',
                                'z2' : '1.00000e+10'}
                          }

    def _generate_sample_input(self):
        """Ask the installed acdtool for its own ``.rfpost`` template and adopt
        it as this run's ``input_data``. Returns whether that worked.

        ``acdtool postprocess rf`` with **no arguments** writes
        ``sample.rfpost`` into the working directory. This is best-effort by
        design: a missing binary, a build that writes no sample, or a file this
        parser cannot read all fall through to :meth:`make_default_input`'s
        hardcoded table rather than failing the run — which is what keeps a
        machine with no ACE3P environment (and the test suite) working.
        """
        sample = os.path.join(self.workdir, SAMPLE_INPUT)
        try:
            if os.path.exists(sample):
                os.remove(sample)
            subprocess.run(self.ACE3P_PATH + 'acdtool postprocess rf',
                           shell=True, cwd=self.workdir,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return False
        if not os.path.isfile(sample):
            return False
        target, self.input_file = self.input_file, SAMPLE_INPUT
        try:
            self.input_parser()
        except (OSError, AssertionError, ValueError):
            self.input_data = {}
        self.input_file = target
        return bool(self.input_data)
