import torch

x = torch.randn(3, 4, 5)

print(x)
print(x.ndim)

# 元组的 加法 和乘法
shape = (4,) + (1, 1, 1)
print(f"{shape} --here")

shape = (1, 2) * (5)
print(f"{shape} --here")
