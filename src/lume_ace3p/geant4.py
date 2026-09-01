import os, shutil

from lume.base import CommandWrapper

from lume_ace3p.logs import run_logged


class Geant4(CommandWrapper):

    def __init__(self, *args, geant4_threads=1, geant4_opts='',
                 mpi_caller=None, geant4_app_path=None, geant4_app_exe=None,
                 log_file=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Where this step's stdout/stderr is teed (see lume_ace3p.logs); None
        # inherits the parent's streams, which is the legacy behavior.
        self.log_file = log_file
        self.MPI_CALLER = mpi_caller if mpi_caller is not None else os.environ.get('MPI_CALLER', '')
        self.GEANT4_APP_PATH = geant4_app_path if geant4_app_path is not None else os.environ.get('GEANT4_APP_PATH', '')
        self.GEANT4_APP_EXE = geant4_app_exe if geant4_app_exe is not None else os.environ.get('GEANT4_APP_EXE', '')
        self.geant4_threads = geant4_threads
        self.geant4_opts = geant4_opts if geant4_opts is not None else ''
        if self.workdir is None:
            self.workdir = os.getcwd()
        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)
        assert self.input_file is not None, 'Error: Geant4 object requires input file'
        # The source file may be given with directory components (e.g. a shared
        # '../assets/input.geant4'); it is copied into the workdir and thereafter
        # referenced by basename, so every 'workdir/input_file' join resolves.
        self.original_input_file = self.input_file
        self.input_file = os.path.basename(self.input_file)
        if not os.path.isfile(os.path.join(self.workdir, self.input_file)):
            shutil.copy(self.original_input_file, self.workdir)
        self.input_parser()

    def input_parser(self):    #Read in Geant4 'key = value' input file
        with open(os.path.join(self.workdir, self.input_file)) as file:
            self.lines = file.readlines()
        numrows = len(self.lines)
        self.ncflag = [0] * numrows
        for i in range(numrows):
            if self._split_kv(self.lines[i]) is not None:
                self.ncflag[i] = i+1
        self.ncflag = [i-1 for i in self.ncflag if i != 0]

    @staticmethod
    def _split_kv(line):
        stripped = line.rstrip('\n')
        leading = len(stripped) - len(stripped.lstrip())
        if not stripped.strip() or stripped.lstrip().startswith('#'):
            return None
        if '=' not in stripped:
            return None
        key, value = stripped.split('=', 1)
        key = key.strip()
        if not key:
            return None
        return leading, key, value.strip()

    def get_value(self, key):    #Read value of first matching key
        for i in self.ncflag:
            parsed = self._split_kv(self.lines[i])
            if parsed and parsed[1] == key:
                return parsed[2] if parsed[2] != '' else None
        print('Warning: \'' + key + '\' not found in Geant4 input file, ' \
              + 'value \'None\' returned.')
        return None

    def set_value(self, kwargs):    #Set value of first matching key
        for key, value in kwargs.items():
            if isinstance(value, (list, tuple)):
                value_str = ' '.join(str(v) for v in value)
            else:
                value_str = str(value)
            replaced = False
            for i in self.ncflag:
                parsed = self._split_kv(self.lines[i])
                if parsed and parsed[1] == key:
                    pad = ' ' * parsed[0]
                    self.lines[i] = pad + key + ' = ' + value_str + '\n'
                    replaced = True
                    break
            if not replaced:
                print('Warning: \'' + key + '\' not found in Geant4 input file, appending.')
                self.lines.append(key + ' = ' + value_str + '\n')
                self.ncflag.append(len(self.lines) - 1)

    def get_values(self):    #Return all 'key = value' pairs as a dict
        values = {}
        for i in self.ncflag:
            parsed = self._split_kv(self.lines[i])
            if parsed:
                values[parsed[1]] = parsed[2]
        return values

    def set_particle_file(self, path, macro_value=None, particle_cmd='particles'):
        # `path` is the on-disk particle file.
        # `macro_value` is what gets written into the input file (defaults to `path`).
        # When the file lives in workdir, pass macro_value=os.path.basename(path).
        # The Geant4 executable auto-derives the event count from the particle
        # file, so no 'beam_on' is written here.
        if macro_value is None:
            macro_value = path
        self.set_value({particle_cmd: macro_value})

    def write_input(self, *args):
        if args:
            file = args[0]
        else:
            file = self.input_file
        if os.path.isfile(os.path.join(self.workdir, self.input_file)):
            if os.path.samefile(os.path.join(self.workdir, self.input_file),
                                os.path.join(os.getcwd(), self.original_input_file)):
                file = file + '_copy'    #Avoid overwriting original input if in same directory
        self.input_file = file
        with open(os.path.join(self.workdir, self.input_file), 'w') as file:
            file.writelines(self.lines)

    def run(self):
        self.write_input()
        exe = os.path.join(self.GEANT4_APP_PATH, self.GEANT4_APP_EXE)
        cmd = (self.MPI_CALLER + ' -n 1 -c ' + str(self.geant4_threads) + ' '
               + self.geant4_opts + ' '
               + exe + ' ' + self.input_file)
        run_logged(cmd, cwd=self.workdir, log_file=self.log_file)

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
