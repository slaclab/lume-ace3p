import os, re, shlex, shutil, warnings

from lume.base import CommandWrapper

from lume_ace3p.launch import BARE, MESHCONVERT, step_command
from lume_ace3p.logs import check_exit, run_logged

# Prefix put on a `quit`/`exit` line commented out of a journal that is not the
# last file of the session (a mid-list quit ends Cubit with rc 0 and no mesh).
QUIT_PREFIX = '## lume-ace3p: '
_QUIT_WORDS = ('quit', 'exit')


class _Journal:
    """One file of a Cubit session: a journal (parsed and editable) or a Python
    script (staged as-is, never read or edited)."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.name = os.path.basename(path)
        self.python = self.name.lower().endswith('.py')
        self.written = self.name  # the file name used in the workdir
        self.parse()

    def parse(self):
        self.lines, self.ncflag = [], []
        if self.python:
            return
        with open(self.path) as file:
            self.lines = file.readlines()
        # Non-empty lines that do not start with '##' (Cubit comment rows).
        self.ncflag = [i for i, line in enumerate(self.lines)
                       if line.strip() != '' and not line.startswith('##')]

    def find_declaration(self, key):
        """Index into ``lines`` of the first declaration of ``key``, or None.

        A declaration is an APREPRO line ``#{key = ...}`` or a plain line
        ``key = ...``. ``key`` must match a whole identifier (``radius`` does
        not match ``cav_radius``), and ``==`` is not an assignment. Comment
        lines are skipped: those starting with ``#`` but not ``#{``, which
        covers ``##{...}`` and ``# key=1``.
        """
        name = re.escape(key)
        aprepro = re.compile(r'#\{\s*' + name + r'\s*=(?!=)')
        plain = re.compile(name + r'\s*=(?!=)')
        for i in self.ncflag:
            line = self.lines[i].lstrip()
            if line.startswith('#{'):
                if aprepro.match(line):
                    return i
            elif not line.startswith('#') and plain.match(line):
                return i
        return None

    def export_indices(self):
        """Indices of this journal's ``export`` lines, in file order."""
        return [i for i in self.ncflag
                if self.lines[i].split()[0] == 'export']

    def output_lines(self, strip_quit):
        """The lines to write; ``quit``/``exit`` commented out if ``strip_quit``."""
        if not strip_quit:
            return self.lines
        out = list(self.lines)
        for i in self.ncflag:
            words = out[i].split()
            if words[0].lower() in _QUIT_WORDS:
                out[i] = QUIT_PREFIX + out[i]
        return out


def _export_name(line):
    for word in line.split():
        if len(word) > 1 and word.startswith('"') and word.endswith('"'):
            return word.strip('"')
    return None


class Cubit(CommandWrapper):
    """Run one Cubit session over a journal or an ordered list of journals and
    Python scripts.

    ``input_file`` is a path or a list of paths. Every file is written into the
    workdir under its basename (``<name>_copy`` when the workdir already holds
    the source itself) and all are passed to one ``cubit -batch`` call, in
    order, so APREPRO variables persist from file to file. Journals have their
    variables edited (:meth:`set_value`) and, on every journal but the last
    file of the list, their ``quit``/``exit`` lines commented out. ``.py``
    files are run as given.
    """

    def __init__(self, *args, ace3p_path=None, cubit_path=None, mpi_caller=None,
                 log_file=None, slice=None, tee=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.ACE3P_PATH = ace3p_path if ace3p_path is not None else os.environ.get('ACE3P_PATH', '')
        self.CUBIT_PATH = cubit_path if cubit_path is not None else os.environ.get('CUBIT_PATH', '')
        self.MPI_CALLER = mpi_caller if mpi_caller is not None else os.environ.get('MPI_CALLER', '')
        # The worker's share of the allocation (lume_ace3p.launch.Slice). With
        # one, the mesher runs under the MPI caller inside it instead of bare
        # on whichever node the driver happens to be.
        self.slice = slice
        self.tee = tee  # echo the log to the parent's streams too
        # Where this step's subprocess output is teed (see lume_ace3p.logs).
        # None inherits the parent's streams, which is the legacy behavior. Both
        # the mesher and meshconvert append to it: they are two invocations of one
        # pipeline step, so one log describes the step.
        self.log_file = log_file
        self.exportfile = None
        if self.workdir is None:
            self.workdir = os.getcwd()
        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)
        assert self.input_file, 'Error: Cubit object requires input journal file'
        self.original_input_file = self.input_file
        self._single = isinstance(self.input_file, (str, os.PathLike))
        paths = [self.input_file] if self._single else list(self.input_file)
        names = {}
        for path in paths:
            name = os.path.basename(path)
            if name in names:
                raise ValueError(
                    f"Cubit files {names[name]!r} and {path!r} share the "
                    f"basename {name!r}; both would be written to the same "
                    f"workdir file.")
            names[name] = path
        self.journals = [_Journal(path) for path in paths]
        if not any(not j.python for j in self.journals):
            raise ValueError('Cubit needs at least one journal (.jou) file.')
        for j in self.journals:
            if not os.path.isfile(os.path.join(self.workdir, j.name)):
                shutil.copy(j.path, self.workdir)

    # The single-journal view (today's attributes) of the first journal.
    @property
    def lines(self):
        return self._only().lines

    @lines.setter
    def lines(self, value):
        self._only().lines = value

    @property
    def ncflag(self):
        return self._only().ncflag

    def _only(self):
        if not self._single:
            raise AttributeError('lines/ncflag are per journal on a multi-file '
                                 'Cubit; use .journals')
        return self.journals[0]

    def input_parser(self):    #Re-read every journal from its source
        for j in self.journals:
            j.parse()

    def declarations(self, key):
        """``(journal, line index)`` of the first declaration of ``key`` in
        each journal that declares it, in list order."""
        found = []
        for j in self.journals:
            i = j.find_declaration(key)
            if i is not None:
                found.append((j, i))
        return found

    def find_declaration(self, key):
        """``(journal, line index)`` of the first declaration of ``key`` across
        the list, or None."""
        found = self.declarations(key)
        return found[0] if found else None

    def get_value(self, key):  #Read value of first instance of variable
        found = self.find_declaration(key)
        if found is None:
            print('Warning: \'' + key + '=\' not found in Cubit journal file, ' \
                  + 'value \'None\' returned.')
            return None
        j, i = found
        line = j.lines[i]
        indx = line.find('=')    #Index of '=' sign following name
        if line.strip().startswith('#{'):    #If APREPRO code line
            value = line[indx+1::].replace('}','')
        else:
            value = line[indx+1::].strip()
        return eval(value)

    def set_value(self, kwargs):    #Set value of first instance of variable
        for key, value in kwargs.items():
            found = self.declarations(key)
            if not found:
                continue  # undeclared names are validated at the workflow level
            if len(found) > 1:
                warnings.warn(
                    f"Cubit variable {key!r} is declared in "
                    f"{', '.join(j.name for j, _ in found)}; only the first "
                    f"({found[0][0].name}) is set, and a later declaration "
                    f"overrides it when Cubit runs.", stacklevel=2)
            j, i = found[0]
            line = j.lines[i]
            indx = line.find('=')    #Index of '=' sign following name
            if line.strip().startswith('#{'):    #If APREPRO code line
                new_line = line[0:indx] + '=' + str(value) + '}\n'
            else:
                new_line = line[0:indx] + '=' + str(value) + '\n'
            j.lines[i] = new_line

    def _exports_reversed(self):
        """``(journal, line index)`` of every ``export``, last first."""
        for j in reversed(self.journals):
            for i in reversed(j.export_indices()):
                yield j, i

    def _last_export(self):
        return next(self._exports_reversed(), None)

    def get_export(self):
        """Set ``exportfile`` from the last ``export`` across the list that
        names a quoted file (Python files are not read)."""
        for j, i in self._exports_reversed():
            name = _export_name(j.lines[i])
            if name is not None:
                self.exportfile = name
                return
        print('Warning: no export command found in Cubit journal, no action taken.')

    def set_export(self, name, format_='genesis', opts='overwrite'):
        """Rewrite the export :meth:`get_export` reads (the last one)."""
        found = self._last_export()
        if found is None:
            print('Warning: no export command found in Cubit journal, no action taken.')
            return
        new_line = ['export', format_, '"' + name + '"']
        if opts is not None:
            if not isinstance(opts, list):
                opts = [opts]
            new_line = new_line + [opt for opt in opts]
        found[0].lines[found[1]] = ' '.join(new_line + ['\n'])
        self.exportfile = name

    def write_input(self, *args):
        """Write every file of the session into the workdir.

        A single journal may be given a file name (``args[0]``), as before."""
        if args and not self._single:
            raise TypeError('write_input takes no file name on a multi-file Cubit')
        last = len(self.journals) - 1
        for k, j in enumerate(self.journals):
            name = args[0] if args else j.name
            dest = os.path.join(self.workdir, name)
            same = os.path.isfile(dest) and os.path.samefile(dest, j.path)
            if j.python:
                if not same:
                    shutil.copyfile(j.path, dest)
                j.written = name
                continue
            if same:
                name = name + '_copy'   #Used to not overwrite original input if in same directory
            j.written = name
            with open(os.path.join(self.workdir, name), 'w') as file:
                file.writelines(j.output_lines(strip_quit=k != last))
        written = [j.written for j in self.journals]
        self.input_file = written[0] if self._single else written

    def command(self):
        """The Cubit command line, over the files :meth:`write_input` wrote."""
        files = ' '.join(shlex.quote(j.written) for j in self.journals)
        return (self.CUBIT_PATH
                + 'cubit -batch -nographics -nojournal -noecho ' + files)

    def run(self, mcflag=True):
        self.write_input()
        check_exit('cubit', run_logged(
            step_command(self.MPI_CALLER, 1, 1, '', self.command(), self.slice,
                         form=BARE),
            cwd=self.workdir, log_file=self.log_file, tee=self.tee,
            timeout=self.timeout),
            self.log_file)
        if mcflag:
            self.meshconvert()

    def meshconvert(self, *args):
        if args:
            self.exportfile = args[0]
        else:
            self.get_export()
        if self.exportfile is not None:
            argv = self.ACE3P_PATH + 'acdtool meshconvert ' + self.exportfile
            check_exit('acdtool meshconvert', run_logged(
                step_command(self.MPI_CALLER, 1, 1, '', argv, self.slice,
                             form=MESHCONVERT),
                cwd=self.workdir, log_file=self.log_file, tee=self.tee,
                timeout=self.timeout),
            self.log_file)
        
    def configure(self):
        return 'Not implemented.'
    
    def archive(self):
        return 'Not implemented.'

    def load_archive(self):
        return 'Not implemented.'
    
    def plot(self):
        return 'Not implemented.'
    
    def load_output(self):
        return 'Not implemented.'
