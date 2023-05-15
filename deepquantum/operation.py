import torch
import torch.nn as nn
import numpy as np
from deepquantum.qmath import inverse_permutation, state_to_tensors
from deepquantum.state import MatrixProductState
import warnings
from copy import copy


class Operation(nn.Module):
    def __init__(self, name=None, nqubit=1, wires=None, den_mat=False, tsr_mode=False):
        super().__init__()
        self.name = name
        self.nqubit = nqubit
        self.wires = wires
        self.den_mat = den_mat
        self.tsr_mode = tsr_mode
        self.npara = 0

    def tensor_rep(self, x):
        if self.den_mat:
            assert x.shape[-1] == 2 ** self.nqubit and x.shape[-2] == 2 ** self.nqubit
            return x.reshape([-1] + [2] * 2 * self.nqubit)
        else:
            if x.ndim == 1:
                assert x.shape[-1] == 2 ** self.nqubit
            else:
                assert x.shape[-1] == 2 ** self.nqubit or x.shape[-2] == 2 ** self.nqubit
            return x.reshape([-1] + [2] * self.nqubit)

    def vector_rep(self, x):
        return x.reshape([-1, 2 ** self.nqubit, 1])

    def matrix_rep(self, x):
        return x.reshape([-1, 2 ** self.nqubit, 2 ** self.nqubit])

    def get_unitary(self):
        raise NotImplementedError
        
    def init_para(self):
        pass

    def forward(self, x):
        if self.tsr_mode:
            return self.tensor_rep(x)
        else:
            if self.den_mat:
                return self.matrix_rep(x)
            else:
                return self.vector_rep(x)


class Gate(Operation):
    qasm_new_gate = []

    def __init__(self, name=None, nqubit=1, wires=[0], controls=None, den_mat=False, tsr_mode=False):
        if type(wires) == int:
            wires = [wires]
        if type(controls) == int:
            controls = [controls]
        if controls == None:
            controls = []
        assert type(wires) == list and type(controls) == list, 'Invalid input type'
        assert all(isinstance(i, int) for i in wires), 'Invalid input type'
        assert all(isinstance(i, int) for i in controls), 'Invalid input type'
        assert min(wires) > -1 and max(wires) < nqubit, 'Invalid input'
        if len(controls) > 0:
            assert min(controls) > -1 and max(controls) < nqubit, 'Invalid input'
        assert len(set(wires)) == len(wires) and len(set(controls)) == len(controls), 'Invalid input'
        for wire in wires:
            assert wire not in controls, 'Use repeated wires'
        self.nwire = len(wires) + len(controls)
        self.controls = controls
        super().__init__(name=name, nqubit=nqubit, wires=wires, den_mat=den_mat, tsr_mode=tsr_mode)

    def update_matrix(self):
        return self.matrix

    def op_state(self, x):
        matrix = self.update_matrix()
        if self.controls == []:
            x = self.op_state_base(x=x, matrix=matrix)
        else:
            x = self.op_state_control(x=x, matrix=matrix)
        if not self.tsr_mode:
            x = self.vector_rep(x).squeeze(0)
        return x
    
    def op_state_base(self, x, matrix):
        nt = len(self.wires)
        wires = [i + 1 for i in self.wires]
        pm_shape = list(range(self.nqubit + 1))
        for i in wires:
            pm_shape.remove(i)
        pm_shape = wires + pm_shape
        x = x.permute(pm_shape).reshape(2 ** nt, -1)
        x = (matrix @ x).reshape([2] * nt + [-1] + [2] * (self.nqubit - nt))
        x = x.permute(inverse_permutation(pm_shape))
        return x
    
    def op_state_control(self, x, matrix):
        nt = len(self.wires)
        nc = len(self.controls)
        wires = [i + 1 for i in self.wires]
        controls = [i + 1 for i in self.controls]
        pm_shape = list(range(self.nqubit + 1))
        for i in wires:
            pm_shape.remove(i)
        for i in controls:
            pm_shape.remove(i)
        pm_shape = wires + pm_shape + controls
        state1 = x.permute(pm_shape).reshape(2 ** nt, -1, 2 ** nc)
        state2 = (matrix @ state1[:, :, -1]).unsqueeze(-1)
        state1 = torch.cat([state1[:, :, :-1], state2], dim=-1)
        state1 = state1.reshape([2] * nt + [-1] + [2] * (self.nqubit - nt - nc) + [2] * nc)
        x = state1.permute(inverse_permutation(pm_shape))
        return x
    
    def op_den_mat(self, x):
        matrix = self.update_matrix()
        if self.controls == []:
            x = self.op_den_mat_base(x=x, matrix=matrix)
        else:
            x = self.op_den_mat_control(x=x, matrix=matrix)
        if not self.tsr_mode:
            x = self.matrix_rep(x).squeeze(0)
        return x
        
    def op_den_mat_base(self, x, matrix):
        nt = len(self.wires)
        # left multiply
        wires = [i + 1 for i in self.wires]
        pm_shape = list(range(2 * self.nqubit + 1))
        for i in wires:
            pm_shape.remove(i)
        pm_shape = wires + pm_shape
        x = x.permute(pm_shape).reshape(2 ** nt, -1)
        x = (matrix @ x).reshape([2] * nt + [-1] + [2] * (2 * self.nqubit - nt))
        x = x.permute(inverse_permutation(pm_shape))
        # right multiply
        wires = [i + 1 + self.nqubit for i in self.wires]
        pm_shape = list(range(2 * self.nqubit + 1))
        for i in wires:
            pm_shape.remove(i)
        pm_shape = wires + pm_shape
        x = x.permute(pm_shape).reshape(2 ** nt, -1)
        x = (matrix.conj() @ x).reshape([2] * nt + [-1] + [2] * (2 * self.nqubit - nt))
        x = x.permute(inverse_permutation(pm_shape))
        return x
    
    def op_den_mat_control(self, x, matrix):
        nt = len(self.wires)
        nc = len(self.controls)
        # left multiply
        wires = [i + 1 for i in self.wires]
        controls = [i + 1 for i in self.controls]
        pm_shape = list(range(2 * self.nqubit + 1))
        for i in wires:
            pm_shape.remove(i)
        for i in controls:
            pm_shape.remove(i)
        pm_shape = wires + pm_shape + controls
        state1 = x.permute(pm_shape).reshape(2 ** nt, -1, 2 ** nc)
        state2 = (matrix @ state1[:, :, -1]).unsqueeze(-1)
        state1 = torch.cat([state1[:, :, :-1], state2], dim=-1)
        state1 = state1.reshape([2] * nt + [-1] + [2] * (2 * self.nqubit - nt - nc) + [2] * nc)
        x = state1.permute(inverse_permutation(pm_shape))
        # right multiply
        wires = [i + 1 + self.nqubit for i in self.wires]
        controls = [i + 1 + self.nqubit for i in self.controls]
        pm_shape = list(range(2 * self.nqubit + 1))
        for i in wires:
            pm_shape.remove(i)
        for i in controls:
            pm_shape.remove(i)
        pm_shape = wires + pm_shape + controls
        state1 = x.permute(pm_shape).reshape(2 ** nt, -1, 2 ** nc)
        state2 = (matrix.conj() @ state1[:, :, -1]).unsqueeze(-1)
        state1 = torch.cat([state1[:, :, :-1], state2], dim=-1)
        state1 = state1.reshape([2] * nt + [-1] + [2] * (2 * self.nqubit - nt - nc) + [2] * nc)
        x = state1.permute(inverse_permutation(pm_shape))
        return x

    def forward(self, x):
        if type(x) == MatrixProductState:
            return self.op_mps(x)
        if not self.tsr_mode:
            x = self.tensor_rep(x)
        if self.den_mat:
            assert x.ndim == 2 * self.nqubit + 1
            return self.op_den_mat(x)
        else:
            assert x.ndim == self.nqubit + 1
            return self.op_state(x)

    def extra_repr(self):
        s = f'wires={self.wires}'
        if self.controls == []:
            return s
        else:
            return s + f', controls={self.controls}'
    
    def qasm_customized(self, name):
        name = name.lower()
        if len(self.controls) > 2:
            name = f'c{len(self.controls)}{name}_'
        else:
            name = 'c' * len(self.controls) + f'{name}_'
        # warnings.warn(f'{name} is an empty gate and should be only used to draw circuit.')
        qasm_str1 = f'gate {name} '
        qasm_str2 = f'{name} '
        for i, wire in enumerate(self.controls):
            qasm_str1 += f'q{i},'
            qasm_str2 += f'q[{wire}],'
        for i, wire in enumerate(self.wires):
            qasm_str1 += f'q{len(self.controls) + i},'
            qasm_str2 += f'q[{wire}],'
        qasm_str1 = qasm_str1[:-1] + ' { }\n'
        qasm_str2 = qasm_str2[:-1] + ';\n'
        if name not in Gate.qasm_new_gate:
            Gate.qasm_new_gate.append(name)
            return qasm_str1 + qasm_str2
        else:
            return qasm_str2

    def get_mpo(self):
        """
        Convert gate to MPO form with identities at empty sites
        """
        # If sites are not adjacent, insert identities in the middle, i.e.
        #   |       |             |   |   |
        # --A---x---B--   ->    --A---I---B--
        #   |       |             |   |   |
        # where
        #      a
        #      |
        # --i--I--j-- = \delta_{i,j} \delta_{a,b}
        #      |
        #      b
        index = self.wires + self.controls
        index_left = min(index)
        nindex = len(index)
        index_sort = sorted(index)
        # convert index to a list of integers from 0 to nindex-1
        s = {x: i for i, x in enumerate(index_sort)}
        index_local = [s[x] for x in index]
        # use shallow copy to share parameters
        gate_copy = copy(self)
        gate_copy.nqubit = nindex
        gate_copy.wires = index_local[:len(gate_copy.wires)]
        gate_copy.controls = index_local[len(gate_copy.wires):]
        u = gate_copy.get_unitary()
        # transform gate from (out1, out2, ..., in1, in2 ...) to (out1, in1, out2, in2, ...)
        order = list(np.arange(2 * nindex).reshape((2, nindex)).T.flatten())
        u = u.reshape([2] * 2 * nindex).permute(order).reshape([4] * nindex)
        main_tensors = state_to_tensors(u, nqubit=nindex, qudit=4)
        # each tensor is in shape of (i, a, b, j)
        tensors = []
        previous_i = None
        for i, main_tensor in zip(index_sort, main_tensors):
            # insert identites in the middle
            if previous_i is not None:
                for _ in range(previous_i + 1, i):
                    chi = tensors[-1].shape[-1]
                    identity = torch.eye(chi * 2, dtype=self.matrix.dtype, device=self.matrix.device)
                    tensors.append(identity.reshape(chi, 2, chi, 2).permute(0, 1, 3, 2))
            nleft, _, nright = main_tensor.shape
            tensors.append(main_tensor.reshape(nleft, 2, 2, nright))
            previous_i = i
        return tensors, index_left
    
    def op_mps(self, mps: MatrixProductState):
        # use TEBD algorithm
        #
        #     contract tensor
        #           a
        #           |
        #     i-----O-----j            a
        #           |        ->        |
        #           b             ik---X---jl
        #           |
        #     k-----T-----l
        mpo_tensors, left = self.get_mpo()
        mps.center_orthogonalization(left, dc=mps.chi, normalize=mps.normalize)
        mps_tensors = mps.tensors
        for i, mpo_tensor in enumerate(mpo_tensors):
            mps_tensors[left + i] = torch.einsum('iabj,...kbl->...ikajl', mpo_tensor, mps_tensors[left + i])
            s = mps_tensors[left + i].shape
            if len(s) == 5:
                mps_tensors[left + i] = mps_tensors[left + i].reshape(s[-5] * s[-4], s[-3], s[-2] * s[-1])
            else:
                mps_tensors[left + i] = mps_tensors[left + i].reshape(-1, s[-5] * s[-4], s[-3], s[-2] * s[-1])
        out = MatrixProductState(nqubit=mps.nqubit, state=mps_tensors, chi=mps.chi, normalize=mps.normalize)
        out.center_orthogonalization(left + len(mpo_tensors) - 1, dc=out.chi, normalize=out.normalize)
        return out


class Layer(Operation):
    def __init__(self, name=None, nqubit=1, wires=[[0]], den_mat=False, tsr_mode=False):
        if type(wires) == int:
            wires = [[wires]]
        assert type(wires) == list, 'Invalid input type'
        if all(isinstance(i, int) for i in wires):
            wires = [[i] for i in wires]
        assert all(isinstance(i, list) for i in wires), 'Invalid input type'
        for wire in wires:
            assert all(isinstance(i, int) for i in wire), 'Invalid input type'
            assert min(wire) > -1 and max(wire) < nqubit, 'Invalid input'
            assert len(set(wire)) == len(wire), 'Invalid input'
        super().__init__(name=name, nqubit=nqubit, wires=wires, den_mat=den_mat, tsr_mode=tsr_mode)
        self.gates = nn.Sequential()

    def get_unitary(self):
        u = None
        for gate in self.gates:
            if u == None:
                u = gate.get_unitary()
            else:
                u = gate.get_unitary() @ u
        return u

    def init_para(self, inputs=None):
        count = 0
        for gate in self.gates:
            if inputs == None:
                gate.init_para(inputs)
            else:
                gate.init_para(inputs[count:count+gate.npara])
            count += gate.npara
    
    def update_npara(self):
        self.npara = 0
        for gate in self.gates:
            self.npara += gate.npara

    def forward(self, x):
        if type(x) == MatrixProductState:
            return self.gates(x)
        if not self.tsr_mode:
            x = self.tensor_rep(x)
        x = self.gates(x)
        if not self.tsr_mode:
            if self.den_mat:
                return self.matrix_rep(x).squeeze(0)
            else:
                return self.vector_rep(x).squeeze(0)
        return x
    
    def qasm(self):
        s = ''
        for gate in self.gates:
            s += gate.qasm()
        return s