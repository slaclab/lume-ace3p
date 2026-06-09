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
        # Skip whitespace (but not newline yet — we need to peek for '{')
        while i < n and text[i] in ' \t':
            i += 1
        if i < n and text[i] == '{':
            tokens.append(('lbrace',))
            i += 1
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

    def __init__(self, *args, ace3p_tasks=1, ace3p_cores=1, ace3p_opts='',
                 ace3p_path=None, mpi_caller=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.ACE3P_PATH = ace3p_path if ace3p_path is not None else os.environ.get('ACE3P_PATH', '')
        self.MPI_CALLER = mpi_caller if mpi_caller is not None else os.environ.get('MPI_CALLER', '')
        self.ace3p_tasks = ace3p_tasks
        self.ace3p_cores = ace3p_cores
        self.ace3p_opts = ace3p_opts
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

    module_name = 'omega3p'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_file = 'omega3p.out'

    def make_default_input(self):
        self.input_file = 'omega3p_input_file.omega3p'
        with open(self.input_file, 'w') as f:
            pass


class S3P(ACE3P):

    module_name = 's3p'

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

    module_name = 't3p'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_file = 't3p.out'


class Track3P(ACE3P):

    module_name = 'track3p'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_file = 'track3p.out'
