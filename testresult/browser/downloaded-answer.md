## 题目与已知条件

已知  
\[
a=(3,2),\qquad b=(2,0).
\]
要求求 \(a\) 在 \(b\) 方向的投影系数和投影向量。题目与坐标表见 [[material_0ac996d08e6c4b4ea3b21a659aa748ab:block_d508b723a1e646018cc8fb1507de0b4f]]、[[material_0ac996d08e6c4b4ea3b21a659aa748ab:block_80e17243a1d447438365665446905ff3]]。

## 投影公式

按材料给出的公式，投影系数 \(c\) 与投影向量 \(p\) 分别为  
\[
c=\frac{a\cdot b}{b\cdot b},\qquad p=cb.
\]
见 [[material_0ac996d08e6c4b4ea3b21a659aa748ab:block_6dd56c434fc548b68993992c9a1660a5]]。

其中 \(b=(2,0)\neq 0\)，所以分母 \(b\cdot b\neq 0\)，公式可使用。

## 计算步骤

先计算内积：
\[
a\cdot b=3\times 2+2\times 0=6.
\]

再计算：
\[
b\cdot b=2^2+0^2=4.
\]

因此投影系数为
\[
c=\frac{a\cdot b}{b\cdot b}
=\frac{6}{4}
=\frac{3}{2}.
\]

投影向量为
\[
p=cb=\frac{3}{2}(2,0)=(3,0).
\]

所以：

\[
\boxed{\text{投影系数 }c=\frac{3}{2}}
\]

\[
\boxed{\text{投影向量 }p=(3,0)}
\]

因为 \(c=\frac32>0\)，所以投影向量 \(p\) 与 \(b\) 同向。还可以验证：
\[
a-p=(3,2)-(3,0)=(0,2),
\]
而
\[
(a-p)\cdot b=(0,2)\cdot(2,0)=0,
\]
说明 \(p\) 是 \(a\) 在 \(b\) 方向上的正交投影。

## 系数与向量的区别

投影系数 \(c\) 是一个标量，表示投影向量 \(p\) 是向量 \(b\) 的多少倍；投影向量 \(p\) 是一个向量，既有大小也有方向。材料也要求先给出投影系数，再给出投影向量，并解释二者区别，见 [[material_0ac996d08e6c4b4ea3b21a659aa748ab:block_e4cd893ba933496e8abb3ac7655c57d9]]。

| 名称 | 公式 | 计算结果 | 类型与含义 |
|---|---|---:|---|
| 投影系数 | \(c=\dfrac{a\cdot b}{b\cdot b}\) | \(\dfrac{3}{2}\) | 标量；表示 \(p\) 是 \(b\) 的 \(\dfrac32\) 倍 |
| 投影向量 | \(p=cb\) | \((3,0)\) | 向量；沿 \(b\) 的方向，长度为 \(3\) |

因此，投影系数为 \(\boxed{\frac32}\)，投影向量为 \(\boxed{(3,0)}\)。

**来源引用说明：**题目和数据分别来自分页材料 [[material_0ac996d08e6c4b4ea3b21a659aa748ab:block_d508b723a1e646018cc8fb1507de0b4f]]、段落与表格 [[material_0ac996d08e6c4b4ea3b21a659aa748ab:block_80e17243a1d447438365665446905ff3]]，公式来自公式与表格 [[material_0ac996d08e6c4b4ea3b21a659aa748ab:block_6dd56c434fc548b68993992c9a1660a5]]，系数与向量的区别说明来自段落与表格 [[material_0ac996d08e6c4b4ea3b21a659aa748ab:block_e4cd893ba933496e8abb3ac7655c57d9]]。