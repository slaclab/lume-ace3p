import os
import numpy as np
import pandas as pd


Q_E = 1.60217663e-19

TRACK3P_COLUMNS = [
    'InitialID', 'ImpactOrder', 'Initial_x', 'Initial_y', 'Initial_z',
    'Impact_x', 'Impact_y', 'Impact_z', 'InitialPhaseinRFcycle',
    'ImpactPhaseinRFcycle', 'ImpactEnergy', 'momentum_x', 'momentum_y',
    'momentum_z', 'ImpactFaceID', 'InitialNormalField', 'InitialFaceArea'
]


class Particles:

    def __init__(self, particle_file, particle_params, output_file=None, workdir=None):
        self.particle_file = particle_file
        self.output_file = output_file or particle_file.replace('.txt', '_modified.txt')
        self.workdir = workdir or os.getcwd()
        impact_order = particle_params.get('impact_order')
        impact_face_id = particle_params.get('impact_face_id')
        self.impact_order = impact_order if isinstance(impact_order, list) else [impact_order]
        self.impact_face_id = impact_face_id if isinstance(impact_face_id, list) else [impact_face_id]
        self.work_function = particle_params.get('work_function')
        self.dt = particle_params.get('dt')
        self.beta = np.array(particle_params.get('beta'))
        self.num_bins = particle_params.get('num_bins', len(self.beta))
        self.bin_edges = particle_params.get('bin_edges', None)
        if self.bin_edges is not None:
            self.bin_edges = np.array(self.bin_edges)
            assert len(self.bin_edges) == self.num_bins + 1, (
                f"Length of bin_edges ({len(self.bin_edges)}) must be num_bins + 1 ({self.num_bins + 1})")
        assert len(self.beta) == self.num_bins, (
            f"Length of beta ({len(self.beta)}) must match num_bins ({self.num_bins})")

    def load(self):
        filepath = os.path.join(self.workdir, self.particle_file)
        self.data = pd.read_csv(filepath, sep=r'\s+', comment='#',
                                header=None, names=TRACK3P_COLUMNS)

    def filter_particles(self):
        mask = (self.data['ImpactOrder'].isin(self.impact_order)) & \
               (self.data['ImpactFaceID'].isin(self.impact_face_id))
        self.filtered = self.data[mask].copy()

    def assign_bins(self):
        z_vals = self.filtered['Initial_z']
        if self.bin_edges is not None:
            bin_edges = self.bin_edges
        else:
            bin_edges = np.linspace(z_vals.min(), z_vals.max() + 1e-15, self.num_bins + 1)
        self.filtered['Bin'] = np.digitize(z_vals, bin_edges) - 1
        self.filtered['Bin'] = self.filtered['Bin'].clip(0, self.num_bins - 1)
        # Warn if any bins are empty
        bin_counts = np.bincount(self.filtered['Bin'], minlength=self.num_bins)
        empty_bins = np.where(bin_counts == 0)[0]
        if len(empty_bins) > 0:
            print(f'WARNING: Bins {empty_bins.tolist()} have no particles. '
                  f'Check that num_bins matches the particle distribution.')

    def calculate_particle_weight(self):
        term1 = (1.54 * 10**(-6 + (4.52 / np.sqrt(self.work_function)))) / self.work_function
        term2 = -6.53e9 * (self.work_function ** 1.5)

        beta_per_particle = self.beta[self.filtered['Bin'].values]
        beta_E = beta_per_particle * self.filtered['InitialNormalField'].values
        J = term1 * np.power(beta_E, 2) * np.exp(term2 / beta_E)
        areas = self.filtered['InitialFaceArea'].values
        self.filtered['ParticleWeight'] = np.round(J * areas * self.dt / Q_E)

    def write_output(self):
        output_path = os.path.join(self.workdir, self.output_file)
        header = '#' + ' '.join(self.filtered.columns.tolist())
        np.savetxt(output_path, self.filtered.values,
                   header=header[1:], comments='#',
                   fmt=['%d', '%d',
                        '%.6e', '%.6e', '%.6e',
                        '%.6e', '%.6e', '%.6e',
                        '%.6e', '%.6e', '%.6e',
                        '%.6e', '%.6e', '%.6e',
                        '%d', '%.6e', '%.6e',
                        '%d', '%.6e'])

    def run(self):
        self.load()
        self.filter_particles()
        self.assign_bins()
        self.calculate_particle_weight()
        self.write_output()
        print(f'Track3P particle file written: {self.output_file}')
        print(f'  Filtered particles: {len(self.filtered)} '
              f'(ImpactOrder={self.impact_order}, ImpactFaceID={self.impact_face_id})')
        print(f'  Bin counts: {np.bincount(self.filtered["Bin"], minlength=self.num_bins).tolist()}')
        print(f'  Bins: {self.num_bins}, Beta values: {self.beta.tolist()}')
        return self.filtered
