import numbers
from concurrent.futures import ThreadPoolExecutor
from time import time

mylist = [1.0] * 100_000

def check(_):
    for x in mylist:
        isinstance(x, numbers.Integral)

for cores in [1, 2, 4, 8]:
    start = time()
    with ThreadPoolExecutor(cores) as pool:
        list(pool.map(check, range(cores)))
    print(cores, time() - start)
