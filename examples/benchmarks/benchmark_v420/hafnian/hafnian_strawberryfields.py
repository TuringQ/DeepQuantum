import json
import time

import numpy as np
import strawberryfields as sf
from thewalrus import hafnian
from tqdm import tqdm

from GlobalHafnian import *

# Print version
print(sf.__version__)

def hafnian_sf(n, l):
    """Generate a random hafnian matrix and calculate its hafnian using DeepQuantum."""
    A = test_sequence_hafnian(n, number_of_sequence=number_of_sequence)

    # 计算 hafnian
    def get_hafnian_sf(A):
        trials = 100
        if l == 100 or l == 1000:
            trials = 1
        time0 = time.time()
        for i in tqdm(range(trials)):
            for j in range(i*l, (i+1)*l):
                results = hafnian(np.array(A[j]))
        time1 = time.time()
        ts = (time1 - time0) / trials
        return ts

    return get_hafnian_sf(A)

# 进行测试
results = {}
platform = 'strawberryfields'

for n in tqdm(n_list):
    for l in l_list:
        print(f"n={n}, l={l}")
        ts = hafnian_sf(n, l)
        results[str(n)+'+'+str(l)] = ts

# 保存结果
with open(f'hafnian/hafnian_{platform}_results.data', 'w') as f:
    json.dump(results, f)

# 读取并打印
with open(f'hafnian/hafnian_{platform}_results.data', 'r') as f:
    print(json.load(f))
