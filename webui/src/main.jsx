import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import MarkdownIt from 'markdown-it';
import { katex as katexPlugin } from '@mdit/plugin-katex';
import hljs from 'highlight.js';

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre class="hljs"><code>' + hljs.highlight(code, { language: lang }).value + '</code></pre>';
      } catch (_) {}
    }
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(code) + '</code></pre>';
  },
}).use(katexPlugin);

const ACCENTS = [
  { id: 'blue', color: '#3b82f6', label: '蓝' },
  { id: 'green', color: '#10b981', label: '绿' },
  { id: 'purple', color: '#8b5cf6', label: '紫' },
  { id: 'orange', color: '#f59e0b', label: '橙' },
];

const TOOL_LABELS = {
  get_ddls: '获取 DDL',
  get_all: '获取全部信息',
  get_next_lab: '获取实验安排',
  get_schedule: '获取课表',
  query_grades: '查询成绩',
  check_setup: '检查配置',
  download_assignments: '下载作业',
  search_campus: '搜索校园',
  web_search: '联网搜索',
  github_repo_search: '搜索 GitHub 仓库',
  browse_mysjtu: '浏览交大门户',
  list_reminders: '查看提醒',
  read_emails: '读取邮件',
  send_email: '发送邮件',
  setup_shuiyuan: '授权水源社区',
  read_shuiyuan_topic: '读取水源帖子',
  setup_course_community: '配置选课社区',
  setup_qq: '配置 QQ Bot',
  tool_install_parse_backend: '安装解析依赖',
};

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const data = await res.json();
      if (data && data.error) message = data.error;
    } catch (_) {}
    throw new Error(message || `HTTP ${res.status}`);
  }
  return res.json();
}

function stripDateContext(text) {
  return String(text || '').replace(/\n{0,2}## 当前时间[\s\S]*?(?=\n\n|$)/, '').trim();
}

function renderMarkdown(text) {
  return md.render(stripDateContext(text));
}

function formatDuration(ms) {
  if (!ms && ms !== 0) return '';
  if (ms < 1000) return ms + 'ms';
  return (ms / 1000).toFixed(1) + 's';
}

function tryParseJson(text) {
  try { return JSON.parse(text); } catch (_) { return null; }
}

function SpecialResult({ name, result }) {
  const data = tryParseJson(result);
  if (!data || typeof data !== 'object') return null;

  if (name === 'read_shuiyuan_topic' && data.title) {
    return (
      <div className="result-cards">
        <a className="result-card" href={data.url} target="_blank" rel="noreferrer">
          <strong>水源帖子：{data.title}</strong>
          <span>{data.posts_count || data.posts?.length || 0} 楼</span>
        </a>
      </div>
    );
  }

  if (name === 'search_campus') {
    const cards = [];
    for (const [site, items] of Object.entries(data)) {
      if (!Array.isArray(items)) continue;
      for (const item of items.slice(0, 5)) {
        if (item && (item.url || item.link)) {
          cards.push({
            title: item.title || item.name || '未命名结果',
            url: item.url || item.link,
            source: site,
            summary: item.summary || item.excerpt || item.content || '',
          });
        }
      }
    }
    if (cards.length) {
      return (
        <div className="result-cards">
          {cards.map((card, i) => (
            <a className="result-card" key={i} href={card.url} target="_blank" rel="noreferrer">
              <strong>{card.title}</strong>
              <span>{card.source}{card.summary ? ' · ' + card.summary.slice(0, 120) : ''}</span>
            </a>
          ))}
        </div>
      );
    }
  }
  return null;
}

const COMMAND_RESULT_MARKER = '__SJTU_COMMAND_RESULT__';
const NEWS_SOURCE_LABELS = { jwc: '教务处', shuiyuan: '水源社区', official: '交大新闻网', canvas: 'Canvas' };

function encodeCommandResult(result) {
  return COMMAND_RESULT_MARKER + JSON.stringify({
    view: result.view || 'markdown',
    text: result.text || '',
    data: result.data || {},
  });
}

function tryParseCommandResult(content) {
  if (typeof content !== 'string' || !content.startsWith(COMMAND_RESULT_MARKER)) return null;
  try {
    const payload = JSON.parse(content.slice(COMMAND_RESULT_MARKER.length));
    return payload && typeof payload === 'object' ? payload : null;
  } catch (_) {
    return null;
  }
}

function CommandText({ text }) {
  return text ? <div className="markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }} /> : null;
}

function ResultDetails({ text }) {
  if (!text) return null;
  return (
    <details className="command-details">
      <summary>查看完整文本</summary>
      <CommandText text={text} />
    </details>
  );
}

function DiningResult({ data, text, onCommand }) {
  if (data.mode === 'invalid_campus' || data.mode === 'error' || data.ok === false) return <CommandText text={text} />;
  const recs = data.recommendations || [];
  const canteens = data.canteens || [];
  const campusButtons = ['闵行', '徐汇', '张江'].filter(c => c !== data.campus);
  return (
    <div className="command-card-view">
      <div className="command-card-title">
        🍽️ {data.meal_type ? data.meal_type + '推荐' : '食堂拥挤度'} · {data.campus}校区
        <span className="command-card-actions">
          {campusButtons.map(c => (
            <button key={c} className="card-chip" onClick={() => onCommand('/eat ' + c)}>{c}</button>
          ))}
        </span>
      </div>
      {data.summary && <p className="command-card-desc">{data.summary}</p>}
      <div className="result-cards">
        {recs.map((r, i) => (
          <div key={i} className="result-card">
            <strong>{r.canteen_name} · {r.overall_label}（{r.overall_rate}%）</strong>
            <span>{r.reasons && r.reasons.join('；')}</span>
            {r.recommended_sub_areas && <span>推荐窗口：{r.recommended_sub_areas.slice(0, 3).join('、')}</span>}
          </div>
        ))}
        {canteens.map((c, i) => (
          <div key={'c' + i} className="result-card">
            <strong>{c.name} · {c.overall_label}（{c.overall_rate}%）</strong>
          </div>
        ))}
      </div>
      <ResultDetails text={text} />
    </div>
  );
}

function NewsResult({ data, text, onCommand }) {
  const items = data.items || [];
  if (items.length === 0) return <CommandText text={text} />;
  return (
    <div className="command-card-view">
      <div className="command-card-title">📰 校园新闻 · {items.length} 条</div>
      <div className="result-cards">
        {items.map((item) => {
          const blockKey = item.category || NEWS_SOURCE_LABELS[item.source] || item.source;
          return (
            <a key={item.id} className="result-card" href={item.url} target="_blank" rel="noreferrer">
              <strong>{item.title}</strong>
              <span>{NEWS_SOURCE_LABELS[item.source] || item.source}{item.category ? ' · ' + item.category : ''}{item.reason ? ' · ' + item.reason : ''}</span>
              <span>{item.summary}</span>
              <button className="card-chip" onClick={(e) => { e.preventDefault(); onCommand('/news_block ' + blockKey); }}>屏蔽该分类</button>
            </a>
          );
        })}
      </div>
      <ResultDetails text={text} />
    </div>
  );
}

function HomeworkResult({ data, text, onCommand }) {
  const items = data.assignments || [];
  if (!Array.isArray(data.assignments) || items.length === 0) return <CommandText text={text} />;
  const isPast = data.kind === 'past' || data.kind === 'all' || data.include_past;
  return (
    <div className="command-card-view">
      <div className="command-card-title">📝 作业列表 · {items.length} 项</div>
      <div className="result-cards">
        {items.map(h => (
          <div key={h.index} className="result-card">
            <strong>[{h.index}] {h.course} — {h.name}</strong>
            <span>截止：{h.due || '未知'}{h.days_left !== null && h.days_left !== undefined ? ` · ${h.days_left > 0 ? h.days_left + ' 天后' : h.days_left === 0 ? '今天截止' : '已截止'}` : ''}{h.submitted ? ' · 已提交' : ''}</span>
            <button className="card-chip" onClick={() => onCommand(isPast ? `/hw past do ${h.index}` : `/hw do ${h.index}`)}>分析</button>
          </div>
        ))}
      </div>
      <ResultDetails text={text} />
    </div>
  );
}

function TemplateResult({ view, data, text, onCommand }) {
  if (view === 'template_list') {
    if (!data.templates || data.templates.length === 0) return <CommandText text={text} />;
    return (
      <div className="command-card-view">
        <div className="command-card-title">📚 LaTeX 模板</div>
        <div className="result-cards">
          {(data.templates || []).map(t => (
            <div key={t.name} className="result-card">
              <strong>{t.name}</strong>
              <span>{t.description}</span>
              <button className="card-chip" onClick={() => onCommand('/template ' + t.name)}>套用</button>
            </div>
          ))}
        </div>
        <ResultDetails text={text} />
      </div>
    );
  }
  if (view === 'template_compile') {
    return (
      <div className="command-card-view">
        <div className="command-card-title">{data.ok ? '✅ LaTeX 编译成功' : '❌ LaTeX 编译失败'}</div>
        {data.ok && <p className="command-card-desc">PDF：{data.pdf}（{data.size_kb} KB）</p>}
        <ResultDetails text={text} />
      </div>
    );
  }
  if (view === 'template_clone' || view === 'template_apply' || view === 'template_push') {
    return (
      <div className="command-card-view">
        <div className="command-card-title">{data.ok ? '✅' : '❌'} {view === 'template_clone' ? '模板克隆' : view === 'template_push' ? '推送到 Overleaf' : '套用模板'}</div>
        {data.name && <p className="command-card-desc">{data.name}</p>}
        <ResultDetails text={text} />
      </div>
    );
  }
  return <ResultDetails text={text} />;
}

function NewsPreferenceResult({ data, text }) {
  if (data.ok === false) return <CommandText text={text} />;
  return (
    <div className="command-card-view">
      <div className="command-card-title">{data.ok ? '✅' : '⚠️'} 新闻偏好</div>
      {data.category && <p className="command-card-desc">已屏蔽：{data.category}</p>}
      <ResultDetails text={text} />
    </div>
  );
}

function CommandResultMessage({ result, onCommand }) {
  const { view, text, data } = result;
  if (view === 'dining') return <DiningResult data={data || {}} text={text} onCommand={onCommand} />;
  if (view === 'news') return <NewsResult data={data || {}} text={text} onCommand={onCommand} />;
  if (view === 'homework') return <HomeworkResult data={data || {}} text={text} onCommand={onCommand} />;
  if (view.startsWith('template_')) return <TemplateResult view={view} data={data || {}} text={text} onCommand={onCommand} />;
  if (view === 'news_preference') return <NewsPreferenceResult data={data || {}} text={text} />;
  return <CommandText text={text} />;
}

function ToolCard({ event }) {
  const [open, setOpen] = useState(false);
  const status = event.status || 'running';
  const label = TOOL_LABELS[event.name] || event.name;
  const duration = event.durationMs !== undefined ? formatDuration(event.durationMs) : '';
  return (
    <div className={'tool-card ' + status + (open ? ' open' : '')}>
      <div className="tool-head" onClick={() => setOpen(v => !v)}>
        <span className="tool-caret">▶</span>
        {status === 'running' ? <span className="tool-spinner" /> : <span className="tool-done">✓</span>}
        <span className="tool-name">{label}</span>
        {duration && <span className="tool-duration">{duration}</span>}
      </div>
      <div className="tool-body">
        <div className="tool-section">参数</div>
        <pre>{JSON.stringify(event.input, null, 2)}</pre>
        <div className="tool-section">结果</div>
        {status === 'done' && <SpecialResult name={event.name} result={event.result} />}
        <pre>{event.result || (status === 'cancelled' ? '已取消' : '等待中…')}</pre>
      </div>
    </div>
  );
}

function CommandCard({ run }) {
  return (
    <div className={'command-card' + (run.status === 'running' ? ' running' : '')}>
      <div className="command-card-head">
        {run.status === 'running' ? <span className="tool-spinner" /> : <span className="tool-done">✓</span>}
        <span className="tool-name">命令：{run.raw}</span>
      </div>
      <div className="command-card-body">{run.progress || '正在执行…'}</div>
    </div>
  );
}

function Message({ message }) {
  const role = message.role === 'user' ? 'user' : 'assistant';
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(stripDateContext(message.content));
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch (_) {}
  };
  return (
    <div className={'message ' + role}>
      <div className="avatar">{role === 'user' ? 'U' : 'A'}</div>
      <div className="bubble">
        {role === 'assistant'
          ? <>
              <button className="copy-btn" onClick={copy}>{copied ? '已复制' : '复制'}</button>
              <div className="markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }} />
            </>
          : stripDateContext(message.content)}
      </div>
    </div>
  );
}

function AttachmentChip({ attachment, onRemove, onPreview }) {
  const isImage = attachment.mime_type && attachment.mime_type.startsWith('image/');
  return (
    <span className="attachment-chip">
      {isImage && <img src={'/api/attachments/' + attachment.id} alt={attachment.filename} onClick={onPreview} />}
      <span className="attachment-name" onClick={onPreview}>{attachment.filename}</span>
      {onRemove && <button className="icon-btn danger" onClick={onRemove}>✕</button>}
    </span>
  );
}

function AttachmentPreview({ attachment, onClose }) {
  const url = '/api/attachments/' + attachment.id;
  const isImage = attachment.mime_type && attachment.mime_type.startsWith('image/');
  const isPdf = attachment.mime_type === 'application/pdf';
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal preview-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <strong>{attachment.filename}</strong>
          <div style={{ display: 'flex', gap: 8 }}>
            <a className="choice" href={url + '?download=1'} download>下载</a>
            <button className="icon-btn" onClick={onClose}>✕</button>
          </div>
        </div>
        {isImage && <img className="preview-image" src={url} alt={attachment.filename} />}
        {isPdf && <iframe className="preview-pdf" src={url} title={attachment.filename} />}
        {!isImage && !isPdf && <div className="preview-text">该文件类型暂不支持在线预览，请下载后查看。</div>}
      </div>
    </div>
  );
}

function ApprovalBanner({ approval, onDecision }) {
  if (!approval) return null;
  return (
    <div className="approval-banner">
      <div>
        <strong>工具需要审批：{TOOL_LABELS[approval.tool_name] || approval.tool_name}</strong>
        <p>{approval.risk_hint}</p>
        <pre>{JSON.stringify(approval.arguments, null, 2)}</pre>
      </div>
      <div className="approval-actions">
        <button className="send-btn" onClick={() => onDecision(approval.approval_id, 'approve')}>允许</button>
        <button className="send-btn stop" onClick={() => onDecision(approval.approval_id, 'reject')}>拒绝</button>
      </div>
    </div>
  );
}

function SettingsModal({ theme, setTheme, accent, setAccent, onClose }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <strong>外观设置</strong>
          <button className="icon-btn" onClick={onClose}>✕</button>
        </div>
        <div className="setting-row">
          <span>主题</span>
          <div className="choice-row">
            <button className={'choice' + (theme === 'dark' ? ' active' : '')} onClick={() => setTheme('dark')}>深色</button>
            <button className={'choice' + (theme === 'light' ? ' active' : '')} onClick={() => setTheme('light')}>浅色</button>
          </div>
        </div>
        <div className="setting-row">
          <span>强调色</span>
          <div className="choice-row">
            {ACCENTS.map(a => (
              <button key={a.id} className={'choice' + (accent === a.color ? ' active' : '')} onClick={() => setAccent(a.color)} title={a.label}>
                <i className="accent-dot" style={{ background: a.color }} />
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function App() {
  const [sessions, setSessions] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [model, setModel] = useState('');
  const [stream, setStream] = useState([]);
  const [attachments, setAttachments] = useState([]);
  const [stagedFiles, setStagedFiles] = useState([]);
  const [previewAttachment, setPreviewAttachment] = useState(null);
  const [approval, setApproval] = useState(null);
  const [search, setSearch] = useState('');
  const [showSettings, setShowSettings] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [commands, setCommands] = useState([]);
  const [cmdIndex, setCmdIndex] = useState(0);
  const [commandPanelOpen, setCommandPanelOpen] = useState(false);
  const [commandRun, setCommandRun] = useState(null);
  const [theme, setThemeState] = useState(() => localStorage.getItem('sjtu-agent-theme') || 'dark');
  const [accent, setAccentState] = useState(() => localStorage.getItem('sjtu-agent-accent') || '#3b82f6');
  const abortRef = useRef(null);
  const partialRef = useRef('');
  const messagesRef = useRef(null);
  const sessionRef = useRef(null);
  const fileInputRef = useRef(null);
  const textareaRef = useRef(null);
  const commandItemRefs = useRef({});

  sessionRef.current = sessionId;

  const setTheme = useCallback((next) => { setThemeState(next); localStorage.setItem('sjtu-agent-theme', next); }, []);
  const setAccent = useCallback((next) => { setAccentState(next); localStorage.setItem('sjtu-agent-accent', next); }, []);

  useEffect(() => {
    document.body.dataset.theme = theme;
    document.body.style.setProperty('--accent', accent);
    document.body.style.setProperty('--accent-hover', accent);
  }, [theme, accent]);

  const loadSessions = useCallback(async () => {
    try {
      const data = await api('/api/sessions');
      setSessions(data.sessions || []);
    } catch (err) { console.error(err); }
  }, []);

  const loadSession = useCallback(async (id) => {
    if (!id) { setSessionId(null); setMessages([]); setStream([]); setAttachments([]); setStagedFiles([]); return; }
    try {
      const [data, attData] = await Promise.all([
        api('/api/sessions/' + id + '/messages'),
        api('/api/attachments?session_id=' + encodeURIComponent(id)),
      ]);
      setSessionId(id);
      setMessages(data.messages || []);
      setStream([]);
      setAttachments(attData.attachments || []);
      setStagedFiles([]);
      setApproval(null);
    } catch (err) { setMessages([]); }
  }, []);

  useEffect(() => {
    loadSessions();
    api('/api/config').then(data => setModel(data.model || '')).catch(() => {});
    api('/api/commands').then(data => setCommands(data.commands || [])).catch(() => {});
  }, [loadSessions]);

  useEffect(() => {
    if (messagesRef.current) messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
  }, [messages, stream, approval]);

  const createSession = async () => {
    const session = await api('/api/sessions', { method: 'POST', body: JSON.stringify({ title: '新会话' }) });
    await loadSessions();
    await loadSession(session.id);
    setMobileNav(false);
    return session;
  };

  const renameSession = async (id, title) => {
    await api('/api/sessions/' + id, { method: 'PATCH', body: JSON.stringify({ title }) });
    await loadSessions();
  };

  const ensureSessionTitle = async (id, message, wasNew) => {
    const existing = sessions.find(s => s.id === id);
    const shouldRename = wasNew || (existing && existing.title === '新会话' && messages.length === 0);
    if (!shouldRename) return;
    const title = message.replace(/\s+/g, ' ').trim().slice(0, 24) || '新会话';
    if (title !== '新会话') {
      try { await renameSession(id, title); } catch (_) {}
    }
  };

  const deleteSession = async (id) => {
    await api('/api/sessions/' + id, { method: 'DELETE' });
    await loadSessions();
    if (sessionId === id) { setSessionId(null); setMessages([]); setStream([]); setAttachments([]); setStagedFiles([]); }
  };

  const clearSession = async (id) => {
    await api('/api/chat/clear', { method: 'POST', body: JSON.stringify({ session_id: id }) });
    await loadSession(id);
    await loadSessions();
  };

  const appendMessage = async (id, role, content) => {
    if (!id || !content || !content.trim()) return;
    try {
      await api('/api/sessions/' + id + '/messages', { method: 'POST', body: JSON.stringify({ role, content }) });
    } catch (_) {}
  };

  const uploadFiles = async (files) => {
    if (!sessionId || !files.length) return;
    const uploaded = [];
    for (const file of files) {
      if (file.size > 20 * 1024 * 1024) { alert(file.name + ' 超过 20MB'); continue; }
      const dataBase64 = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      const item = await api('/api/attachments', {
        method: 'POST',
        body: JSON.stringify({
          session_id: sessionId,
          filename: file.name,
          mime_type: file.type || 'application/octet-stream',
          data_base64: dataBase64,
        }),
      });
      uploaded.push(item);
    }
    setAttachments(prev => [...uploaded, ...prev]);
    setStagedFiles(prev => [...prev, ...uploaded]);
  };

  const removeStaged = async (attachment) => {
    try { await api('/api/attachments/' + attachment.id, { method: 'DELETE' }); } catch (_) {}
    setAttachments(prev => prev.filter(a => a.id !== attachment.id));
    setStagedFiles(prev => prev.filter(a => a.id !== attachment.id));
  };

  const focusComposer = () => {
    requestAnimationFrame(() => {
      const ta = textareaRef.current;
      if (ta) {
        ta.focus();
        ta.setSelectionRange(ta.value.length, ta.value.length);
      }
    });
  };

  const chooseCommandPrompt = async (text) => {
    const value = String(text || '').trim();
    if (!value) return;
    // 基础命令（无参数）直接用本地元数据，避免一次网络往返
    const exact = commands.find(c => c.name === value);
    if (exact) {
      setInput(exact.prompt || value);
    } else {
      try {
        const data = await api('/api/commands/resolve?text=' + encodeURIComponent(value));
        setInput(data.prompt || value);
      } catch (_) {
        setInput(value);
      }
    }
    setCmdIndex(0);
    setCommandPanelOpen(false);
    focusComposer();
  };

  const insertCommandText = (text) => {
    const value = String(text || '').trim();
    if (!value) return;
    const known = commands.find(c => c.name === value.split(/\s+/)[0].toLowerCase());
    setInput(known && known.exec !== true && known.prompt ? known.prompt : value);
    setCmdIndex(0);
    setCommandPanelOpen(false);
    focusComposer();
  };

  const parseSSE = (text) => {
    const events = [];
    for (const line of text.split('\n')) {
      if (!line.startsWith('data: ')) continue;
      const payload = line.slice(6).trim();
      if (!payload || payload === '[DONE]') continue;
      try { events.push(JSON.parse(payload)); } catch (_) {}
    }
    return events;
  };

  const commandNameOf = (text) => String(text || '').trim().split(/\s+/)[0].toLowerCase();
  const isExecutableCommand = (text) => {
    const name = commandNameOf(text);
    return name.startsWith('/') && commands.some(c => c.exec === true && c.name === name);
  };

  const sendCommand = async (commandText) => {
    if (!commandText || sending) return;
    setSending(true);
    setInput('');
    partialRef.current = '';

    let id = sessionId;
    let wasNew = false;
    if (!id) {
      try {
        const session = await createSession();
        id = session.id;
        wasNew = true;
        setSessionId(id);
      } catch (err) { setSending(false); return; }
    }

    setMessages(prev => [...prev, { role: 'user', content: commandText }]);
    ensureSessionTitle(id, commandText, wasNew);
    setStagedFiles([]);
    setStream([]);
    setApproval(null);
    setCommandRun({ raw: commandText, status: 'running', progress: '' });

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const res = await fetch('/api/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: commandText, session_id: id }),
        signal: controller.signal,
      });
      if (!res.ok) {
        let message = 'HTTP ' + res.status;
        try {
          const data = await res.json();
          if (data && data.error) message = data.error;
          if (data && data.prompt) setInput(data.prompt);
        } catch (_) {}
        throw new Error(message);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const event of parseSSE(lines.join('\n'))) {
          if (event.command_start) {
            setCommandRun(prev => prev ? { ...prev, name: event.command_start.name } : prev);
          }
          if (event.command_progress) {
            setCommandRun(prev => prev ? { ...prev, progress: event.command_progress.message } : prev);
          }
          if (event.command_result) {
            setMessages(prev => [...prev, { role: 'assistant', content: encodeCommandResult(event.command_result) }]);
            setCommandRun(null);
          }
          if (event.error) throw new Error(event.error);
        }
      }
      setCommandRun(null);
      await loadSession(id);
      await loadSessions();
    } catch (err) {
      setCommandRun(null);
      if (err.name === 'AbortError') {
        setMessages(prev => [...prev, { role: 'assistant', content: '（已停止）' }]);
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: '❌ ' + (err.message || '命令执行失败') }]);
      }
    } finally {
      abortRef.current = null;
      setSending(false);
    }
  };

  const send = async () => {
    const rawMessage = input.trim();
    if ((!rawMessage && stagedFiles.length === 0) || sending) return;
    if (rawMessage.startsWith('/') && stagedFiles.length === 0 && isExecutableCommand(rawMessage)) {
      await sendCommand(rawMessage);
      return;
    }
    let message = rawMessage || '请查看我上传的附件';
    if (rawMessage.startsWith('/')) {
      const name = commandNameOf(rawMessage);
      const known = commands.find(c => c.name === name);
      if (known && known.exec !== true) message = known.prompt || rawMessage;
    }
    setSending(true);
    setInput('');
    partialRef.current = '';

    let id = sessionId;
    let wasNew = false;
    if (!id) {
      try {
        const session = await createSession();
        id = session.id;
        wasNew = true;
        setSessionId(id);
      } catch (err) { setSending(false); return; }
    }

    const attachmentIds = stagedFiles.map(a => a.id);
    setMessages(prev => [...prev, { role: 'user', content: message }]);
    ensureSessionTitle(id, message, wasNew);
    setStagedFiles([]);
    setStream([]);
    setApproval(null);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, session_id: id, attachment_ids: attachmentIds }),
        signal: controller.signal,
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let assistantText = '';
      let hasToken = false;

      const pushAssistant = (next) => {
        if (sessionRef.current !== id) return;
        setMessages(prev => {
          const copy = prev.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === 'assistant' && last._streaming) copy[copy.length - 1] = { ...last, content: next };
          else copy.push({ role: 'assistant', content: next, _streaming: true });
          return copy;
        });
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const event of parseSSE(lines.join('\n'))) {
          if (event.cancelled) {
            await appendMessage(id, 'assistant', assistantText + '\n\n（已停止）');
            setStream(prev => prev.map(t => t.status === 'running' ? { ...t, status: 'cancelled' } : t));
            setMessages(prev => {
              const copy = prev.slice();
              const last = copy[copy.length - 1];
              if (last && last._streaming) copy[copy.length - 1] = { ...last, content: (last.content || '') + '\n\n（已停止）', _streaming: false };
              return copy;
            });
            return;
          }
          if (event.error) throw new Error(event.error);
          if (event.token) {
            assistantText += event.token;
            partialRef.current = assistantText;
            hasToken = true;
            pushAssistant(assistantText);
          }
          if (event.tool_start) {
            setStream(prev => [...prev, { kind: 'tool', ...event.tool_start, status: 'running', startedAt: Date.now() }]);
          }
          if (event.approval_required) setApproval(event.approval_required);
          if (event.tool_end) {
            setStream(prev => {
              const copy = prev.slice();
              for (let i = copy.length - 1; i >= 0; i--) {
                if (copy[i].kind === 'tool' && copy[i].name === event.tool_end.name && copy[i].status === 'running') {
                  copy[i] = { ...copy[i], status: 'done', result: event.tool_end.result, durationMs: Date.now() - copy[i].startedAt };
                  break;
                }
              }
              return copy;
            });
            setApproval(null);
          }
        }
      }

      if (!hasToken && !assistantText) setMessages(prev => [...prev, { role: 'assistant', content: '（已完成）' }]);
      await loadSession(id);
      await loadSessions();
    } catch (err) {
      if (err.name === 'AbortError') {
        if (partialRef.current) await appendMessage(id, 'assistant', partialRef.current + '\n\n（已停止）');
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: '❌ ' + (err.message || '请求失败') }]);
      }
    } finally {
      abortRef.current = null;
      setSending(false);
    }
  };

  const stop = async () => {
    const id = sessionId;
    if (abortRef.current) abortRef.current.abort();
    try { await api('/api/chat/cancel', { method: 'POST', body: JSON.stringify({ session_id: id || '' }) }); } catch (_) {}
    setStream(prev => prev.map(t => t.status === 'running' ? { ...t, status: 'cancelled' } : t));
    setCommandRun(null);
    setApproval(null);
    setMessages(prev => {
      const copy = prev.slice();
      const last = copy[copy.length - 1];
      if (last && last._streaming) copy[copy.length - 1] = { ...last, content: (last.content || '') + '\n\n（已停止）', _streaming: false };
      return copy;
    });
  };

  const decideApproval = async (approvalId, decision) => {
    try { await api('/api/approvals/' + approvalId, { method: 'POST', body: JSON.stringify({ decision }) }); } catch (_) {}
    setApproval(null);
  };

  const onKeyDown = (e) => {
    const panelOpen = commandPanelOpen && commandOpen && commandCandidates.length > 0;
    if (panelOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setCmdIndex(i => Math.min(i + 1, commandCandidates.length - 1));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setCmdIndex(i => Math.max(i - 1, 0));
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        const active = Math.min(cmdIndex, commandCandidates.length - 1);
        if (commandCandidates[active]) insertCommandText(commandCandidates[active].value);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setInput('');
        setCmdIndex(0);
        setCommandPanelOpen(false);
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  };

  const activeTitle = useMemo(() => sessions.find(s => s.id === sessionId)?.title || '开始新会话', [sessions, sessionId]);
  const commandQuery = useMemo(() => input.trimStart(), [input]);
  const commandOpen = commandQuery.startsWith('/');
  const commandCandidates = useMemo(() => {
    if (!commandOpen) return [];
    const q = commandQuery.toLowerCase();
    const qName = q.split(/\s+/)[0];
    const seen = new Set();
    const out = [];
    const push = (value, command) => {
      if (!value || seen.has(value)) return;
      seen.add(value);
      out.push({
        value,
        label: command.label,
        icon: command.icon,
        description: command.description,
        kind: value === command.name ? 'command' : 'example',
      });
    };
    for (const command of commands) {
      if (command.name.toLowerCase().startsWith(q)) push(command.name, command);
      // 输入了完整命令名 + 参数时，补全其示例变体（如 /hw d → /hw do 3）
      if (command.name.toLowerCase() === qName) {
        for (const example of command.examples || []) {
          if (example.toLowerCase().startsWith(q)) push(example, command);
        }
      }
    }
    return out.slice(0, 8);
  }, [commands, commandOpen, commandQuery]);
  const activeCmdIndex = commandCandidates.length ? Math.min(cmdIndex, commandCandidates.length - 1) : 0;
  useEffect(() => {
    const active = commandCandidates[activeCmdIndex];
    const el = active && commandItemRefs.current[active.value];
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
  }, [activeCmdIndex, commandCandidates]);
  const filteredSessions = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter(s => (s.title || '').toLowerCase().includes(q));
  }, [sessions, search]);

  return (
    <div className="app">
      <aside className={'sidebar' + (mobileNav ? ' open' : '')}>
        <div className="sidebar-header">
          <div className="brand">SJTU Agent</div>
          <button className="new-session" title="新建会话" onClick={createSession}>+</button>
        </div>
        <input className="session-search" placeholder="搜索会话…" value={search} onChange={e => setSearch(e.target.value)} />
        <div className="session-list">
          {filteredSessions.length === 0 && <div className="empty-sessions">{search ? '没有匹配的会话' : '还没有会话，点击 + 开始'}</div>}
          {filteredSessions.map(s => (
            <div key={s.id} className={'session-item' + (s.id === sessionId ? ' active' : '')} onClick={() => { loadSession(s.id); setMobileNav(false); }}>
              <span className="session-title">{s.title}</span>
              {s.id === sessionId && sending && <span className="session-running">●</span>}
              <span className="session-actions">
                <button className="icon-btn" title="重命名" onClick={(e) => { e.stopPropagation(); const title = prompt('会话名称', s.title); if (title) renameSession(s.id, title); }}>✎</button>
                <button className="icon-btn" title="清空消息" onClick={(e) => { e.stopPropagation(); if (confirm('清空该会话消息？')) clearSession(s.id); }}>⌫</button>
                <button className="icon-btn danger" title="删除" onClick={(e) => { e.stopPropagation(); if (confirm('删除该会话？')) deleteSession(s.id); }}>🗑</button>
              </span>
            </div>
          ))}
        </div>
        <div className="sidebar-footer">
          <a href="/legacy" target="_blank">旧版配置页</a>
          <a href="https://kuan-er.github.io/sjtu-agent/docs/" target="_blank" rel="noreferrer">文档</a>
        </div>
      </aside>
      {mobileNav && <div className="scrim" onClick={() => setMobileNav(false)} />}

      <main className="main">
        <header className="main-header">
          <button className="icon-btn menu-btn" onClick={() => setMobileNav(v => !v)}>☰</button>
          <div className="header-title">{activeTitle}</div>
          <div className="header-actions">
            <div className="model-tag">{model ? '模型：' + model : '加载中…'}</div>
            <button className="icon-btn" title="外观设置" onClick={() => setShowSettings(true)}>⚙</button>
          </div>
        </header>
        <div className="messages" ref={messagesRef}>
          {messages.length === 0 && stream.length === 0 && !sending ? (
            <div className="hero">
              <div>
                <h1>SJTU Agent</h1>
                <p>问课表、查 DDL、搜水源、推荐食堂——直接开始。</p>
              </div>
            </div>
          ) : (
            <>
              {messages.map((m, i) => {
                const commandPayload = m.role === 'assistant' ? tryParseCommandResult(m.content) : null;
                return (
                  <React.Fragment key={i}>
                    {m.role === 'assistant' && m._streaming
                      ? <div className="message assistant"><div className="avatar">A</div><div className="bubble markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content) }} /></div>
                      : commandPayload
                        ? <div className="message assistant"><div className="avatar">A</div><div className="bubble"><CommandResultMessage result={commandPayload} onCommand={insertCommandText} /></div></div>
                        : <Message message={m} />}
                  </React.Fragment>
                );
              })}
              {stream.map((t, i) => <ToolCard key={i} event={t} />)}
              {commandRun && <CommandCard run={commandRun} />}
            </>
          )}
        </div>
        {approval && <ApprovalBanner approval={approval} onDecision={decideApproval} />}
        {stagedFiles.length > 0 && (
          <div className="attachment-strip">
            <span className="attachment-staged">待发送：</span>
            {stagedFiles.map(a => (
              <AttachmentChip key={a.id} attachment={a} onPreview={() => setPreviewAttachment(a)} onRemove={() => removeStaged(a)} />
            ))}
          </div>
        )}
        {attachments.length > 0 && (
          <div className="attachment-strip compact">
            <span className="attachment-staged">本会话附件：</span>
            {attachments.slice(-6).map(a => (
              <AttachmentChip key={a.id} attachment={a} onPreview={() => setPreviewAttachment(a)} />
            ))}
          </div>
        )}
        <div className="composer-wrap">
          <div className="quick-chips">
            {commands.filter(c => c.chip !== false).map(c => (
              <button key={c.name} className="quick-chip" title={c.description} onClick={() => chooseCommandPrompt(c.name)}>
                <span className="quick-chip-icon">{c.icon}</span>{c.label}
              </button>
            ))}
            <button className="quick-chip" title="外观与模型设置" onClick={() => setShowSettings(true)}>
              <span className="quick-chip-icon">⚙️</span>配置
            </button>
          </div>
          <div className="composer">
            {commandPanelOpen && commandOpen && commandCandidates.length > 0 && (
              <div className="command-panel" role="listbox" aria-label="命令补全">
                {commandCandidates.map((candidate, i) => (
                  <button
                    key={candidate.value}
                    ref={el => { if (el) commandItemRefs.current[candidate.value] = el; }}
                    role="option"
                    aria-selected={i === activeCmdIndex}
                    className={'command-item' + (i === activeCmdIndex ? ' active' : '')}
                    onMouseDown={e => e.preventDefault()}
                    onMouseEnter={() => setCmdIndex(i)}
                    onClick={() => insertCommandText(candidate.value)}
                  >
                    <span className="command-item-head">
                      <span className="command-icon">{candidate.icon}</span>
                      <span className="command-name">{candidate.value}</span>
                      <span className="command-label">{candidate.label}</span>
                    </span>
                    <span className="command-desc">{candidate.description}</span>
                  </button>
                ))}
                <div className="command-hint">↑↓ 选择 · Enter 填入 · 再按 Enter 执行 · Esc 关闭</div>
              </div>
            )}
            <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }} onChange={e => { uploadFiles(Array.from(e.target.files || [])); e.target.value = ''; }} />
            <button className="attach-btn" title="上传附件" onClick={() => fileInputRef.current && fileInputRef.current.click()}>📎</button>
            <textarea ref={textareaRef} rows="1" value={input} onChange={e => { const next = e.target.value; setInput(next); setCmdIndex(0); setCommandPanelOpen(next.trimStart().startsWith('/')); }} onKeyDown={onKeyDown} placeholder="输入消息，/ 唤起命令，Enter 发送，Shift+Enter 换行" />
            {sending ? <button className="send-btn stop" onClick={stop}>停止</button> : <button className="send-btn" onClick={send}>发送</button>}
          </div>
        </div>
      </main>
      {previewAttachment && <AttachmentPreview attachment={previewAttachment} onClose={() => setPreviewAttachment(null)} />}
      {showSettings && <SettingsModal theme={theme} setTheme={setTheme} accent={accent} setAccent={setAccent} onClose={() => setShowSettings(false)} />}
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
