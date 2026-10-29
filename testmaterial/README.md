# 上传测试材料

这些是人工构造的确定性样本，不包含个人材料或 API Key。使用根目录开发环境运行 `python scripts/create_test_materials.py` 可重建；20 MiB 超限文件由脚本生成，不提交 Git。

| 样本 | 用途 |
|---|---|
| 投影题.txt | UTF-8 中文纯文本 |
| 公式与表格.md | LaTeX、Markdown 表格与全文注入 |
| 中文题目.pdf | 中文文本型 PDF |
| 分页材料.pdf | 只选第 2 页，排除其他页的无关数据 |
| 段落与表格.docx | 段落和表格阅读顺序 |
| same_a/同名.txt、same_b/同名.txt | 同名原件、不同来源 ID |
| 缺少条件.txt、不可信指令.txt | 缺少必要题设与材料中的指令攻击 |
| 空白页.pdf、扫描页.pdf、题图.pdf | 空页、扫描文字与必要矢量题图 |
| 公式对象.docx、内嵌图片.docx | 未实现的 Word 公式对象与图像 |
| 加密.pdf、损坏.pdf、空文件.txt | 无法处理或没有内容的输入 |
| 错误编码.txt、不支持.csv、扫描图片.png | 上传白名单和编码检查 |
| 超限.txt | 单文件实际字节上限 |

加密 PDF 的测试密码为 `test-password`，仅用于构造拒绝处理场景。PDF 与 DOCX 的“公式候选无法可靠读取”不等同于模型已理解；这些样本应在解析阶段进入待处理状态。

`manifest.json` 记录材料大小。PDF 预览保存在 testresult/fixture-previews；DOCX 原件通过 OOXML、阅读顺序和表格结构验证，当前环境缺少 bundled LibreOffice，因此没有把 DOCX 原件的 PDF 预览列为通过项。应用输出的文字、公式与表格另做真实浏览器验收。
