"""Plot the power balance of a t3p_power_balance result table.

    python power_balance.py [power_balance_output.txt]

The result table LUME-ACE3P writes is long-format over time: one row per
(coating thickness, t) with the three monitored powers as columns, plus the
balance

    P_balance = P_in - P_out - P_wall

which the example's ``derived_parameters`` block computes during the run.

Writes ``power_balance.png`` (one panel per swept thickness). Under dry-run every
power column is NaN, so the balance is NaN too and there is nothing to plot; that
is the reachability check, not a result.

Sign convention follows the monitors: ``inputPower`` measures flow *into* the
structure through reference surface 4, ``outputPower`` flow out through 5, and
``wallossPower`` dissipation on the coated wire. Once the pulse has cleared the
structure the three should account for each other, so ``P_balance`` settles
toward zero. While the pulse is inside, the difference is the energy still
stored in the volume and is *not* expected to vanish.
"""

import os
import sys

import numpy as np
import pandas as pd

# The sweep axis, as LUME-ACE3P names an ACE3P input parameter column.
THICKNESS = 'ace3p:ModelInfo.SurfaceMaterial.Coating.Thickness'
POWERS = ['P_in', 'P_out', 'P_wall']


def check_columns(table):
    """Exit with a message naming what is missing if `table` is not a
    t3p_power_balance result table."""
    missing = [name for name in POWERS + ['P_balance']
               if name not in table.columns]
    if missing:
        raise SystemExit(
            'table is missing ' + str(missing) + '; it has '
            + str(list(table.columns)) + '. This script expects the '
            't3p_power_balance example\'s output_parameters and '
            'derived_parameters.')


def main(path):
    table = pd.read_csv(path, sep='\t')
    check_columns(table)

    if THICKNESS in table.columns:
        groups = list(table.groupby(THICKNESS))
    else:
        groups = [(None, table)]
    for thickness, rows in groups:
        finite = int(np.isfinite(rows['P_balance']).sum())
        label = 'all rows' if thickness is None else f'thickness {thickness} m'
        print(f'  {label}: {len(rows)} rows, {finite} with a finite balance')
    if not any(np.isfinite(table['P_balance'])):
        print('  every balance is NaN — this looks like a dry run, so there is '
              'nothing to plot.')
        return

    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(len(groups), 1, sharex=True, squeeze=False,
                             figsize=(7, 3 * len(groups)))
    for axis, (thickness, rows) in zip(axes[:, 0], groups):
        time_ns = rows['t'] * 1e9
        for name in POWERS:
            axis.plot(time_ns, rows[name], label=name)
        axis.plot(time_ns, rows['P_balance'], 'k--', label='P_in - P_out - P_wall')
        axis.set_ylabel('Power [W]')
        axis.legend(fontsize='small')
        if thickness is not None:
            axis.set_title(f'coating thickness {thickness} m', fontsize='small')
    axes[-1, 0].set_xlabel('Time [ns]')
    fig.tight_layout()
    fig.savefig('power_balance.png', dpi=150)
    print('wrote power_balance.png')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'power_balance_output.txt')
