#!/usr/bin/env python3
"""
Trains Gaussian process emulators.

When run as a script, allows retraining emulators, specifying the number of
principal components, and other options (however it is not necessary to do this
explicitly --- the emulators will be trained automatically when needed).  Run
``python3 src/emulator.py --help`` for usage information.

Uses the `scikit-learn <http://scikit-learn.org>`_ implementations of
`principal component analysis (PCA)
<http://scikit-learn.org/stable/modules/generated/sklearn.decomposition.PCA.html>`_
and `Gaussian process regression
<http://scikit-learn.org/stable/modules/generated/sklearn.gaussian_process.GaussianProcessRegressor.html>`_.
"""

import logging
import pickle
import dill

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.decomposition import PCA
from sklearn.externals import joblib
from sklearn.gaussian_process import GaussianProcessRegressor as GPR
from sklearn.gaussian_process import kernels
from sklearn.preprocessing import StandardScaler

from configurations import *
from calculations_load import trimmed_model_data 

###########################################################
############### Emulator and help functions ###############
###########################################################

class _Covariance:
    """
    Proxy object to extract observable sub-blocks from a covariance array.
    Returned by Emulator.predict().

    """
    def __init__(self, array, slices):
        self.array = array
        self._slices = slices

    def __getitem__(self, key):
        (obs1), (obs2) = key
        return self.array[
            ...,
            self._slices[obs1],
            self._slices[obs2]
        ]


class Emulator:
    """
    Multidimensional Gaussian process emulator using principal component
    analysis.

    The model training data are standardized (subtract mean and scale to unit
    variance), then transformed through PCA.  The first `npc` principal
    components (PCs) are emulated by independent Gaussian processes (GPs).  The
    remaining components are neglected, which is equivalent to assuming they
    are standard zero-mean unit-variance GPs.

    This class has become a bit messy but it still does the job.  It would
    probably be better to refactor some of the data transformations /
    preprocessing into modular classes, to be used with an sklearn pipeline.
    The classes would also need to handle transforming uncertainties, which
    could be tricky.

    """

    def __init__(self, system, npc, nrestarts=2):

        system_str = "{:s}-{:s}-{:d}".format(*system)
        print("Emulators for system " + system_str)
        print("with viscous correction type {:d}".format(idf))
        print("NPC : " + str(npc) )
        print("Nrestart : " + str(nrestarts))

        #list of observables is defined in calculations_file_format_event_average
        #here we get their names and sum all the centrality bins to find the total number of observables nobs
        self.nobs = 0
        self.observables = []
        self._slices = {}

        for obs, cent_list in obs_cent_list[system_str].items():
        #for obs, cent_list in calibration_obs_cent_list[system_str].items():
            self.observables.append(obs)
            n = np.array(cent_list).shape[0]
            self._slices[obs] = slice(self.nobs, self.nobs + n)
            self.nobs += n

        print("self.nobs = " + str(self.nobs))
        #read in the model data from file
        print("Loading model calculations from " + f_obs_main)

        # things to drop
        delete = []
        # build a matrix of dimension (num design pts) x (number of observables)
        Y = []
        for pt in range(n_design_pts_main - len(delete_design_pts_set)):
            row = np.array([])
            for obs in self.observables:
                #n_bins_bayes = len(calibration_obs_cent_list[system_str][obs]) # only using these bins for calibration
                #values = np.array(trimmed_model_data[system_str][pt, idf][obs]['mean'][:n_bins_bayes] )
                values = np.array(trimmed_model_data[system_str][pt, idf][obs]['mean'])
                if np.isnan(values).sum() > 0:
                    print("Warning! found nan in model data!")
                    print("Design pt = " + str(pt) + "; Obs = " + obs)
                row = np.append(row, values)
            Y.append(row)
        Y = np.array(Y)
        print("Y_Obs shape[Ndesign, Nobs] = " + str(Y.shape))

        #Principal Components
        self.npc = npc
        self.scaler = StandardScaler(copy=False)
        #whiten to ensure uncorrelated outputs with unit variances
        self.pca = PCA(copy=False, whiten=True, svd_solver='full')
        # Standardize observables and transform through PCA.  Use the first
        # `npc` components but save the full PC transformation for later.
        Z = self.pca.fit_transform( self.scaler.fit_transform(Y) )[:, :npc] # save all the rows (design points), but keep first npc columns

        design, design_max, design_min, labels = prepare_emu_design(system)

        #delete undesirable data
        if len(delete_design_pts_set) > 0:
            print("Warning! Deleting " + str(len(delete_design_pts_set)) + " points from data")
        design = np.delete(design, list(delete_design_pts_set), 0)

        ptp = design_max - design_min
        print("Design shape[Ndesign, Nparams] = " + str(design.shape))
        # Define kernel (covariance function):
        # Gaussian correlation (RBF) plus a noise term.
        # noise term is necessary since model calculations contain statistical noise
        k0 = 1. * kernels.RBF(
                      length_scale=ptp,
                      length_scale_bounds=np.outer(ptp, (6e-1, 1e2)),
                      #nu = 3.5
                   )
        k1 = kernels.ConstantKernel()
        k2 = kernels.WhiteKernel(
                                 noise_level=.3,
                                 noise_level_bounds=(1e-2, 1e2)
                                 )
        kernel = (k0 + k1 + k2)
        # Fit a GP (optimize the kernel hyperparameters) to each PC.
        print("Fitting a GP to each PC")
        self.gps = [
            GPR(
            kernel=kernel,
            alpha=0.1,
            n_restarts_optimizer=nrestarts,
            copy_X_train=False
            ).fit(design, z)
            for i, z in enumerate(Z.T)
        ]

        for n, (z, gp) in enumerate(zip(Z.T, self.gps)):
            print("GP " + str(n) + " score : " + str(gp.score(design, z)))

        print("Constructing full linear transformation matrix")
        # Construct the full linear transformation matrix, which is just the PC
        # matrix with the first axis multiplied by the explained standard
        # deviation of each PC and the second axis multiplied by the
        # standardization scale factor of each observable.
        self._trans_matrix = (
            self.pca.components_
            * np.sqrt(self.pca.explained_variance_[:, np.newaxis])
            * self.scaler.scale_
        )

        # Pre-calculate some arrays for inverse transforming the predictive
        # variance (from PC space to physical space).

        # Assuming the PCs are uncorrelated, the transformation is
        #
        #   cov_ij = sum_k A_ki var_k A_kj
        #
        # where A is the trans matrix and var_k is the variance of the kth PC.
        # https://en.wikipedia.org/wiki/Propagation_of_uncertainty

        print("Computing partial transformation for first npc components")
        # Compute the partial transformation for the first `npc` components
        # that are actually emulated.
        A = self._trans_matrix[:npc]
        self._var_trans = np.einsum('ki,kj->kij', A, A, optimize=False).reshape(npc, self.nobs**2)

        # Compute the covariance matrix for the remaining neglected PCs
        # (truncation error).  These components always have variance == 1.
        B = self._trans_matrix[npc:]
        self._cov_trunc = np.dot(B.T, B)

        # Add small term to diagonal for numerical stability.
        self._cov_trunc.flat[::self.nobs + 1] += 1e-4 * self.scaler.var_


    @classmethod
    def build_emu(cls, system, retrain=False, **kwargs):
        emu = cls(system, **kwargs)

        return emu

    def _inverse_transform(self, Z):
        """
        Inverse transform principal components to observables.

        Returns a nested dict of arrays.

        """
        # Z shape (..., npc)
        # Y shape (..., nobs)
        Y = np.dot(Z, self._trans_matrix[:Z.shape[-1]])
        Y += self.scaler.mean_

        """
        return {
            obs: {
                subobs: Y[..., s]
                for subobs, s in slices.items()
            } for obs, slices in self._slices.items()
        }
        """

        return {
            obs: Y[..., s] for obs, s in self._slices.items()
        }

    def predict(self, X, return_cov=False, extra_std=0):
        """
        Predict model output at `X`.

        X must be a 2D array-like with shape ``(nsamples, ndim)``.  It is passed
        directly to sklearn :meth:`GaussianProcessRegressor.predict`.

        If `return_cov` is true, return a tuple ``(mean, cov)``, otherwise only
        return the mean.

        The mean is returned as a nested dict of observable arrays, each with
        shape ``(nsamples, n_cent_bins)``.

        The covariance is returned as a proxy object which extracts observable
        sub-blocks using a dict-like interface:

        >>> mean, cov = emulator.predict(X, return_cov=True)

        >>> mean['dN_dy']['pion']
        <mean prediction of pion dN/dy>

        >>> cov[('dN_dy', 'pion'), ('dN_dy', 'pion')]
        <covariance matrix of pion dN/dy>

        >>> cov[('dN_dy', 'pion'), ('mean_pT', 'kaon')]
        <covariance matrix between pion dN/dy and kaon mean pT>

        The shape of the extracted covariance blocks are
        ``(nsamples, n_cent_bins_1, n_cent_bins_2)``.

        NB: the covariance is only computed between observables and centrality
        bins, not between sample points.

        `extra_std` is additional uncertainty which is added to each GP's
        predictive uncertainty, e.g. to account for model systematic error.  It
        may either be a scalar or an array-like of length nsamples.

        """
        X = transform_design(X)

        gp_mean = [gp.predict(X, return_cov=return_cov) for gp in self.gps]

        if return_cov:
            gp_mean, gp_cov = zip(*gp_mean)

        mean = self._inverse_transform(
            np.concatenate([m[:, np.newaxis] for m in gp_mean], axis=1)
        )

        if return_cov:
            # Build array of the GP predictive variances at each sample point.
            # shape: (nsamples, npc)
            gp_var = np.concatenate([
                c.diagonal()[:, np.newaxis] for c in gp_cov
            ], axis=1)

            # Add extra uncertainty to predictive variance.
            extra_std = np.array(extra_std, copy=False).reshape(-1, 1)
            gp_var += extra_std**2

            # Compute the covariance at each sample point using the
            # pre-calculated arrays (see constructor).
            cov = np.dot(gp_var, self._var_trans).reshape(
                X.shape[0], self.nobs, self.nobs
            )
            cov += self._cov_trunc

            return mean, _Covariance(cov, self._slices)
        else:
            return mean

    def sample_y(self, X, n_samples=1, random_state=None):
        """
        Sample model output at `X`.

        Returns a nested dict of observable arrays, each with shape
        ``(n_samples_X, n_samples, n_cent_bins)``.

        """
        # Sample the GP for each emulated PC.  The remaining components are
        # assumed to have a standard normal distribution.
        return self._inverse_transform(
            np.concatenate([
                gp.sample_y(
                    X, n_samples=n_samples, random_state=random_state
                )[:, :, np.newaxis]
                for gp in self.gps
            ] + [
                np.random.standard_normal(
                    (X.shape[0], n_samples, self.pca.n_components_ - self.npc)
                )
            ], axis=2)
        )

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='train emulators for each collision system',
        argument_default=argparse.SUPPRESS
    )

    parser.add_argument(
        '--npc', type=int,
        help='number of principal components'
    )
    parser.add_argument(
        '--nrestarts', type=int,
        help='number of optimizer restarts'
    )

    parser.add_argument(
        '--retrain', action='store_true',
        help='retrain even if emulator is cached'
    )

    args = parser.parse_args()
    kwargs = vars(args)

    for s in systems:
        print("system = " + str(s))
        emu = Emulator.build_emu(s, **kwargs)

        print('{} PCs explain {:.5f} of variance'.format(
            emu.npc,
            emu.pca.explained_variance_ratio_[:emu.npc].sum()
        ))

        for n, (evr, gp) in enumerate(zip(
                emu.pca.explained_variance_ratio_, emu.gps
        )):
            print(
                'GP {}: {:.5f} of variance, LML = {:.5g}, kernel: {}'
                .format(n, evr, gp.log_marginal_likelihood_value_, gp.kernel_)
            )

        #dill the emulator to be loaded later
        system_str = "{:s}-{:s}-{:d}".format(*s)
        with open('emulator/emu-' + system_str +'.dill', 'wb') as file:
            dill.dump(emu, file)


if __name__ == "__main__":
    main()

Trained_Emulators = {}
for s in system_strs:
    Trained_Emulators[s] = dill.load(open('emulator/emu-' + s + '.dill', "rb"))
