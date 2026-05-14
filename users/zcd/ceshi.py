import numpy as np

# 生成 6行6列 的随机矩阵
random_matrix = np.random.rand(6, 6)

# 打印输出
print("6×6 随机矩阵（0~1）：")
print(random_matrix)
np.savetxt("matrix.csv", random_matrix, delimiter=",", fmt="%.6f")
print("\n已保存到当前文件夹：matrix.csv")