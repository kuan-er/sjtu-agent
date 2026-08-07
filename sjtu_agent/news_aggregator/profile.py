"""sjtu_agent/news_aggregator/profile.py — 用户画像分析与更新。

画像存储在 user_profile.json，包含：
- keywords: 关键词词频（从对话历史提取）
- interests: 兴趣标签权重（LLM 分析）
- blocked_categories: 用户屏蔽的分类
- persona_summary: LLM 生成的人设描述
- conversation_count: 累计对话轮次

更新策略：
- 轻量更新：每次对话结束后调用 record_conversation()，增量更新 keywords
- 深度更新：每天 23:00 调用 deep_update()，LLM 重新生成 interests + persona
"""
from __future__ import annotations

import json
import math
import re
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from sjtu_agent import paths as _paths
from sjtu_agent.paths import atomic_write_json, read_json_safe

CST = timezone(timedelta(hours=8))

# 停用词（不计入关键词）
_STOP_WORDS = {
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "们",
    "这", "那", "有", "和", "与", "或", "但", "也", "都", "就",
    "不", "没", "很", "太", "更", "最", "还", "已", "被", "把",
    "什么", "怎么", "为什么", "如何", "哪里", "哪个", "多少",
    "可以", "需要", "应该", "能够", "帮我", "帮助", "请问",
    "谢谢", "好的", "好", "嗯", "啊", "哦", "吧", "呢",
}

# 兴趣标签候选（用于 LLM 分析时的参考）
_INTEREST_TAGS = [
    "保研", "考研", "出国留学", "就业求职", "实习",
    "课业学习", "作业", "考试", "成绩", "绩点",
    "电路实验", "物理实验", "编程", "数学",
    "校园生活", "宿舍", "餐厅", "社团活动",
    "二手交易", "失物招领", "拼车",
    "学术科研", "竞赛", "奖学金",
    "心理健康", "运动健身",
]

# 时效性：时间敏感的画像字段及其过期窗口（秒）。
# 稳定字段（姓名/专业/课程/偏好等）不在表内，恒视为有效。
TIME_SENSITIVE_EXPIRY_SECONDS = {
    "care_notes": 48 * 3600,            # 2 天
    "mood": 3 * 24 * 3600,              # 3 天
    "stress_level": 3 * 24 * 3600,      # 3 天
    "recent_events": 7 * 24 * 3600,     # 7 天
    "persona_summary": 30 * 24 * 3600,  # 30 天
}


def _field_timestamp(data: dict, field: str) -> str | None:
    """字段写入时间：优先 _timestamps[field]，回退 last_updated/updated_at。"""
    ts = data.get("_timestamps")
    if isinstance(ts, dict) and ts.get(field):
        return ts[field]
    return data.get("last_updated") or data.get("updated_at") or None


def _field_age_seconds(data: dict, field: str, now=None) -> float | None:
    stamp = _field_timestamp(data, field)
    if not stamp:
        return None
    try:
        dt0 = datetime.fromisoformat(stamp)
        if dt0.tzinfo is None:
            dt0 = dt0.replace(tzinfo=CST)
        if now is None:
            now = datetime.now(CST)
        if now.tzinfo is None:
            now = now.replace(tzinfo=CST)
        return (now - dt0).total_seconds()
    except Exception:
        return None


def _is_fresh(data: dict, field: str) -> bool:
    """时效字段是否未过期；稳定字段恒 fresh；无时间戳的旧画像保守显示。"""
    window = TIME_SENSITIVE_EXPIRY_SECONDS.get(field)
    if window is None:
        return True
    age = _field_age_seconds(data, field)
    if age is None:
        return True
    return age <= window


class UserProfile:
    """用户画像，线程安全读写。"""

    def __init__(self):
        self._path = _paths.USER_PROFILE_PATH

    def _default(self) -> dict:
        return {
            "version": 1,
            "updated_at": datetime.now(CST).isoformat(),
            "interests": {},
            "keywords": {},
            "blocked_categories": [],
            "persona_summary": "",
            "conversation_count": 0,
            "last_analyzed_count": 0,
            "last_topics": [],
        }

    def load(self) -> dict:
        data = read_json_safe(self._path, default=None)
        if data is None or not isinstance(data, dict):
            return self._default()
        # 补全缺失字段
        default = self._default()
        for k, v in default.items():
            data.setdefault(k, v)
        return data

    def save(self, data: dict) -> None:
        data["updated_at"] = datetime.now(CST).isoformat()
        atomic_write_json(self._path, data)

    # ------------------------------------------------------------------
    # 轻量更新：每次对话后调用
    # ------------------------------------------------------------------

    def record_conversation(self, user_text: str, agent_reply: str) -> None:
        """增量更新关键词词频，记录对话轮次。"""
        data = self.load()

        # 提取关键词（简单分词：按标点/空格切分，过滤停用词和短词）
        combined = f"{user_text} {agent_reply}"
        tokens = re.split(r'[\s，。！？、；：“”‘’（）【】\[\],.!?;:()\n]+', combined)
        keywords: dict[str, int] = data.get("keywords", {})
        for tok in tokens:
            tok = tok.strip()
            if len(tok) < 2 or tok in _STOP_WORDS:
                continue
            if re.match(r"^[a-zA-Z0-9]+$", tok) and len(tok) < 3:
                continue
            keywords[tok] = keywords.get(tok, 0) + 1

        # 只保留 top 200 关键词，避免无限增长
        if len(keywords) > 200:
            sorted_kw = sorted(keywords.items(), key=lambda x: x[1], reverse=True)
            keywords = dict(sorted_kw[:200])

        data["keywords"] = keywords
        data["conversation_count"] = data.get("conversation_count", 0) + 1

        # 记录最近话题（最多保留 20 条）
        last_topics = data.get("last_topics", [])
        topic_summary = user_text[:50].strip()
        if topic_summary:
            last_topics.insert(0, {
                "topic": topic_summary,
                "timestamp": datetime.now(CST).isoformat(),
            })
            data["last_topics"] = last_topics[:20]

        self.save(data)

    # ------------------------------------------------------------------
    # 深度更新：开机 / 会话触发（LLM 重生成 interests + persona）
    # ------------------------------------------------------------------

    def needs_deep_update(self) -> bool:
        """画像是否需要进行 LLM 深度分析。

        首次（无 persona_summary）或有新对话（conversation_count 增长）
        时返回 True，供开机自动分析判断。
        """
        data = self.load()
        if not data.get("persona_summary"):
            return True
        return data.get("conversation_count", 0) > data.get("last_analyzed_count", 0)

    def deep_update(self, llm_client=None, model: str = "") -> None:
        """用 LLM 重新生成 interests + persona_summary。"""
        data = self.load()

        # 读取最近 7 天对话日志
        conversations = self._load_recent_conversations(days=7)
        if not conversations and not data.get("keywords"):
            return  # 没有足够数据，跳过

        # 剔除过期的时效性字段，避免把旧事实烘进新画像
        for field in TIME_SENSITIVE_EXPIRY_SECONDS:
            if not _is_fresh(data, field):
                data.pop(field, None)

        # 衰减旧 interests（防止画像僵化）
        interests = data.get("interests", {})
        for k in list(interests.keys()):
            interests[k] = round(interests[k] * 0.92, 3)
            if interests[k] < 0.05:
                del interests[k]

        if llm_client and model:
            try:
                new_profile = self._llm_analyze(llm_client, model, conversations, data)
                if new_profile:
                    # 合并新 interests（取最大值，不直接覆盖）
                    for tag, weight in new_profile.get("interests", {}).items():
                        interests[tag] = max(interests.get(tag, 0), float(weight))
                    data["interests"] = interests
                    if new_profile.get("persona_summary"):
                        data["persona_summary"] = new_profile["persona_summary"]
                        data.setdefault("_timestamps", {})["persona_summary"] = datetime.now(CST).isoformat()
            except Exception as e:
                print(f"[profile] LLM 深度更新失败：{e}", flush=True)
        else:
            # 无 LLM 时，从关键词推断兴趣
            keywords = data.get("keywords", {})
            for tag in _INTEREST_TAGS:
                score = sum(keywords.get(kw, 0) for kw in tag.split())
                if score > 0:
                    interests[tag] = min(round(interests.get(tag, 0) + score * 0.01, 3), 1.0)
            data["interests"] = interests

        self.save(data)

    def _load_recent_conversations(self, days: int = 7) -> list[dict]:
        """从 conversation_log.jsonl 读取最近 N 天的对话。"""
        log_path = _paths.CONVERSATION_LOG_PATH
        if not log_path.exists():
            return []
        cutoff = datetime.now(CST) - timedelta(days=days)
        conversations = []
        try:
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts_str = entry.get("timestamp", "")
                        if ts_str:
                            ts = datetime.fromisoformat(ts_str)
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=CST)
                            if ts < cutoff:
                                continue
                        conversations.append(entry)
                    except Exception:
                        continue
        except Exception:
            pass
        return conversations[-200:]  # 最多 200 条

    def _llm_analyze(self, client, model: str, conversations: list, data: dict) -> dict | None:
        """调用 LLM 分析对话历史，返回新画像。"""
        from sjtu_agent.agent.runner import _is_anthropic_model

        # 构建对话摘要
        conv_text = ""
        for c in conversations[-50:]:  # 最多 50 条
            role = c.get("role", "")
            text = c.get("text", "")[:100]
            if role and text:
                conv_text += f"{role}: {text}\n"

        # top 关键词
        keywords = data.get("keywords", {})
        top_kw = sorted(keywords.items(), key=lambda x: x[1], reverse=True)[:20]
        kw_text = ", ".join(f"{k}({v})" for k, v in top_kw)

        now_str = datetime.now(CST).strftime("%Y年%m月%d日")
        prompt = f"""分析以下用户与 AI 助手的对话历史，输出用户画像 JSON。

今天是 {now_str}。对话历史可能包含已过期的具体日期/学期/状态断言（例如"下周考试""下周期末"早已过去，"小学期"可能已结束）。persona_summary 中不要写入这类会过期的时间性断言；确需提及时请用「截至某时」表述，并避免与当前日期冲突。

## 高频关键词（词频）
{kw_text}

## 最近对话（最多50条）
{conv_text or '（暂无对话记录）'}

## 可选兴趣标签
{', '.join(_INTEREST_TAGS)}

## 输出格式（仅输出 JSON，无其他内容）
{{
  "persona_summary": "100-150字的用户画像描述（学习阶段、专业方向、当前关注点）",
  "interests": {{
    "标签名": 权重(0.0-1.0)
  }}
}}

注意：interests 最多 8 个标签，权重根据关键词频次和对话内容判断。"""

        try:
            if _is_anthropic_model(model):
                resp = client.messages.create(
                    model=model,
                    max_tokens=512,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = resp.content[0].text
            else:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=512,
                )
                text = resp.choices[0].message.content

            # 提取 JSON
            import re as _re
            m = _re.search(r"\{.*\}", text, _re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception as e:
            print(f"[profile] LLM 分析失败：{e}", flush=True)
        return None

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def is_blocked(self, item) -> bool:
        """判断新闻是否被用户屏蔽。"""
        data = self.load()
        blocked = data.get("blocked_categories", [])
        if not blocked:
            return False
        text = f"{item.title} {item.category} {' '.join(item.tags)}"
        return any(cat in text for cat in blocked)

    def block_category(self, category: str) -> None:
        """添加屏蔽分类。"""
        data = self.load()
        blocked = data.get("blocked_categories", [])
        if category not in blocked:
            blocked.append(category)
            data["blocked_categories"] = blocked
            self.save(data)

    def unblock_category(self, category: str) -> None:
        """移除屏蔽分类。"""
        data = self.load()
        blocked = data.get("blocked_categories", [])
        data["blocked_categories"] = [c for c in blocked if c != category]
        self.save(data)

    def reset(self) -> None:
        """重置画像（保留 blocked_categories）。"""
        data = self.load()
        blocked = data.get("blocked_categories", [])
        new_data = self._default()
        new_data["blocked_categories"] = blocked
        self.save(new_data)


# ------------------------------------------------------------------
# 开机自动分析（issue #113 #4）：bot 首次会话时后台触发画像深度分析
# ------------------------------------------------------------------

_analysis_started = False
_analysis_lock = threading.Lock()


def ensure_profile_analyzed_async() -> None:
    """后台触发一次画像深度分析（LLM 重生成 interests + persona_summary）。

    进程内最多执行一次；无新数据或分析已启动时直接返回。非阻塞——分析在
    daemon 线程中运行，不延迟首次回复。供 bot 首次会话（make_session /
    feishu _init_messages）调用。
    """
    global _analysis_started
    with _analysis_lock:
        if _analysis_started:
            return
        _analysis_started = True

    profile = UserProfile()
    if not profile.needs_deep_update():
        return

    def _run() -> None:
        try:
            import agent
            cfg = agent.load_agent_config()
            client = agent._make_client(cfg) if cfg.get("api_key") else None
            profile.deep_update(llm_client=client, model=cfg.get("model", ""))
            # 标记已分析，避免下次启动重复跑
            data = profile.load()
            data["last_analyzed_count"] = data.get("conversation_count", 0)
            profile.save(data)
            print("[profile] 画像深度分析完成", flush=True)
        except Exception as e:
            print(f"[profile] 画像深度分析失败：{e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()


def log_conversation(user_text: str, agent_reply: str) -> None:
    """将一轮对话追加到 conversation_log.jsonl（供 deep_update 使用）。

    最多保留最近 1000 行（500 轮对话），旧数据自动截断。
    """
    log_path = _paths.CONVERSATION_LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(CST).isoformat(),
        "role": "user",
        "text": user_text[:500],
    }
    reply_entry = {
        "timestamp": datetime.now(CST).isoformat(),
        "role": "assistant",
        "text": agent_reply[:500],
    }
    try:
        lines = []
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").rstrip("\n").split("\n")
        lines.append(json.dumps(entry, ensure_ascii=False))
        lines.append(json.dumps(reply_entry, ensure_ascii=False))
        if len(lines) > 1000:
            lines = lines[-1000:]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass

    # 同时更新画像关键词
    try:
        profile = UserProfile()
        profile.record_conversation(user_text, agent_reply)
    except Exception:
        pass
