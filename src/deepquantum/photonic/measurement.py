"""
Photonic measurements
"""

from typing import Any, List, Optional, Union

import numpy as np
import torch
from scipy.special import factorial
from torch import nn, vmap
from torch.distributions.multivariate_normal import MultivariateNormal

import deepquantum.photonic as dqp
from .gate import PhaseShift, Displacement
from .operation import Operation, evolve_state, evolve_den_mat
from .qmath import sample_homodyne_fock, sample_reject_bosonic


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

    def forward(self, x: List[torch.Tensor]) -> List[torch.Tensor]:
        """Perform a forward pass for Gaussian states.

        See Quantum Continuous Variables: A Primer of Theoretical Methods (2024)
        by Alessio Serafini Eq.(5.143) and Eq.(5.144) in page 121

        For Bosonic state, see https://arxiv.org/abs/2103.05530 Eq.(35-37)
        """
        cov, mean = x[:2]
        wires = torch.tensor(self.wires)
        size = cov.size()
        idx = torch.cat([wires, wires + self.nmode]) # xxpp order
        idx_all = torch.arange(2 * self.nmode)
        mask = ~torch.isin(idx_all, idx)
        idx_rest = idx_all[mask]

        cov_a  = cov[..., idx_rest[:, None], idx_rest]
        cov_b  = cov[..., idx[:, None], idx]
        cov_ab = cov[..., idx_rest[:, None], idx]
        mean_a = mean[..., idx_rest, :]
        mean_b = mean[..., idx, :]
        cov_t = cov_b + self.cov_m

        cov_a = cov_a - cov_ab @ torch.linalg.solve(cov_t, cov_ab.mT) # update the unmeasured part
        cov_out = cov.new_ones(size[:-1].numel()).reshape(*size[:-1]).diag_embed()
        cov_out[..., idx_rest[:, None], idx_rest] = cov_a # update the total cov mat

        if len(x) == 2: # Gaussian
            mean_m = MultivariateNormal(mean_b.squeeze(-1), cov_t).sample([1])[0] # (batch, 2 * nwire)
            mean_a = mean_a + cov_ab @ torch.linalg.solve(cov_t, mean_m.unsqueeze(-1) - mean_b)
        elif len(x) == 3: # Bosonic
            weight = x[2]
            mean_m = sample_reject_bosonic(cov_b, mean_b, weight, self.cov_m, 1)[:, 0] # (batch, 2 * nwire)
            # (batch, ncomb)
            exp_real = torch.exp(mean_b.imag.mT @ torch.linalg.solve(cov_t, mean_b.imag) / 2).squeeze()
            gaus_b = MultivariateNormal(mean_b.squeeze(-1).real, cov_t) # (batch, ncomb, 2 * nwire)
            prob_g = gaus_b.log_prob(mean_m.unsqueeze(-2)).exp() # (batch, ncomb, 2 * nwire) -> (batch, ncomb)
            rm = mean_m.unsqueeze(-1).unsqueeze(-3) # (batch, 2 * nwire) -> (batch, 1, 2 * nwire, 1)
            # (batch, ncomb)
            exp_imag = torch.exp((rm - mean_b.real).mT @ torch.linalg.solve(cov_t, mean_b.imag) * 1j).squeeze()
            weight *= exp_real * prob_g * exp_imag
            weight /= weight.sum(dim=-1, keepdim=True)
            mean_a = mean_a + cov_ab.to(mean_b.dtype) @ torch.linalg.solve(cov_t.to(mean_b.dtype), rm - mean_b)

        mean_out = torch.zeros_like(mean)
        mean_out[..., idx_rest, :] = mean_a

        self.samples = mean_m # xxpp order
        if len(x) == 2:
            return [cov_out, mean_out]
        elif len(x) == 3:
            return [cov_out, mean_out, weight]


class Homodyne(Generaldyne):
    """Homodyne measurement.

    Args:
        phi (Any, optional): The homodyne measurement angle. Default: ``None``
        nmode (int, optional): The number of modes that the quantum operation acts on. Default: 1
        wires (List[int] or None, optional): The indices of the modes that the quantum operation acts on.
            Default: ``None``
        cutoff (int or None, optional): The Fock space truncation. Default: ``None``
        den_mat (bool, optional): Whether to use density matrix representation. Default: ``False``
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
        den_mat: bool = False,
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
        self.den_mat = den_mat

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

    def op_fock(self, x: torch.Tensor) -> torch.Tensor:
        """Perform a forward pass for Fock state tensors."""
        r = PhaseShift(inputs=-self.phi, nmode=self.nmode, wires=self.wires, cutoff=self.cutoff, den_mat=self.den_mat)
        samples = sample_homodyne_fock(r(x), self.wires[0], self.nmode, self.cutoff, 1, self.den_mat) # (batch, 1, 1)
        self.samples = samples.squeeze(-2) # with dimension \sqrt{m\omega\hbar}
        # projection operator as single gate
        vac_state = x.new_zeros(self.cutoff) # (cutoff)
        vac_state[0] = 1
        inf_sqz_vac = torch.zeros_like(vac_state) # (cutoff)
        orders = torch.arange(np.ceil(self.cutoff / 2), dtype=x.real.dtype, device=x.device)
        fac_2n = torch.tensor(factorial(2 * orders), dtype=orders.dtype, device=orders.device)
        fac_n = torch.tensor(factorial(orders), dtype=orders.dtype, device=orders.device)
        inf_sqz_vac[::2] = (-0.5)**orders * fac_2n**0.5 / fac_n # unnormalized
        alpha = self.samples * dqp.kappa / dqp.hbar**0.5
        d = Displacement(cutoff=self.cutoff)
        d_mat = vmap(d.get_matrix_state, in_dims=(0, None))(alpha, 0) # (batch, cutoff, cutoff)
        r = PhaseShift(inputs=self.phi, nmode=1, wires=0, cutoff=self.cutoff)
        eigenstate = r(evolve_state(inf_sqz_vac.unsqueeze(0), d_mat, 1, [0], self.cutoff)) # (batch, cutoff)
        project_op = vmap(torch.outer, in_dims=(None, 0))(vac_state, eigenstate.conj()) # (batch, cutoff, cutoff)
        if self.den_mat: # (batch, 1, [cutoff] * 2 * nmode)
            evolve = vmap(evolve_den_mat, in_dims=(0, 0, None, None, None))
        else: # (batch, 1, [cutoff] * nmode)
            evolve = vmap(evolve_state, in_dims=(0, 0, None, None, None))
        x = evolve(x.unsqueeze(1), project_op, self.nmode, self.wires, self.cutoff).squeeze(1)
        # normalization
        if self.den_mat:
            norm = vmap(torch.trace)(x.reshape(-1, self.cutoff ** self.nmode, self.cutoff ** self.nmode)) # (batch)
            x = x / norm.reshape([-1] + [1] * 2 * self.nmode)
        else:
            norm = (abs(x.reshape(-1, self.cutoff ** self.nmode)) ** 2).sum(-1) ** 0.5 # (batch)
            x = x / norm.reshape([-1] + [1] * self.nmode)
        return x

    def op_cv(self, x: List[torch.Tensor]) -> List[torch.Tensor]:
        """Perform a forward pass for Gaussian (Bosonic) states."""
        r = PhaseShift(inputs=-self.phi, nmode=self.nmode, wires=self.wires, cutoff=self.cutoff)
        cov, mean = x[:2]
        cov, mean = r([cov, mean])
        return super().forward([cov, mean] + x[2:])

    def forward(self, x: Union[torch.Tensor, List[torch.Tensor]]) -> Union[torch.Tensor, List[torch.Tensor]]:
        """Perform a forward pass."""
        if isinstance(x, torch.Tensor):
            return self.op_fock(x)
        elif isinstance(x, list):
            return self.op_cv(x)

    def extra_repr(self) -> str:
        return f'wires={self.wires}, phi={self.phi.item()}'
