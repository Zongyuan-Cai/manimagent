## 题目

给定  
\[
\mathbf a=(3,2),\qquad \mathbf b=(2,0),
\]
求 \(\mathbf a\) 在 \(\mathbf b\) 方向上的投影系数 \(c\) 与投影向量 \(\mathbf p\)。[[material_47b6f7bb057c442f95316d60e69e907c:block_7cc35611d5f94c10a6496525015474c3]]

这里采用常见定义：设投影向量  
\[
\mathbf p=c\mathbf b,
\]
即 \(c\) 是相对于向量 \(\mathbf b\) 本身的投影系数。

---

## 计算

向量点积为  
\[
\mathbf a\cdot \mathbf b=3\cdot 2+2\cdot 0=6.
\]

又因为  
\[
\mathbf b\cdot \mathbf b=2^2+0^2=4,
\]
所以投影系数为  
\[
c=\frac{\mathbf a\cdot \mathbf b}{\mathbf b\cdot \mathbf b}
=\frac{6}{4}
=\frac32.
\]

因此投影向量为  
\[
\mathbf p=c\mathbf b
=\frac32(2,0)
=(3,0).
\]

---

## 结论

\[
\boxed{c=\frac32}
\]

\[
\boxed{\mathbf p=(3,0)}
\]

也可以说明：\(\mathbf b=(2,0)\) 的方向是 \(x\) 轴正方向，所以 \(\mathbf a=(3,2)\) 在该方向上的投影向量就是 \((3,0)\)，投影长度为  
\[
|\mathbf p|=3.
\]

验证正交性：  
\[
\mathbf a-\mathbf p=(3,2)-(3,0)=(0,2),
\]
而  
\[
(0,2)\cdot (2,0)=0,
\]
说明 \(\mathbf a-\mathbf p\) 与 \(\mathbf b\) 垂直，符合正交投影的定义。

---

## 关于“投影系数”的说明

如果题目把 \(c\) 定义为相对于 \(\mathbf b\) 的**单位方向向量**  
\[
\mathbf e_{\mathbf b}=\frac{\mathbf b}{|\mathbf b|}=(1,0)
\]
的系数，那么  
\[
c'=\mathbf a\cdot \mathbf e_{\mathbf b}
=3\cdot 1+2\cdot 0=3,
\]
此时  
\[
\mathbf p=c'\mathbf e_{\mathbf b}=3(1,0)=(3,0).
\]

所以：

- 若定义为 \(\mathbf p=c\mathbf b\)，则  
  \[
  \boxed{c=\frac32};
  \]
- 若定义为 \(\mathbf p=c'\mathbf e_{\mathbf b}\)，则  
  \[
  \boxed{c'=3}.
  \]

无论采用哪种系数定义，投影向量都相同：
\[
\boxed{\mathbf p=(3,0)}.
\]