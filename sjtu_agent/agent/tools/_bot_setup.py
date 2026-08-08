"""Bot setup guides — returned on demand via get_bot_setup_guide.

4 个平台的接入配置步骤原本写死在 system prompt（~60 行，只在用户问「怎么接入 XX」
时需要）。移出到此处，Agent 需要时按平台读取，前缀不再背负这 12% 的 token。
"""

_BOT_SETUP_GUIDES = {
    "telegram": """## Telegram Bot 配置

用户说「接入Telegram」「配置Telegram」「怎么把你接入Telegram」「Telegram bot 怎么用」时：

1. 如果用户还没有 Bot Token：先引导去 Telegram 找 @BotFather，发 /newbot，按提示创建，拿到 Token
2. 用户提供 Token 后：调用 setup_telegram(telegram_token=...) 保存配置并验证 Token 有效性
3. 配置成功后告知用户：
   - 运行 `sjtu-agent telegram-bot` 启动 Bot（长轮询，适合本地/服务器常驻）
   - 在 Telegram 中给 Bot 发 /id，获取自己的 user_id
   - 如果想限制 Bot 只响应自己，再次调用 setup_telegram 补填 allowed_ids
4. Bot 功能与终端版本完全相同：可以查 DDL、看课表、查成绩、搜索校园内容等""",

    "wechat": """## 微信 Bot 配置（ilink 协议）

用户说「接入微信」「配置微信」「微信 bot」「把你接入微信」「微信推送」时：

1. 调用 setup_wechat()，**这会在终端直接打印二维码并等待扫码**，整个过程在终端完成，无需用户手动操作
2. 扫码成功后 bot_token 自动保存到 config.json，告知用户：
   - 在微信里找到你刚才登录的 AI Bot（搜索"AI小助手"）
   - 给 Bot 发一条消息（如「你好」），系统自动记录 context_token
   - 运行 `python3 wechat_bot.py` 启动 Bot 后台服务（或 `sjtu-agent wechat-bot`）
3. Bot 功能与终端版本完全相同：查 DDL、看课表、查成绩、搜索校园内容、接收日报推送等""",

    "feishu": """## 飞书 Bot 配置

用户说「接入飞书」「配置飞书」「飞书 bot」「把你接入飞书」「飞书推送」「飞书」时：

1. 引导用户在 https://open.feishu.cn/app 创建企业自建应用（无需企业资质，个人即可创建）
2. 依次在应用设置中完成：开启 Bot 能力 → 添加 im:message 权限 → 事件订阅 im.message.receive_v1 并选择 WebSocket 模式（长连接，无需公网地址）→ 发布应用
3. 从「凭证与基础信息」页面获取 App ID 和 App Secret
4. 调用 setup_feishu(feishu_app_id=..., feishu_app_secret=...) 保存凭据并验证
5. 配置成功后告知用户：
   - 运行 `sjtu-agent feishu-bot` 启动 Bot（WebSocket 长连接模式，无需公网 IP）
   - 在飞书中搜索创建的应用名，进入机器人对话窗口，直接发消息即可
   - 需要后台常驻时运行 `sjtu-agent install-daemons` 安装守护进程
6. Bot 功能与终端版本完全相同：查 DDL、看课表、查成绩、搜索校园内容、接收日报推送等""",

    "qq": """## QQ Bot 配置

用户说「接入QQ」「配置QQ bot」「QQ机器人」时：

1. 引导用户先登录 https://q.qq.com/ ，进入机器人平台（OpenClaw）
2. 指引用户「选择机器人」→「创建机器人」，然后获取 app_id（AppID）和 app_secret（AppSecret）
3. 收集 app_id 和 app_secret 后调用 setup_qq 保存并验证
4. 配置成功后告知用户：
   - 让用户先从 QQ 给 Bot 发送一条消息，获取「QQ 用户标识」
   - 让用户把该用户标识回填，用于加入白名单
5. 如需限制可用用户，按白名单流程引导：
   - 第一次先不填 qq_allowed_user_ids（留空=允许所有人），先跑通收消息链路
   - 让目标用户给 Bot 发一条消息，记录机器人提示或日志里的「QQ 用户标识」
   - 再次调用 setup_qq 补填 qq_allowed_user_ids（可传多个）
   - 明确提醒：qq_allowed_user_ids 填的是 QQ 用户标识（openid/id），不是 QQ 号
6. QQ 用户管理：
   - 用户说「增加QQ用户」「添加QQ白名单用户」→ 调用 qq_add_user。若用户还没提供用户标识，先提示该账号给 Bot 发消息，拿到「QQ 用户标识」后回填
   - 用户说「QQ用户列表」「查看QQ白名单」→ 调用 qq_list_users
   - 用户说「删除QQ用户」「移除QQ白名单用户」→ 调用 qq_remove_user""",
}

TOOLS_ENTRIES = [
    {
        "type": "function",
        "function": {
            "name": "get_bot_setup_guide",
            "description": (
                "获取指定平台（telegram/wechat/feishu/qq）的 Bot 接入配置步骤。"
                "用户说「接入/配置 XX Bot」「怎么接入 XX」时调用，按返回步骤引导用户，不要凭记忆编造配置流程。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "enum": ["telegram", "wechat", "feishu", "qq"],
                        "description": "平台名：telegram / wechat / feishu / qq",
                    },
                },
                "required": ["platform"],
            },
        },
    },
]


def tool_get_bot_setup_guide(platform: str) -> str:
    """返回指定平台的 Bot 接入配置步骤。"""
    guide = _BOT_SETUP_GUIDES.get((platform or "").lower())
    if not guide:
        return "不支持的平台：{}。可选：telegram / wechat / feishu / qq。".format(platform)
    return guide
