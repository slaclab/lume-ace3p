import os, shutil
import subprocess

from lume.base import CommandWrapper


class Geant4(CommandWrapper):

    def __init__(self, *args, geant4_threads=1, geant4_opts='',
                 mpi_caller=None, geant4_app_path=None, geant4_app_exe=None,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.MPI_CALLER = mpi_caller if mpi_caller is not None else os.environ.get('MPI_CALLER', '')
        self.GEANT4_APP_PATH = geant4_app_path if geant4_app_path is not None else os.environ.get('GEANT4_APP_PATH', '')
        self.GEANT4_APP_EXE = geant4_app_exe if geant4_app_exe is not None else os.environ.get('GEANT4_APP_EXE', '')
        self.geant4_threads = geant4_threads
        self.geant4_opts = geant4_opts if geant4_opts is not None else ''
        if self.workdir is None:
            self.workdir = os.getcwd()
        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)
        assert self.input_file is not None, 'Error: Geant4 object requires input macro file'
        self.original_input_file = self.input_file
        if not os.path.isfile(os.path.join(self.workdir, self.input_file)):
            shutil.copy(self.input_file, self.workdir)
        self.input_parser()

    def input_parser(self):    #Read in Geant4 macro file
        with open(self.input_file) as file:
            self.lines = file.readlines()
        numrows = len(self.lines)
        self.ncflag = [0] * numrows
        for i in range(numrows):
            if self.lines[i].strip() != '':
                if not self.lines[i].lstrip().startswith('#'):
                    self.ncflag[i] = i+1
        self.ncflag = [i-1 for i in self.ncflag if i != 0]

    @staticmethod
    def _split_cmd(line):
        stripped = line.rstrip('\n')
        leading = len(stripped) - len(stripped.lstrip())
        tokens = stripped.split()
        if not tokens or not tokens[0].startswith('/'):
            return None
        return leading, tokens[0], tokens[1:]

    def get_value(self, key):    #Read args of first matching command
        for i in self.ncflag:
            parsed = self._split_cmd(self.lines[i])
            if parsed and parsed[1] == key:
                args = parsed[2]
                return ' '.join(args) if args else None
        print('Warning: \'' + key + '\' not found in Geant4 macro file, ' \
              + 'value \'None\' returned.')
        return None

    def set_value(self, kwargs):    #Set args of first matching command
        for key, value in kwargs.items():
            if isinstance(value, (list, tuple)):
                value_str = ' '.join(str(v) for v in value)
            else:
                value_str = str(value)
            replaced = False
            for i in self.ncflag:
                parsed = self._split_cmd(self.lines[i])
                if parsed and parsed[1] == key:
                    pad = ' ' * parsed[0]
                    self.lines[i] = pad + key + ' ' + value_str + '\n'
                    replaced = True
                    break
            if not replaced:
                print('Warning: \'' + key + '\' not found in Geant4 macro file, appending.')
                self.lines.append(key + ' ' + value_str + '\n')
                self.ncflag.append(len(self.lines) - 1)

    def set_particle_file(self, path, macro_value=None, particle_cmd='/lume/particleFile'):
        # `path` is the on-disk file used to count rows.
        # `macro_value` is what gets written into the macro (defaults to `path`).
        # When the file lives in workdir, pass macro_value=os.path.basename(path).
        n = 0
        with open(path) as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith('#'):
                    n += 1
        if macro_value is None:
            macro_value = path
        self.set_value({particle_cmd: macro_value, '/run/beamOn': n})

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
        cmd = (self.MPI_CALLER + ' --nodes=1 --ntasks=1 '
               + '--cpus-per-task=' + str(self.geant4_threads) + ' '
               + self.geant4_opts + ' '
               + exe + ' ' + self.input_file)
        subprocess.run(cmd, shell=True, cwd=self.workdir)

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
