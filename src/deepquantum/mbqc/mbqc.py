"""
Measurement based quantum circuit
"""
from typing import Any, List, Optional, Tuple, Union
from copy import copy, deepcopy
import torch
from networkx import Graph, draw_networkx
from torch import nn
from . import gate
from .operation import Operation, Node, Entanglement, Measurement, Correction, XCorrection, ZCorrection
from .qmath import kron, list_xor

class Pattern(Operation):
    r"""Measurement based quantum circuit.
    n_input_nodes: the number of input qubits
    """
    def __init__(
        self,
        n_input_nodes: int,
        init_state: Any = None,
        name: Optional[str] = None
    ) -> None:
        super().__init__(name=name, n_input_nodes=n_input_nodes, node=list(range(n_input_nodes)))
        self._bg_state = None
        self._bg_qubit = n_input_nodes
        self.n_input_nodes = n_input_nodes
        self._graph = None
        self.cmds = nn.Sequential()
        self.encoders = [ ]
        self.npara = 0
        self.ndata = 0
        self._node_list = list(range(self.n_input_nodes))
        self._edge_list = [ ]
        self.measured_dic = {}
        self.unmeasured_list = list(range(self.n_input_nodes))
        self.nout_wire_dic = {i:i for i in range(self.n_input_nodes)}

        if init_state is None:
            plus_state = torch.sqrt(torch.tensor(2))*torch.tensor([1,1])/2
            init_state = kron([plus_state] * n_input_nodes)
        if not isinstance(init_state, torch.Tensor):
            init_state = torch.tensor(init_state)
        if init_state.ndim == 1:
            init_state = init_state.unsqueeze(0)
        self.init_state = init_state

    def set_graph(self, graph: List[List]):
        vertices, edges = graph
        assert len(vertices) > self.n_input_nodes
        self._node_list = list(range(vertices))
        for i in vertices:
            if i not in self._node_list:
                self.n(i)
        for edge in edges:
            self.e(edge)
        return

    def get_graph(self):
        assert len(self._node_list) == self._bg_qubit
        g = Graph()
        g.add_nodes_from(self._node_list)
        g.add_edges_from(self._edge_list)
        self._graph = g
        return g

    def __add__(self, rhs: 'Pattern') -> 'Pattern':
        """Addition of the ``Pattern``.

        The initial state is the same as the first ``Pattern``.
        """
        pattern = Pattern(n_input_nodes=self.n_input_nodes, init_state=self.init_state, name=self.name)
        for op in rhs.cmds:
           new_node =  [i + self._bg_qubit for i in op.node]
           op.node = new_node

        pattern.cmds = self.cmds + rhs.cmds
        pattern.encoders = self.encoders + rhs.encoders
        pattern.npara = self.npara + rhs.npara
        pattern.ndata = self.ndata + rhs.ndata
        return pattern

    def add(
        self,
        op: Operation,
        encode: bool = False,
        node: Union[int, List[int], None] = None
    ) -> None:
        """A method that adds an operation to the mbqc.

        The operation can be a gate or another photonic quantum circuit. The method also updates the
        attributes of the photonic quantum circuit. If ``node`` is specified, the parameters of gates
        are shared.

        Args:
            op (Operation): The operation to add. It is an instance of ``Operation`` class or its subclasses,
                such as ``Gate``, or ``QumodeCircuit``.
            encode (bool): Whether the gate is to encode data. Default: ``False``
            node (Union[int, List[int], None]): The node to apply the gate on. It can be an integer
                or a list of integers specifying the indices of the node. Default: ``None`` (which means
                the gate has its own node)

        Raises:
            AssertionError: If the input arguments are invalid or incompatible with the quantum circuit.
        """
        assert isinstance(op, Operation)
        if node is not None:
            node = self._convert_indices(node)
            assert len(node) == len(op.node), 'Invalid input'
            op = copy(op)
            op.node = node
        self.cmds.append(op)
        if encode:
            assert not op.requires_grad, 'Please set requires_grad of the operation to be False'
            self.encoders.append(op)
            self.ndata += op.npara
        else:
            self.npara += op.npara

    def n(self, node: Union[int, List[int]] = None):
        node_ = Node(node=node)
        assert node_.node[0] not in self._node_list, 'node already exists'
        self._node_list.append(node_.node[0])
        self.add(node_)
        self._bg_qubit += 1
        self.unmeasured_list.append(node_.node[0])

    def e(self, node: List[int] = None):
        assert node[0] in self._node_list and node[1] in self._node_list, \
            'no command acts on a qubit not yet prepared, unless it is an input qubit'
        entang_ = Entanglement(node=node)
        self._edge_list.append(node)
        self.add(entang_)

    def m(
        self,
        node: Optional[int] = None,
        plane: Optional[str] = 'XY',
        angle: float = 0,
        t_domain: Union[int, List[int]] = [],
        s_domain: Union[int, List[int]] = []
    ):
        mea_op = Measurement(node=node, plane=plane, angle=angle, t_domain=t_domain, s_domain=s_domain)
        self.add(mea_op)

    def x(self, node: int = None, signal_domain: List[int] = None):
        assert node in self._node_list, 'no command acts on a qubit not yet prepared, unless it is an input qubit'
        x_ = XCorrection(node=node, signal_domain=signal_domain)
        self.add(x_)

    def z(self, node: int = None, signal_domain: List[int] = None):
        assert node in self._node_list, 'no command acts on a qubit not yet prepared, unless it is an input qubit'
        z_ = ZCorrection(node=node, signal_domain=signal_domain)
        self.add(z_)

    def forward(self):
        state = self.init_state
        for op in self.cmds:
            self._check_measured(op.node)
            if isinstance(op, Measurement):
                node = self.unmeasured_list.index(op.node[0])
                state = op.forward(node, state, self.measured_dic)
                self.measured_dic[op.node[0]] = op.sample
                del self.unmeasured_list[node]
            elif isinstance(op, (XCorrection, ZCorrection)):
                node = self.unmeasured_list.index(op.node[0])
                state = op.forward(node, state, self.measured_dic)
            elif isinstance(op, Entanglement):
                node = [self.unmeasured_list.index(op.node[0]), self.unmeasured_list.index(op.node[1])]
                state = op.forward(node, state)
            else:
                state = op.forward(state)
        self._bg_state = state
        return state.squeeze()

    def _check_measured(self, node):
        """
        check if the qubit already measured.
        """
        measured_list = list(self.measured_dic.keys())
        for i in node:
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
        node_edge_colors = ['red' if i < self.n_input_nodes else 'black' for i in self._node_list]
        draw_networkx(g, pos=pos,
                      node_color=node_colors,
                      edgecolors=node_edge_colors,
                      node_size=500,
                      width=2)
        return

    def is_standard(self) -> bool:
        """Determine whether the command sequence is standard.

        Returns
        -------
        is_standard : bool
            True if the pattern follows NEMC standardization, False otherwise
        """
        it = iter(self.cmds)
        try:
            # Check if operations follow NEMC order
            op = next(it)
            while isinstance(op, Node):  # First all Node operations
                op = next(it)
            while isinstance(op, Entanglement):  # Then all Entanglement operations
                op = next(it)
            while isinstance(op, Measurement):  # Then all Measurement operations
                op = next(it)
            while isinstance(op, Correction):  # Finally all Correction operations
                op = next(it)
            return False  # If we get here, there were operations after NEMC sequence
        except StopIteration:
            return True  # If we run out of operations, pattern is standard

    def standardize(self):
        """Standardize the command sequence into NEMC form.

        This function reorders operations into the standard form:
        - Node preparations (N)
        - Entanglement operations (E)
        - Measurement operations (M)
        - Correction operations (C)

        It handles the propagation of correction operations by:
        1. Moving X-corrections through entanglements (generating Z-corrections)
        2. Moving corrections through measurements (modifying measurement signal domains)
        3. Collecting remaining corrections at the end
        """
        # Initialize lists for each operation type
        n_list = []  # Node operations
        e_list = []  # Entanglement operations
        m_list = []  # Measurement operations
        z_dict = {}  # Tracks Z corrections by node
        x_dict = {}  # Tracks X corrections by node

        def add_correction_domain(domain_dict: dict, node, domain) -> None:
            """Helper function to update correction domains with XOR operation"""
            if previous_domain := domain_dict.get(node):
                previous_domain = list_xor(previous_domain, domain)
            else:
                domain_dict[node] = domain.copy()

        # Process each operation and reorganize into standard form
        for op in self.cmds:
            if isinstance(op, Node):
                n_list.append(op)
            elif isinstance(op, Entanglement):
                for side in (0, 1):
                    # Propagate X corrections through entanglement (generates Z corrections)
                    if s_domain := x_dict.get(op.node[side], None):
                        add_correction_domain(z_dict, op.node[1 - side], s_domain)
                e_list.append(op)
            elif isinstance(op, Measurement):
                # Apply pending corrections to measurement parameters
                new_op = deepcopy(op)
                if t_domain := z_dict.pop(op.node[0], None):
                    new_op.t_domain = list_xor(new_op.t_domain, t_domain)
                if s_domain := x_dict.pop(op.node[0], None):
                    new_op.s_domain = list_xor(new_op.s_domain, s_domain)
                m_list.append(new_op)
            elif isinstance(op, ZCorrection):
                add_correction_domain(z_dict, op.node[0], op.signal_domain)
            elif isinstance(op, XCorrection):
                add_correction_domain(x_dict, op.node[0], op.signal_domain)

        # Reconstruct command sequence in standard order
        self.cmds = nn.Sequential(
                    *n_list,
                    *e_list,
                    *m_list,
                    *(ZCorrection(node=node, signal_domain=domain) for node, domain in z_dict.items()),
                    *(XCorrection(node=node, signal_domain=domain) for node, domain in x_dict.items())
        )

    def _update(self):
        if len(self.measured_dic) == 0:
            self._bg_qubit = len(self._node_list)
            self.unmeasured_list = list(range(self._bg_qubit))
        return

    def _apply_single(
        self,
        gate,
        input_node: int,
        required_ancilla: int,
        ancilla: Optional[List[int]]=None,
        **kwargs
    ):
        """Helper method to apply quantum gate patterns.

        Args:
            gate: Gate function to apply (h, pauli_x, pauli_y, etc.)
            input_node: Input qubit node
            required_ancilla: Number of required ancilla qubits
            ancilla: Optional ancilla qubits
        """
        if ancilla is None:
            ancilla = list(range(self._bg_qubit, self._bg_qubit + required_ancilla))
        pattern = gate(input_node, ancilla, **kwargs)
        self.cmds += pattern[0]
        self._node_list += pattern[1]
        self._edge_list += pattern[2]
        self._update()

    def h(self, input_node: int, ancilla: Optional[List[int]]=None):
        self._apply_single(gate.h, input_node, 1, ancilla)

    def pauli_x(self, input_node: int, ancilla: Optional[List[int]]=None):
        self._apply_single(gate.pauli_x, input_node, 2, ancilla)

    def pauli_y(self, input_node: int, ancilla: Optional[List[int]]=None):
        self._apply_single(gate.pauli_y, input_node, 4, ancilla)

    def pauli_z(self, input_node: int, ancilla: Optional[List[int]]=None):
        self._apply_single(gate.pauli_z, input_node, 2, ancilla)

    def s(self, input_node: int, ancilla: Optional[List[int]]=None):
        self._apply_single(gate.s, input_node, 2, ancilla)

    def rx(self, input_node: int, theta: Optional[torch.Tensor]=None, ancilla: Optional[List[int]]=None):
        self._apply_single(gate.rx, input_node, required_ancilla=2, ancilla=ancilla, theta=theta)

    def ry(self, input_node: int, theta: Optional[torch.Tensor]=None, ancilla: Optional[List[int]]=None):
        self._apply_single(gate.ry, input_node, required_ancilla=4, ancilla=ancilla, theta=theta)

    def rz(self, input_node: int, theta: Optional[torch.Tensor]=None, ancilla: Optional[List[int]]=None):
        self._apply_single(gate.rz, input_node, required_ancilla=2, ancilla=ancilla, theta=theta)

    def cnot(self, control_node: int, target_node: int, ancilla: Optional[List[int]]=None):
        if ancilla is None:
            ancilla = [self._bg_qubit, self._bg_qubit+1]
        pattern_cnot = gate.cnot(control_node, target_node, ancilla)
        self.cmds += pattern_cnot[0]
        self._node_list += pattern_cnot[1]
        self._edge_list += pattern_cnot[2]
        self._update()
