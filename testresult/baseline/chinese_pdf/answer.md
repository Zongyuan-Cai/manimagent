## 题设

材料中的投影题为：给定  
\[
\mathbf a=(3,2),\qquad \mathbf b=(2,0),
\]
求投影系数 \(c\) 与投影向量 \(\mathbf p\)，并保留计算过程。[[material_eb0783686cf6458d8fb18ea3ffd8d650:block_6699dfff324b466f8f069174d37443d3]]

通常“求 \(\mathbf a\) 在 \(\mathbf b\) 上的投影”指：把 \(\mathbf a\) 分解为沿 \(\mathbf b\) 方向的分量与垂直于 \(\mathbf b\) 方向的分量，即
\[
\mathbf a=c\mathbf b+\mathbf e,\qquad \mathbf e\perp \mathbf b.
\]
其中 \(\mathbf p=c\mathbf b\) 就是 \(\mathbf a\) 在 \(\mathbf b\) 上的投影向量。

## 计算投影系数

投影系数公式为
\[
c=\frac{\mathbf a\cdot \mathbf b}{\mathbf b\cdot \mathbf b}.
\]

先计算点积：
\[
\mathbf a\cdot \mathbf b
=3\cdot 2+2\cdot 0
=6,
\]
\[
\mathbf b\cdot \mathbf b
=2^2+0^2
=4.
\]

因此
\[
c=\frac{6}{4}=\frac32.
\]

## 计算投影向量

\[
\mathbf p=c\mathbf b
=\frac32(2,0)
=(3,0).
\]

所以
\[
\boxed{c=\frac32,\qquad \mathbf p=(3,0).}
\]

## 几何解释与验证

向量 \(\mathbf b=(2,0)\) 指向水平方向，因此 \(\mathbf a=(3,2)\) 在 \(\mathbf b\) 方向上的投影就是它的水平分量：
\[
\mathbf p=(3,0).
\]
垂直于 \(\mathbf b\) 的分量为
\[
\mathbf a-\mathbf p=(3,2)-(3,0)=(0,2),
\]
而
\[
(0,2)\cdot(2,0)=0,
\]
说明该分量确实与 \(\mathbf b\) 垂直。

若把“投影系数”理解为带符号的投影长度，则
\[
\frac{\mathbf a\cdot\mathbf b}{\|\mathbf b\|}
=\frac{6}{2}=3,
\]
它与上面的系数 \(c=\frac32\) 相差一个因子 \(\|\mathbf b\|=2\)。在题目同时要求“投影系数 \(c\) 与投影向量 \(\mathbf p\)”的常见语境下，取
\[
\boxed{c=\frac32,\quad \mathbf p=(3,0)}
\]
是合适的。