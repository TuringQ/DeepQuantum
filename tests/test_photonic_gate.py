import deepquantum as dq
import pytest
import torch


def test_2_mode_squeezing_gate():
    r = torch.rand(1)[0]
    theta = torch.rand(1)[0] * 2 * torch.pi
    cutoff = 5
    cir1 = dq.QumodeCircuit(nmode=2, init_state='vac', cutoff=cutoff, backend='gaussian')
    cir1.s2([0,1], r, theta)
    cov1, mean1 = cir1()
    sym1 = cir1.get_symplectic()

    cir2 = dq.QumodeCircuit(nmode=2, init_state='vac', cutoff=cutoff, backend='gaussian')
    cir2.bs([0,1], [torch.pi / 4, 0])
    cir2.s(0, r, theta)
    cir2.s(1, -r, theta)
    cir2.bs([0,1], [-torch.pi / 4, 0])
    cov2, mean2 = cir2()
    sym2 = cir2.get_symplectic()
    assert torch.allclose(cov1, cov2, atol=1e-6)
    assert torch.allclose(mean1, mean2, atol=1e-6)
    assert torch.allclose(sym1, sym2, atol=1e-6)
