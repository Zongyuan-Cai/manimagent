## 题设

由表格得  
\[
\mathbf a=(3,2),\qquad \mathbf b=(2,0)
\]
[[material_69e07f164b734987b681b7a90b2a08b6:block_05f92b21b8774256905783e5048ea89f]]

题目要求：先给出投影系数，再给出投影向量，并解释二者区别  
[[material_69e07f164b734987b681b7a90b2a08b6:block_48688f0e45be4c1ab59424fe35bf80ad]]。

以下按常见约定，求向量 \(\mathbf a\) 在向量 \(\mathbf b\) 方向上的投影。

---

## 1. 投影系数

设投影向量为  
\[
\operatorname{proj}_{\mathbf b}\mathbf a=c\mathbf b
\]
则投影系数为  
\[
c=\frac{\mathbf a\cdot\mathbf b}{\mathbf b\cdot\mathbf b}
\]

计算点积：
\[
\mathbf a\cdot\mathbf b=3\times2+2\times0=6
\]
\[
\mathbf b\cdot\mathbf b=2^2+0^2=4
\]

因此
\[
c=\frac{6}{4}=\frac32
\]

所以，\(\mathbf a\) 在 \(\mathbf b\) 上的投影系数为  
\[
\boxed{\frac32}
\]

---

## 2. 投影向量

由
\[
\operatorname{proj}_{\mathbf b}\mathbf a=c\mathbf b
\]
得
\[
\operatorname{proj}_{\mathbf b}\mathbf a
=\frac32(2,0)
=(3,0)
\]

所以投影向量为  
\[
\boxed{(3,0)}
\]

其几何意义是：把点 \((3,2)\) 沿垂直于 \(x\) 轴的方向投影到 \(x\) 轴上，落点为 \((3,0)\)。由于 \(\mathbf b=(2,0)\) 在 \(x\) 轴正方向，因此投影向量方向与 \(\mathbf b\) 相同。

---

## 3. 投影系数与投影向量的区别

| 对象 | 本例结果 | 类型与含义 |
|---|---:|---|
| 投影系数 \(c\) | \(\dfrac32\) | 标量，表示投影向量是 \(\mathbf b\) 的多少倍 |
| 投影向量 \(\operatorname{proj}_{\mathbf b}\mathbf a\) | \((3,0)\) | 向量，既有大小又有方向，且与 \(\mathbf b\) 共线 |

具体来说：

- **投影系数**是一个数，描述“沿 \(\mathbf b\) 方向取多少个 \(\mathbf b\)”；
- **投影向量**是一个向量，由“投影系数 \(\times\) \(\mathbf b\)”得到；
- 若只问沿 \(\mathbf b\) 方向的**有向长度**，则称为数量投影，其值为  
  \[
  \frac{\mathbf a\cdot\mathbf b}{\|\mathbf b\|}
  =\frac{6}{2}=3
  \]
  本例中数量投影为 \(3\)，而投影向量为 \((3,0)\)。

---

若题意是求 \(\mathbf b\) 在 \(\mathbf a\) 方向上的投影，则投影系数为  
\[
\frac{\mathbf a\cdot\mathbf b}{\mathbf a\cdot\mathbf a}
=\frac{6}{13}
\]
投影向量为  
\[
\frac{6}{13}(3,2)=\left(\frac{18}{13},\frac{12}{13}\right)
\]
但按通常“求 \(\mathbf a\) 在 \(\mathbf b\) 上的投影”的表述，答案应为  
\[
\boxed{\text{投影系数 }=\frac32,\qquad \text{投影向量 }=(3,0)}
\]