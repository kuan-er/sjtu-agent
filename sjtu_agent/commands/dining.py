"""
sjtu_agent/commands/dining.py — /eat 命令执行（飞书 / WebUI 共享）。
"""

from __future__ import annotations


def fetch_eat_recommendation(campus: str = "闵行") -> str:
    """获取食堂推荐，返回 Markdown。"""
    from sjtu_agent.agent.tools._dining import tool_recommend_canteen, tool_get_canteen_crowd

    result = tool_recommend_canteen(campus=campus)
    if not result.get("ok"):
        crowd = tool_get_canteen_crowd(campus=campus)
        if crowd.get("ok"):
            lines = ["## 🍽️ 食堂实时拥挤度", ""]
            for c in crowd.get("canteens", []):
                label = c["overall_label"]
                status = ("🟢" if label in ("空闲",) else
                          "🟡" if label in ("适中",) else
                          "🟠" if label in ("较挤",) else "🔴")
                lines.append(f"- {status} **{c['name']}** — {label}（{c['overall_rate']}%）")
            return "\n".join(lines)
        return f"食堂数据暂时不可用：{result.get('error', '')}"

    lines = [
        f"## 🍽️ {result['meal_type']}推荐 · {result['campus']}校区",
        "",
        result.get("summary", ""),
        "",
    ]
    for r in result.get("recommendations", []):
        label = r["overall_label"]
        status = ("🟢" if label in ("空闲",) else
                  "🟡" if label in ("适中",) else
                  "🟠" if label in ("较挤",) else "🔴")
        lines.append(f"### {r['canteen_name']} {status} {label}（{r['overall_rate']}%）")
        for reason in r.get("reasons", []):
            lines.append(f"- {reason}")
        if r.get("recommended_sub_areas"):
            areas = "、".join(r["recommended_sub_areas"][:3])
            lines.append(f"- 推荐窗口：{areas}")
        lines.append("")

    if result.get("has_history"):
        lines.append(f"_基于 {result['history_count']} 条历史记录，推荐会越来越准_")
    lines.append("_用 `/eat 徐汇` 切换校区，选好后告诉我「我去XX吃了」帮你记录偏好_")

    return "\n".join(lines)


def cmd_eat(user_id: str, parts: list[str]) -> str:
    del user_id
    campus = parts[1].strip() if len(parts) > 1 else "闵行"
    valid = ("闵行", "徐汇", "张江")
    if campus not in valid:
        return f"[eat] 未知校区「{campus}」，可选：{' / '.join(valid)}"
    return "[eat] 正在查询食堂拥挤度…\n\n" + fetch_eat_recommendation(campus)
