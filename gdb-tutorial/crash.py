import numpy as np

# Years and seconds have no common unit, so this should raise a TypeError...
print(divmod(np.timedelta64(1, "Y"), np.timedelta64(1, "s")))
