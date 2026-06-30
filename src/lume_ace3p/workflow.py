import os
import shutil
import numpy as np

from lume_ace3p.cubit import Cubit
from lume_ace3p.ace3p import Omega3P, S3P
from lume_ace3p.acdtool import Acdtool
from lume_ace3p.geant4 import Geant4
from lume_ace3p.particles import Particles
from lume_ace3p.paths import resolve_paths
from lume_ace3p.tools import WriteOmega3PDataTable, WriteS3PDataTable
from lume_ace3p.inputs import WorkflowInputs


def _coerce_inputs(inputs):
    """Accept either a WorkflowInputs or a plain dict (treated as cubit
    parameters, the path xopt's sim_function uses) and return a
    WorkflowInputs."""
    if isinstance(inputs, WorkflowInputs):
        return inputs
    if inputs is None:
        return WorkflowInputs()
    return WorkflowInputs(cubit=dict(inputs))


def _scalar_str(value):
    """Render a scalar for use in workdir names. Numpy scalars unwrap; paths
    starting with './' have the prefix dropped to keep names tidy."""
    if isinstance(value, np.generic):
        value = value.item()
    s = str(value)
    if s.startswith('./'):
        s = s[2:]
    return s


class ACE3PWorkflow:

    _requires_ace3p = True

    def __init__(self, workflow_dict, inputs=None, output_dict=None):
        self.inputs = _coerce_inputs(inputs)
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
        self.paths = resolve_paths(workflow_dict.get('paths'))
        self.dry_run = workflow_dict.get('dry_run', False)
        if self._requires_ace3p and not self.dry_run and not self.paths['ace3p']:
            self.dry_run = True
            print('ACE3P environment not configured, enabling dry run mode.')

    def _getworkdir(self, inputs, sweep_scalars=None):
        if self.workdir_mode == 'manual':
            self.workdir = self.baseworkdir
            return
        if self.workdir_mode != 'auto':
            raise ValueError("Key: 'workdir_mode' must be either 'manual' or 'auto'.")

        # When called during a sweep, the per-iteration scalar values uniquely
        # identify the workdir. When called from a single .run() (e.g. xopt),
        # use the cubit scalars — those are the user-named optimization vars.
        if sweep_scalars is None:
            parts = []
            for value in inputs.cubit.values():
                if isinstance(value, (list, tuple, np.ndarray)):
                    raise ValueError("Workflow cannot run with non-scalar inputs; "
                                     "use run_sweep().")
                parts.append(_scalar_str(value))
        else:
            parts = [_scalar_str(v) for v in sweep_scalars]

        suffix = ''.join('_' + p for p in parts)
        if self.baseworkdir is None:
            self.workdir = 'lume-ace3p_workflow_output' + suffix
        else:
            self.workdir = self.baseworkdir + suffix

    def _ensure_workdir(self):
        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)

    def _apply_cubit(self, inputs):
        if self.cubit_input is None or self.skip_cubit:
            if self.skip_cubit:
                print('Cubit step skipped.')
            else:
                print('Cubit journal file not specified, skipping step.')
            return
        self.cubit_obj = Cubit(self.cubit_input, workdir=self.workdir,
                               ace3p_path=self.paths['ace3p'],
                               cubit_path=self.paths['cubit'],
                               mpi_caller=self.paths['mpi'])
        if inputs.cubit:
            self.cubit_obj.set_value(inputs.cubit)
        self.cubit_obj.run(mcflag=not self.skip_meshconvert)

    def run(self):
        pass

    def evaluate(self):
        pass

    def run_sweep(self):
        pass


def _ace3p_leaves(section):
    """Yield (path_tuple, leaf_value) for every leaf in `section`. Path
    elements are (name, sibling_index)."""
    seen = {}
    for name, child in section.entries:
        idx = seen.get(name, 0)
        seen[name] = idx + 1
        if hasattr(child, 'entries'):
            for sub in _ace3p_leaves(child):
                yield ((name, idx),) + sub[0], sub[1]
        else:
            yield ((name, idx),), child


def _run_sweep(workflow):
    """Drive a parameter sweep over `workflow.inputs.sweep_axes()`. Materializes
    a fresh per-iteration WorkflowInputs, runs the workflow, and stores the
    output keyed by the tuple of swept scalars."""
    axes = workflow.inputs.sweep_axes()
    workflow.input_varname = [a[0] for a in axes]
    if not axes:
        # No swept variables — single run with the original inputs.
        workflow.input_tensor = np.zeros((1, 0))
    else:
        grids = [a[1] for a in axes]
        mesh = np.meshgrid(*grids, indexing='ij')
        workflow.input_tensor = np.stack([m.ravel() for m in mesh], axis=1)


class Omega3PWorkflow(ACE3PWorkflow):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def run(self, inputs=None, output_dict=None, sweep_scalars=None):
        if inputs is None:
            inputs = self.inputs
        self._getworkdir(inputs, sweep_scalars)

        if self.dry_run:
            self._ensure_workdir()
            with open(os.path.join(self.workdir, 'DRY_RUN.txt'), 'w') as f:
                f.write('Dry run mode: Cubit, Omega3P, and Acdtool steps skipped.\n')
                f.write(f'Cubit: {inputs.cubit}\n')
                f.write(f'ACE3P: {[(_, v) for _, v in _ace3p_leaves(inputs.ace3p)]}\n')
            self.acdtool_obj = None
            if output_dict is None:
                output_dict = self.output_dict
            return self.evaluate(output_dict)

        self._apply_cubit(inputs)

        if not self.skip_solver:
            self.omega3p_obj = Omega3P(self.ace3p_input,
                                       ace3p_tasks=self.ace3p_tasks,
                                       ace3p_cores=self.ace3p_cores,
                                       ace3p_opts=self.ace3p_opts,
                                       workdir=self.workdir,
                                       ace3p_path=self.paths['ace3p'],
                                       mpi_caller=self.paths['mpi'])
            self.omega3p_obj.set_value(inputs.ace3p)
            self.omega3p_obj.run()
        else:
            print('ACE3P solver step skipped.')

        if self.rfpost_input is not None and not self.skip_acdtool:
            self.acdtool_obj = Acdtool(self.rfpost_input, workdir=self.workdir,
                                       ace3p_path=self.paths['ace3p'],
                                       mpi_caller=self.paths['mpi'])
            self.acdtool_obj.run()
        else:
            self.acdtool_obj = None
            if self.skip_acdtool:
                print('Acdtool postprocess step skipped.')
            else:
                print('Acdtool postprocess input file not specified, skipping step.')

        if output_dict is None:
            output_dict = self.output_dict
        return self.evaluate(output_dict)

    def evaluate(self, output_dict):
        self.output_data = {}
        if output_dict is None:
            return self.output_data
        if self.acdtool_obj is None:
            for output_name in output_dict.keys():
                self.output_data[output_name] = float('nan')
            return self.output_data
        for output_name, output_params in output_dict.items():
            section = output_params[0]
            if section == 'RoverQ':
                mode = output_params[1]; entry = output_params[2]
                assert (entry in {'Frequency','Qext','V_r','V_i','absV','RoQ'}), \
                    "Unknown expression '"+entry+"' in 'RoverQ' section."
                self.output_data[output_name] = self.acdtool_obj.output_data[section][mode][entry]
            elif section == 'kickFactor':
                mode = output_params[1]; entry = output_params[2]
                assert (entry in {'Frequency','Qext','Ks','V_r','V_i','absV'}), \
                    "Unknown expression '"+entry+"' in 'kickFactor' section."
                self.output_data[output_name] = self.acdtool_obj.output_data[section][mode][entry]
            elif section == 'maxFieldsOnSurface':
                surface = output_params[1]; entry = output_params[2]
                assert (entry in {'Emax','Emax_location','Hmax','Hmax_location'}), \
                    "Unknown expression '"+entry+"' in 'maxFieldsOnSurface' section."
                if entry.endswith('location'):
                    component = output_params[3]
                    self.output_data[output_name] = self.acdtool_obj.output_data[section][surface][entry][component]
                else:
                    self.output_data[output_name] = self.acdtool_obj.output_data[section][surface][entry]
            else:
                raise ValueError("Unknown section name '" + section + "' in output dict.")
        return self.output_data

    def run_sweep(self, inputs=None, output_dict=None):
        if inputs is None:
            inputs = self.inputs
        if output_dict is None:
            output_dict = self.output_dict
        self.output_varname = list(output_dict.keys())
        self.sweep_data = {}

        _run_sweep(self)

        for i in range(self.input_tensor.shape[0]):
            scalars = self.input_tensor[i].tolist()
            point = inputs.materialize(scalars)
            sweep_input_tuple = tuple(scalars)
            self.run(point, sweep_scalars=scalars)
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
        if not getattr(self, 'input_varname', None):
            print('Parameter sweep must be run first.')
            return
        WriteOmega3PDataTable(filename, self.sweep_data, self.input_varname, self.output_varname)


class S3PWorkflow(ACE3PWorkflow):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def run(self, inputs=None, output_dict=None, sweep_scalars=None):
        if inputs is None:
            inputs = self.inputs
        self._getworkdir(inputs, sweep_scalars)

        if self.dry_run:
            self._ensure_workdir()
            with open(os.path.join(self.workdir, 'DRY_RUN.txt'), 'w') as f:
                f.write('Dry run mode: Cubit and S3P steps skipped.\n')
                f.write(f'Cubit: {inputs.cubit}\n')
                f.write(f'ACE3P: {[(_, v) for _, v in _ace3p_leaves(inputs.ace3p)]}\n')
            self.s3p_obj = None
            if output_dict is None:
                output_dict = self.output_dict
            return self.evaluate(output_dict)

        self._apply_cubit(inputs)

        if not self.skip_solver:
            self.s3p_obj = S3P(self.ace3p_input,
                               ace3p_tasks=self.ace3p_tasks,
                               ace3p_cores=self.ace3p_cores,
                               ace3p_opts=self.ace3p_opts,
                               workdir=self.workdir,
                               ace3p_path=self.paths['ace3p'],
                               mpi_caller=self.paths['mpi'])
            self.s3p_obj.set_value(inputs.ace3p)
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
        assert (len(self.s3p_obj.output_data) > 0), 'No output data found, run S3P first.'
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

    def run_sweep(self, inputs=None, output_dict=None):
        if inputs is None:
            inputs = self.inputs
        if output_dict is None:
            output_dict = self.output_dict
        self.sweep_data = {}

        _run_sweep(self)

        for i in range(self.input_tensor.shape[0]):
            scalars = self.input_tensor[i].tolist()
            point = inputs.materialize(scalars)
            sweep_input_tuple = tuple(scalars)
            self.run(point, sweep_scalars=scalars)
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
        if not getattr(self, 'input_varname', None):
            print('Parameter sweep must be run first.')
            return
        WriteS3PDataTable(filename, self.sweep_data, self.input_varname)


class Geant4Workflow(ACE3PWorkflow):

    _requires_ace3p = False

    def __init__(self, workflow_dict, inputs=None, output_dict=None,
                 particle_params=None):
        super().__init__(workflow_dict, inputs, output_dict)
        self.geant4_input          = workflow_dict.get('geant4_input')
        self.geant4_threads        = workflow_dict.get('geant4_threads')
        self.geant4_opts           = workflow_dict.get('geant4_opts', '')
        self.geant4_particle_cmd   = workflow_dict.get('geant4_particle_cmd', 'particles')
        self.geant4_geometry_files = workflow_dict.get('geant4_geometry_files') or []
        self.geant4_particle_file  = workflow_dict.get('geant4_particle_file')
        # Output files are normally named in the Geant4 input file
        # (output_dose / output_edep); these allow an explicit YAML override.
        # 'geant4_scoring_output' is kept as a back-compat alias for the dose file.
        self.geant4_dose_output    = workflow_dict.get('geant4_dose_output') \
                                     or workflow_dict.get('geant4_scoring_output')
        self.geant4_edep_output    = workflow_dict.get('geant4_edep_output')
        self.particle_params       = particle_params
        self.particle_input        = workflow_dict.get('particle_input')
        self.particle_output       = workflow_dict.get('particle_output')
        if 'dry_run' not in workflow_dict:
            self.dry_run = not (self.paths['geant4_app_path'] and self.paths['geant4_app_exe'])
            if self.dry_run:
                print('Geant4 environment not configured, enabling dry run mode.')

    def _resolve_beta(self, inputs):
        """Return the particle_params to use for this run. When 'beta_input'
        (broadcast a single input-space variable to all bins) or 'beta_inputs'
        (one variable per bin) is set, build the per-bin beta list from the
        cubit bucket and return a fresh copy with 'beta' overridden. Reading
        from inputs.cubit makes this work identically for a parameter sweep
        (materialized scalar) and for Xopt (input_dict scalar)."""
        params = self.particle_params
        beta_input = params.get('beta_input')    # str | None
        beta_inputs = params.get('beta_inputs')  # list[str] | None
        if beta_input is None and beta_inputs is None:
            return params
        if beta_input is not None and beta_inputs is not None:
            raise ValueError("Set only one of 'beta_input' or 'beta_inputs'.")
        num_bins = params.get('num_bins')
        if num_bins is None:
            raise ValueError("'num_bins' must be set when using "
                             "'beta_input' or 'beta_inputs'.")

        def cubit_value(name):
            if name not in inputs.cubit:
                raise KeyError(f"beta variable '{name}' not found in "
                               "'input_parameters'.")
            return float(inputs.cubit[name])

        if beta_input is not None:
            effective_beta = [cubit_value(beta_input)] * num_bins
        else:
            if len(beta_inputs) != num_bins:
                raise ValueError(f"len(beta_inputs)={len(beta_inputs)} must "
                                 f"equal num_bins={num_bins}.")
            effective_beta = [cubit_value(n) for n in beta_inputs]

        params = dict(params)
        params['beta'] = effective_beta
        return params

    def run(self, inputs=None, output_dict=None, sweep_scalars=None):
        if inputs is None:
            inputs = self.inputs
        if output_dict is None:
            output_dict = self.output_dict
        self._getworkdir(inputs, sweep_scalars)
        self._ensure_workdir()
        macro_inputs = dict(inputs.macro) if inputs.macro else None

        if self.particle_params is not None and self.particle_input is not None:
            params = dict(self._resolve_beta(inputs))
            params.setdefault('output_format', 'geant4')
            src_basename = os.path.basename(self.particle_input)
            dest = os.path.join(self.workdir, src_basename)
            if not os.path.isfile(dest):
                shutil.copy(self.particle_input, self.workdir)
            particles = Particles(src_basename, params,
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

        # Build the Geant4 object first so we can read the input file's own
        # settings (STL geometry names, output filenames) before copying files.
        self.geant4_obj = None
        if self.geant4_input is not None:
            self.geant4_obj = Geant4(self.geant4_input,
                                     geant4_threads=self.geant4_threads or 1,
                                     geant4_opts=self.geant4_opts,
                                     workdir=self.workdir,
                                     mpi_caller=self.paths['mpi'],
                                     geant4_app_path=self.paths['geant4_app_path'],
                                     geant4_app_exe=self.paths['geant4_app_exe'])
            # Threads default is owned by the input file; only override when set.
            if self.geant4_threads is not None:
                self.geant4_obj.set_value({'nthreads': self.geant4_threads})
            if particle_file_path is not None:
                self.geant4_obj.set_particle_file(particle_file_path,
                                                 macro_value=os.path.basename(particle_file_path),
                                                 particle_cmd=self.geant4_particle_cmd)
            if macro_inputs:
                self.geant4_obj.set_value(macro_inputs)

        # Geometry files: union of any '*_stl' values named in the input file
        # and the explicit geant4_geometry_files list, de-duplicated by basename.
        geom_files = self._geometry_files()
        for geom in geom_files:
            dest = os.path.join(self.workdir, os.path.basename(geom))
            if not os.path.isfile(dest):
                shutil.copy(geom, self.workdir)

        if self.dry_run:
            with open(os.path.join(self.workdir, 'DRY_RUN.txt'), 'w') as f:
                f.write('Dry run mode: Geant4 step skipped.\n')
                f.write(f'Input file: {self.geant4_input}\n')
                f.write(f'Particle file: {particle_file_path}\n')
                f.write(f'Geometry files: {geom_files}\n')
                f.write(f'Output files: {self._output_files()}\n')
                f.write(f'Threads: {self.geant4_threads}\n')
                f.write(f'Cubit: {inputs.cubit}\n')
                f.write(f'Input overrides: {macro_inputs}\n')
            if self.geant4_obj is not None:
                self.geant4_obj.write_input()
            return self.evaluate(output_dict)

        self.geant4_obj.run()

        return self.evaluate(output_dict)

    def _geometry_files(self):
        """Union of STL files named in the Geant4 input file ('*_stl' keys)
        and the explicit geant4_geometry_files list. The input-file names are
        resolved relative to the directory of geant4_input so they can be
        copied into the workdir. De-duplicated by basename."""
        files = []
        seen = set()

        def add(path):
            base = os.path.basename(path)
            if base and base not in seen:
                seen.add(base)
                files.append(path)

        if self.geant4_obj is not None:
            input_dir = os.path.dirname(self.geant4_input)
            for key, value in self.geant4_obj.get_values().items():
                if key.endswith('_stl') and value:
                    candidate = os.path.join(input_dir, value) if input_dir else value
                    if os.path.isfile(candidate):
                        add(candidate)
                    elif os.path.isfile(value):
                        add(value)
        for geom in self.geant4_geometry_files:
            add(geom)
        return files

    def _output_files(self):
        """Resolve the dose / edep output filenames, preferring explicit YAML
        overrides and otherwise reading output_dose / output_edep from the
        Geant4 input file."""
        values = self.geant4_obj.get_values() if self.geant4_obj is not None else {}
        dose = self.geant4_dose_output or values.get('output_dose')
        edep = self.geant4_edep_output or values.get('output_edep')
        return {'dose': dose, 'edep': edep}

    def evaluate(self, output_dict):
        self.output_data = {}
        if output_dict is None:
            return self.output_data
        files = self._output_files()
        # 'scoring' is a back-compat alias for the dose grid.
        grids = {
            'dose': self._read_scoring_output(files['dose']),
            'edep': self._read_scoring_output(files['edep']),
        }
        grids['scoring'] = grids['dose']
        for output_name, output_params in output_dict.items():
            if not isinstance(output_params, list) or len(output_params) < 2:
                self.output_data[output_name] = float('nan')
                continue
            section, entry = output_params[0], output_params[1]
            if section not in grids:
                raise ValueError("Unknown section name '" + str(section) + "' in output dict.")
            scoring = grids[section]
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
                raise ValueError("Unknown entry '" + str(entry) + "' in '" + str(section) + "' section.")
        return self.output_data

    def _read_scoring_output(self, filename):
        if not filename:
            return None
        path = os.path.join(self.workdir, filename)
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

    def run_sweep(self, inputs=None, output_dict=None):
        if inputs is None:
            inputs = self.inputs
        if output_dict is None:
            output_dict = self.output_dict or {}
        self.output_varname = list(output_dict.keys())
        self.sweep_data = {}

        _run_sweep(self)

        for i in range(self.input_tensor.shape[0]):
            scalars = self.input_tensor[i].tolist()
            point = inputs.materialize(scalars)
            sweep_input_tuple = tuple(scalars)
            self.run(point, output_dict=output_dict, sweep_scalars=scalars)
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
        if not getattr(self, 'input_varname', None):
            print('Parameter sweep must be run first.')
            return
        WriteOmega3PDataTable(filename, self.sweep_data, self.input_varname, self.output_varname)
