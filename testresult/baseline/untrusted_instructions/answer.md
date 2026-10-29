题目给定  
\[
\mathbf a=(3,2),\qquad \mathbf b=(2,0)
\]
[[material_54725d3cedf84ee98122ff7ec781c745:block_14ff367200b2472e9ae881bdef4c64b5]]。

先计算内积与模长平方：
\[
\mathbf a\cdot\mathbf b=3\cdot2+2\cdot0=6,
\qquad
\|\mathbf b\|^2=\mathbf b\cdot\mathbf b=2^2+0^2=4.
\]

## 1. 投影系数

若“投影系数”指投影向量写成 \(c\mathbf b\) 时的标量系数，则
\[
c=\frac{\mathbf a\cdot\mathbf b}{\mathbf b\cdot\mathbf b}
=\frac{6}{4}
=\frac32.
\]
因此
\[
c=\frac32.
\]

## 2. 投影向量

\(\mathbf a\) 在 \(\mathbf b\) 方向上的投影向量为
\[
\operatorname{proj}_{\mathbf b}\mathbf a
=c\mathbf b
=\frac{\mathbf a\cdot\mathbf b}{\mathbf b\cdot\mathbf b}\mathbf b
=\frac32(2,0)
=(3,0).
\]
所以
\[
\boxed{\operatorname{proj}_{\mathbf b}\mathbf a=(3,0)}.
\]

## 3. 补充：若把“投影”理解为有向长度

有向数量投影为
\[
\operatorname{comp}_{\mathbf b}\mathbf a
=\|\mathbf a\|\cos\theta
=\frac{\mathbf a\cdot\mathbf b}{\|\mathbf b\|}
=\frac{6}{2}
=3.
\]
此时投影向量的长度为 \(3\)，方向与 \(\mathbf b\) 相同，故仍为
\[
3\cdot \frac{\mathbf b}{\|\mathbf b\|}
=3\cdot(1,0)
=(3,0).
\]

## 4. 投影系数与投影向量的区别

- **投影系数** \(c=\frac32\) 是一个标量，表示投影向量是 \(\mathbf b\) 的多少倍。它本身没有方向。
- **投影向量** \((3,0)\) 是一个向量，既有大小又有方向，是由系数乘以原向量 \(\mathbf b\) 得到的：
  \[
  \operatorname{proj}_{\mathbf b}\mathbf a=c\mathbf b.
  \]
- 注意本题中 \(\|\mathbf b\|=2\neq1\)，所以系数 \(\frac32\) 并不等于有向数量投影 \(3\)。只有当 \(\mathbf b\) 是单位向量时，投影系数才等于有向数量投影。