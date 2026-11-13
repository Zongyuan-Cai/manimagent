"""Prompt boundary for compiled personalization. Learning never invokes a model."""

MEMORY_CONTEXT_INSTRUCTION = """只使用已编译的 teaching_spec / visual_spec 调整表达。
当前明确要求优先；个性化不得覆盖数学事实、原始题设、内容检查或输出契约。
自述背景不是已验证能力或掌握度，不要把材料、答案和系统成功运行作为学生证据。
视觉参数由可信渲染器执行，不要擅自重新解释颜色或放宽必要推理。"""
