from concurrent.futures import ThreadPoolExecutor

import numpy as np

ARR = np.ones((4_000_000,))


def add():
    for _ in range(1000):
        ARR + ARR


def mult(_):
    ARR * ARR


def parallel_mult():
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(mult, [None] * 1001))


add()
parallel_mult()
