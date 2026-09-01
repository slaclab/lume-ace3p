import glob
import os, re, shutil
import warnings

import numpy as np

from lume.base import CommandWrapper

from lume_ace3p.logs import run_logged


class Section:
    """An ACE3P input section: ordered list of (name, child) entries.

    A child is either a leaf string or another Section. Same-named siblings
    are stored as separate entries — order and duplicates are preserved
    end-to-end through parse / mutate / write.
    """

    def __init__(self, entries=None):
        self.entries = list(entries) if entries else []

    def append(self, name, value):
        self.entries.append((name, value))

    def children(self, name):
        return [v for k, v in self.entries if k == name]

    def find(self, name, **discriminators):
        """Return the first child Section matching `name` whose own leaves
        match every (key, value) pair in `discriminators`. Returns None if
        nothing matches."""
        for k, v in self.entries:
            if k != name or not isinstance(v, Section):
                continue
            if all(v.get_leaf(dk) == str(dv) for dk, dv in discriminators.items()):
                return v
        return None

    def get_leaf(self, name):
        for k, v in self.entries:
            if k == name and not isinstance(v, Section):
                return v
        return None

    def set_leaf(self, name, value):
        for i, (k, v) in enumerate(self.entries):
            if k == name and not isinstance(v, Section):
                self.entries[i] = (k, str(value))
                return
        self.entries.append((name, str(value)))


def parse_ace3p(text):
    """Parse an .ace3p input string into a Section tree.

    The format is a sequence of `key : value` entries. A value is either a
    free-form string up to end-of-line (commas inside are kept verbatim) or
    a `{ ... }` block containing more entries. `//` starts a line comment.
    """
    tokens = _tokenize(text)
    tree, _ = _parse_section(tokens, 0, top_level=True)
    return tree


def write_ace3p(section, indent=0):
    """Serialize a Section tree back to .ace3p text."""
    pad = '  ' * indent
    out = []
    for name, value in section.entries:
        if isinstance(value, Section):
            out.append(pad + name + ' : {\n')
            out.append(write_ace3p(value, indent + 1))
            out.append(pad + '}\n')
        else:
            out.append(pad + name + ' : ' + str(value) + '\n')
    return ''.join(out)


def _tokenize(text):
    """Strip comments and emit a flat list of tokens: ('key', str),
    ('value', str), ('lbrace',), ('rbrace',). Whitespace and newlines
    are not significant beyond terminating values and comments."""
    # Strip // line comments first
    cleaned = []
    for line in text.split('\n'):
        i = line.find('//')
        cleaned.append(line if i == -1 else line[:i])
    text = '\n'.join(cleaned)

    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == '}':
            tokens.append(('rbrace',))
            i += 1
            continue
        # Read key up to ':'
        j = i
        while j < n and text[j] != ':' and text[j] != '}':
            j += 1
        if j >= n:
            break
        if text[j] == '}':
            # malformed — skip
            i = j
            continue
        key = text[i:j].strip()
        tokens.append(('key', key))
        i = j + 1  # past ':'
        # Skip whitespace on this line first
        while i < n and text[i] in ' \t':
            i += 1
        # A block's '{' may sit on the same line as the key ('Key: {', the
        # S3P/Omega3P style) or on its own line below it ('Key:\n{', the T3P
        # style). Peek across ALL whitespace for it; only commit to the block
        # form if a brace is what we find, otherwise fall through and read the
        # value from where this line left off — so 'Key:' with an empty value
        # followed by another key still parses as an empty leaf.
        k = i
        while k < n and text[k].isspace():
            k += 1
        if k < n and text[k] == '{':
            tokens.append(('lbrace',))
            i = k + 1
            continue
        # Otherwise read value to end of line
        j = i
        while j < n and text[j] != '\n':
            j += 1
        value = text[i:j].strip()
        tokens.append(('value', value))
        i = j
    return tokens


def _parse_section(tokens, idx, top_level=False):
    section = Section()
    while idx < len(tokens):
        tok = tokens[idx]
        if tok[0] == 'rbrace':
            if top_level:
                idx += 1
                continue
            return section, idx + 1
        if tok[0] != 'key':
            idx += 1
            continue
        key = tok[1]
        idx += 1
        if idx >= len(tokens):
            break
        nxt = tokens[idx]
        if nxt[0] == 'lbrace':
            child, idx = _parse_section(tokens, idx + 1, top_level=False)
            section.append(key, child)
        elif nxt[0] == 'value':
            section.append(key, nxt[1])
            idx += 1
        else:
            # Unexpected — skip
            idx += 1
    return section, idx


def merge_overrides(target, overrides):
    """Merge every leaf from `overrides` (a Section) into `target` (also a
    Section), in-place. Same-named siblings are matched positionally — the
    n-th `Port` in overrides updates the n-th `Port` in target, creating
    new siblings as needed."""
    seen = {}
    for name, child in overrides.entries:
        idx = seen.get(name, 0)
        seen[name] = idx + 1
        # Find the idx-th existing same-named entry in target (if any).
        target_idx = -1
        existing = None
        for i, (k, v) in enumerate(target.entries):
            if k == name:
                target_idx += 1
                if target_idx == idx:
                    existing = (i, v)
                    break
        if isinstance(child, Section):
            if existing is not None and isinstance(existing[1], Section):
                merge_overrides(existing[1], child)
            else:
                target.entries.append((name, _clone(child)))
        else:
            value = _format_value(child)
            if existing is not None and not isinstance(existing[1], Section):
                target.entries[existing[0]] = (name, value)
            else:
                target.entries.append((name, value))


def _clone(section):
    out = Section()
    for name, child in section.entries:
        if isinstance(child, Section):
            out.append(name, _clone(child))
        else:
            out.append(name, child)
    return out


def _format_value(value):
    """Format a value for the .ace3p file. Numpy scalars unwrap to their
    Python value, lists/tuples become comma-joined strings, everything else
    is str()."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (list, tuple)):
        return ', '.join(_format_value(v) for v in value)
    return str(value)


def results_path(job_name, results_subdir=''):
    """``<job_name>[/<results_subdir>]`` — a solver's results directory relative
    to its workdir.

    A plain join would render an empty subdirectory as a trailing separator, and
    the resulting ``'omega3p_results/'`` would not compare equal to the
    ``'omega3p_results'`` every test and message uses."""
    return (os.path.join(job_name, results_subdir) if results_subdir
            else job_name)


def input_job_name(text):
    """The top-level ``JobName`` leaf of ``.ace3p`` input `text`, or ``None``.

    The stateless form of :meth:`ACE3P.job_name`'s second rung, for a caller
    holding the input file but no solver — the module layer asking where a
    solver's results *would* be before (or without) running it. Same relationship
    :func:`declared_monitors` has to :meth:`T3P.monitors`, and the same caveat:
    ``JobName`` is undocumented for every solver and unexercised by any shipped
    example, so it is a best-effort fallback rather than the mechanism."""
    return parse_ace3p(text).get_leaf('JobName')


class ACE3P(CommandWrapper):

    module_name = ''

    # The directory each solver writes its results into when nothing overrides
    # it. This default is the *authoritative* path: no solver reference
    # documents a 'JobName' input container (it is set by the batch job
    # submission script, outside the input file), and no shipped tutorial input
    # of any type sets one. See ``job_name`` for the resolution order.
    default_job_name = ''

    # Whether this solver takes its results directory as a *second positional
    # argument* on the command line, which is how a batch script overrides the
    # default. Set per class rather than assumed, because no reference in
    # ``references/`` describes a command line at all (they document input
    # syntax only) — the evidence is the CW23 batch scripts, e.g.
    # ``omega3p DeformedRfGunVacuum.omega3p omega3p_results_deformed`` and
    # ``track3p Pillbox2.3MV.track3p 2.3MV``. T3P is the one solver that leaves
    # this False; see :class:`T3P`.
    accepts_results_dir_arg = False

    # Subdirectory of the job-name directory this solver writes its results
    # into. Empty for every solver but :class:`T3P`, which writes to
    # ``<job_name>/OUTPUT``. A class attribute rather than a ``results_dir``
    # override so a caller holding only the *class* — the module layer asking
    # where a solver's results would be without instantiating one — gets the same
    # answer as the instance.
    results_subdir = ''

    def __init__(self, *args, ace3p_tasks=1, ace3p_cores=1, ace3p_opts='',
                 ace3p_path=None, mpi_caller=None, results_dir=None,
                 log_file=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Where this solver's stdout/stderr is teed (see lume_ace3p.logs); None
        # inherits the parent's streams, which is the legacy behavior.
        self.log_file = log_file
        self.ACE3P_PATH = ace3p_path if ace3p_path is not None else os.environ.get('ACE3P_PATH', '')
        self.MPI_CALLER = mpi_caller if mpi_caller is not None else os.environ.get('MPI_CALLER', '')
        self.ace3p_tasks = ace3p_tasks
        self.ace3p_cores = ace3p_cores
        self.ace3p_opts = ace3p_opts
        self._results_dir = results_dir
        self.output_file = None
        self.output_data = {}
        if self.workdir is None:
            self.workdir = os.getcwd()
        if self.ace3p_opts is None:
            self.ace3p_opts = ''
        if self.ace3p_opts.startswith('--cpu-bind') and self.MPI_CALLER != 'srun':
            self.ace3p_opts = ''
        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)
        if self.input_file is None:
            print('WARNING: no .ace3p input file specified, writing one based on contents of .yaml file. Errors may occur if essential parameters like ModelInfo are not specified in .yaml file.')
            self.make_default_input()
        self.original_input_file = self.input_file
        if not os.path.isfile(os.path.join(self.workdir, self.input_file)):
            shutil.copy(self.input_file, self.workdir)
        with open(self.input_file) as file:
            self.input_data = file.read()
        self._tree = None  # parsed lazily by set_value

    def solver_command(self):
        """The solver invocation, as the shell string :meth:`run` executes.

        A results directory supplied through a module's ``results_dir:`` key is
        appended as the **second positional argument**, the way the CW23 batch
        scripts select one. Without that the key would tell lume-ace3p where to
        *look* while the solver still wrote to its default, so any non-default
        value would leave the reader chasing an empty directory.

        Only an explicit ``results_dir`` is passed. The other two rungs of
        :meth:`job_name`'s resolution order are already known to the solver
        itself — a ``JobName`` leaf because it is in the input file it reads, the
        per-solver default because it *is* the solver's default — so passing
        either would add an argument without changing an outcome. Solvers whose
        :attr:`accepts_results_dir_arg` is False never get the argument.
        """
        command = [self.ACE3P_PATH + self.module_name, self.input_file]
        if self._results_dir and self.accepts_results_dir_arg:
            command.append(self._results_dir)
        return (self.MPI_CALLER + ' -n ' + str(self.ace3p_tasks) + ' -c '
                + str(self.ace3p_cores) + ' ' + self.ace3p_opts + ' '
                + ' '.join(command))

    def run(self):
        self.write_input()
        run_logged(self.solver_command(), cwd=self.workdir,
                   log_file=self.log_file)
        self.output_parser()

    def load_input_file(self, *args):
        if args:
            self.input_file = args[0]
        with open(self.input_file) as file:
            self.input_data = file.read()
        self._tree = None

    def input_parser(self, text):
        """Parse .ace3p text into a Section tree. Kept for backward
        compatibility with any external callers."""
        return parse_ace3p(text)

    def set_value(self, overrides):
        """Merge `overrides` (a Section of leaf updates) into the parsed
        input tree and re-serialize. A no-op when `overrides` is empty —
        which is the fast path for "user provided an .ace3p file and isn't
        sweeping anything inside it": the file is copied to workdir as-is."""
        if overrides is None or not overrides.entries:
            return
        if self._tree is None:
            self._tree = parse_ace3p(self.input_data)
        merge_overrides(self._tree, overrides)
        self.input_data = write_ace3p(self._tree)

    def write_input(self, *args):
        if args:
            file = args[0]
        else:
            file = self.input_file
        if os.path.isfile(os.path.join(self.workdir, self.input_file)):
            if os.path.samefile(os.path.join(self.workdir, self.input_file), os.path.join(os.getcwd(), self.original_input_file)):
                file = file + '_copy'
        self.input_file = file
        with open(os.path.join(self.workdir, file), 'w') as f:
            f.write(self.input_data)

    def make_default_input(self):
        pass

    # ---- output locations ------------------------------------------------- #

    def _input_tree(self):
        """The parsed input tree, parsed on demand.

        ``set_value`` populates ``_tree`` only when it has overrides to merge, so
        a run with no swept ACE3P parameters reaches the parser for the first
        time here."""
        if self._tree is None:
            self._tree = parse_ace3p(self.input_data)
        return self._tree

    def job_name(self):
        """The solver's results directory name, resolved in this order:

        1. the ``results_dir`` argument (set from a module's ``results_dir:``
           YAML key) — **the supported override**, because the directory is
           really chosen by the batch job submission script's job name, outside
           the input file;
        2. a top-level ``JobName`` leaf in the input file — a best-effort
           fallback. Undocumented for every solver and unexercised by any
           shipped example, so it is not presented as the mechanism; it costs
           nothing and may be real;
        3. :attr:`default_job_name`, the authoritative per-solver default.
        """
        if self._results_dir:
            return self._results_dir
        return self._input_tree().get_leaf('JobName') or self.default_job_name

    def results_dir(self):
        """The directory this solver writes results into, relative to the
        workdir. Most solvers write straight into ``<job_name>``; :class:`T3P`
        appends an ``OUTPUT`` subdirectory (see :attr:`results_subdir`)."""
        return results_path(self.job_name(), self.results_subdir)

    def output_parser(self):
        pass

    def load_output(self):
        return 'Not implemented.'

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


class Omega3P(ACE3P):
    """The ACE3P eigensolver.

    A run writes ``<job_name>/omega3p.out`` (see :meth:`ACE3P.job_name`;
    ``omega3p_results`` by default) — which, despite the name, is KVC input
    syntax, so :func:`parse_ace3p` reads it unmodified. Its top-level
    ``Mode`` sections carry the eigensolve results, one per computed mode; those
    are what :meth:`output_parser` turns into index-aligned arrays. Reading them
    here is what makes a mode frequency or Q available *without* an acdtool
    ``RoverQ`` postprocess step.
    """

    module_name = 'omega3p'

    default_job_name = 'omega3p_results'

    # CW23 overrides this on the command line, including to a non-default value:
    # ``omega3p DeformedRfGunVacuum.omega3p omega3p_results_deformed``.
    accepts_results_dir_arg = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_file = 'omega3p.out'

    def make_default_input(self):
        self.input_file = 'omega3p_input_file.omega3p'
        with open(self.input_file, 'w') as f:
            pass

    def output_parser(self):
        """Parse ``<results_dir>/omega3p.out`` into ``output_data``.

        Leaves ``output_data`` empty — rather than raising, following
        :class:`T3P` — when the file is absent (an interrupted or failed run);
        the module layer raises with the resolved path when a workflow actually
        *asks* for a mode quantity."""
        self.output_data = {}
        path = os.path.join(self.workdir, self.results_dir(), self.output_file)
        if not os.path.isfile(path):
            return
        self.output_data = parse_omega3p_output(path)


def _split_pair(value):
    """Return ``(real, imag)`` for a ``'<real> , <imag>'`` value, or ``None``
    when ``value`` is not such a pair.

    A lossy or port eigensolve writes ``Frequency`` and ``TotalEnergy`` this
    way — those are the two seen in practice. The split is detected from the
    *value* rather than from a list of key names, so a future complex-valued
    leaf needs no change here: both halves must parse as floats, which is what
    keeps a comma inside a file path or a mode label from being mistaken for a
    complex eigenvalue."""
    parts = value.split(',')
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def parse_omega3p_output(path):
    """Parse an Omega3P ``omega3p.out`` file into a dict of eigenmode results.

    Returns

    * ``'Modes'`` — one dict per computed mode, in file order, holding that
      mode's own leaves (``Frequency``, ``QualityFactor``, ``TotalEnergy``,
      ``PowerLoss``, ``File``, plus ``ExternalQ`` on a run with a port). This is
      the readable form; it is *not* a table.
    * ``'ModeID'`` plus one array per quantity, all aligned to it — the
      index-aligned form :class:`~lume_ace3p.modules.Omega3PModule` exposes as a
      table axis, the way S3P's arrays align to ``Frequency``.

    A lossy or port run reports complex eigenvalues as ``'<real> , <imag>'``
    pairs. ``Frequency`` keeps the real part, so it stays a plottable table
    column, and the imaginary half lands in ``Frequency_imag`` (same for
    ``TotalEnergy``). An ``_imag`` array appears only when some mode reported a
    pair, and missing entries pad with 0.0 — a real eigenvalue *has* a zero
    imaginary part. ``ExternalQ``, by contrast, is genuinely absent on a run
    with no port, so it pads with NaN.

    ``Mode`` sections are found by name: top-level section order differs between
    runs (the tutorial's ``pillbox`` has ``Mode`` sixth, ``pillbox-rtop+coax``
    second), and the license banner inside ``Version`` is absorbed into the
    first key's name — garbage that is ignored rather than cleaned up.
    """
    with open(path) as file:
        tree = parse_ace3p(file.read())

    modes = []
    for section in tree.children('Mode'):
        if not isinstance(section, Section):
            continue
        mode = {}
        for key, value in section.entries:
            if isinstance(value, Section):
                continue
            pair = _split_pair(value)
            if pair is not None:
                mode[key], mode[key + '_imag'] = pair
                continue
            try:
                mode[key] = float(value)
            except ValueError:
                mode[key] = value.strip()
        modes.append(mode)

    data = {'Modes': modes, 'ModeID': np.arange(len(modes))}
    # Column order follows first appearance across the modes, so it tracks the
    # file rather than an assumed key list.
    keys = []
    for mode in modes:
        keys += [key for key in mode if key not in keys]
    for key in keys:
        # An absent imaginary part is zero; an absent ExternalQ is unknown.
        fill = 0.0 if key.endswith('_imag') else float('nan')
        values = [mode.get(key, fill) for mode in modes]
        data[key] = (np.array(values) if all(isinstance(v, float) for v in values)
                     else values)
    return data


class S3POutputWarning(UserWarning):
    """Raised when an S3P results directory is readable but incomplete — an
    older build that wrote no ``SParameter.out``, or a complex table that does
    not line up with the magnitudes. The magnitudes are still returned, so this
    warns rather than raising (mirroring
    :class:`lume_ace3p.acdtool.AcdtoolOutputWarning`)."""


class S3P(ACE3P):
    """The ACE3P S-parameter solver.

    A run writes its S-parameter magnitudes to ``<job_name>/Reflection.out``
    (``s3p_results`` by default; see :meth:`ACE3P.job_name`). The S3P reference
    documents **no** output files at all, so ``s3p_results`` is the authoritative
    default and a module-level ``results_dir:`` is the supported override; the
    input-tree ``JobName`` lookup :meth:`ACE3P.job_name` also consults is kept
    for symmetry with :class:`T3P` and is unverified against a real run.

    The same silence covers the *formats*: neither ``Reflection.out`` nor its
    siblings ``SParameter.out`` (the same matrix with phase) and
    ``PortRef<n>_<m>.out`` (port mode field profiles) is documented anywhere, so
    the frozen ``s3p/90DegreeBend`` fixtures under
    ``tests/fixtures/acdtool/solver_outputs`` are the only specification of them
    this codebase has. That is why :func:`parse_sparameters` reads the column
    names out of the file's own header row rather than trusting a column order,
    and why the ``abs(S_complex) == S_magnitude`` cross-check in the tests is
    load-bearing — it is the only way to confirm the two files are read
    consistently.
    """

    module_name = 's3p'

    default_job_name = 's3p_results'

    # CW23 passes it: ``s3p FPC-Vacuum.s3p s3p_results``.
    accepts_results_dir_arg = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_file = 's3p.out'

    def output_parser(self):
        """Read the S-parameters and the port mode profiles out of the results
        directory into ``output_data``:

        * ``IndexMap`` — port / mode / type / cutoff per S-matrix index;
        * ``Frequency`` — the scan, which every S-parameter array aligns to;
        * ``S(m,n)`` — the magnitude ``|S|``, from ``Reflection.out``. **Unchanged**
          by Phase 5: this is the key the shipped examples and the frozen
          baselines use, and it is not redefined;
        * ``S(m,n)_real`` / ``S(m,n)_imag`` / ``S(m,n)_phase_deg`` — the complex
          S-parameter from ``SParameter.out``, which this parser used to discard
          entirely. The magnitude keeps its own key rather than becoming complex,
          so every table column stays real-valued and plottable — the same split
          :func:`parse_omega3p_output` gives a complex eigenvalue;
        * one ``{column: array}`` dict per ``PortRef<n>_<m>.out``, keyed by the
          file's stem. These are indexed by *position*, not by frequency, so they
          reach a caller through
          :meth:`lume_ace3p.modules.S3PModule.field` and are never result-table
          columns.

        A missing ``Reflection.out`` still raises — a run that did not write it
        produced nothing. A missing ``SParameter.out`` only warns
        (:class:`S3POutputWarning`): older ACE3P builds do not write one, and the
        magnitudes alone are exactly what this parser returned before.
        """
        self.output_data = {}
        results = os.path.join(self.workdir, self.results_dir())
        index_map, frequency, magnitudes = parse_sparameters(
            os.path.join(results, 'Reflection.out'))
        self.output_data['IndexMap'] = index_map
        self.output_data['Frequency'] = frequency
        self.output_data.update(magnitudes)
        self._read_complex_sparameters(results, frequency)
        self._read_port_profiles(results)

    def _read_complex_sparameters(self, results, frequency):
        """Add ``S(m,n)_real`` / ``_imag`` / ``_phase_deg`` from
        ``SParameter.out``, or warn and leave the magnitudes standing alone."""
        path = os.path.join(results, 'SParameter.out')
        if not os.path.isfile(path):
            warnings.warn(
                "no 'SParameter.out' in " + results + ", so S-parameter phase "
                "is unavailable and only the |S| magnitudes from "
                "'Reflection.out' were read. Older ACE3P builds write no "
                "complex S-parameters.", S3POutputWarning, stacklevel=3)
            return
        _, complex_frequency, columns = parse_sparameters(path)
        if (complex_frequency.shape != np.asarray(frequency).shape
                or not np.allclose(complex_frequency, frequency)):
            warnings.warn(
                "'SParameter.out' covers " + str(len(complex_frequency))
                + " frequencies and 'Reflection.out' " + str(len(frequency))
                + ", so they are not the same scan; the complex S-parameters "
                "were dropped rather than misaligned with the magnitudes.",
                S3POutputWarning, stacklevel=3)
            return
        for name, values in columns.items():
            self.output_data[name + '_real'] = np.real(values)
            self.output_data[name + '_imag'] = np.imag(values)
            self.output_data[name + '_phase_deg'] = np.degrees(np.angle(values))

    def _read_port_profiles(self, results):
        """Add each ``PortRef<n>_<m>.out`` port mode field profile, keyed by the
        file's stem (``PortRef7_0``). Columns ``x y Ex Ey Hx Hy`` under a
        ``%``-commented header — read by :func:`parse_column_file`, the same
        header-driven reader the acdtool curve blocks use."""
        pattern = os.path.join(results, 'PortRef*_*.out')
        for path in sorted(glob.glob(pattern)):
            name = os.path.basename(path)[:-len('.out')]
            self.output_data[name] = parse_column_file(path)

    def make_default_input(self):
        self.input_file = 's3p_input_file.s3p'
        with open(self.input_file, 'w') as f:
            pass


# One '( real,  imag )' cell of SParameter.out. Reflection.out writes the same
# matrix as plain floats, which is the only difference between the two files.
_COMPLEX_CELL = re.compile(r'\(([^(),]*),([^(),]*)\)')


def parse_sparameters(path):
    """Parse an S3P S-parameter table into ``(index_map, frequency, columns)``.

    Both tables S3P writes share one layout — an index-mapping block, a
    ``#Frequency[Hz]`` header naming the columns, then one row per swept
    frequency::

        #Index mapping:
        #          0 : Port 7, Mode 0, Type: TE (cutoff: 6.55719e+09 Hz)
        #Frequency[Hz]          S(0,0)          S(0,1) ...
        9.42400000e+09  3.23077414e-02  2.24462693e-04 ...

    ``Reflection.out`` holds the magnitudes ``|S(m,n)|`` as one plain float per cell;
    ``SParameter.out`` holds the same matrix as ``( real,  imag )`` pairs. The
    cell form is the *only* difference, so one reader covers both: ``columns``
    maps each header name to a real array for the first file and a **complex**
    array for the second.

    Column names come from the header row. That is a weaker assumption than the
    ``id1 * n + id2`` position arithmetic this parser used before — it survives a
    build that reorders or adds a column — but the rebuild stays as a fallback
    for a file whose header names do not line up with its data rows. Neither file
    is documented (see :class:`S3P`), so the layout above comes from the frozen
    ``s3p/90DegreeBend`` fixtures.
    """
    with open(path) as file:
        lines = file.readlines()

    index_map = {}
    names = None
    frequency = []
    rows = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        if text.startswith('#'):
            body = text.lstrip('#').strip()
            if body.startswith('Frequency'):
                names = body.split()[1:]
            elif 'Port' in body and 'Mode' in body:
                index_map.update(_parse_index_map_entry(body))
            continue
        cells = _COMPLEX_CELL.findall(text)
        if cells:
            frequency.append(float(text.split('(', 1)[0]))
            rows.append([complex(float(real), float(imag))
                         for real, imag in cells])
        else:
            values = text.split()
            frequency.append(float(values[0]))
            rows.append([float(value) for value in values[1:]])

    table = np.array(rows).transpose() if rows else np.zeros((0, 0))
    if names is None or len(names) != len(table):
        size = len(index_map)
        names = ['S(' + str(id1) + ',' + str(id2) + ')'
                 for id1 in range(size) for id2 in range(size)]
    columns = {name: table[index] for index, name in enumerate(names)
               if index < len(table)}
    return index_map, np.array(frequency), columns


def _parse_index_map_entry(body):
    """One index-mapping line -> ``{id: {Port, Mode, Type, Cutoff}}``.

    e.g. ``0 : Port 7, Mode 0, Type: TE (cutoff: 6.55719e+09 Hz)``. Driven by
    substring search rather than one regex, so a build that spaces or punctuates
    the line differently still reads — this is undocumented output, and the
    tutorial files already differ in whether the ``Type`` is parenthesized.
    """
    entry = {'Port': body.split('Port')[1].split()[0].strip(','),
             'Mode': body.split('Mode')[1].split()[0].strip(','),
             'Type': body.split('Type:')[1].split()[0].strip('(')}
    if 'cutoff:' in body:
        entry['Cutoff'] = float(body.split('cutoff:')[1].split('Hz')[0].strip())
    return {body.split()[0]: entry}


class T3POutputWarning(UserWarning):
    """Raised when a declared T3P ``Monitor`` produced no readable output.

    A whole run must not die because one monitor of six did not write — the other
    five are still results — but neither may the hole be silent, which is what it
    was before ``plans/t3p_monitor_plan.md``: a run declaring ``Power`` and
    ``ModeVoltage`` monitors wrote those files, LUME-ACE3P read none of them, and
    nothing said so. Mirrors :class:`S3POutputWarning` and
    :class:`lume_ace3p.acdtool.AcdtoolOutputWarning`."""


# --------------------------------------------------------------------------- #
# Monitor table — one row per T3P 'Monitor' Type
# --------------------------------------------------------------------------- #

# The output shapes, mirroring :data:`lume_ace3p.acdtool.SECTIONS`'s so the two
# tables read the same way.
WAKE = 'wake'      # '# Loss factor = ...' header over (s, W, I_bunch)
SERIES = 'series'  # a headerless (t, ...) column table
GRID = 'grid'      # netCDF field dumps; filenames recorded, never parsed

# 'Point: Monitors the fields at a location. The output is the format
# (t Hx Hy Hz Ex Ey Ez) in SI.' -- references/t3p-commands.pdf. The file itself
# carries no header, so these names come from the reference and nowhere else.
POINT_COLUMNS = ('t', 'Hx', 'Hy', 'Hz', 'Ex', 'Ey', 'Ez')

# Bunch0.out's own '## t[sec]    I[A]' header would give the header-driven reader
# column names *with units*, which cannot double as the module layer's 't' index
# axis -- and its other comment line ('## Bunch distribution') is the same two
# tokens wide, so 'last match wins' is the only thing keeping that from becoming
# the names. Both are why this file's columns are named explicitly.
BUNCH_COLUMNS = ('t', 'I')


class Monitor:
    """One T3P ``Monitor`` ``Type``'s *output*: its shape and where it lands.

    Attributes
    ----------
    shape : str
        One of :data:`WAKE`, :data:`SERIES`, :data:`GRID`.
    files : tuple of str
        Glob patterns for the files this monitor writes, formatted over its own
        ``{name}`` — which is both the monitor's ``Name`` and its output filename
        stem. A ``Volume`` monitor's is a *glob* (one file per dump time) where
        every other type writes exactly one file.
    columns : tuple of str or None
        Column names for a :data:`SERIES` monitor, since these files carry **no
        header at all** (the one exception, ``Bunch0.out``, names its columns
        with units attached). ``None`` for the other shapes.
    axis : str or None
        The index axis this monitor's arrays are aligned to — ``'s'`` for the
        wake, ``'t'`` for every series monitor, ``None`` for a grid dump, which
        has no extractable quantity at all.
    validated : bool
        Whether real output for this type exists in
        ``tests/fixtures/acdtool/t3p_outputs/``. Where this is ``False`` there is
        no ground truth, so the reader follows the file's own width rather than an
        assumed layout — the same rule the unenabled ``.rfpost`` blocks get.
    note : str
        Anything a reader or its caller has to know about this type.
    """

    def __init__(self, shape, files=(), columns=None, axis=None,
                 validated=False, note=''):
        self.shape = shape
        self.files = tuple(files)
        self.columns = tuple(columns) if columns else None
        self.axis = axis
        self.validated = validated
        self.note = note

    def filenames(self, name):
        """Expand :attr:`files` over a monitor's ``Name``."""
        return [pattern.format(name=name) for pattern in self.files]


MONITORS = {
    'WakeField': Monitor(WAKE, files=('{name}.out',), axis='s', validated=True,
                         note='the only type read before the multi-monitor '
                              'plan; a run may legitimately declare none'),
    'Point': Monitor(SERIES, files=('{name}.out',), columns=POINT_COLUMNS,
                     axis='t', validated=True,
                     note='fields at one location vs. time, in SI'),
    'Power': Monitor(SERIES, files=('{name}.out',), columns=('t', 'P'),
                     axis='t', validated=True,
                     note='power through a boundary port [W]; a run may declare '
                          'several, addressed by Name'),
    'ModeVoltage': Monitor(SERIES, files=('{name}.out',), columns=('t', 'V'),
                           axis='t', validated=True,
                           note='generalized voltage of a waveguide mode at a '
                                'boundary port [V]'),
    'SurfacePowerLoss': Monitor(SERIES, files=('{name}.out',),
                                columns=('t', 'P'), axis='t',
                                note='documented but UNVALIDATED — no CW23 run '
                                     'declares one; SIBC uses Power on a lossy '
                                     'surface instead'),
    'Volume': Monitor(GRID, files=('{name}ts_t*ps.out', '{name}ts_t*ps.out.mod'),
                      note='netCDF despite the .out extension (verified: leading '
                           'bytes are CDF\\x02) — recorded, never parsed, and it '
                           'provides no extractable quantity'),
}

# Emitted by every run, declared by no monitor. The structural twin of acdtool's
# '[scaling]': read outside the per-monitor loop.
ALWAYS = {'Bunch0': Monitor(SERIES, files=('Bunch0.out',),
                            columns=BUNCH_COLUMNS, axis='t', validated=True,
                            note='the bunch current profile T3P loaded, written '
                                 'by every run and declared by no monitor')}


def monitor_identity(section):
    """``(Type, Name)`` for one ``Monitor`` section.

    A ``WakeField`` monitor with no ``Name`` falls back to ``'wakefield'``, which
    is what T3P names the file; for every other type a missing ``Name`` means
    there is no filename stem to look for, so it stays ``None`` and the caller
    warns."""
    monitor_type = section.get_leaf('Type')
    name = section.get_leaf('Name')
    if not name and monitor_type == 'WakeField':
        name = 'wakefield'
    return monitor_type, name


def tree_monitors(tree):
    """``[(Type, Name)]`` for every ``Monitor`` block of a parsed tree, in file
    order. Used against a ``.t3p`` input, against the ``Input :`` echo inside
    ``t3p.out``, and against input text nothing has instantiated a solver for."""
    return [monitor_identity(section) for section in tree.children('Monitor')
            if isinstance(section, Section)]


def declared_monitors(text):
    """``[(Type, Name)]`` for every ``Monitor`` block in ``.t3p`` input `text`.

    The stateless form of :meth:`T3P.monitors`, for a caller holding the input
    file but no solver — a validation pass before the run, or the module layer
    deciding which index axis a *dry* run should report. This is possible at all
    because T3P declares both its axes in the input file."""
    return tree_monitors(parse_ace3p(text))


def read_monitor(results, monitor_type, name, spec):
    """Read one monitor's output from the `results` directory.

    Returns the monitor's dict — the bare wake result for :data:`WAKE`,
    ``{'Type', <columns...>}`` for :data:`SERIES`, ``{'Type', 'files'}`` for
    :data:`GRID` — or ``None`` after warning when the monitor wrote nothing.

    The wake shape deliberately returns its keys *bare*, with no ``'Type'``
    marker: :meth:`T3P.output_parser` lifts them to the top level of
    ``output_data`` unchanged, which is what makes every existing output spec and
    frozen baseline keep working by construction rather than by a shim.
    """
    if spec.shape == GRID:
        files = []
        for pattern in spec.filenames(name):
            files += [os.path.basename(path) for path in
                      glob.glob(os.path.join(results, pattern))]
        if not files:
            _warn_no_output(results, monitor_type, name, spec)
            return None
        return {'Type': monitor_type, 'files': sorted(files)}

    paths = [os.path.join(results, filename)
             for filename in spec.filenames(name)]
    path = next((path for path in paths if os.path.isfile(path)), None)
    if path is None:
        _warn_no_output(results, monitor_type, name, spec)
        return None
    if spec.shape == WAKE:
        return parse_wakefield(path)
    data = {'Type': monitor_type}
    data.update(parse_column_file(path, columns=spec.columns))
    return data


def _warn_no_output(results, monitor_type, name, spec):
    """One :class:`T3POutputWarning` naming the monitor, the path looked for, and
    whether the type's format is validated by a real fixture."""
    expected = ', '.join(os.path.join(results, filename)
                         for filename in spec.filenames(name))
    warnings.warn(
        "T3P monitor '" + str(name) + "' (Type: " + str(monitor_type) + ") is "
        'declared in the input file but wrote no output; expected ' + expected
        + '.' + ('' if spec.validated else
                 ' No real output fixture exists for this monitor type — see '
                 'tests/fixtures/acdtool/COVERAGE.md.'),
        T3POutputWarning, stacklevel=4)


class T3P(ACE3P):
    """The ACE3P time-domain solver, used for wakefield calculations.

    Structurally the analogue of :class:`S3P`: a run produces one scalar figure
    of merit (the loss factor for a longitudinal wake, the kick factor for a
    transverse one) plus arrays indexed by the wake coordinate ``s``, where S3P
    produces S-parameters indexed by frequency.

    Unlike S3P, T3P's output locations are not fixed: everything lands under
    ``<job_name>/OUTPUT`` (see :meth:`ACE3P.job_name`; ``t3p_results`` by
    default), and each monitor's files are named after that monitor's own
    ``Name``, which is read back out of the input file rather than hardcoded.

    A wake is only one of the six things a run can monitor, though. The input file
    declares any number of ``Monitor`` blocks of six documented ``Type`` values,
    and :data:`MONITORS` says what each writes; :meth:`output_parser` reads them
    all. Two consequences shape the interface:

    * **``Name`` is the selector, ``Type`` supplies the shape.** A run may declare
      several monitors of one type — CW23's ``SIBC`` has three ``Power`` monitors,
      giving input, output and wall-loss power on one run — so ``Type`` cannot
      address one. ``Name`` can, and is also the output filename stem.
    * **A run may have no wake at all.** ``SIBC`` has no ``WakeField`` monitor.
      That was always tolerated; since the multi-monitor plan it is no longer a
      dead end, because such a run's series monitors are read.

    T3P is also the one solver whose results directory lume-ace3p cannot select:
    see :attr:`accepts_results_dir_arg`.
    """

    module_name = 't3p'

    # T3P's default job name — the directory it writes results into when the
    # input file does not set 'JobName' explicitly.
    default_job_name = 't3p_results'

    # False, unlike every other solver here, and deliberately: of the eight
    # distinct T3P invocations in CW23, *none* passes a second positional
    # argument, so nothing establishes that t3p accepts one — and no reference
    # documents a command line to settle it either way. Nor is it clear what the
    # argument would name for this solver, since T3P writes to
    # '<job_name>/OUTPUT' rather than straight into the directory. Passing an
    # unverified argument would risk breaking every T3P run to make a rarely
    # used key work, so 'results_dir:' on a t3p module stays read-only: it tells
    # lume-ace3p where to look, and the run must already be writing there
    # (via a 'JobName' leaf, which t3p.out confirms it honors). Documented as
    # such in docs/yaml_reference.md. Flip this to True given one CW23-style
    # invocation that proves the argument.
    accepts_results_dir_arg = False

    # T3P is the one solver that writes into a subdirectory of its job-name
    # directory: everything lands under '<job_name>/OUTPUT'. See
    # :meth:`ACE3P.job_name` for how the parent is resolved.
    results_subdir = 'OUTPUT'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_file = 't3p.out'

    def make_default_input(self):
        self.input_file = 't3p_input_file.t3p'
        with open(self.input_file, 'w') as f:
            pass

    # ---- output locations, read from the input file ----------------------- #

    def monitors(self):
        """``[(Type, Name)]`` for every ``Monitor`` the input file declares, in
        file order.

        **The input file is the monitor list**, not the run log: it is available
        before the run, so a validation pass can use it, and it is all that exists
        under dry-run. :meth:`echoed_monitors` is the cross-check on what the run
        actually resolved.

        ``Name`` is the selector — ``Type`` alone cannot address a monitor,
        because a run may declare several of one type (CW23's ``SIBC`` has three
        ``Power`` monitors) — and it is also the output filename stem. A monitor
        with no ``Name`` comes back as ``None`` rather than being dropped, so
        :meth:`output_parser` can say so."""
        return tree_monitors(self._input_tree())

    def echoed_monitors(self):
        """``[(Type, Name)]`` from the ``Input :`` echo in ``t3p.out``, or ``None``
        when there is no readable log.

        T3P writes a normalized KVC echo of its whole resolved input into its run
        log, monitors included. It is the record of what the run *used*, so it is
        worth cross-checking — but it is not the primary source: it does not exist
        until the solver has run, and it **normalizes keys** (``BPM.t3p``'s
        ``Start contour`` is ``Startcontour`` in the echo), so only ``(Type,
        Name)`` pairs are comparable."""
        path = os.path.join(self.workdir, self.results_dir(), self.output_file)
        if not os.path.isfile(path):
            return None
        with open(path) as file:
            echo = parse_ace3p(file.read()).find('Input')
        if echo is None:
            return None
        return tree_monitors(echo)

    def wake_monitor_name(self):
        """The ``Name`` of the ``WakeField`` monitor, which is what T3P names its
        wakefield output files after, or ``None`` when the input declares no such
        monitor.

        Declaring none is a legitimate configuration — a pulse-propagation run
        that only monitors power, e.g. CW23's ``SIBC`` — and since the
        multi-monitor plan it is no longer a dead end: the other monitors are read
        too. A thin wrapper over :meth:`monitors`, kept because it is public-ish
        and referenced from :class:`lume_ace3p.modules.T3PModule`."""
        for monitor_type, name in self.monitors():
            if monitor_type == 'WakeField':
                return name
        return None

    # ---- output parsing --------------------------------------------------- #

    def output_parser(self):
        """Read every declared monitor's output into ``output_data``.

        The wake monitor's keys stay at the **top level**, exactly where they were
        before the multi-monitor plan: ``s`` / ``W`` / ``I_bunch`` arrays plus
        ``WakeType`` and either ``LossFactor`` (longitudinal) or ``KickFactor``
        (transverse), with ``TransversePoints`` and ``Offset`` on a transverse
        run. That is load-bearing rather than incidental — it is what makes every
        existing output spec and every frozen baseline keep working by
        construction instead of through a compatibility shim.

        Everything else arrives beside them::

            {'s': ..., 'W': ..., 'I_bunch': ..., 'KickFactor': ..., 'WakeType': ...,
             'Monitors': {'point':      {'Type': 'Point',  't': ..., 'Ex': ..., ...},
                          'inputPower': {'Type': 'Power',  't': ..., 'P': ...},
                          'volume':     {'Type': 'Volume', 'files': [...]}},
             'Bunch0': {'t': ..., 'I': ...}}

        ``Monitors`` is keyed by monitor ``Name``, because that is the only unique
        selector (see :meth:`monitors`). ``Bunch0`` is read outside the loop: every
        run writes it and no monitor declares it, the same way acdtool's
        ``[scaling]`` is read outside its ``ionoff`` loop.

        Leaves ``output_data`` empty — rather than raising, as :class:`S3P` does —
        when nothing was readable at all. A T3P run with no wake monitor is a valid
        run, so that is not an error here; the module layer raises if a workflow
        actually *asks* for a quantity that is missing. A monitor that was declared
        and wrote nothing does warn (:class:`T3POutputWarning`), naming itself."""
        self.output_data = {}
        results = os.path.join(self.workdir, self.results_dir())
        monitors = {}
        # Declared names, not successfully-read ones: two monitors sharing a Name
        # collide whether or not either of them wrote.
        declared_names = set()
        for monitor_type, name in self.monitors():
            spec = MONITORS.get(monitor_type)
            if spec is None:
                warnings.warn(
                    "T3P monitor Type '" + str(monitor_type) + "' is not one of "
                    'the documented types ' + str(sorted(MONITORS))
                    + ', so its output shape is unknown and nothing was read '
                    'from it.', T3POutputWarning, stacklevel=2)
                continue
            if not name:
                warnings.warn(
                    "a T3P monitor of Type '" + str(monitor_type) + "' declares "
                    "no 'Name', which is what T3P names its output file after, so "
                    'there is nothing to look for.', T3POutputWarning,
                    stacklevel=2)
                continue
            if name in declared_names:
                warnings.warn(
                    "two T3P monitors share the Name '" + str(name) + "', so "
                    'they write the same file and only the first was kept. Names '
                    'are the only way to address a monitor.', T3POutputWarning,
                    stacklevel=2)
                continue
            declared_names.add(name)
            entry = read_monitor(results, monitor_type, name, spec)
            if entry is None:
                continue
            if spec.shape == WAKE:
                self.output_data.update(entry)
            else:
                monitors[name] = entry
        if monitors:
            self.output_data['Monitors'] = monitors
        self._read_always(results)
        self._cross_check_echo()

    def _read_always(self, results):
        """Read the files every run writes and no monitor declares.

        Absence is silent here: nothing *declared* these, so there is no
        declaration to contradict, and a results directory that is missing
        wholesale has already warned once per declared monitor."""
        for name, spec in ALWAYS.items():
            path = os.path.join(results, spec.filenames(name)[0])
            if os.path.isfile(path):
                self.output_data[name] = parse_column_file(
                    path, columns=spec.columns)

    def _cross_check_echo(self):
        """Warn when the run log's resolved monitor list disagrees with the input
        file's. Comparing ``(Type, Name)`` pairs only — see
        :meth:`echoed_monitors` for why the keys themselves are not comparable —
        and *unordered*, since which monitor the solver echoes first says nothing
        about what it wrote."""
        echoed = self.echoed_monitors()
        declared = self.monitors()
        if echoed is None or sorted(map(str, echoed)) == sorted(map(str,
                                                                   declared)):
            return
        warnings.warn(
            'the monitor list in ' + self.output_file + ' does not match the '
            'input file: the run resolved ' + str(echoed) + ' where the input '
            'declares ' + str(declared) + '. The input file is what was read; '
            'the log is what the solver actually used.',
            T3POutputWarning, stacklevel=3)


# Header forms written by T3P above the (s, W, I_bunch) columns, e.g.
#   '# Loss factor = -3.88576373282202e-01 V/pC'
#   '# Kick factor = 9.64058337896157e-02 V/pC'
_FACTOR_KEYS = {'loss factor': ('LossFactor', 'longitudinal'),
                'kick factor': ('KickFactor', 'transverse')}


def parse_wakefield(path):
    """Parse a T3P wakefield monitor output file into a dict.

    The file is a ``#``-commented header followed by three whitespace-separated
    columns: ``s[m]``, the wake potential ``W(s)[V/pC]`` (longitudinal or
    transverse depending on the run), and the bunch current ``I_bunch(s)[C/m]``.
    The header carries the run's figure of merit and, for a transverse run, the
    two transverse sampling points and their offset.
    """
    with open(path) as file:
        lines = file.readlines()

    data = {}
    points = []
    for line in lines:
        if not line.startswith('#'):
            continue
        body = line.lstrip('#').strip()
        lowered = body.lower()
        for prefix, (key, wake_type) in _FACTOR_KEYS.items():
            if lowered.startswith(prefix):
                # '<name> = <value> V/pC' -> the value.
                value = body.split('=', 1)[1]
                data[key] = float(value.replace('V/pC', '').strip())
                data['WakeType'] = wake_type
        if lowered.startswith('with offset'):
            data['Offset'] = float(lowered.split('offset', 1)[1]
                                   .replace('m', '').strip())
        if body.startswith('('):
            # Transverse header lists the two sampling points, one per line,
            # as '(x,y)' (the second continues with a trailing 'and').
            for chunk in body.split('and'):
                chunk = chunk.strip().strip(',')
                if chunk.startswith('(') and chunk.endswith(')'):
                    coords = chunk[1:-1].split(',')
                    if len(coords) == 2:
                        points.append(tuple(float(c) for c in coords))
    if points:
        data['TransversePoints'] = points

    columns = []
    for line in lines:
        if line.startswith('#') or not line.strip():
            continue
        columns.append([float(entry) for entry in line.split()])
    table = np.array(columns).transpose() if columns else np.zeros((3, 0))
    labels = ('s', 'W', 'I_bunch')
    for index, label in enumerate(labels):
        data[label] = table[index] if index < len(table) else np.array([])
    data.setdefault('WakeType', 'longitudinal')
    return data


# Comment markers seen at the head of an ACE3P column table: acdtool's curve
# files and T3P's wakefield use '#', S3P's PortRef<n>_<m>.out uses '%'.
_COMMENT_CHARS = '#%'


def parse_column_file(path, columns=None):
    """Parse a ``#``- or ``%``-commented column table into ``{column: array}``.

    The shape :func:`parse_wakefield` already handles, read the same way but
    **driven by the header row rather than by an assumed filename pattern** —
    which is what lets one reader cover all six acdtool curve blocks whose output
    filenames follow six different schemes, plus ``postprocess track3p``'s ``en``
    and S3P's ``PortRef<n>_<m>.out`` port mode profiles.

    The column names are the *last* header line whose token count matches the
    data rows: the curve files lead with a ``# 1 2 3 ...`` ordinal line and then
    name the columns (``# x y z Ex Ey Ez Bx By Bz Sz``). A header line need not be
    commented — ``postprocess track3p``'s ``en`` names its seven columns on a bare
    first line — so any non-numeric line *before* the first data row counts as
    one.

    `columns` names them explicitly, for the files that carry **no** header at
    all — ``postprocess coaxsignal``'s ``signal.out`` is three unlabeled columns
    (:data:`lume_ace3p.acdtool.SIGNAL_COLUMNS`).

    Lives here rather than in :mod:`lume_ace3p.acdtool`, where Phase 3 of
    ``plans/acdtool_rework_plan.md`` first wrote it, because Phase 5 needs it for
    S3P: the postprocessor may depend on the solver layer, not the other way
    round. ``acdtool.parse_column_file`` re-exports it.
    """
    comments, rows = [], []
    with open(path) as file:
        for line in file:
            text = line.strip()
            if not text:
                continue
            if text[0] in _COMMENT_CHARS:
                comments.append(text.lstrip(_COMMENT_CHARS).split())
                continue
            try:
                rows.append([float(token.rstrip(',')) for token in text.split()])
            except ValueError:
                if not rows:
                    comments.append(text.split())   # an uncommented header row
                continue
    width = max((len(row) for row in rows), default=0)
    if columns is None:
        columns = next((tokens for tokens in reversed(comments)
                        if len(tokens) == width), None)
    if columns is None:
        columns = ['column' + str(i + 1) for i in range(width)]
    table = np.array(rows).transpose() if rows else np.zeros((width, 0))
    return {name: (table[i] if i < len(table) else np.array([]))
            for i, name in enumerate(columns)}


class Track3P(ACE3P):

    module_name = 'track3p'

    default_job_name = 'track3p_results'

    # CW23 overrides this to non-default values freely, and names the directory
    # after whatever distinguishes the run: ``track3p Pillbox2.3MV.track3p 2.3MV``,
    # ``track3p Muon-MagField.track3p track3p_MagField``.
    accepts_results_dir_arg = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_file = 'track3p.out'
