# Reference configs for the Geant4 dose-surrogate modes

The four surrogate modes (`collect_training_data`, `train_surrogate`,
`invert_optimize`, `invert_bayesian`) ship no example: the pipeline has one
user, and commit 13deade retired `examples/geant4_beta_surrogate` in 0.8.2.
These are that example's configs, kept so `tests/test_config_validation.py`
still checks their key sets against `modes.MODE_KEYS` the way it checks the
shipped examples. They are read for their keys, not run; the paths inside them
are the retired example's.
