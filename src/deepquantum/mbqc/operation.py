"""
Base classes
"""

from typing import Any, List, Optional, Tuple, Union

import numpy as np
import random
import torch
from torch import nn

class Operation(nn.Module):
    """A base class for quantum operations.

    Args:
        name (str or None, optional): The name of the quantum operation. Default: ``None``
        nmode (int, optional): The number of modes that the quantum operation acts on. Default: 1
        wires (int, List or None, optional): The indices of the modes that the quantum operation acts on.
            Default: ``None``
        cutoff (int, optional): The Fock space truncation. Default: 2
        noise (bool, optional): Whether to introduce Gaussian noise. Default: ``False``
        mu (float, optional): The mean of Gaussian noise. Default: 0
        sigma (float, optional): The standard deviation of Gaussian noise. Default: 0.1
    """
    def __init__(
        self,
        name: Optional[str] = None,
        nqubit: int = 1,
        wires: Union[int, List, None] = None,
        signal_domain: List[int] = None
    ) -> None:
        super().__init__()
        self.name = name
        self.nqubit = nqubit
        self.wires = wires
        self.npara = 0
        self.signal_domain = signal_domain

    def tensor_rep(self, x: torch.Tensor) -> torch.Tensor:
        """Get the tensor representation of the state."""
        return x.reshape([-1] + [self.cutoff] * self.nmode)

    def init_para(self) -> None:
        """Initialize the parameters."""
        pass

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Perform a forward pass."""
        return self.tensor_rep(x)

    def _convert_indices(self, indices: Union[int, List[int]]) -> List[int]:
        """Convert and check the indices of the modes."""
        if isinstance(indices, int):
            indices = [indices]
        assert isinstance(indices, list), 'Invalid input type'
        assert all(isinstance(i, int) for i in indices), 'Invalid input type'
        assert len(set(indices)) == len(indices), 'Invalid input'
        return indices

    def _check_minmax(self, minmax: List[int]) -> None:
        """Check the minimum and maximum indices of the modes."""
        assert isinstance(minmax, list)
        assert len(minmax) == 2
        assert all(isinstance(i, int) for i in minmax)
        assert -1 < minmax[0] <= minmax[1] < self.nqubit

class Node(Operation):
    """
    Adding a node in MBQC graph
    """
    def __init__(
        self,
        wires: Union[int, List[int]] = None,
        state: Optional[torch.Tensor] = None
    ) -> None:
        wires = self._convert_indices(wires)
        if state is None:
            state = torch.sqrt(torch.tensor(2)) * torch.tensor([1,1]) / 2
        if not isinstance(state, torch.Tensor):
            state = torch.tensor(state)
        self.node_state = state
        super().__init__(name='node', wires=wires)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.kron(x, self.node_state)

    def extra_repr(self) -> str:
        return f'wires={self.wires}'

class Entanglement(Operation):
    """
    Entangling a pair of qubits via CZ gate
    """
    def __init__(
        self,
        wires: List[int] = None
    ) -> None:
        self.matrix = torch.tensor([[1, 0, 0, 0],
                                    [0, 1, 0, 0],
                                    [0, 0, 1, 0],
                                    [0, 0, 0, -1]])
        wires = self._convert_indices(wires)
        super().__init__(name='entanglement', wires=wires)

    def forward(self, wires:List, x: torch.Tensor) -> torch.Tensor:
        i, j = wires
        x = x.reshape(-1)
        nqubit = int(torch.log2(torch.tensor(len(x))))
        perm = [i, j] + [k for k in range(nqubit) if k not in (i, j)]
        inv_perm = [perm.index(k) for k in range(nqubit)]
        x = x.reshape([2] * nqubit)
        x = x.permute(*perm).reshape(-1)
        self.matrix = self.matrix.to(x.dtype)
        x = torch.matmul(self.matrix, x.view(4, -1))
        x = x.view([2] * nqubit).permute(*inv_perm).reshape(-1)
        return x

    def extra_repr(self) -> str:
        return f'wires={self.wires}'

class Measurement(Operation):
    """
    Measurement operator acting on single qubit with certain measurement plane and angle
    """
    def __init__(
        self,
        wires: Union[int, List[int]] = None,
        plane: Optional[str] = 'XY',
        angle: float = 0,
        t_domain: Union[int, List[int]] = [],
        s_domain: Union[int, List[int]] = []
    ) -> None:
        if plane is None:
            plane = 'XY'
        self.plane = plane
        if not isinstance(angle, torch.Tensor):
            angle = torch.tensor(angle)
        self.angle = angle
        self.t_domain = t_domain
        self.s_domain = s_domain
        wires = self._convert_indices(wires)
        super().__init__(name='Measurement', wires=wires)

    def func_j_alpha(self, alpha):
        if self.plane in ['XY', 'YX']: # need check
            matrix_j = torch.sqrt(torch.tensor(2))/2 * torch.tensor([[1, torch.exp(-1j * alpha)],
                                                                     [1, -torch.exp(-1j * alpha)]])
        elif self.plane in ['XZ', 'ZX']:
            matrix_j = torch.tensor([[torch.cos(alpha/2), -1j * torch.sin(alpha/2)],
                                     [torch.cos(alpha/2), 1j * torch.sin(alpha/2)]])

        elif self.plane in ['YZ', 'ZY']:
            matrix_j = torch.tensor([[torch.cos(alpha/2), torch.sin(alpha/2)],
                                     [torch.sin(alpha/2), -torch.cos(alpha/2)]])
        else:
            raise ValueError(f"Unsupported measurement plane: {self.plane}")
        return matrix_j

    def forward(self, wires: int, x: torch.Tensor, measured_dic: dict) -> torch.Tensor:
        i = wires
        s_signal = 0
        t_signal = 0
        if len(measured_dic) > 0:
            s_signal = sum(measured_dic.get(wire, 0) for wire in self.s_domain) % 2
            t_signal = sum(measured_dic.get(wire, 0) for wire in self.t_domain) % 2
        nqubit =  int(torch.log2(torch.tensor(len(x))))
        perm = [i] + [k for k in range(nqubit) if k != i]
        x = x.reshape([2] * nqubit)
        x = x.permute(*perm).reshape(-1)
        angle = (-1) ** s_signal * self.angle + torch.pi * t_signal
        j_alpha = self.func_j_alpha(angle)
        if self.plane in ['YZ', 'ZY']:
            x = torch.matmul(j_alpha.to(x.dtype), x.view(2, -1))
        else:
            x = torch.matmul(j_alpha.to(torch.complex64), x.view(2, -1).to(torch.complex64))
        probs = torch.abs(x) ** 2
        probs = probs.sum(-1)
        sample = random.choices([0, 1], weights=probs, k=1)
        self.sample = sample
        x = x.reshape([2] * nqubit)
        x_measured = x[sample[0], ...]
        x_measured = nn.functional.normalize(x_measured.reshape(2**(nqubit-1)), dim=0)
        return x_measured

    def extra_repr(self) -> str:
        return f'wires={self.wires}, plane={self.plane}, angle={self.angle}, t_domain={self.t_domain}, s_domain={self.s_domain}'

class Correction(Operation):
    """
    correction operator acting on single qubit
    """
    def __init__(
        self,
        name: Optional[str] = None,
        wires: Union[int, List[int]] = None,
        signal_domain: Union[int, List[int]] = None,
        matrix: Any = None
    ) -> None:
        wires = self._convert_indices(wires)
        if signal_domain is None:
            signal_domain = [ ]
        signal_domain = self._convert_indices(signal_domain)
        self.signal_domain = signal_domain
        self.matrix = matrix
        super().__init__(name=name, wires=wires, signal_domain=signal_domain)

    def forward(self, wires: int, x: torch.Tensor, measured_dic: dict):
        # Calculate the parity of the sum of signal values in the signal domain
        parity = sum(measured_dic.get(wire, 0) for wire in self.signal_domain) % 2
        # If parity is odd (1), apply the X or Z correction on self.wires
        if parity == 1:
            i = wires
            nqubit =  int(torch.log2(torch.tensor(len(x))))
            perm = [i] + [k for k in range(nqubit) if k != i]
            x = x.reshape([2] * nqubit)
            x = x.permute(*perm).reshape(2, -1)
            x = torch.matmul(self.matrix.to(x.dtype), x)
            # Reshape and permute back
            x = x.reshape([2] * nqubit)
            inv_perm = [perm.index(i) for i in range(nqubit)]
            x = x.permute(inv_perm).reshape(-1)
        return x

    def extra_repr(self) -> str:
        return f'wires={self.wires}, signal_domain={self.signal_domain}'

class XCorrection(Correction):
    """
    X correction operator acting on single qubit
    """
    def __init__(
        self,
        wires: Union[int, List[int]] = None,
        signal_domain: List[int] = None
    ) -> None:
        matrix = torch.tensor([[0, 1],
                               [1, 0]])
        super().__init__(name='xcorrection', wires=wires, signal_domain=signal_domain, matrix=matrix)

class ZCorrection(Correction):
    """
    X correction operator acting on single qubit
    """
    def __init__(
        self,
        wires: Union[int, List[int]] = None,
        signal_domain: List[int] = None
    ) -> None:
        matrix = torch.tensor([[1, 0],
                               [0, -1]])
        super().__init__(name='zcorrection', wires=wires, signal_domain=signal_domain, matrix=matrix)