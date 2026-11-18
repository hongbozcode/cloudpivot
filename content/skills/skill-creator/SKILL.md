---
name: skill-creator
description: 创建或更新可在 CloudPivot 中复用的 Skill。用户要求把流程封装成 Skill、修改已有 Skill，或制作并试用新 Skill 时使用。
---

# 制作 Skill

1. 确定要复用的任务、触发条件、输入和预期产物。更新已有 Skill 时，先读取其 `SKILL.md` 和本次相关的资源。
2. 选择由小写字母、数字和单个连字符组成的名称，最多 64 个字符，首尾使用字母或数字。使用虚拟目录 `/skills/<name>/`。将 `skill-creator` 留作内置 Skill 名称。
3. 如有必要，先用 `write_file` 保存 `references/` 或 `assets/` 中的资源。只加入流程实际依赖的文件，并在正文里用相对路径说明何时读取它们。设计工作流程时使用 CloudPivot 现有的工具。
4. 用 `write_file` 创建 `/skills/<name>/SKILL.md`，或用 `edit_file` 更新已有文件。文件应从 YAML 元数据开始，`name` 与目录名完全一致，`description` 描述何时应选择该 Skill。正文记录关键步骤和可验证的产物。

```markdown
---
name: format-meeting-notes
description: 将会议笔记整理为决议、待办、负责人和截止日期。用户要求生成会后纪要时使用。
---

# 整理会议纪要

- 提取明确作出的决议和已承诺的待办事项。
- 给每项待办标注负责人和截止日期。原文缺失的字段标记为待确认。
```

写入后，在下一次模型调用的 Skill 列表中确认名称和路径。若未出现，读取 `SKILL.md` 检查 YAML、目录名和文件名。用户要求立即使用时，在当前会话里用 `read_file` 读取新 Skill，按其步骤处理任务。汇报保存路径和实际验证结果。
