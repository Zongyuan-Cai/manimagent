原题给出向量  
\[
\mathbf a=(3,2),\qquad \mathbf b=(2,0),
\]
求 \(\mathbf a\) 在 \(\mathbf b\) 方向上的投影系数与投影向量，并说明公式及区别[[material_1c4f594b3ac643caae83454aeaed704b:block_0e74fabb63a94d01a42f56669f67e9bf]]。

## 1. 投影系数

若“投影系数”指把投影向量写成 \(\mathbf b\) 的倍数时的系数，即使得
\[
\operatorname{proj}_{\mathbf b}\mathbf a=c\mathbf b
\]
的标量 \(c\)，则
\[
c=\frac{\mathbf a\cdot\mathbf b}{\mathbf b\cdot\mathbf b}.
\]
代入数据：
\[
\mathbf a\cdot\mathbf b=3\cdot2+2\cdot0=6,
\]
\[
\mathbf b\cdot\mathbf b=2^2+0^2=4.
\]
因此
\[
c=\frac{6}{4}=\frac32.
\]

所以投影系数为
\[
\boxed{c=\frac32}.
\]

## 2. 投影向量

投影向量公式为
\[
\operatorname{proj}_{\mathbf b}\mathbf a
=
\frac{\mathbf a\cdot\mathbf b}{\mathbf b\cdot\mathbf b}\,\mathbf b.
\]
代入得
\[
\operatorname{proj}_{\mathbf b}\mathbf a
=
\frac32(2,0)
=
(3,0).
\]
因此
\[
\boxed{\operatorname{proj}_{\mathbf b}\mathbf a=(3,0)}.
\]

也可以用单位向量表示。令
\[
\mathbf e_{\mathbf b}=\frac{\mathbf b}{\|\mathbf b\|}.
\]
因为
\[
\|\mathbf b\|=\sqrt{2^2+0^2}=2,
\]
所以
\[
\mathbf e_{\mathbf b}=\frac{(2,0)}2=(1,0).
\]
于是
\[
\operatorname{proj}_{\mathbf b}\mathbf a
=
(\mathbf a\cdot\mathbf e_{\mathbf b})\mathbf e_{\mathbf b}
=
(3)(1,0)
=
(3,0).
\]

## 3. 验证

原向量与投影向量之差为
\[
\mathbf a-\operatorname{proj}_{\mathbf b}\mathbf a
=
(3,2)-(3,0)
=
(0,2).
\]
它满足
\[
(0,2)\cdot(2,0)=0,
\]
即该差向量与 \(\mathbf b\) 垂直，符合正交投影的几何意义。

## 4. 投影系数、数量投影与投影向量的区别

| 名称 | 公式 | 本题结果 | 含义 |
|---|---|---|---|
| 投影系数 | \(\displaystyle c=\frac{\mathbf a\cdot\mathbf b}{\mathbf b\cdot\mathbf b}\) | \(\displaystyle \frac32\) | 投影向量是 \(\mathbf b\) 的多少倍 |
| 数量投影（有向投影长度） | \(\displaystyle \mathbf a\cdot\mathbf e_{\mathbf b}=\frac{\mathbf a\cdot\mathbf b}{\|\mathbf b\|}\) | \(3\) | 投影向量的有向长度 |
| 投影向量 | \(\displaystyle \operatorname{proj}_{\mathbf b}\mathbf a=\frac{\mathbf a\cdot\mathbf b}{\mathbf b\cdot\mathbf b}\mathbf b\) | \((3,0)\) | 位于 \(\mathbf b\) 所在直线上的实际向量 |

因此，本题中：

\[
\boxed{\text{投影系数}=\frac32}
\]

\[
\boxed{\text{数量投影}=3}
\]

\[
\boxed{\text{投影向量}=(3,0)}
\]

其中投影系数是一个标量，投影向量是一个向量；投影系数依赖所选方向向量 \(\mathbf b\) 的长度，而投影向量不随 \(\mathbf b\) 换成同方向的非零倍数而改变。