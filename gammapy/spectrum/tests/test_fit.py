# Licensed under a 3-clause BSD style license - see LICENSE.rst
import astropy.units as u
import numpy as np
from numpy.testing import assert_allclose
from ...utils.testing import requires_data, requires_dependency
from ...utils.random import get_random_state
from ...utils.fitting import Fit
from ...irf import EffectiveAreaTable
from ...spectrum import (
    PHACountsSpectrum,
    SpectrumObservationList,
    SpectrumObservation,
    models,
    SpectrumDatasetOnOff,
    SpectrumDataset,
)


@requires_dependency("sherpa")
class TestFit:
    """Test fit on counts spectra without any IRFs"""

    def setup(self):
        self.nbins = 30
        binning = np.logspace(-1, 1, self.nbins + 1) * u.TeV
        self.source_model = models.PowerLaw(
            index=2, amplitude=1e5 / u.TeV, reference=0.1 * u.TeV
        )
        self.bkg_model = models.PowerLaw(
            index=3, amplitude=1e4 / u.TeV, reference=0.1 * u.TeV
        )

        self.alpha = 0.1
        random_state = get_random_state(23)
        npred = self.source_model.integral(binning[:-1], binning[1:])
        source_counts = random_state.poisson(npred)
        self.src = PHACountsSpectrum(
            energy_lo=binning[:-1],
            energy_hi=binning[1:],
            data=source_counts,
            backscal=1,
        )
        # Currently it's necessary to specify a lifetime
        self.src.livetime = 1 * u.s

        npred_bkg = self.bkg_model.integral(binning[:-1], binning[1:])

        bkg_counts = random_state.poisson(npred_bkg)
        off_counts = random_state.poisson(npred_bkg * 1.0 / self.alpha)
        self.bkg = PHACountsSpectrum(
            energy_lo=binning[:-1], energy_hi=binning[1:], data=bkg_counts
        )
        self.off = PHACountsSpectrum(
            energy_lo=binning[:-1],
            energy_hi=binning[1:],
            data=off_counts,
            backscal=1.0 / self.alpha,
        )

    def test_cash(self):
        """Simple CASH fit to the on vector"""
        dataset = SpectrumDataset(model=self.source_model, counts=self.src)

        npred = dataset.npred().data.data
        assert_allclose(npred[5], 660.5171, rtol=1e-5)

        stat_val = dataset.likelihood()
        assert_allclose(stat_val, -107346.5291, rtol=1e-5)

        self.source_model.parameters["index"].value = 1.12

        fit = Fit([dataset])
        result = fit.run()

        # These values are check with sherpa fits, do not change
        pars = result.parameters
        assert_allclose(pars["index"].value, 1.995525, rtol=1e-3)
        assert_allclose(pars["amplitude"].value, 100245.9, rtol=1e-3)

    def test_wstat(self):
        """WStat with on source and background spectrum"""
        on_vector = self.src.copy()
        on_vector.data.data += self.bkg.data.data

        dataset = SpectrumDatasetOnOff(model=self.source_model, counts_on=on_vector, counts_off=self.off)

        self.source_model.parameters.index = 1.12
        fit = Fit([dataset])
        result = fit.run()
        pars = result.parameters
        assert_allclose(pars["index"].value, 1.997342, rtol=1e-3)
        assert_allclose(pars["amplitude"].value, 100245.187067, rtol=1e-3)
        assert_allclose(result.total_stat, 30.022316, rtol=1e-3)

    def test_joint(self):
        """Test joint fit for obs with different energy binning"""
        dataset_1 = SpectrumDataset(model=self.source_model, counts=self.src)

        src_rebinned = self.src.rebin(2)
        dataset_2 = SpectrumDataset(model=self.source_model, counts=src_rebinned)

        fit = Fit([dataset_1, dataset_2])
        result = fit.run()
        pars = result.parameters
        assert_allclose(pars["index"].value, 1.996456, rtol=1e-3)

    def test_fit_range(self):
        """Test fit range without complication of thresholds"""
        obs = SpectrumObservation(on_vector=self.src)
        dataset = obs.to_spectrum_dataset()

        assert np.sum(dataset.mask) == self.nbins
        e_min, e_max = dataset.energy_range

        assert_allclose(e_max.value, 10)
        assert_allclose(e_min.value, 0.1)


    def test_likelihood_profile(self):
        dataset = SpectrumDataset(model=self.source_model, counts=self.src)
        fit = Fit([dataset])
        result = fit.run()
        true_idx = result.parameters["index"].value
        values = np.linspace(0.95 * true_idx, 1.05 * true_idx, 100)
        profile = fit.likelihood_profile("index", values=values)
        actual = values[np.argmin(profile["likelihood"])]
        assert_allclose(actual, true_idx, rtol=0.01)


@requires_dependency("iminuit")
@requires_data("gammapy-data")
class TestSpectralFit:
    """Test fit in astrophysical scenario"""

    def setup(self):
        path = "$GAMMAPY_DATA/joint-crab/spectra/hess/"
        obs1 = SpectrumObservation.read(path + "pha_obs23523.fits")
        obs2 = SpectrumObservation.read(path + "pha_obs23592.fits")
        self.obs_list = SpectrumObservationList([obs1, obs2])

        self.pwl = models.PowerLaw(
            index=2, amplitude=1e-12 * u.Unit("cm-2 s-1 TeV-1"), reference=1 * u.TeV
        )

        self.ecpl = models.ExponentialCutoffPowerLaw(
            index=2,
            amplitude=1e-12 * u.Unit("cm-2 s-1 TeV-1"),
            reference=1 * u.TeV,
            lambda_=0.1 / u.TeV,
        )


    def test_basic_results(self):
        dataset = self.obs_list[0].to_spectrum_dataset()
        dataset.model = self.pwl

        fit = Fit([dataset])
        result = fit.run()

        pars = result.parameters

        assert_allclose(result.total_stat, 38.343, rtol=1e-3)
        assert_allclose(pars["index"].value, 2.817, rtol=1e-3)
        assert pars["amplitude"].unit == "cm-2 s-1 TeV-1"
        assert_allclose(pars["amplitude"].value, 5.142e-11, rtol=1e-3)

        npred = dataset.npred().data.data
        assert_allclose(npred[60], 0.6102, rtol=1e-3)

    def test_basic_errors(self):
        dataset = self.obs_list[0].to_spectrum_dataset()
        dataset.model = self.pwl

        fit = Fit([dataset])
        result = fit.run()

        pars = result.parameters
        assert_allclose(pars.error("index"), 0.1496, rtol=1e-3)
        assert_allclose(pars.error("amplitude"), 6.423e-12, rtol=1e-3)

    def test_compound(self):
        dataset = self.obs_list[0].to_spectrum_dataset()
        dataset.model = self.pwl * 2

        fit = Fit([dataset])
        result = fit.run()

        pars = result.parameters
        assert_allclose(pars["index"].value, 2.8166, rtol=1e-3)
        p = pars["amplitude"]
        assert p.unit == "cm-2 s-1 TeV-1"
        assert_allclose(p.value, 5.0714e-12, rtol=1e-3)

    def test_stats(self):
        dataset = self.obs_list[0].to_spectrum_dataset()
        dataset.model = self.pwl

        fit = Fit([dataset])
        result = fit.run()

        stats = dataset.likelihood_per_bin()
        actual = np.sum(stats[dataset.mask])

        desired = result.total_stat
        assert_allclose(actual, desired)

    def test_fit_range(self):
        # Fit range not restriced fit range should be the thresholds
        obs = self.obs_list[0]
        desired = obs.on_vector.lo_threshold

        dataset = obs.to_spectrum_dataset()
        actual = dataset.energy_range[0]

        assert actual.unit == "keV"
        assert_allclose(actual.value, desired.value)

    def test_no_edisp(self):
        dataset = self.obs_list[0].to_spectrum_dataset()

        # Bring aeff in RECO space
        data = dataset.aeff.data.evaluate(energy=dataset.counts_on.energy.nodes)
        dataset.aeff = EffectiveAreaTable(
            data=data,
            energy_lo=dataset.counts_on.energy.lo,
            energy_hi=dataset.counts_on.energy.hi,
        )
        dataset.edisp = None
        dataset.model = self.pwl

        fit = Fit([dataset])
        result = fit.run()
        assert_allclose(
            result.parameters["index"].value, 2.7961, atol=0.02
        )

    def test_ecpl_fit(self):
        dataset = self.obs_list[0].to_spectrum_dataset()
        dataset.model = self.ecpl

        fit = Fit([dataset])
        result = fit.run()
        actual = result.parameters["lambda_"].quantity
        assert actual.unit == "TeV-1"
        assert_allclose(actual.value, 0.145215, rtol=1e-2)

    def test_joint_fit(self):
        datasets = [_.to_spectrum_dataset() for _  in self.obs_list]

        for dataset in datasets:
            dataset.model = self.pwl

        fit = Fit(datasets)
        result = fit.run()

        pars = result.parameters
        assert_allclose(pars["index"].value, 2.7806, rtol=1e-3)

        actual = pars["amplitude"].quantity
        assert actual.unit == "cm-2 s-1 TeV-1"
        assert_allclose(actual.value, 5.200e-11, rtol=1e-3)

    def test_stacked_fit(self):
        stacked_obs = self.obs_list.stack()

        dataset = stacked_obs.to_spectrum_dataset()
        dataset.model = self.pwl

        fit = Fit([dataset])
        result = fit.run()
        pars = result.parameters

        assert_allclose(pars["index"].value, 2.7767, rtol=1e-3)
        assert u.Unit(pars["amplitude"].unit) == "cm-2 s-1 TeV-1"
        assert_allclose(pars["amplitude"].value, 5.191e-11, rtol=1e-3)

    @requires_dependency("sherpa")
    def test_sherpa_fit(self, tmpdir):
        # this is to make sure that the written PHA files work with sherpa
        import sherpa.astro.ui as sau
        from sherpa.models import PowLaw1D

        # TODO: this works a little bit, but some info and warnings
        # from Sherpa remain. Not sure what to do, OK as-is for now.
        import logging

        logging.getLogger("sherpa").setLevel("ERROR")

        self.obs_list.write(tmpdir, use_sherpa=True)
        filename = tmpdir / "pha_obs23523.fits"
        sau.load_pha(str(filename))
        sau.set_stat("wstat")
        model = PowLaw1D("powlaw1d.default")
        model.ref = 1e9
        model.ampl = 1
        model.gamma = 2
        sau.set_model(model * 1e-20)
        sau.fit()
        assert_allclose(model.pars[0].val, 2.732, rtol=1e-3)
        assert_allclose(model.pars[2].val, 4.647, rtol=1e-3)
