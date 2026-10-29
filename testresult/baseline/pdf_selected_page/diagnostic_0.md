题目为：给定 \(\mathbf a=(3,2)\)，\(\mathbf b=(2,0)\)，求投影系数和投影向量 [[material_188a8292379444ae8b6619f0e7181607:block_12de89cebc9e4285bd8c4cb050723129]]。

按通常理解，求 \(\mathbf a\) 在 \(\mathbf b\) 方向上的投影。

先计算内积与模长平方：

\[
\mathbf a\cdot \mathbf b
=3\cdot 2+2\cdot 0=6
\]

\[
\mathbf b\cdot \mathbf b
=2^2+0^2=4
\]

若投影系数定义为相对于向量 \(\mathbf b\) 的系数，则

\[
\lambda=\frac{\mathbf a\cdot \mathbf b}{\mathbf b\cdot \mathbf b}
=\frac{6}{4}
=\frac{3}{2}
\]

因此投影向量为

\[
\operatorname{proj}_{\mathbf b}\mathbf a
=\lambda \mathbf b
=\frac{3}{2}(2,0)
=(3,0)
\]

所以：

\[
\boxed{\text{投影系数 } \lambda=\frac{3}{2}}
\]

\[
\boxed{\text{投影向量 } \operatorname{proj}_{\mathbf b}\mathbf a=(3,0)}
\]

补充说明：若把“投影系数”理解为沿 \(\mathbf b\) 的**单位方向**的标量投影，则其为

\[
\frac{\mathbf a\cdot \mathbf b}{\|\mathbf b\|}
=\frac{6}{2}=3
\]

但投影向量仍为 \((3,0)\)。