import pandas as pd
import numpy as np

from xopt import Xopt, Evaluator


with open("saved_xopt_model.yaml", 'r') as file:
    YAML = file.read()


X = Xopt.from_yaml(YAML)
X.generator.train_model()

generator = X.generator

#Define some new data to evaluate the surrogate model at. Note that this data must be in pytorch tensor form, and fidelity must be specified
#X_data = 
posterior = generator.model.posterior(X_data)
mean = posterior.mean
variance = posterior.variance