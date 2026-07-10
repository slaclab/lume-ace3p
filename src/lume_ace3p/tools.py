"""Legacy sweep-table writers, reduced to thin ``to_csv`` helpers (Phase 5).

Historically these built tab-delimited tables by hand from the dict
``sweep_data`` tuple-keyed structure. Phase 5 consolidates *all* result-table
writing onto the single shared path in :mod:`lume_ace3p.results`
(``DataFrame.to_csv``). The new mode layer no longer uses ``sweep_data`` at all;
these helpers survive only because the legacy
``Omega3PWorkflow``/``S3PWorkflow`` subclasses in ``workflow.py`` still call
them (kept callable through Phase 5 for the equivalence tests, deleted in
Phase 6). They now translate the dict into a DataFrame and defer to
:func:`lume_ace3p.results.write_table`, so there is one and only one writer.
"""

import pandas as pd

from lume_ace3p.results import write_table


def _clean_input_name(name):
    """Reproduce the legacy readable-name parse: an ``ACE3P``-prefixed input
    name (the old uppercase prefix run_lume_ace3p.py used to attach) is reduced
    to its last ``_``-delimited segment. Modern ``ace3p:``-prefixed names do not
    match and pass through unchanged."""
    if name.startswith('ACE3P'):
        return name.rsplit('_', 1)[1]
    return name


def _sweep_data_to_frame(sweep_data, input_names, output_names):
    """Turn the tuple-keyed ``sweep_data`` dict into a wide result DataFrame:
    one row per evaluation, columns = (cleaned) input names + output names."""
    columns = [_clean_input_name(n) for n in input_names] + list(output_names)
    rows = []
    for key, value in sweep_data.items():
        row = {}
        for i, name in enumerate(input_names):
            row[_clean_input_name(name)] = key[i]
        for name in output_names:
            row[name] = value[name]
        rows.append(row)
    return pd.DataFrame(rows, columns=columns)


def WriteOmega3PDataTable(filename, sweep_data, input_names, output_names):
    """Write an Omega3P sweep table. Thin wrapper: build the wide DataFrame from
    ``sweep_data`` and route it through the shared writer."""
    df = _sweep_data_to_frame(sweep_data, input_names, output_names)
    write_table(df, filename)


def _s3p_sweep_data_to_frame(sweep_data, input_names):
    """Turn the tuple-keyed S3P ``sweep_data`` dict into a long/tidy DataFrame:
    one row per ``(evaluation, frequency)``; columns = (cleaned) input names +
    ``Frequency`` + every S-parameter key present in the run."""
    first = list(sweep_data.values())[0]
    skeys = [k for k in first.keys() if k not in ('IndexMap', 'Frequency')]
    columns = [_clean_input_name(n) for n in input_names] + ['Frequency'] + skeys
    rows = []
    for key, value in sweep_data.items():
        for idf in range(len(value['Frequency'])):
            row = {}
            for i, name in enumerate(input_names):
                row[_clean_input_name(name)] = key[i]
            row['Frequency'] = value['Frequency'][idf]
            for skey in skeys:
                row[skey] = value[skey][idf]
            rows.append(row)
    return pd.DataFrame(rows, columns=columns)


def WriteS3PDataTable(filename, sweep_data, input_names, is_xopt=False,
                      iteration_index=None):
    """Write an S3P long-format sweep table via the shared writer.

    The ``is_xopt`` append path (an Xopt iteration column, appended per step)
    was a legacy ``run_xopt`` logging shape; Phase 4's generic Xopt modes log
    ``X.data`` directly through :func:`lume_ace3p.results.write_table`, so that
    path is dropped here. The remaining (sweep) behavior builds a tidy DataFrame
    and defers to the one writer."""
    df = _s3p_sweep_data_to_frame(sweep_data, input_names)
    if is_xopt:
        # Legacy xopt-append logging is superseded by modes._log_xopt; keep a
        # minimal iteration-tagged append so any legacy caller still functions.
        df.insert(0, 'Iteration', iteration_index or 0)
        with open(filename, 'a') as f:
            df.to_csv(f, sep='\t', index=False, na_rep='nan',
                      header=(iteration_index in (None, 0)))
        return
    write_table(df, filename)


def WriteXoptData(filename, param_dict, Xopt_data, iteration_index,
                  final_iteration=False):
    """Write an Xopt data table. Reduced to the shared ``to_csv`` writer
    (``Xopt_data`` is already a DataFrame); the old ``to_string`` pretty-dump is
    dropped. Retained only for legacy ``run_xopt`` callers (Phase 6 removes it).
    """
    write_table(Xopt_data, filename)
