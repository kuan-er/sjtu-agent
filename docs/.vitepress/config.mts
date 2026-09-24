import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'SJTU Agent 文档',
  description: '上海交通大学校园助手 SJTU Agent 的安装、部署与排错文档',
  lang: 'zh-CN',
  base: '/sjtu-agent/docs/',
  cleanUrls: true,
  lastUpdated: true,
  srcExclude: [
    'SECURITY_AUDIT.md',
    // 原始调研记录：面向维护者，体量大且结论会随价格/版本变动，
    // 留在仓库里（从下面那篇的正文链到 GitHub），不上文档站，避免站点越堆越乱。
    'research/harness-context-practice-2026H2.md',
    'research/agent-token-survey-2026H2.md',
  ],
  head: [
    ['link', { rel: 'icon', href: '/sjtu-agent/docs/favicon.svg' }],
  ],
  themeConfig: {
    nav: [
      { text: '新手指南', link: '/guide/' },
      { text: '项目展示页', link: 'https://kuan-er.github.io/sjtu-agent' },
      { text: 'GitHub', link: 'https://github.com/kuan-er/sjtu-agent' },
    ],
    sidebar: [
      {
        text: '新手指南',
        items: [
          { text: '先看这里（总览）', link: '/guide/' },
          { text: 'AI 使用基础', link: '/guide/ai-basics' },
          { text: '从零安装', link: '/guide/install' },
          { text: '日常话术库', link: '/guide/cookbook' },
        ],
      },
      {
        text: '开始',
        items: [
          { text: '文档首页', link: '/' },
          { text: 'Web GUI', link: '/WEB_GUI_REDESIGN' },
          { text: 'Textual TUI', link: '/TUI' },
          { text: '服务器部署', link: '/SERVER_DEPLOYMENT' },
          { text: '排错手册', link: '/TROUBLESHOOTING' },
          { text: '飞书 Bot 排错', link: '/feishu-bot-troubleshooting' },
          { text: '代码修改后重载', link: '/reload-after-code-changes' },
        ],
      },
      {
        text: '调研与参考',
        collapsed: true,
        items: [
          { text: 'Agent token 量级与预算建议', link: '/research/agent-token-budget-2026H2' },
          { text: '各模型窗口与价格对照', link: '/research/ai-agent-token-magnitudes-h2-2026' },
        ],
      },
      {
        text: '历史设计归档',
        items: [
          { text: '安装优化设计', link: '/DEPLOYMENT' },
          { text: 'Agent 架构', link: '/AGENT_ARCHITECTURE' },
        ],
      },
    ],
    outline: { level: [2, 3] },
    search: { provider: 'local' },
    lastUpdatedText: '最后更新',
  },
})
