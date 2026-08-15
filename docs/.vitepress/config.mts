import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'SJTU Agent 文档',
  description: '上海交通大学校园助手 SJTU Agent 的安装、部署与排错文档',
  lang: 'zh-CN',
  base: '/sjtu-agent/docs/',
  cleanUrls: true,
  lastUpdated: true,
  srcExclude: ['SECURITY_AUDIT.md'],
  head: [
    ['link', { rel: 'icon', href: '/sjtu-agent/docs/favicon.svg' }],
  ],
  themeConfig: {
    nav: [
      { text: '项目展示页', link: 'https://kuan-er.github.io/sjtu-agent' },
      { text: 'GitHub', link: 'https://github.com/kuan-er/sjtu-agent' },
    ],
    sidebar: [
      {
        text: '开始',
        items: [
          { text: '文档首页', link: '/' },
          { text: '服务器部署', link: '/SERVER_DEPLOYMENT' },
          { text: '排错手册', link: '/TROUBLESHOOTING' },
          { text: '飞书 Bot 排错', link: '/feishu-bot-troubleshooting' },
        ],
      },
      {
        text: '设计文档',
        items: [
          { text: '安装优化设计', link: '/DEPLOYMENT' },
          { text: 'Agent 架构', link: '/AGENT_ARCHITECTURE' },
          { text: 'MCP / Skills 更新', link: '/MCP_SKILL_UPDATE' },
          { text: '代码修改后重载', link: '/reload-after-code-changes' },
        ],
      },
    ],
    outline: { level: [2, 3] },
    search: { provider: 'local' },
    lastUpdatedText: '最后更新',
  },
})
