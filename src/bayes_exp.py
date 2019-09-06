#!/usr/bin/env python3
#import logging
from configurations import *
import numpy as np


#get model calculations at VALIDATION POINTS
if validation:
    print("Load calculations from " + f_obs_validation)
    Y_exp_data = np.fromfile(f_obs_validation, dtype=bayes_dtype)

#get experimental data
else :
    print("Loading experimental data from " + dir_obs_exp)
    #entry = np.zeros(1, dtype=np.dtype(bayes_dtype) )
    entry = np.zeros(1, dtype=np.dtype(calibration_bayes_dtype) )

    for system_str in system_strs:
        expt = expt_for_system[system_str]
        path_to_data = 'HIC_experimental_data/' + system_str + '/' + expt_for_system[system_str] + '/'
        #for obs in list( obs_cent_list[system].keys() ):
        for obs in list( calibration_obs_cent_list[system_str].keys() ):
            #print(obs)
            n_bins_bayes = len(calibration_obs_cent_list[system_str][obs]) # only using these bins for calibration
            for idf in range(number_of_models_per_run):

                #for STAR identified yields we have the positively charged particles only, not the sum of pos. + neg.
                if (obs in STAR_id_yields.keys() and system_str == 'Au-Au-200'):
                    expt_data = pd.read_csv(path_to_data + obs + '_+.dat', sep = ' ', skiprows=2, escapechar='#')
                    entry[system_str][obs]['mean'][:, idf] = expt_data['val'].iloc[:n_bins_bayes] * 2.0
                else :
                    expt_data = pd.read_csv(path_to_data + obs + '.dat', sep = ' ', skiprows=2, escapechar='#')
                    entry[system_str][obs]['mean'][:, idf] = expt_data['val'].iloc[:n_bins_bayes]

                try :
                    err_expt = expt_data['err'].iloc[:n_bins_bayes]
                except KeyError :
                    stat = expt_data['stat_err'].iloc[:n_bins_bayes]
                    sys = expt_data['sys_err'].iloc[:n_bins_bayes]
                    err_expt = np.sqrt(stat**2 + sys**2)

                if (obs in STAR_id_yields.keys() and system_str == 'Au-Au-200'):
                    err_expt *= np.sqrt(2.0)

                entry[system_str][obs]['err'][:, idf] = err_expt

                #print(entry[system_str][obs]['mean'][:, idf])


    Y_exp_data = entry
