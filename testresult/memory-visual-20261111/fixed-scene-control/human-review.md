# 固定输入对照：R11 s5 与 v20

本实验独立于 20 轮学生任务，不写入记忆数据库，不修改 R11 原始记录，也不将其失败追记为成功。

## 固定条件与调用证据

取 R11 s5 首次场景生成请求的原 user payload，保留全文、原请求、原首个响应与最终通过结构检查的旧场景；仅更换为当前 v20 SCENE_PROMPT。新调用使用同一 deepseek-flash，thinking disabled，max_tokens=16000，实际恰好 1 次请求，禁止自动修复或重试，没有增加 feedback。

新旧 user payload 按字节完全一致，SHA256 均为 `94d0beb67fd883b1a849ab42b32594ea74c857a029a2ad02a6ab7edabdf6b4ca`。当前 prompt SHA256 为 `016eac03aa7f7ea00e704cd1baa0b6693be1f89f5aad4a30052442da19d38133`。新调用实际输入 9083 tokens、输出 1760 tokens；原始 provider usage 见 `candidate_calls.json` 和 `candidate_usage.json`。

旧版 R11 最终可渲染场景曾经过一次结构修复，第二次请求包含纠错消息，但首个 user payload 不变。因此此对照不能伪称两边调用轨迹完全一致；全部记录已保留。

## 旧版真实像素

原 R11 最终 s5 未经改写，resolve_scene 得到与原记录完全相同的数据，真实 layout/render 成功，产生 78.17 秒单场景影片与 5 张关键帧。我已检查 `original/visual_review/contact_sheet_01.png` 的全部 5 帧。

可以直接看到蓝色 V 形，却配有“画出区间上的抛物线”与 y=(x-1)²；后面又在尖点放水平红色“切线”。此画面把不可导的尖角图像与导数为 0 的抛物线混在一起，**确认存在数学视觉硬伤**。文字和公式没有明显裁切，但这不能抵消图像错误。红色切线也不符合该固定 payload 中辅助对象金色的偏好。

## v20 新候选：仍失败

新候选确实改成“采样点”方向，但把多个采样坐标塞进同一个 `kind=point` 的 `curve_pts` 对象，四个节拍违反“point 只能一个坐标”的契约。真实客户端抛出 InvalidResponse，完整异常链保留在 `result.json`。没有手改候选、没有添加反馈、没有第二次调用。

因此新候选没有进入 resolve_scene/layout/render，也没有真实 PNG。**不能声称 v20 已修复画面**；仅能说它避免了旧的连接折线意图，却出现了另一处结构错误。

下一最小规则是：每个 point 对象恰好一个坐标；多个采样点分别使用独立 object_id，仍遵守总对象数限制。不能通过 renderer 默默拆分或丢弃点来兜底。
