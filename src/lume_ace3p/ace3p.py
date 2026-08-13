import os, shutil
import subprocess
import numpy as np

from lume.base import CommandWrapper


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


class ACE3P(CommandWrapper):

    module_name = ''

    # The directory each solver writes its results into when nothing overrides
    # it. This default is the *authoritative* path: no solver reference
    # documents a 'JobName' input container (it is set by the batch job
    # submission script, outside the input file), and no shipped tutorial input
    # of any type sets one. See ``job_name`` for the resolution order.
    default_job_name = ''

    def __init__(self, *args, ace3p_tasks=1, ace3p_cores=1, ace3p_opts='',
                 ace3p_path=None, mpi_caller=None, results_dir=None, **kwargs):
        super().__init__(*args, **kwargs)
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

    def run(self):
        self.write_input()
        subprocess.run(self.MPI_CALLER + ' -n ' + str(self.ace3p_tasks) + ' -c ' + str(self.ace3p_cores) + ' ' + self.ace3p_opts + ' ' + self.ACE3P_PATH + self.module_name + ' ' + self.input_file, shell=True, cwd=self.workdir)
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
        overrides this to append its ``OUTPUT`` subdirectory."""
        return self.job_name()

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


class S3P(ACE3P):

    module_name = 's3p'

    # Not yet consulted: 'output_parser' below still hardcodes 's3p_results'.
    # Recorded for symmetry — the S3P reference documents no output files at all,
    # so the default is all there is to go on.
    default_job_name = 's3p_results'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_file = 's3p.out'

    def output_parser(self):
        self.output_data = {}
        with open(os.path.join(self.workdir, 's3p_results/Reflection.out')) as file:
            lines = file.readlines()
        for ind in range(len(lines)):
            if lines[ind].startswith('#Index'):
                indrow = ind
            if lines[ind].startswith('#Frequency'):
                freqrow = ind
                break
        self.output_data['IndexMap'] = {}
        for row in lines[indrow+1:freqrow]:
            id = row.strip('#').split()[0]
            self.output_data['IndexMap'][id] = {}
            self.output_data['IndexMap'][id]['Port'] = row.split('Port')[1].split()[0].strip(',')
            self.output_data['IndexMap'][id]['Mode'] = row.split('Mode')[1].split()[0].strip(',')
            self.output_data['IndexMap'][id]['Type'] = row.split('Type:')[1].split()[0].strip('(')
            self.output_data['IndexMap'][id]['Cutoff'] = eval(row.split('cutoff:')[1].split('Hz')[0].strip())
        frequency= []
        sparameters = []
        for row in lines[freqrow+1::]:
            rowlist = row.split()
            frequency.append(eval(rowlist[0]))
            sparameter = []
            for entry in rowlist[1::]:
                sparameter.append(eval(entry))
            sparameters.append(sparameter)
        sparameters = np.array(sparameters).transpose()
        self.output_data['Frequency'] = np.array(frequency)
        num_ids = len(self.output_data['IndexMap'].keys())
        for id1 in range(num_ids):
            for id2 in range(num_ids):
                sname = 'S(' + str(id1) + ',' + str(id2) + ')'
                self.output_data[sname] = sparameters[id1*num_ids+id2]

    def make_default_input(self):
        self.input_file = 's3p_input_file.s3p'
        with open(self.input_file, 'w') as f:
            pass


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
    """

    module_name = 't3p'

    # T3P's default job name — the directory it writes results into when the
    # input file does not set 'JobName' explicitly.
    default_job_name = 't3p_results'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_file = 't3p.out'

    def make_default_input(self):
        self.input_file = 't3p_input_file.t3p'
        with open(self.input_file, 'w') as f:
            pass

    # ---- output locations, read from the input file ----------------------- #

    def results_dir(self):
        """The ``<job_name>/OUTPUT`` directory T3P writes results into, relative
        to the workdir. T3P is the one solver with a subdirectory here; see
        :meth:`ACE3P.job_name` for how the parent is resolved."""
        return os.path.join(self.job_name(), 'OUTPUT')

    def wake_monitor_name(self):
        """The ``Name`` of the ``WakeField`` monitor, which is what T3P names its
        wakefield output files after, or ``None`` when the input declares no such
        monitor (a legitimate configuration — e.g. a pulse-propagation run that
        only monitors power)."""
        monitor = self._input_tree().find('Monitor', Type='WakeField')
        if monitor is None:
            return None
        return monitor.get_leaf('Name') or 'wakefield'

    # ---- output parsing --------------------------------------------------- #

    def output_parser(self):
        """Parse the wakefield monitor's ``.out`` file into ``output_data``.

        Populates ``s`` / ``W`` / ``I_bunch`` arrays plus ``WakeType`` and either
        ``LossFactor`` (longitudinal) or ``KickFactor`` (transverse); a transverse
        result also records ``TransversePoints`` and ``Offset`` from the header.

        Leaves ``output_data`` empty — rather than raising, as :class:`S3P` does —
        when the input declares no WakeField monitor or the file is absent. A T3P
        run without a wake monitor is a valid run, so this is not an error here;
        the module layer raises if such a workflow actually *asks* for a wakefield
        quantity."""
        self.output_data = {}
        monitor = self.wake_monitor_name()
        if monitor is None:
            return
        path = os.path.join(self.workdir, self.results_dir(), monitor + '.out')
        if not os.path.isfile(path):
            return
        self.output_data = parse_wakefield(path)


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


class Track3P(ACE3P):

    module_name = 'track3p'

    default_job_name = 'track3p_results'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_file = 'track3p.out'
