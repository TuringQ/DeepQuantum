"""
Photonic measurements
"""

import numpy as np
from copy import deepcopy
from typing import Any, List, Optional, Union

import torch
from torch import nn
from torch.distributions.multivariate_normal import MultivariateNormal

from .gate import PhaseShift
from .operation import Operation


class Generaldyne(Operation):
    """General-dyne measurement.

    Args:
        cov_m (Any): The covariance matrix for the general-dyne measurement.
        nmode (int, optional): The number of modes that the quantum operation acts on. Default: 1
        wires (List[int] or None, optional): The indices of the modes that the quantum operation acts on.
            Default: ``None``
        cutoff (int or None, optional): The Fock space truncation. Default: ``None``
        name (str, optional): The name of the measurement. Default: ``'Generaldyne'`
        noise (bool, optional): Whether to introduce Gaussian noise. Default: ``False``
        mu (float, optional): The mean of Gaussian noise. Default: 0
        sigma (float, optional): The standard deviation of Gaussian noise. Default: 0.1
    """
    def __init__(
        self,
        cov_m: Any,
        nmode: int = 1,
        wires: Union[int, List[int], None] = None,
        cutoff: Optional[int] = None,
        name: str = 'Generaldyne',
        noise: bool = False,
        mu: float = 0,
        sigma: float = 0.1
    ) -> None:
        self.nmode = nmode
        if wires is None:
            wires = list(range(nmode))
        wires = self._convert_indices(wires)
        if cutoff is None:
            cutoff = 2
        super().__init__(name=name, nmode=nmode, wires=wires, cutoff=cutoff, noise=noise, mu=mu, sigma=sigma)
        if not isinstance(cov_m, torch.Tensor):
            cov_m = torch.tensor(cov_m, dtype=torch.float).reshape(-1, 2 * len(self.wires), 2 * len(self.wires))
        assert cov_m.shape[-2] == cov_m.shape[-1] == 2 * len(self.wires), 'The size of cov_m does not match the wires'
        self.register_buffer('cov_m', cov_m)
        self.samples = None

    def forward(self, x: List[torch.Tensor], shots: Optional[int] = None) -> List[torch.Tensor]:
        """Perform a forward pass for Gaussian states.

        See Quantum Continuous Variables: A Primer of Theoretical Methods (2024)
        by Alessio Serafini Eq.(5.143) and Eq.(5.144) in page 121
        """
        cov, mean = x[:2]
        wires = torch.tensor(self.wires)
        idx = torch.cat([wires, wires + self.nmode]) # xxpp order
        idx_all = torch.arange(2 * self.nmode)
        mask = ~torch.isin(idx_all, idx)
        idx_rest = idx_all[mask]
        cov_b  = cov[:, idx[:, None], idx]
        mean_b = mean[:, idx]

        if len(x) == 2:
            mean_m = MultivariateNormal(mean_b.squeeze(-1), cov_b + self.cov_m).sample([1])[0] # (batch, 2 * nwire)
            cov_out, mean_out = self.update_state(mean_m, x, idx, idx_rest)
            x_out = [cov_out, mean_out]
        if len(x) == 3:
            weight = x[2]
            mean_m = self._reject_sample(cov, mean, weight, shots, wires.tolist())
            mean_m_copy = torch.stack([mean_m[0]] * len(x[2]))
            cov_out, mean_out, weight_out = self.update_state(mean_m_copy, x, idx, idx_rest) # only take the first example to update
            x_out = [cov_out, mean_out, weight_out]
        self.samples = mean_m[:, :len(self.wires)] # xxpp order
        return x_out

    def update_state(
        self,
        mean_m: List[torch.Tensor],
        x: List[torch.Tensor],
        idx: torch.Tensor,
        idx_rest: torch.Tensor
    ) -> List[torch.Tensor]:
        """
        update the state after homodyne measuremnet
        """
        cov, mean = x[:2]
        size = cov.size()
        cov_a  = cov[:, idx_rest[:, None], idx_rest]
        cov_b  = cov[:, idx[:, None], idx]
        cov_ab = cov[:, idx_rest[:, None], idx]
        mean_a = mean[:, idx_rest]
        mean_b = mean[:, idx]

        cov_a = cov_a - cov_ab @ torch.linalg.solve(cov_b + self.cov_m, cov_ab.mT) # update the unmeasured part
        cov_out = torch.stack([torch.eye(size[-1], dtype=cov.dtype, device=cov.device)] * size[0])
        cov_out[:, idx_rest[:, None], idx_rest] = cov_a # update the total cov mat

        mean_a = mean_a + cov_ab @ torch.linalg.solve(cov_b + self.cov_m, mean_m.unsqueeze(-1) - mean_b)
        mean_out = torch.zeros_like(mean)
        mean_out[:, idx_rest] = mean_a
        if len(x) == 2:
            return [cov_out, mean_out]
        if len(x) == 3: # update weight
            weight = x[2]
            exp_factor = (mean_m.unsqueeze(-1) - mean_b).mT @ torch.linalg.solve(cov_b + self.cov_m, mean_m.unsqueeze(-1) - mean_b)
            exp_factor = exp_factor.squeeze()
            reweights = torch.exp(-0.5 * exp_factor) / torch.sqrt(torch.linalg.det(2 * torch.pi * (cov_b + self.cov_m)))
            new_weight = weight * reweights
            new_weight /= torch.sum(new_weight)
            idx_out = abs(new_weight) > 1e-8
            cov_out = cov_out[idx_out]
            mean_out = mean_out[idx_out]
            weight_out = new_weight[idx_out]
            return [cov_out, mean_out, weight_out]

    @staticmethod
    def _reject_sample(cov_i, mean_i, weight_i, shots, wires):
        """
        reject sample algorithm for bosonic state

        see https://arxiv.org/abs/2103.05530
        """
        nmode = int(cov_i.shape[-1]/2)
        indices = wires + [wire + nmode for wire in wires] # xxpp
        indices = torch.tensor(indices)
        vals = torch.zeros([shots, 2 * len(wires)])
        cov_sub = cov_i[:, indices[:, None], indices]
        mean_sub = mean_i[:, indices]
        imag_means_ind = torch.where(mean_sub.imag.any(dim=1))[0]
        nonneg_weights_ind = torch.where(torch.angle(weight_i) != torch.pi)[0]
        combined_ind = torch.cat((imag_means_ind, nonneg_weights_ind))
        ub_ind = torch.unique(combined_ind)
        ub_weight = abs(weight_i)
        if len(imag_means_ind) > 0:
            imag_means = mean_sub[imag_means_ind].imag
            imag_covs = cov_sub[imag_means_ind]
            imag_exp_arg = imag_means.mT @ torch.linalg.solve(imag_covs.real, imag_means)
            imag_prefactor = np.exp(0.5 * imag_exp_arg)
            ub_weight[imag_means_ind] *= imag_prefactor.flatten()
        ub_weight = ub_weight[ub_ind]
        ub_weights_prob = ub_weight / ub_weight.sum()
        for k in range(shots):
            drawn = False
            while not drawn:
                random_ind = torch.multinomial(ub_weights_prob, 1).item()
                peak_ind_sample = ub_ind[random_ind]
                mean_sample = mean_sub[peak_ind_sample].real # complex mean
                cov_sample = cov_sub[peak_ind_sample]
                peak_sample = MultivariateNormal(mean_sample.squeeze(-1), cov_sample.real).sample([1])[0]
                peak_sample = peak_sample.reshape(2 * len(wires), 1)
                ## compare probs
                diff_sample = peak_sample - mean_sub # complex
                cov_sub = cov_sub.to(diff_sample)
                exp_arg = diff_sample.mT @ torch.linalg.solve(cov_sub, diff_sample)
                exp_arg = exp_arg.flatten()
                ub_exp_arg = deepcopy(exp_arg)
                if len(imag_means_ind) > 0:
                    diff_sample_ub = peak_sample - mean_sub[imag_means_ind].real
                    temp = diff_sample_ub.mT @ torch.linalg.solve(imag_covs.real, diff_sample_ub)
                    temp = temp.to(ub_exp_arg.dtype)
                    ub_exp_arg[imag_means_ind] = temp.flatten()
                ub_exp_arg = ub_exp_arg[ub_ind]

                prefactors = 1 / torch.sqrt(torch.linalg.det(2 * torch.pi * cov_sub))
                prob_dist_val = torch.sum(weight_i * prefactors * torch.exp(-0.5 * exp_arg)) # f(x0)
                prob_upbnd = torch.sum(ub_weight * prefactors[ub_ind] * torch.exp(-0.5 * ub_exp_arg)) # g(x0)
                assert abs(prob_dist_val.imag) < 1e-6
                assert abs(prob_upbnd.imag) < 1e-6
                vertical_sample = torch.rand(1)[0] * prob_upbnd
                if vertical_sample.real < prob_dist_val.real:
                    drawn = True
                    vals[k] = peak_sample.flatten() # xxpp order
        return vals


class Homodyne(Generaldyne):
    """Homodyne measurement.

    Args:
        phi (Any, optional): The homodyne measurement angle. Default: ``None``
        nmode (int, optional): The number of modes that the quantum operation acts on. Default: 1
        wires (List[int] or None, optional): The indices of the modes that the quantum operation acts on.
            Default: ``None``
        cutoff (int or None, optional): The Fock space truncation. Default: ``None``
        eps (float, optional): The measurement accuracy. Default: 2e-4
        requires_grad (bool, optional): Whether the parameter is ``nn.Parameter`` or ``buffer``.
            Default: ``False`` (which means ``buffer``)
        name (str, optional): The name of the gate. Default: ``'Homodyne'``
        noise (bool, optional): Whether to introduce Gaussian noise. Default: ``False``
        mu (float, optional): The mean of Gaussian noise. Default: 0
        sigma (float, optional): The standard deviation of Gaussian noise. Default: 0.1
    """
    def __init__(
        self,
        phi: Any = None,
        nmode: int = 1,
        wires: Union[int, List[int], None] = None,
        cutoff: Optional[int] = None,
        eps: float = 2e-4,
        requires_grad: bool = False,
        noise: bool = False,
        mu: float = 0,
        sigma: float = 0.1,
        name: str = 'Homodyne'
    ) -> None:
        self.nmode = nmode
        if wires is None:
            wires = [0]
        wires = self._convert_indices(wires)
        cov_m = torch.diag(torch.tensor([eps ** 2] * len(wires) + [1 / eps ** 2] * len(wires))) # xxpp
        super().__init__(cov_m=cov_m, nmode=nmode, wires=wires, cutoff=cutoff, name=name,
                         noise=noise, mu=mu, sigma=sigma)
        assert len(self.wires) == 1, f'{self.name} must act on one mode'
        self.requires_grad = requires_grad
        self.init_para(inputs=phi)
        self.npara = 1

    def inputs_to_tensor(self, inputs: Any = None) -> torch.Tensor:
        """Convert inputs to torch.Tensor."""
        if inputs is None:
            inputs = torch.rand(len(self.wires)) * 2 * torch.pi
        elif not isinstance(inputs, (torch.Tensor, nn.Parameter)):
            inputs = torch.tensor(inputs, dtype=torch.float)
        inputs = inputs.reshape(-1)
        assert len(inputs) == len(self.wires)
        if self.noise:
            inputs = inputs + torch.normal(self.mu, self.sigma, size=(len(self.wires), )).squeeze()
        return inputs

    def init_para(self, inputs: Any = None) -> None:
        """Initialize the parameters."""
        phi = self.inputs_to_tensor(inputs)
        if self.requires_grad:
            self.phi = nn.Parameter(phi)
        else:
            self.register_buffer('phi', phi)

    def forward(self, x: List[torch.Tensor], shots: Optional[int] = None) -> List[torch.Tensor]:
        """Perform a forward pass for Gaussian states."""
        cov, mean = x[:2]
        r = PhaseShift(inputs=-self.phi, nmode=self.nmode, wires=self.wires, cutoff=self.cutoff)
        r.to(cov.dtype).to(cov.device)
        cov, mean = r([cov, mean])
        x_out = [cov, mean]
        if len(x) == 3:
            x_out.append(x[2])
        return super().forward(x_out, shots)

    def extra_repr(self) -> str:
        return f'wires={self.wires}, phi={self.phi.item()}'
