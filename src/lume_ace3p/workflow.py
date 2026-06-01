import os
import shutil
import numpy as np

from lume_ace3p.cubit import Cubit
from lume_ace3p.ace3p import Omega3P, S3P
from lume_ace3p.acdtool import Acdtool
from lume_ace3p.geant4 import Geant4
from lume_ace3p.particles import Particles
from lume_ace3p.tools import WriteOmega3PDataTable, WriteS3PDataTable

class ACE3PWorkflow:

    def __init__(self, workflow_dict, input_dict=None, output_dict=None):
        self.input_dict = input_dict
        self.output_dict = output_dict
        self.cubit_input = workflow_dict.get('cubit_input')
        self.ace3p_input = workflow_dict.get('ace3p_input')
        self.ace3p_tasks = workflow_dict.get('ace3p_tasks')
        self.ace3p_cores = workflow_dict.get('ace3p_cores')
        self.ace3p_opts = workflow_dict.get('ace3p_opts')
        self.rfpost_input = workflow_dict.get('rfpost_input')
        self.workdir_mode = workflow_dict.get('workdir_mode', 'manual')
        self.baseworkdir = workflow_dict.get('workdir', os.getcwd())
        self.sweep_output = workflow_dict.get('sweep_output', False)
        self.sweep_output_file = workflow_dict.get('sweep_output_file')
        self.skip_cubit = workflow_dict.get('skip_cubit', False)
        self.skip_solver = workflow_dict.get('skip_solver', False)
        self.skip_acdtool = workflow_dict.get('skip_acdtool', False)
        self.skip_meshconvert = workflow_dict.get('skip_meshconvert', False)
        # Dry run: explicit flag or auto-detect when ACE3P tools aren't available
        self.dry_run = workflow_dict.get('dry_run', False)
        if not self.dry_run and not os.environ.get('ACE3P_PATH', ''):
            self.dry_run = True
            print('ACE3P environment not configured, enabling dry run mode.')

    def _getworkdir(self, input_dict):
        if self.workdir_mode == 'manual':
            self.workdir = self.baseworkdir
        elif self.workdir_mode == 'auto':
            name_str = ''
            if input_dict is not None:
                for key, value in input_dict.items():
                    if isinstance(value, (list, tuple, np.ndarray)):
                        raise ValueError('Workflow cannot use \'.run()\' with non-scalar input dictonaries, use \'.run_sweep()\' instead.')
                    value = str(input_dict[key])
                    #this prevents errors that come with file names being a parameter
                    if value.startswith('./'):
                        value = value.replace('./', '')
                    #elements of input_dict that have this flag are not being swept over and should not be included in dict name
                    if not key.startswith('DONTINCLUDE'):
                        name_str = name_str + '_' + value
                if self.baseworkdir is None:
                    self.workdir = 'lume-ace3p_workflow_output' + name_str
                else:
                    self.workdir = self.baseworkdir + name_str
            else:
                self.workdir = self.baseworkdir
        else:
            raise ValueError("Key: \'workdir_mode\' must be either \'manual\' or \'auto\'.")

    def _build_input_tensor(self, input_dict):
        """Build the full tensor product of all input parameter combinations."""
        self.input_varname = []
        self.input_vardim = []
        self.input_vardata = []

        for var, value in input_dict.items():
            self.input_varname.append(var)
            self.input_vardim.append(len(value))
            self.input_vardata.append(np.array(value))

        self.input_tensor = self.input_vardata[0]
        if len(self.input_varname) == 1:
            self.input_tensor = np.reshape(self.input_tensor, (self.input_vardim[0], 1))
        else:
            t1 = np.tile(self.input_tensor, self.input_vardim[1])
            t2 = np.repeat(self.input_vardata[1], self.input_vardim[0])
            try:
                self.input_tensor = np.vstack([t1, t2]).T
            except ValueError:
                print("Error in finding input_tensor. Expected and actual dimensions of input data do not match. This often occurs when a list of lists is put as a parameter in the .yaml file, such as [[3,4],[5,6]]. If this is the case, replace with a list of strings: ['3,4','5,6'].")
            if len(self.input_varname) > 2:
                for i in range(2, len(self.input_varname)):
                    t1 = np.tile(self.input_tensor, (self.input_vardim[i], 1))
                    t2 = np.repeat(self.input_vardata[i], np.size(self.input_tensor, 0))
                    try:
                        self.input_tensor = np.vstack([t1.T, t2]).T
                    except ValueError:
                        print("Error in finding input_tensor. Expected and actual dimensions of input data do not match. This often occurs when a list of lists is put as a parameter in the .yaml file, such as [[3,4],[5,6]]. If this is the case, replace with a list of strings: ['3,4','5,6'].")

    def run(self):
        pass

    def evaluate(self):
        pass

    def run_sweep(self):
        pass


class Omega3PWorkflow(ACE3PWorkflow):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def run(self, input_dict=None, output_dict=None,
            skip_cubit=None, skip_solver=None, skip_acdtool=None, skip_meshconvert=None):
        # Per-call overrides fall back to instance defaults set from workflow_dict
        skip_cubit       = self.skip_cubit       if skip_cubit       is None else skip_cubit
        skip_solver      = self.skip_solver      if skip_solver      is None else skip_solver
        skip_acdtool     = self.skip_acdtool     if skip_acdtool     is None else skip_acdtool
        skip_meshconvert = self.skip_meshconvert if skip_meshconvert is None else skip_meshconvert

        if input_dict is None:
            input_dict = self.input_dict
        self._getworkdir(input_dict)

        if self.dry_run:
            if not os.path.exists(self.workdir):
                os.mkdir(self.workdir)
            with open(os.path.join(self.workdir, 'DRY_RUN.txt'), 'w') as f:
                f.write('Dry run mode: Cubit, Omega3P, and Acdtool steps skipped.\n')
                f.write(f'Input parameters: {input_dict}\n')
            self.acdtool_obj = None
            if output_dict is None:
                output_dict = self.output_dict
            return self.evaluate(output_dict)

        #Load Cubit journal, update values, and run
        if self.cubit_input is not None and not skip_cubit:
            self.cubit_obj = Cubit(self.cubit_input, workdir=self.workdir)
            if input_dict is not None:
                self.cubit_obj.set_value(input_dict)
            self.cubit_obj.run(mcflag=not skip_meshconvert)
        elif skip_cubit:
            print('Cubit step skipped.')
        else:
            print('Cubit journal file not specified, skipping step.')

        #Load Omega3P input and run
        if not skip_solver:
            self.omega3p_obj = Omega3P(self.ace3p_input,
                                  ace3p_tasks=self.ace3p_tasks,
                                  ace3p_cores=self.ace3p_cores,
                                  ace3p_opts=self.ace3p_opts,
                                  workdir=self.workdir)
            if input_dict is not None:
                self.omega3p_obj.set_value(input_dict)
            self.omega3p_obj.run()
        else:
            print('ACE3P solver step skipped.')

        #Load acdtool rfpost input and run
        if self.rfpost_input is not None and not skip_acdtool:
            self.acdtool_obj = Acdtool(self.rfpost_input, workdir=self.workdir)
            self.acdtool_obj.run()
        elif skip_acdtool:
            print('Acdtool postprocess step skipped.')
        else:
            print('Acdtool postprocess input file not specified, skipping step.')

        if output_dict is None:
            output_dict = self.output_dict
        return self.evaluate(output_dict)

    def evaluate(self, output_dict):
        #Read acdtool postprocess RF output and return values referenced in output_dict
        self.output_data = {}
        if output_dict is not None:
            if self.acdtool_obj is None:
                for output_name in output_dict.keys():
                    self.output_data[output_name] = float('nan')
                return self.output_data
            else:
                for output_name, output_params in output_dict.items():
                    section = output_params[0]
                    if section == 'RoverQ':
                        mode = output_params[1]
                        entry = output_params[2]
                        assert (entry in set(['Frequency', 'Qext', 'V_r', 'V_i', 'absV', 'RoQ'])), ("Unknown expression '" + entry + "' in 'RoverQ' section.")
                        self.output_data[output_name] = self.acdtool_obj.output_data[section][mode][entry]
                    elif section == 'kickFactor':
                        mode = output_params[1]
                        entry = output_params[2]
                        assert (entry in set(['Frequency', 'Qext', 'Ks', 'V_r', 'V_i', 'absV'])), ("Unknown expression '" + entry + "' in 'kickFactor' section.")
                        self.output_data[output_name] = self.acdtool_obj.output_data[section][mode][entry]
                    elif section == 'maxFieldsOnSurface':
                        surface = output_params[1]
                        entry = output_params[2]
                        assert (entry in set(['Emax', 'Emax_location', 'Hmax', 'Hmax_location'])), ("Unknown expression '" + entry + "' in 'maxFieldsOnSurface' section.")
                        if entry.endswith('location'):
                            component = output_params[3]
                            self.output_data[output_name] = self.acdtool_obj.output_data[section][surface][entry][component]
                        else:
                            self.output_data[output_name] = self.acdtool_obj.output_data[section][surface][entry]
                    else:
                        raise ValueError("Unknown section name '" + section + "' in output dict.")
        return self.output_data

    def run_sweep(self, input_dict=None, output_dict=None):
        if input_dict is None:
            input_dict = self.input_dict
        if output_dict is None:
            output_dict = self.output_dict
        self.output_varname = []
        self.sweep_data = {}

        self._build_input_tensor(input_dict)

        for var in output_dict.keys():
            self.output_varname.append(var)

        for i in range(np.size(self.input_tensor, 0)):
            sweep_input_dict = {}
            sweep_input_tuple = tuple(self.input_tensor[i])
            for j in range(len(self.input_varname)):
                sweep_input_dict[self.input_varname[j]] = self.input_tensor[i][j]
            self.run(sweep_input_dict)
            self.sweep_data[sweep_input_tuple] = self.evaluate(output_dict)
            if self.sweep_output:
                self.print_sweep_output()
        return self.sweep_data

    def print_sweep_output(self, filename=None):
        if filename is None:
            filename = self.sweep_output_file
            if self.sweep_output_file is None:
                print('No sweep output file specified.')
                return
        if len(self.input_varname) == 0:
            print('Parameter sweep must be run first.')
            return
        WriteOmega3PDataTable(filename, self.sweep_data, self.input_varname, self.output_varname)


class S3PWorkflow(ACE3PWorkflow):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def run(self, input_dict=None, output_dict=None,
            skip_cubit=None, skip_solver=None, skip_meshconvert=None):
        # Per-call overrides fall back to instance defaults set from workflow_dict
        skip_cubit       = self.skip_cubit       if skip_cubit       is None else skip_cubit
        skip_solver      = self.skip_solver      if skip_solver      is None else skip_solver
        skip_meshconvert = self.skip_meshconvert if skip_meshconvert is None else skip_meshconvert

        if input_dict is None:
            input_dict = self.input_dict
        self._getworkdir(input_dict)

        if self.dry_run:
            if not os.path.exists(self.workdir):
                os.mkdir(self.workdir)
            with open(os.path.join(self.workdir, 'DRY_RUN.txt'), 'w') as f:
                f.write('Dry run mode: Cubit and S3P steps skipped.\n')
                f.write(f'Input parameters: {input_dict}\n')
            self.s3p_obj = None
            if output_dict is None:
                output_dict = self.output_dict
            return self.evaluate(output_dict)

        #Load Cubit journal, update values, and run
        if self.cubit_input is not None and not skip_cubit:
            self.cubit_obj = Cubit(self.cubit_input, workdir=self.workdir)
            if input_dict is not None:
                self.cubit_obj.set_value(input_dict)
            self.cubit_obj.run(mcflag=not skip_meshconvert)
        elif skip_cubit:
            print('Cubit step skipped.')
        else:
            print('Cubit journal file not specified, skipping step.')

        #Load S3P input and run
        if not skip_solver:
            self.s3p_obj = S3P(self.ace3p_input,
                               ace3p_tasks=self.ace3p_tasks,
                               ace3p_cores=self.ace3p_cores,
                               ace3p_opts=self.ace3p_opts,
                               workdir=self.workdir)
            if input_dict is not None:
                self.s3p_obj.set_value(input_dict)
            self.s3p_obj.run()
        else:
            print('ACE3P solver step skipped.')

        if output_dict is None:
            output_dict = self.output_dict
        return self.evaluate(output_dict)

    def evaluate(self, output_dict):
        self.output_data = {}
        if self.s3p_obj is None:
            self.output_data['IndexMap'] = {}
            self.output_data['Frequency'] = np.array([0.0])
            if output_dict is not None:
                for output_name in output_dict.keys():
                    self.output_data[output_name] = np.array([float('nan')])
            return self.output_data
        assert (len(self.s3p_obj.output_data) > 0), ('No output data found, run S3P first.')
        if output_dict is not None:
            self.output_data['IndexMap'] = self.s3p_obj.output_data['IndexMap']
            self.output_data['Frequency'] = self.s3p_obj.output_data['Frequency']
            for output_name, sparameter in output_dict.items():
                if isinstance(sparameter, list):
                    sparameter = sparameter[0]
                if sparameter in self.s3p_obj.output_data.keys():
                    self.output_data[output_name] = self.s3p_obj.output_data[sparameter]
                else:
                    raise ValueError("Unknown section name '" + sparameter + "' in output dict.")
        else:
            self.output_data = self.s3p_obj.output_data
        return self.output_data

    def run_sweep(self, input_dict=None, output_dict=None):
        if input_dict is None:
            input_dict = self.input_dict
        if output_dict is None:
            output_dict = self.output_dict
        self.sweep_data = {}

        self._build_input_tensor(input_dict)

        for i in range(np.size(self.input_tensor, 0)):
            sweep_input_dict = {}
            if len(self.input_varname) > 1:
                sweep_input_tuple = tuple(self.input_tensor[i])
                for j in range(len(self.input_varname)):
                    sweep_input_dict[self.input_varname[j]] = self.input_tensor[i][j]
            else:
                sweep_input_tuple = tuple([self.input_tensor[i]])
                sweep_input_dict[self.input_varname[0]] = self.input_tensor[i]
            self.run(sweep_input_dict)
            self.sweep_data[sweep_input_tuple] = self.evaluate(output_dict)
            if self.sweep_output:
                self.print_sweep_output()
        return self.sweep_data

    def print_sweep_output(self, filename=None):
        if filename is None:
            filename = self.sweep_output_file
            if self.sweep_output_file is None:
                print('No sweep output file specified.')
                return
        if len(self.input_varname) == 0:
            print('Parameter sweep must be run first.')
            return
        WriteS3PDataTable(filename, self.sweep_data, self.input_varname)


class Geant4Workflow(ACE3PWorkflow):

    def __init__(self, workflow_dict, input_dict=None, output_dict=None,
                 particle_params=None):
        super().__init__(workflow_dict, input_dict, output_dict)
        self.geant4_input          = workflow_dict.get('geant4_input')
        self.geant4_threads        = workflow_dict.get('geant4_threads', 1)
        self.geant4_opts           = workflow_dict.get('geant4_opts', '')
        self.geant4_particle_cmd   = workflow_dict.get('geant4_particle_cmd', '/lume/particleFile')
        self.geant4_geometry_files = workflow_dict.get('geant4_geometry_files') or []
        self.geant4_particle_file  = workflow_dict.get('geant4_particle_file')
        self.geant4_scoring_output = workflow_dict.get('geant4_scoring_output')
        self.particle_params       = particle_params
        self.particle_input        = workflow_dict.get('particle_input')
        self.particle_output       = workflow_dict.get('particle_output')
        # Re-evaluate dry_run for Geant4: ACE3P_PATH is irrelevant here.
        # Honor an explicit YAML setting; otherwise auto-enable only if Geant4 env is unset.
        if 'dry_run' not in workflow_dict:
            self.dry_run = (not os.environ.get('GEANT4_APP_PATH', '')
                            or not os.environ.get('GEANT4_APP_EXE', ''))
            if self.dry_run:
                print('Geant4 environment not configured, enabling dry run mode.')

    @staticmethod
    def _normalize_macro_inputs(input_dict):
        # Strip DONTINCLUDE/ACE3P prefixes added by input_to_dict, and unwrap
        # single-element lists so set_value receives clean macro commands.
        if input_dict is None:
            return None
        clean = {}
        for key, value in input_dict.items():
            new_key = key
            if new_key.startswith('DONTINCLUDE'):
                new_key = new_key[len('DONTINCLUDE'):]
            if new_key.startswith('ACE3P'):
                continue  # not a Geant4 macro command
            if not new_key.startswith('/'):
                continue  # ignore non-macro keys (e.g. Cubit params)
            if isinstance(value, list) and len(value) == 1:
                value = value[0]
            clean[new_key] = value
        return clean

    def run(self, input_dict=None, output_dict=None):
        if input_dict is None:
            input_dict = self.input_dict
        if output_dict is None:
            output_dict = self.output_dict
        self._getworkdir(input_dict)
        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)
        macro_inputs = self._normalize_macro_inputs(input_dict)

        # Optional Track3P pre-step
        if self.particle_params is not None and self.particle_input is not None:
            src_basename = os.path.basename(self.particle_input)
            dest = os.path.join(self.workdir, src_basename)
            if not os.path.isfile(dest):
                shutil.copy(self.particle_input, self.workdir)
            particles = Particles(src_basename, self.particle_params,
                                  output_file=self.particle_output, workdir=self.workdir)
            particles.run()
            particle_file_path = os.path.join(self.workdir, particles.output_file)
        else:
            particle_file_path = self.geant4_particle_file
            if particle_file_path is not None:
                dest = os.path.join(self.workdir, os.path.basename(particle_file_path))
                if not os.path.isfile(dest):
                    shutil.copy(particle_file_path, self.workdir)
                particle_file_path = dest

        # Copy geometry files into workdir
        for geom in self.geant4_geometry_files:
            dest = os.path.join(self.workdir, os.path.basename(geom))
            if not os.path.isfile(dest):
                shutil.copy(geom, self.workdir)

        if self.dry_run:
            with open(os.path.join(self.workdir, 'DRY_RUN.txt'), 'w') as f:
                f.write('Dry run mode: Geant4 step skipped.\n')
                f.write(f'Macro: {self.geant4_input}\n')
                f.write(f'Particle file: {particle_file_path}\n')
                f.write(f'Geometry files: {self.geant4_geometry_files}\n')
                f.write(f'Threads: {self.geant4_threads}\n')
                f.write(f'Input parameters: {input_dict}\n')
            self.geant4_obj = None
            # Still patch the macro so the dry run shows the rewritten file.
            if self.geant4_input is not None:
                self.geant4_obj = Geant4(self.geant4_input,
                                         geant4_threads=self.geant4_threads,
                                         geant4_opts=self.geant4_opts,
                                         workdir=self.workdir)
                self.geant4_obj.set_value({'/run/numberOfThreads': self.geant4_threads})
                if particle_file_path is not None:
                    self.geant4_obj.set_particle_file(particle_file_path,
                                                     macro_value=os.path.basename(particle_file_path),
                                                     particle_cmd=self.geant4_particle_cmd)
                if macro_inputs:
                    self.geant4_obj.set_value(macro_inputs)
                self.geant4_obj.write_input()
            return self.evaluate(output_dict)

        # Real run
        self.geant4_obj = Geant4(self.geant4_input,
                                 geant4_threads=self.geant4_threads,
                                 geant4_opts=self.geant4_opts,
                                 workdir=self.workdir)
        self.geant4_obj.set_value({'/run/numberOfThreads': self.geant4_threads})
        if particle_file_path is not None:
            self.geant4_obj.set_particle_file(os.path.basename(particle_file_path),
                                             particle_cmd=self.geant4_particle_cmd)
        if macro_inputs:
            self.geant4_obj.set_value(macro_inputs)
        self.geant4_obj.run()

        return self.evaluate(output_dict)

    def evaluate(self, output_dict):
        self.output_data = {}
        if output_dict is None:
            return self.output_data
        scoring = self._read_scoring_output()
        for output_name, output_params in output_dict.items():
            if not isinstance(output_params, list) or len(output_params) < 2:
                self.output_data[output_name] = float('nan')
                continue
            section, entry = output_params[0], output_params[1]
            if section == 'scoring':
                if scoring is None:
                    self.output_data[output_name] = float('nan')
                elif entry == 'total':
                    self.output_data[output_name] = float(np.sum(scoring['values']))
                elif entry == 'peak':
                    self.output_data[output_name] = float(np.max(scoring['values']))
                elif entry == 'peak_index':
                    idx = int(np.argmax(scoring['values']))
                    self.output_data[output_name] = tuple(scoring['indices'][idx])
                else:
                    raise ValueError("Unknown entry '" + str(entry) + "' in 'scoring' section.")
            else:
                raise ValueError("Unknown section name '" + str(section) + "' in output dict.")
        return self.output_data

    def _read_scoring_output(self):
        if not self.geant4_scoring_output:
            return None
        path = os.path.join(self.workdir, self.geant4_scoring_output)
        if not os.path.isfile(path):
            return None
        indices = []
        values = []
        with open(path) as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith('#'):
                    continue
                parts = s.replace(',', ' ').split()
                # Expect: iX iY iZ value [...]
                if len(parts) < 4:
                    continue
                try:
                    ix, iy, iz = int(parts[0]), int(parts[1]), int(parts[2])
                    val = float(parts[3])
                except ValueError:
                    continue
                indices.append((ix, iy, iz))
                values.append(val)
        if not values:
            return None
        return {'indices': indices, 'values': np.array(values)}
