"""
Optimizer: various on-chip optimization algorthims
"""

import copy
from typing import Dict, List

import numpy as np
from bayes_opt import BayesianOptimization, UtilityFunction





class Optimizer(object):
    r"""A base class for Optimizer.

    Args:
        target_func (function): The target function to optimize, more specifically, to minimize. \
            It is supposed to accept **kwargs in the format of `param_init` as inputs.
        param_init (Dict): The initial guess of solutions for the target function. \
            The keys of it should be consistent with inputs of `target_func`.
        random_state (int): the random seed for this optimization process.
    """
    def __init__(self, target_func, param_init, random_state = 0):
        self.target_func = target_func
        self.param_dict = copy.deepcopy(param_init)
        self.random_state = random_state
    def __str__(self) -> str:
        return 'Optimizer'

class OptimizerBayesian(Optimizer):
    r"""Opimizer based on Bayesian optimization. 

    See https://github.com/bayesian-optimization/BayesianOptimization.

    Args:
        target_func (function): The target function to optimize, more specifically, to minimize. \
            It is supposed to accept **kwargs in the format of `param_init` as inputs.
        param_init (dict): The initial guess of solutions for the target function. \
            The keys of it should be consistent with inputs of `target_func`.
        random_state (int): the random seed for this optimization process.

    Attention: 
        In the scenerio of on-chip optimization, the periods of phase shifters are all from 0 to $$2\pi$$, \
            so in this program the `pbound` (a parameter determining the search region in \
            Bayesian-Optimization package) is fixed from 0 to $$2\pi$$.
    """
    def __init__(self, target_func, param_init, random_state = 0):
        super().__init__(target_func, param_init, random_state)
        def func_to_maximize(**param_dict: Dict) -> float:
            return -self.target_func(**param_dict)
        self.pbounds = self.gen_pbounds()
        self.optimizer = BayesianOptimization(
            f = func_to_maximize,
            pbounds = self.pbounds,
            random_state = self.random_state
        )
        self.util = UtilityFunction(
                    kind='ucb',
                    kappa=2.576,
                    xi=0.0,
                    kappa_decay=1,
                    kappa_decay_delay=0)
        self.best_param_dict = copy.deepcopy(self.param_dict)
        self.best_target = -np.inf
        self.iter = 0

    def gen_pbounds(self) -> Dict:
        pbounds = {}
        for key in self.param_dict.keys():
            pbounds[key] = (0,np.pi*2)
        return pbounds

    def param_suggest(self) -> np.ndarray:
        self.util.update_params()
        x_probe = self.optimizer.suggest(self.util)
        x = self.optimizer._space._as_array(x_probe) # a list
        param_array = np.asarray(x).reshape(1,-1)
        return param_array

    def param_register(self, param_array: np.ndarray, target: float) -> None:
        for i in range(len(param_array)):
            x = param_array[i]
            param_dict = dict(zip(self.param_dict.keys(), x))
            if self.optimizer._space._constraint is None:
                self.optimizer._space.register(x, target[i])
            else:
                constraint_value = self.optimizer._space._constraint.eval(**param_dict)
                self.optimizer._space.register(x, target[i], constraint_value)

            if target[i] > self.best_target:
                self.best_param_dict = copy.deepcopy(param_dict)
                self.best_target = target[i]
        self.iter += 1

    def run(self, nstep: int) -> List:
        for _ in range(nstep):
            p1 = self.param_suggest()
            # BO 内置用法是最大化目标；但是接下来打印时再添一个符号即可
            f1 = [-self.target_func(p1)]
            self.param_register(p1, f1)
        return list(self.best_param_dict.values())


class OptimizerSPSA(Optimizer):
    r"""Opimizer based on SPSA (Simultaneous Perturbation Stochastic Approximation). 

    See https://www.jhuapl.edu/spsa/Pages/MATLAB.htm.

    Args:
        target_func (function): The target function to optimize, more specifically, to minimize. \
            It is supposed to accept **kwargs in the format of `param_init` as inputs.
        param_init (dict): The initial guess of solutions for the target function. \
            The keys of it should be consistent with inputs of `target_func`.
        random_state (int): the random seed for this optimization process.
    """
    def __init__(self, target_func, param_init, random_state = 0):
        super().__init__(target_func, param_init, random_state)
        self.random_state_ori = np.random.get_state()
        np.random.seed(self.random_state)
        self.hyperparam = {
            'a': 1e-1,
            'c': 1e-2,
            'A': 200,
            'nepoch': 2000,
            'alpha': 0.602,
            'gamma': 0.101
        }
        self.iter = 0
        self.nparam = len(param_init)
        self.best_param_dict = copy.deepcopy(self.param_dict)
        self.best_target = np.inf

    def param_suggest(self) -> np.ndarray:
        tmp_param = np.asarray(list(self.param_dict.values()))
        delta_lr = self.hyperparam['c'] / (1+self.iter)**self.hyperparam['gamma']
        delta = (np.random.randint(0, 2, self.nparam) * 2 - 1) * delta_lr
        param_array = np.zeros((2, self.nparam))
        param_array[0] = tmp_param - delta
        param_array[1] = tmp_param + delta
        return param_array

    def param_register(self, param_array: np.ndarray, target: np.ndarray) -> None:
        assert len(param_array)==2
        assert len(target)==2
        param_lr = self.hyperparam['a'] / (1+self.iter+self.hyperparam['A'])**self.hyperparam['alpha']
        param1 = param_array[0]
        param2 = param_array[1]
        target1 = target[0]
        target2 = target[1]
        delta = param2 - param1
        grad = (target2-target1) / delta
        param_new = 0.5*(param1+param2) - param_lr * grad
        self.param_dict = dict(zip(self.param_dict.keys(),param_new))
        self.iter += 1

        if target1 < self.best_target:
            self.best_param_dict = dict(zip(self.param_dict.keys(), param1))
            self.best_target = target1

        if target2 < self.best_target:
            self.best_param_dict = dict(zip(self.param_dict.keys(), param2))
            self.best_target = target2

    def ori_random_state(self) -> None:
        np.random.set_state(self.random_state_ori)

    def run(self, nstep: int) -> List:
        for _ in range(nstep):
            p1, p2 = self.param_suggest()
            f1 = self.target_func(p1)
            f2 = self.target_func(p2)
            self.param_register([p1,p2], [f1,f2])
            if (f1 < -0.92) and (f2 < -0.92):
                self.hyperparam['c'] = 0.001
            elif (f1 < -0.999) and (f2 < -0.999):
                self.hyperparam['c'] = 1e-4
        return list(self.best_param_dict.values())


class OptimizerFourier(Optimizer):
    r"""Opimizer based on Fourier series approximation of the target function \
        in order to obtain the approximation of gradients.

    Args:
        target_func (function): The target function to optimize, more specifically, to minimize. \
            It is supposed to accept **kwargs in the format of `param_init` as inputs.
        param_init (dict): The initial guess of solutions for the target function. \
            The keys of it should be consistent with inputs of `target_func`.
        R (int): the order of Fourier series to approximate.
        lr: the step length (or equivalently, learning rate) of the learning process \
            (namely, gradient descent process).
        random_state (int): the random seed for this optimization process.
    """
    def __init__(self, target_func, param_init, R = 5, lr = 0.1, random_state = 0):
        super().__init__(target_func, param_init, random_state)
        self.iter = 0
        self.r = R
        self.nparam = len(param_init)
        self.best_param_dict = copy.deepcopy(self.param_dict)
        self.best_target = np.inf
        self.lr = lr
        self.a = self.gen_a()
        self.u = np.zeros((2*R+1)*self.nparam)
        self.iter = 0

    def gen_a(self) -> np.ndarray:
        a = np.zeros((2*self.r+1, 2*self.r+1))
        mu = np.arange(2*self.r+1)
        x_mu = 2*np.pi/(2*self.r+1)*(mu-self.r)
        a[:,0] = 1
        a[:,1:self.r+1] = np.cos(x_mu.reshape(-1,1)@np.arange(1,self.r+1).reshape(1,-1))
        a[:,self.r+1:2*self.r+2] = np.sin(x_mu.reshape(-1,1)@np.arange(1,self.r+1).reshape(1,-1))
        return a

    def param_suggest(self) -> np.ndarray:
        tmp_param = np.asarray(list(self.param_dict.values()),dtype=float).reshape(1,-1)
        mu = np.arange(2*self.r+1)
        varied_param = 2*np.pi/(2*self.r+1)*(mu-self.r)
        param_array = np.repeat(tmp_param,self.nparam*(2*self.r+1),axis=0)
        for param_id in range(self.nparam):
            param_array[param_id*(2*self.r+1):(param_id+1)*(2*self.r+1),param_id] = varied_param
        return  param_array

    def param_register(self, param_array: np.ndarray, target: np.ndarray):
        assert len(param_array)==(2*self.r+1)*self.nparam
        assert len(target)==(2*self.r+1)*self.nparam
        # 求解线性方程组得出组合系数
        param = np.asarray(list(self.param_dict.values()))
        for param_id in range(self.nparam):
            idx1 = param_id*(2*self.r+1)
            idx2 = (1+param_id)*(2*self.r+1)
            self.u[idx1:idx2] = np.linalg.solve(self.a,target[idx1:idx2])

        # 根据组合系数计算当前位置处的偏导数
        grad = np.zeros(self.nparam)
        for param_id in range(self.nparam):
            theta = param[param_id]
            idx = 1+param_id*(2*self.r+1)
            grad[param_id] = -(np.arange(1,self.r+1)*np.sin(theta*np.arange(1,self.r+1)))@\
                            self.u[idx:self.r+idx]+(np.arange(1,self.r+1)*\
                            np.cos(theta*np.arange(1,self.r+1)))@self.u[self.r+idx:self.r*2+idx]
        param_new = param - self.lr * grad
        self.param_dict = dict(zip(self.param_dict.keys(),param_new))
        if target.min() < self.best_target:
            self.best_target = target.min()
            self.best_param_dict = dict(zip(self.param_dict.keys(),param_array[target.argmin()]))
        self.iter += 1

    def run(self, nstep: int) -> List:
        for _ in range(nstep):
            param_array = self.param_suggest()
            target = np.zeros(len(param_array))
            for i in range(len(param_array)):
                target[i] = self.target_func(param_array[i])
            self.param_register(param_array, target)
        return list(self.best_param_dict.values())
