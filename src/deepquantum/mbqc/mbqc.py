"""
Measurement based quantum circuit
"""
from typing import Any, List, Optional, Tuple, Union
from copy import copy, deepcopy
import torch
from networkx import Graph, draw_networkx
from torch import nn
from .operation import Operation, Node, Entanglement, Measurement, Correction, XCorrection, ZCorrection
from .qmath import kron, list_xor

class MBQC(Operation):
    r"""Measurement based quantum circuit.
    nqubit: the number of input qubits
    """
    def __init__(
        self,
        nqubit: int,
        init_state: Optional[torch.Tensor] = None
    ) -> None:
        super().__init__(nqubit=nqubit, wires=list(range(nqubit)))
        self._bg_state = None
        self._bg_qubit = nqubit
        self.nqubit = nqubit
        self._graph = None
        self.pattern = None
        self.operators = nn.Sequential()
        self.encoders = [ ]
        self.npara = 0
        self.ndata = 0
        self._node_list = list(range(self.nqubit))
        self._edge_list = [ ]
        self.measured_dic = {}
        self.unmeasured_dic = {i: i for i in range(self.nqubit)}

        if init_state is None:
            plus_state = torch.sqrt(torch.tensor(2))*torch.tensor([1,1])/2
            init_state = kron([plus_state] * nqubit)
        self.init_state = init_state

    def set_graph(self, graph: List[List]):
        vertices, edges = graph
        self.unmeasured_dic = {i: i for i in range(self.nqubit)}
        assert len(vertices) > self.nqubit
        for i in vertices:
            if i not in self._node_list:
                self.node(i)
        for edge in edges:
            self.entanglement(edge)
        return

    def get_graph(self):
        assert len(self._node_list) == self._bg_qubit
        g = Graph()
        g.add_nodes_from(self._node_list)
        g.add_edges_from(self._edge_list)
        self._graph = g
        return g

    def add(
        self,
        op: Operation,
        encode: bool = False,
        wires: Union[int, List[int], None] = None
    ) -> None:
        """A method that adds an operation to the mbqc.

        The operation can be a gate or another photonic quantum circuit. The method also updates the
        attributes of the photonic quantum circuit. If ``wires`` is specified, the parameters of gates
        are shared.

        Args:
            op (Operation): The operation to add. It is an instance of ``Operation`` class or its subclasses,
                such as ``Gate``, or ``QumodeCircuit``.
            encode (bool): Whether the gate is to encode data. Default: ``False``
            wires (Union[int, List[int], None]): The wires to apply the gate on. It can be an integer
                or a list of integers specifying the indices of the wires. Default: ``None`` (which means
                the gate has its own wires)

        Raises:
            AssertionError: If the input arguments are invalid or incompatible with the quantum circuit.
        """
        assert isinstance(op, Operation)
        if wires is not None:
            wires = self._convert_indices(wires)
            assert len(wires) == len(op.wires), 'Invalid input'
            op = copy(op)
            op.wires = wires
        self.operators.append(op)
        if encode:
            assert not op.requires_grad, 'Please set requires_grad of the operation to be False'
            self.encoders.append(op)
            self.ndata += op.npara
        else:
            self.npara += op.npara

    def node(self, wires: Union[int, List[int]] = None):
        node_ = Node(wires=wires)
        assert node_.wires[0] not in self._node_list, 'node already exists'
        self._node_list.append(node_.wires[0])
        self.add(node_)
        self._bg_qubit += 1
        self.unmeasured_dic[node_.wires[0]] = node_.wires[0]

    def entanglement(self, wires: List[int] = None):
        assert wires[0] in self._node_list and wires[1] in self._node_list, \
            'no command acts on a qubit not yet prepared, unless it is an input qubit'
        entang_ = Entanglement(wires=wires)
        self._edge_list.append(wires)
        self.add(entang_)

    def measurement(
        self,
        wires: Optional[int] = None,
        plane: Optional[str] = 'XY',
        angle: float = 0,
        t_domain: Union[int, List[int]] = [],
        s_domain: Union[int, List[int]] = []
    ):
        mea_op = Measurement(wires=wires, plane=plane, angle=angle, t_domain=t_domain, s_domain=s_domain)
        self.add(mea_op)

    def X(self, wires: int = None, signal_domain: List[int] = None):
        assert wires in self._node_list, 'no command acts on a qubit not yet prepared, unless it is an input qubit'
        x_ = XCorrection(wires=wires, signal_domain=signal_domain)
        self.add(x_)

    def Z(self, wires: int = None, signal_domain: List[int] = None):
        assert wires in self._node_list, 'no command acts on a qubit not yet prepared, unless it is an input qubit'
        z_ = ZCorrection(wires=wires, signal_domain=signal_domain)
        self.add(z_)

    def forward(self):
        state = self.init_state
        for op in self.operators:
            self._check_measured(op.wires)
            if isinstance(op, Measurement):
                wires = self.unmeasured_dic[op.wires[0]]
                state = op.forward(wires, state, self.measured_dic)
                self.measured_dic[op.wires[0]] = op.sample[0]
                del self.unmeasured_dic[op.wires[0]]
                for key in self.unmeasured_dic:
                    if key > op.wires[0]:
                        self.unmeasured_dic[key] -= 1
            elif isinstance(op, (XCorrection, ZCorrection)):
                wires = self.unmeasured_dic[op.wires[0]]
                state = op.forward(wires, state, self.measured_dic)
            elif isinstance(op, Entanglement):
                wires = [self.unmeasured_dic[op.wires[0]], self.unmeasured_dic[op.wires[1]]]
                state = op.forward(wires, state)
            else:
                state = op.forward(state)
        self._bg_state = state
        return state

    def _check_measured(self, wires):
        """
        check if the qubit already measured.
        """
        measured_list = list(self.measured_dic.keys())
        for i in wires:
            if i in measured_list:
                raise ValueError (f'qubit {i} already measured')
        return

    def draw(self, wid: int=3):
        g = self.get_graph()
        pos = {}
        for i in self._node_list:
            pos_x = i % wid
            pos_y = i // wid
            pos[i] = (pos_x, -pos_y)
        measured_nq = list(self.measured_dic.keys())
        node_colors = ['gray' if i in measured_nq else 'green' for i in self._node_list]
        node_edge_colors = ['red' if i < self.nqubit else 'black' for i in self._node_list]
        draw_networkx(g, pos=pos,
                      node_color=node_colors,
                      edgecolors=node_edge_colors,
                      node_size=500,
                      width=2)
        return

    def is_standard(self):
        """Determine whether the command sequence is standard.

        Returns
        -------
        is_standard : bool
            True if the pattern is standard
        """
        it = iter(self.operators)
        try:
            op = next(it)
            while isinstance(op, Node):
                op = next(it)
            while isinstance(op, Entanglement):
                op = next(it)
            while isinstance(op, Measurement):
                op = next(it)
            while isinstance(op, Correction):
                op = next(it)
            return False
        except StopIteration:
            return True

    def standardize(self):

        n_list = []
        e_list = []
        m_list = []
        z_dict = {}
        x_dict = {}

        def add_correction_domain(
            domain_dict: dict, wires, domain
        ) -> None:
            if previous_domain := domain_dict.get(wires):
                previous_domain = list_xor(previous_domain, domain)
            else:
                domain_dict[wires] = domain.copy()
        for op in self.operators:
            if isinstance(op, Node):
                n_list.append(op)
            elif isinstance(op, Entanglement):
                for side in (0, 1):
                    if s_domain := x_dict.get(op.wires[side], None):
                        add_correction_domain(z_dict, op.wires[1 - side], s_domain)
                e_list.append(op)
            elif isinstance(op, Measurement):
                new_op = deepcopy(op)
                if t_domain := z_dict.pop(op.wires[0], None):
                    new_op.t_domain = list_xor(new_op.t_domain, t_domain)
                if s_domain := x_dict.pop(op.wires[0], None):
                    new_op.s_domain = list_xor(new_op.s_domain, s_domain)
                m_list.append(new_op)
            elif isinstance(op, ZCorrection):
                add_correction_domain(z_dict, op.wires[0], op.signal_domain)
            elif isinstance(op, XCorrection):
                add_correction_domain(x_dict, op.wires[0], op.signal_domain)
        self.operators = nn.Sequential(*n_list,
                                        *e_list,
                                        *m_list,
                                        *(ZCorrection(wires=wires, signal_domain=domain) for wires, domain in z_dict.items()),
                                        *(XCorrection(wires=wires, signal_domain=domain) for wires, domain in x_dict.items()))