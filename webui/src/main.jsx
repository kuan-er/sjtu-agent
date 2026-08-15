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
  const [theme, setThemeState] = useState(() => localStorage.getItem('sjtu-agent-theme') || 'dark');
  const [accent, setAccentState] = useState(() => localStorage.getItem('sjtu-agent-accent') || '#3b82f6');
  const abortRef = useRef(null);
  const partialRef = useRef('');
  const messagesRef = useRef(null);
  const sessionRef = useRef(null);
  const fileInputRef = useRef(null);

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

  const send = async () => {
    const message = input.trim();
    if (!message || sending) return;
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
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  };

  const activeTitle = useMemo(() => sessions.find(s => s.id === sessionId)?.title || '开始新会话', [sessions, sessionId]);
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
              {messages.map((m, i) => (
                <React.Fragment key={i}>
                  {m.role === 'assistant' && m._streaming
                    ? <div className="message assistant"><div className="avatar">A</div><div className="bubble markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content) }} /></div>
                    : <Message message={m} />}
                </React.Fragment>
              ))}
              {stream.map((t, i) => <ToolCard key={i} event={t} />)}
            </>
          )}
        </div>
        {approval && <ApprovalBanner approval={approval} onDecision={decideApproval} />}
        {(stagedFiles.length > 0 || attachments.length > 0) && (
          <div className="attachment-strip">
            {attachments.map(a => <AttachmentChip key={a.id} attachment={a} onPreview={() => setPreviewAttachment(a)} />)}
            {stagedFiles.length > 0 && <span className="attachment-staged">已选 {stagedFiles.length} 个附件</span>}
          </div>
        )}
        <div className="composer-wrap">
          <div className="composer">
            <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }} onChange={e => { uploadFiles(Array.from(e.target.files || [])); e.target.value = ''; }} />
            <button className="attach-btn" title="上传附件" onClick={() => fileInputRef.current && fileInputRef.current.click()}>📎</button>
            <textarea rows="1" value={input} onChange={e => setInput(e.target.value)} onKeyDown={onKeyDown} placeholder="输入消息，Enter 发送，Shift+Enter 换行" />
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
