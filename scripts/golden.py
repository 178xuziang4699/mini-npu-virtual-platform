#==============================================================================
# golden.py — 独立参考计算模型（对照验证专用，绝不参与 RTL 执行）
# 重要：本模块使用 Python 任意精度整数 + 显式 32 位补码截断，
#       与 RTL 的 3 级流水 MAC 结构完全独立，避免同源错误。
#==============================================================================

def to_signed(v, bits):
    """把无符号位模式 v 解释为 bits 位有符号数"""
    v &= (1 << bits) - 1
    if v & (1 << (bits - 1)):
        v -= (1 << bits)
    return v

def to_bits(v, bits):
    """把有符号数 v 转成 bits 位补码无符号表示"""
    return v & ((1 << bits) - 1)

def golden_mac(a, b, acc, AW=8, CW=32):
    """参考 MAC: acc + (a*b)，先取 int8 操作数，结果按 int32 截断"""
    sa, sb = to_signed(to_bits(a, AW), AW), to_signed(to_bits(b, AW), AW)
    res = acc + sa * sb
    return to_signed(to_bits(res, CW), CW)

def golden_matmul(A, B, N, K, AW=8, CW=32):
    """
    参考矩阵乘: C[N][N] = A[N][K] x B[K][N]
    A/B 为行优先 int8 列表；用 64 位累加后按 int32 截断（模拟 int32 累加器回绕）
    返回行优先 C 列表。
    """
    # 解包为有符号 int8
    As = [to_signed(to_bits(x, AW), AW) for x in A]
    Bs = [to_signed(to_bits(x, AW), AW) for x in B]
    C = []
    for i in range(N):
        for j in range(N):
            acc = 0
            for k in range(K):
                acc += As[i * K + k] * Bs[k * N + j]
            C.append(to_signed(to_bits(acc, CW), CW))
    return C
