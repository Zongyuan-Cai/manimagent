按常见约定，计算向量 \(a\) 在向量 \(b\) 上的投影。表中给出  
\[
a=(3,2),\qquad b=(2,0)
\]
[[material_66786d0fbccc4ed2b29e67e918800490:block_289a7c8071bb446cb42c987580dd60fd]]。题目要求先给出投影系数，再给出投影向量，并解释二者区别 [[material_66786d0fbccc4ed2b29e67e918800490:block_081a7ff0ee704436bf7d3acc35a07bfc]]。

## 1. 投影系数

设投影向量为 \(\lambda b\)，则
\[
a\cdot b=3\cdot 2+2\cdot 0=6,
\]
\[
b\cdot b=2^2+0^2=4.
\]

因此投影系数为
\[
\lambda=\frac{a\cdot b}{b\cdot b}
=\frac{6}{4}
=\frac{3}{2}.
\]

所以投影系数是
\[
\boxed{\lambda=\frac32}.
\]

## 2. 投影向量

向量 \(a\) 在 \(b\) 上的投影向量为
\[
\operatorname{proj}_{b}a=\lambda b
=\frac32(2,0)
=(3,0).
\]

因此投影向量是
\[
\boxed{\operatorname{proj}_{b}a=(3,0)}.
\]

也可以验证：
\[
a=(3,2)=(3,0)+(0,2),
\]
其中 \((3,0)\) 沿 \(b\) 的方向，\((0,2)\) 与 \(b=(2,0)\) 垂直。

## 3. 投影系数与投影向量的区别

- **投影系数** \(\lambda=\frac32\) 是一个**标量**，表示投影向量相对于向量 \(b\) 的缩放倍数：
  \[
  \operatorname{proj}_{b}a=\lambda b.
  \]
- **投影向量** \((3,0)\) 是一个**向量**，表示 \(a\) 在 \(b\) 所在直线方向上的分量。
- 若把“投影长度”单独理解，则有向投影长度为
  \[
  \frac{a\cdot b}{\|b\|}
  =\frac{6}{2}=3,
  \]
  它等于 \(\lambda\|b\|=\frac32\cdot 2=3\)。

因此：
\[
\boxed{\text{投影系数为 }\frac32,\qquad \text{投影向量为 }(3,0).}
\]

> 若题意是反过来求 \(b\) 在 \(a\) 上的投影，则投影系数为 \(\frac{b\cdot a}{a\cdot a}=\frac{6}{13}\)，投影向量为 \(\frac{6}{13}(3,2)=\left(\frac{18}{13},\frac{12}{13}\right)\)。