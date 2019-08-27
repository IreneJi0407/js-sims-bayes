#!/usr/bin/env python3

from configurations import *
import logging
import numpy as np

print("Loading model calculations from " + f_obs_main)
model_data = np.fromfile(f_obs_main, dtype=bayes_dtype)
print("model_data.shape = " + str(model_data.shape))

# things to drop
delete_sets = set()
for pt in range(n_design_pts_main): # loop over all design points
    system_str = 'Pb-Pb-2760'
    for obs in active_obs_list[system_str]:
        #print(model_data[system_str][:,:][obs]['mean'].shape)
        values = np.array( model_data[system_str][pt, idf][obs]['mean'] )
        # delete Nan dataset
        isnan = np.isnan(values)
        if np.sum(isnan) > 0:
            model_data[system_str][pt, idf][obs]['mean'][isnan] = np.mean(values[np.logical_not(isnan)])

delete_sets = []

if len(delete_sets) > 0 :
    print("Design points which will be deleted from training : " + str( np.sort( list(delete_sets) ) ) )
    trimmed_model_data = np.delete(model_data, list(delete_sets), 0)
else :
    print("No design points will be deleted from training")
    trimmed_model_data = model_data
