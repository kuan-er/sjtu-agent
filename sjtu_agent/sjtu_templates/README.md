# SJTU LaTeX Templates

内置模板来自 SJTU Overleaf (latex.sjtu.edu.cn) Gallery。学生可用 `/template <名称>` 套用。

## 如何新增模板

1. 在 Overleaf Gallery 找到模板项目
2. 通过 Git Bridge 克隆：
   ```bash
   git clone https://latex.sjtu.edu.cn/git/<project-id> sjtu_agent/sjtu_templates/<模板名>
   ```
3. 添加 README.md 说明用途
4. 提交 PR

## 当前模板

| 名称 | 用途 |
|------|------|
| `bachelor-thesis` | 本科毕业论文（请在 Overleaf 克隆实际模板后替换） |
| `course-report` | 课程报告（请在 Overleaf 克隆实际模板后替换） |

这些目录目前仅包含框架，实际 LaTeX 模板文件(.cls/.sty/main.tex)需从 SJTU Overleaf Gallery 获取。
