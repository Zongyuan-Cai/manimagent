题设给出  
\[
\mathbf a=(3,2),\qquad \mathbf b=(2,0)
\]
[[material_a39c39996ab94067a69de31cfa386f71:block_1589efa38f3f46728761769b7b77d23e]]

先计算内积与模长平方：
\[
\mathbf a\cdot\mathbf b=3\cdot 2+2\cdot 0=6,
\]
\[
\|\mathbf a\|^2=3^2+2^2=13,\qquad
\|\mathbf b\|^2=2^2+0^2=4.
\]

题目未明确是“\(\mathbf b\) 在 \(\mathbf a\) 上的投影”还是“\(\mathbf a\) 在 \(\mathbf b\) 上的投影”，因此分两种常见约定说明。

---

### 1. 若求 \(\mathbf b\) 在 \(\mathbf a\) 上的投影

投影系数为使投影向量等于 \(\lambda\mathbf a\) 的系数：
\[
\lambda=\frac{\mathbf a\cdot\mathbf b}{\mathbf a\cdot\mathbf a}
=\frac{6}{13}.
\]
投影向量为
\[
\operatorname{proj}_{\mathbf a}\mathbf b
=\lambda\mathbf a
=\frac{6}{13}(3,2)
=\left(\frac{18}{13},\frac{12}{13}\right).
\]

若“投影”指标量有向投影长度，则为
\[
\frac{\mathbf a\cdot\mathbf b}{\|\mathbf a\|}
=\frac{6}{\sqrt{13}}.
\]

---

### 2. 若求 \(\mathbf a\) 在 \(\mathbf b\) 上的投影

投影系数为
\[
\lambda=\frac{\mathbf a\cdot\mathbf b}{\mathbf b\cdot\mathbf b}
=\frac{6}{4}=\frac32.
\]
投影向量为
\[
\operatorname{proj}_{\mathbf b}\mathbf a
=\lambda\mathbf b
=\frac32(2,0)
=(3,0).
\]

对应的标量有向投影长度为
\[
\frac{\mathbf a\cdot\mathbf b}{\|\mathbf b\|}
=\frac{6}{2}=3.
\]

---

因此，若按常见说法“求 \(\mathbf b\) 在 \(\mathbf a\) 上的投影”，则答案为  
\[
\boxed{\lambda=\frac{6}{13},\qquad
\operatorname{proj}_{\mathbf a}\mathbf b
=\left(\frac{18}{13},\frac{12}{13}\right)}
\]
若按“求 \(\mathbf a\) 在 \(\mathbf b\) 上的投影”，则答案为  
\[
\boxed{\lambda=\frac32,\qquad
\operatorname{proj}_{\mathbf b}\mathbf a=(3,0)}
\]

注意：“投影系数”若指组合系数，则是 \(\frac{6}{13}\) 或 \(\frac32\)；若指标量投影长度，则分别是 \(\frac{6}{\sqrt{13}}\) 或 \(3\)。