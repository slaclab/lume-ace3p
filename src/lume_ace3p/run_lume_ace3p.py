import sys
import os

from lume_ace3p.inputs import load_yaml, build_inputs
from lume_ace3p.workflow import S3PWorkflow, Omega3PWorkflow, Geant4Workflow
from lume_ace3p.run_xopt import run_xopt, run_lf_sweep
from lume_ace3p.particles import Particles


def main():
    input_file = sys.argv[1]

    try:
        lume_ace3p_data = load_yaml(input_file)
    except Exception as exc:
        print(exc)
        sys.exit(1)

    workflow_dict = lume_ace3p_data.get('workflow_parameters')
    assert 'module' in workflow_dict.keys(), "Lume-ACE3P keyword 'module' not defined"
    assert 'mode' in workflow_dict.keys(), "Lume-ACE3P keyword 'mode' not defined"

    inputs = build_inputs(lume_ace3p_data)

    output_dict = lume_ace3p_data.get('output_parameters')

    mode = workflow_dict['mode'].lower()
    module = workflow_dict['module'].lower()

    if mode == 'parameter_sweep':
        if module == 's3p':
            workflow = S3PWorkflow(workflow_dict, inputs)
            workflow.run_sweep()
        elif module == 'omega3p':
            workflow = Omega3PWorkflow(workflow_dict, inputs, output_dict)
            workflow.run_sweep()

    elif mode == 'scalar_optimize':
        if module == 's3p':
            vocs_dict = lume_ace3p_data.get('vocs_parameters')
            xopt_dict = lume_ace3p_data.get('xopt_parameters')
            vocs_dict.setdefault('constraints', {})
            vocs_dict.setdefault('observables', [])
            vocs_dict.setdefault('constants', {})
            run_xopt(workflow_dict, vocs_dict, xopt_dict)

    elif mode == 'gp_parameter_sweep':
        if module == 's3p':
            sweep_dict = lume_ace3p_data.get('sweep_parameters')
            vocs_dict = lume_ace3p_data.get('vocs_parameters')
            xopt_dict = lume_ace3p_data.get('xopt_parameters')
            vocs_dict.setdefault('constraints', {})
            vocs_dict.setdefault('observables', [])
            vocs_dict.setdefault('constants', {})
            run_lf_sweep(workflow_dict, sweep_dict, vocs_dict, xopt_dict)

    elif mode == 'particle_weight':
        if module == 'track3p':
            particle_params = lume_ace3p_data.get('particle_parameters')
            particle_input = workflow_dict.get('particle_input')
            particle_output = workflow_dict.get('particle_output')
            workdir = workflow_dict.get('workdir', os.getcwd())
            particles = Particles(particle_input, particle_params,
                                  output_file=particle_output, workdir=workdir)
            particles.run()

    elif mode == 'geant4':
        if module == 'geant4':
            particle_params = lume_ace3p_data.get('particle_parameters')
            workflow = Geant4Workflow(workflow_dict, inputs, output_dict,
                                      particle_params=particle_params)
            workflow.run()


if __name__ == '__main__':
    main()
