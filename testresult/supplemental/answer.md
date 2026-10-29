已知 \(\mathbf a=(3,2)\)，\(\mathbf b=(2,0)\)，求 \(\mathbf a\) 在 \(\mathbf b\) 方向上的投影。[[material_5f1b0073ffae4f8593a8082a82bd624a:block_2137940836f54012a214f44222a73761]]

先计算：

\[
\mathbf a\cdot \mathbf b=3\cdot 2+2\cdot 0=6,
\]

\[
\|\mathbf b\|=\sqrt{2^2+0^2}=2,\qquad 
\|\mathbf b\|^2=4.
\]

## 1. 数量投影（标量投影）

\(\mathbf a\) 在 \(\mathbf b\) 方向上的数量投影为

\[
\operatorname{proj}_{\mathbf b}\mathbf a
= \|\mathbf a\|\cos\theta
= \frac{\mathbf a\cdot\mathbf b}{\|\mathbf b\|}
= \frac{6}{2}=3.
\]

它表示 \(\mathbf a\) 沿 \(\mathbf b\) 方向的有向长度。

## 2. 投影向量

\(\mathbf b\) 方向的单位向量为

\[
\mathbf e_{\mathbf b}
=\frac{\mathbf b}{\|\mathbf b\|}
=\frac{(2,0)}{2}=(1,0).
\]

因此投影向量为

\[
\operatorname{proj}_{\mathbf b}^{\text{vec}}\mathbf a
=(\mathbf a\cdot \mathbf e_{\mathbf b})\mathbf e_{\mathbf b}
=\frac{\mathbf a\cdot\mathbf b}{\|\mathbf b\|^2}\mathbf b
=\frac{6}{4}(2,0)
=\frac32(2,0)
=(3,0).
\]

也可以验证：

\[
\mathbf a-(3,0)=(0,2),
\]

而

\[
(0,2)\cdot (2,0)=0,
\]

所以 \((3,0)\) 确实是 \(\mathbf a\) 沿 \(\mathbf b\) 方向的投影向量。

## 3. 投影系数的两种常见约定

“投影系数”在不同教材中可能有两种含义：

### 约定一：相对于单位方向向量

若把投影写成“数量投影 \(\times\) 单位向量”，则系数就是数量投影：

\[
\operatorname{proj}_{\mathbf b}\mathbf a=3.
\]

此时

\[
\text{投影向量}=3\mathbf e_{\mathbf b}
=3(1,0)=(3,0).
\]

### 约定二：相对于原向量 \(\mathbf b\) 的线性组合系数

若把 \(\mathbf a\) 分解为

\[
\mathbf a=\lambda\mathbf b+\mathbf a_\perp,
\qquad \mathbf a_\perp\perp\mathbf b,
\]

则

\[
\lambda=\frac{\mathbf a\cdot\mathbf b}{\mathbf b\cdot\mathbf b}
=\frac{6}{4}=\frac32.
\]

此时

\[
\text{投影向量}=\lambda\mathbf b
=\frac32(2,0)=(3,0).
\]

## 4. 区别

- **投影系数是一个数**。它表示分解中某个方向上的“倍数”。
  - 若以单位向量 \(\mathbf e_{\mathbf b}\) 为基底，系数为 \(3\)；
  - 若以原向量 \(\mathbf b\) 为基底，系数为 \(\dfrac32\)。
- **投影向量是一个向量**。它有方向，方向沿 \(\mathbf b\)；若系数为负，则方向与 \(\mathbf b\) 相反。本题投影向量为
  \[
  (3,0).
  \]
- 关系为：
  \[
  \text{投影向量}
  =\text{数量投影}\times \mathbf e_{\mathbf b}
  =\frac{\mathbf a\cdot\mathbf b}{\|\mathbf b\|^2}\mathbf b.
  \]

因此本题中：

\[
\boxed{\text{数量投影}=3}
\]

\[
\boxed{\text{相对于 }\mathbf b\text{ 的投影系数}=\frac32}
\]

\[
\boxed{\text{投影向量}=(3,0)}
\]

若把“投影系数”理解为相对于单位方向的系数，则它是 \(3\)，而不是 \(\frac32\)。