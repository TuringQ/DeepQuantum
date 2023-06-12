import torch
from deepquantum.qmath import int_to_bitstring
from deepquantum.circuit import QubitCircuit
import random


class Ansatz(QubitCircuit):
    def __init__(self, nqubit, wires=None, minmax=None, ancilla=None, controls=None, init_state='zeros',
                 name=None, den_mat=False, mps=False, chi=None):
        super().__init__(nqubit=nqubit, init_state=init_state, name=name, den_mat=den_mat, mps=mps, chi=chi)
        if type(wires) == int:
            wires = [wires]
        if wires == None:
            if minmax == None:
                minmax = [0, nqubit - 1]
            assert type(minmax) == list
            assert len(minmax) == 2
            assert all(isinstance(i, int) for i in minmax)
            assert minmax[0] > -1 and minmax[0] <= minmax[1] and minmax[1] < nqubit
            wires = list(range(minmax[0], minmax[1] + 1))
        if type(ancilla) == int:
            ancilla = [ancilla]
        if ancilla == None:
            ancilla = []
        if type(controls) == int:
            controls = [controls]
        if controls == None:
            controls = []
        assert type(wires) == list and type(ancilla) == list and type(controls) == list, 'Invalid input type'
        assert all(isinstance(i, int) for i in wires), 'Invalid input type'
        assert all(isinstance(i, int) for i in ancilla), 'Invalid input type'
        assert all(isinstance(i, int) for i in controls), 'Invalid input type'
        assert min(wires) > -1 and max(wires) < nqubit, 'Invalid input'
        if len(ancilla) > 0:
            assert min(ancilla) > -1 and max(ancilla) < nqubit, 'Invalid input'
        if len(controls) > 0:
            assert min(controls) > -1 and max(controls) < nqubit, 'Invalid input'
        assert len(set(wires)) == len(wires), 'Invalid input'
        assert len(set(ancilla)) == len(ancilla) and len(set(controls)) == len(controls), 'Invalid input'
        for wire in wires:
            assert wire not in ancilla and wire not in controls, 'Use repeated wires'
        self.wires = sorted(wires)
        self.minmax = [min(wires), max(wires)]
        self.ancilla = ancilla
        self.controls = controls


class ControlledMultiplier(Ansatz):
    # See https://arxiv.org/pdf/quant-ph/0205095.pdf Fig.6
    def __init__(self, nqubit, a, mod, minmax=None, nqubitx=None, ancilla=None, controls=None,
                 den_mat=False, mps=False, chi=None, debug=False):
        assert type(a) == int
        assert type(mod) == int
        if minmax == None:
            minmax = [0, nqubit - 2]
        if nqubitx == None:
            nqubitx = len(bin(mod)) - 2
        if ancilla == None:
            ancilla = [minmax[1] + 1]
        super().__init__(nqubit=nqubit, wires=None, minmax=minmax, ancilla=ancilla, controls=controls,
                         init_state='zeros', name='ControlledMultiplier', den_mat=den_mat, mps=mps, chi=chi)
        # one extra qubit to prevent overflow
        assert len(self.wires) >= nqubitx + len(bin(mod)) - 1, 'Quantum register is not enough.'
        minmax1 = [minmax[0], minmax[0] + nqubitx - 1]
        minmax2 = [minmax1[1] + 1, minmax[1]]
        qft = QuantumFourierTransform(nqubit=nqubit, minmax=minmax2, reverse=True,
                                      den_mat=self.den_mat, mps=self.mps, chi=self.chi)
        iqft = qft.inverse()
        self.add(qft)
        k = 0
        for i in range(minmax1[1], minmax1[0] - 1, -1): # the significant bit in |x> is reversed in Fig.6
            if debug and 2**k * a >= 2 * mod:
                print(f'The number 2^{k}*{a} in {self.name} may be too large, unless the control qubit {i} is 0.')
            pma = PhiModularAdder(nqubit=nqubit, number=2**k * a, mod=mod, minmax=minmax2,
                                  ancilla=self.ancilla, controls=self.controls + [i],
                                  den_mat=self.den_mat, mps=self.mps, chi=self.chi, debug=debug)
            self.add(pma)
            k += 1
        self.add(iqft)


class ControlledUa(Ansatz):
    # See https://arxiv.org/pdf/quant-ph/0205095.pdf Fig.7
    # `a` has a modular inverse only if `a` is coprime to `mod` 
    def __init__(self, nqubit, a, mod, minmax=None, ancilla=None, controls=None, den_mat=False,
                 mps=False, chi=None, debug=False):
        # |x> with n bits, |0> with n+1 bits and one extra ancilla bit
        nregister = len(bin(mod)) - 2
        nancilla = len(bin(mod))
        if minmax == None:
            minmax = [0, nregister - 1]
        if ancilla == None:
            ancilla = list(range(minmax[1] + 1, minmax[1] + 1 + nancilla))
        super().__init__(nqubit=nqubit, wires=None, minmax=minmax, ancilla=ancilla, controls=controls,
                         init_state='zeros', name='ControlledUa', den_mat=den_mat, mps=mps, chi=chi)
        assert len(self.wires) == nregister
        assert len(self.ancilla) == nancilla
        cmult = ControlledMultiplier(nqubit=nqubit, a=a, mod=mod, minmax=[self.minmax[0], self.ancilla[-2]],
                                     nqubitx=nregister, ancilla=self.ancilla[-1], controls=self.controls,
                                     den_mat=self.den_mat, mps=self.mps, chi=self.chi, debug=debug)
        self.add(cmult)
        for i in range(len(self.wires)):
            self.swap([self.wires[i], self.ancilla[i + 1]], controls=self.controls)
        a_inv = pow(a, -1, mod)
        cmult_inv = ControlledMultiplier(nqubit=nqubit, a=a_inv, mod=mod, minmax=[self.minmax[0], self.ancilla[-2]],
                                         nqubitx=nregister, ancilla=self.ancilla[-1], controls=self.controls,
                                         den_mat=self.den_mat, mps=self.mps, chi=self.chi, debug=debug).inverse()
        self.add(cmult_inv)


class NumberEncoder(Ansatz):
    def __init__(self, nqubit, number, minmax=None, den_mat=False, mps=False, chi=None):
        super().__init__(nqubit=nqubit, wires=None, minmax=minmax, ancilla=None, controls=None,
                         init_state='zeros', name='NumberEncoder', den_mat=den_mat, mps=mps, chi=chi)
        bits = int_to_bitstring(number, len(self.wires))
        for i, wire in enumerate(self.wires):
            if bits[i] == '1':
                self.x(wire)


class PhiAdder(Ansatz):
    # See https://arxiv.org/pdf/quant-ph/0205095.pdf Fig.2 and Fig.3
    def __init__(self, nqubit, number, minmax=None, controls=None, den_mat=False, mps=False, chi=None, debug=False):
        super().__init__(nqubit=nqubit, wires=None, minmax=minmax, ancilla=None, controls=controls,
                         init_state='zeros', name='PhiAdder', den_mat=den_mat, mps=mps, chi=chi)
        bits = int_to_bitstring(number, len(self.wires), debug=debug)
        for i, wire in enumerate(self.wires):
            phi = 0
            k = 0
            for j in range(i, len(bits)):
                if bits[j] == '1':
                    phi += torch.pi / 2 ** k
                k += 1
            if phi != 0:
                self.p(wires=wire, inputs=phi, controls=self.controls)


class PhiModularAdder(Ansatz):
    # See https://arxiv.org/pdf/quant-ph/0205095.pdf Fig.5
    def __init__(self, nqubit, number, mod, minmax=None, ancilla=None, controls=None, den_mat=False,
                 mps=False, chi=None, debug=False):
        if minmax == None:
            minmax = [0, nqubit - 2]
        if ancilla == None:
            ancilla = [minmax[1] + 1]
        super().__init__(nqubit=nqubit, wires=None, minmax=minmax, ancilla=ancilla, controls=controls,
                         init_state='zeros', name='PhiModularAdder', den_mat=den_mat, mps=mps, chi=chi)
        if debug and number >= 2 * mod:
            print(f'The number {number} in {self.name} is too large.')
        phi_add_number = PhiAdder(nqubit=nqubit, number=number, minmax=self.minmax, controls=self.controls,
                                  den_mat=self.den_mat, mps=self.mps, chi=self.chi, debug=debug)
        phi_sub_number = phi_add_number.inverse()
        phi_add_mod = PhiAdder(nqubit=nqubit, number=mod, minmax=self.minmax, controls=self.ancilla,
                               den_mat=self.den_mat, mps=self.mps, chi=self.chi, debug=debug)
        phi_sub_mod = PhiAdder(nqubit=nqubit, number=mod, minmax=self.minmax,
                               den_mat=self.den_mat, mps=self.mps, chi=self.chi, debug=debug).inverse()
        qft = QuantumFourierTransform(nqubit=nqubit, minmax=self.minmax, reverse=True,
                                      den_mat=self.den_mat, mps=self.mps, chi=self.chi)
        iqft = qft.inverse()
        self.add(phi_add_number)
        self.add(phi_sub_mod)
        self.add(iqft)
        self.cnot(self.minmax[0], self.ancilla[0])
        self.add(qft)
        self.add(phi_add_mod)
        self.add(phi_sub_number)
        self.add(iqft)
        self.x(self.minmax[0])
        self.cnot(self.minmax[0], self.ancilla[0])
        self.x(self.minmax[0])
        self.add(qft)
        self.add(phi_add_number)


class QuantumFourierTransform(Ansatz):
    def __init__(self, nqubit, minmax=None, reverse=False, init_state='zeros', den_mat=False,
                 mps=False, chi=None, show_barrier=False):
        super().__init__(nqubit=nqubit, wires=None, minmax=minmax, ancilla=None, controls=None,
                         init_state=init_state, name='QuantumFourierTransform', den_mat=den_mat,
                         mps=mps, chi=chi)
        # the default output order of phase is x/2, ..., x/2**n
        # if reverse=True, the output order of phase is x/2**n, ..., x/2
        self.reverse = reverse
        for i in self.wires:
            self.qft_block(i)
            if show_barrier:
                self.barrier(self.wires)
        if not reverse:
            for i in range(len(self.wires) // 2):
                self.swap([self.wires[i], self.wires[-1 - i]])
        
    def qft_block(self, n):
        self.h(n)
        k = 2
        for i in range(n, self.minmax[1]):
            self.cp(i + 1, n, torch.pi / 2 ** (k - 1))
            k += 1


class QuantumPhaseEstimationSingleQubit(Ansatz):
    def __init__(self, t, phase, den_mat=False, mps=False, chi=None):
        nqubit = t + 1
        self.phase = phase
        super().__init__(nqubit=nqubit, wires=None, minmax=None, ancilla=None, controls=None,
                         init_state='zeros', name='QuantumPhaseEstimationSingleQubit', den_mat=den_mat,
                         mps=mps, chi=chi)
        self.hlayer(list(range(t)))
        self.x(t)
        for i in range(t):
            self.cp(i, t, torch.pi * phase * (2 ** (t - i)))
        iqft = QuantumFourierTransform(nqubit=nqubit, minmax=[0, t - 1],
                                       den_mat=self.den_mat, mps=self.mps, chi=self.chi).inverse()
        self.add(iqft)


class RandomCircuitG3(Ansatz):
    def __init__(self, nqubit, ngate, wires=None, minmax=None, init_state='zeros', den_mat=False,
                 mps=False, chi=None):
        super().__init__(nqubit=nqubit, wires=wires, minmax=minmax, ancilla=None, controls=None,
                         init_state=init_state, name='RandomCircuitG3', den_mat=den_mat, mps=mps, chi=chi)
        self.ngate = ngate
        self.gate_set = ['CNOT', 'H', 'T']
        for _ in range(ngate):
            gate = random.sample(self.gate_set, 1)[0]
            if gate == 'CNOT':
                wire = random.sample(self.wires, 2)
            else:
                wire = random.sample(self.wires, 1)
            if gate == 'CNOT':
                self.cnot(wire[0], wire[1])
            elif gate == 'H':
                self.h(wire)
            elif gate == 'T':
                self.t(wire)


class ShorCircuit(Ansatz):
    def __init__(self, mod, ncount, a, den_mat=False, mps=False, chi=None, debug=False):
        nreg = len(bin(mod)) - 2
        nqubit = ncount + 2 * nreg + 2
        super().__init__(nqubit=nqubit, wires=None, minmax=None, ancilla=None, controls=None,
                         init_state='zeros', name='ShorCircuit', den_mat=den_mat, mps=mps, chi=chi)
        minmax1 = [0, ncount - 1]
        minmax2 = [ncount, ncount + nreg - 1]
        ancilla = list(range(ncount + nreg, nqubit))
        self.hlayer(list(range(ncount)))
        self.x(ncount + nreg - 1)
        n = 0
        for i in range(ncount - 1, -1, -1):
            # Compute a^{2^n} (mod N) by repeated squaring
            an = a
            for _ in range(n):
                an = an ** 2 % mod
            cua = ControlledUa(nqubit=nqubit, a=an, mod=mod, minmax=minmax2, ancilla=ancilla, controls=[i],
                               den_mat=self.den_mat, mps=self.mps, chi=self.chi, debug=debug)
            self.add(cua)
            n += 1
        iqft = QuantumFourierTransform(nqubit=nqubit, minmax=minmax1,
                                       den_mat=self.den_mat, mps=self.mps, chi=self.chi).inverse()
        self.add(iqft)


class ShorCircuitFor15(Ansatz):
    def __init__(self, ncount, a, den_mat=False, mps=False, chi=None):
        mod = 15
        nreg = len(bin(mod)) - 2
        nqubit = ncount + nreg
        self.ncount = ncount
        super().__init__(nqubit=nqubit, wires=None, minmax=None, ancilla=None, controls=None,
                         init_state='zeros', name='ShorCircuitFor15', den_mat=den_mat, mps=mps, chi=chi)
        minmax = [0, ncount - 1]
        self.hlayer(list(range(ncount)))
        self.x(ncount + nreg - 1)
        n = 0
        for i in range(ncount - 1, -1, -1):
            self.cua(a, 2 ** n, i)
            n += 1
        iqft = QuantumFourierTransform(nqubit=nqubit, minmax=minmax,
                                       den_mat=self.den_mat, mps=self.mps, chi=self.chi).inverse()
        self.add(iqft)

    # See https://learn.qiskit.org/course/ch-algorithms/shors-algorithm
    def cua(self, a, power, controls):
        assert a in [2, 4, 7, 8, 11, 13]
        for _ in range(power):
            if a in [2, 13]:
                self.swap([self.ncount + 2, self.ncount + 3], controls)
                self.swap([self.ncount + 1, self.ncount + 2], controls)
                self.swap([self.ncount + 0, self.ncount + 1], controls)
            if a in [7, 8]:
                self.swap([self.ncount + 0, self.ncount + 1], controls)
                self.swap([self.ncount + 1, self.ncount + 2], controls)
                self.swap([self.ncount + 2, self.ncount + 3], controls)
            if a in [4, 11]:
                self.swap([self.ncount + 1, self.ncount + 3], controls)
                self.swap([self.ncount + 0, self.ncount + 2], controls)
            if a in [7, 11, 13]:
                for q in range(4):
                    self.x(self.ncount + q, controls)