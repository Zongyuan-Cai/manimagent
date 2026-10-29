题设：\(a=(3,2)\)，\(b=(2,0)\)，求 \(a\) 在 \(b\) 方向上的投影系数与投影向量。[[material_b4afc80e48354ceaa90b6bfa42c082a7:block_e98916c5e2b5433b86d9e26719cbf10d]]

先计算：

\[
a\cdot b=3\cdot 2+2\cdot 0=6
\]

\[
\|b\|=\sqrt{2^2+0^2}=2,
\qquad 
\|b\|^2=b\cdot b=4
\]

\(b\) 方向的单位向量为

\[
e_b=\frac{b}{\|b\|}=\frac{(2,0)}{2}=(1,0)
\]

因此 \(a\) 在 \(b\) 方向上的标量投影为

\[
a\cdot e_b=(3,2)\cdot(1,0)=3
\]

投影向量为

\[
\operatorname{proj}_b a=(a\cdot e_b)e_b=3(1,0)=(3,0)
\]

若“投影系数”定义为使投影向量写成 \(c b\) 的系数，即

\[
\operatorname{proj}_b a=c b
\]

则

\[
c=\frac{a\cdot b}{b\cdot b}=\frac{6}{4}=\frac32
\]

并且

\[
c b=\frac32(2,0)=(3,0)
\]

**结论：**

- 若“投影系数”指沿 \(b\) 分解的系数，则  
  \[
  \boxed{c=\frac32}
  \]
- 若“投影系数”指标量投影，则  
  \[
  \boxed{a\cdot e_b=3}
  \]
- 投影向量为  
  \[
  \boxed{\operatorname{proj}_b a=(3,0)}
  \]

通常在线性代数中，“投影系数”多指 \(c=\dfrac{a\cdot b}{b\cdot b}\)，本题即为 \(\boxed{\dfrac32}\)；对应的投影向量仍为 \(\boxed{(3,0)}\)。