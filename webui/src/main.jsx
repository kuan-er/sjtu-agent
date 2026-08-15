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

function ToolCard({ event, onToggle }) {
  const [open, setOpen] = useState(false);
  const status = event.status || 'running';
  const label = TOOL_LABELS[event.name] || event.name;
  const duration = event.durationMs !== undefined ? formatDuration(event.durationMs) : '';
  return (
    <div className={'tool-card ' + status + (open ? ' open' : '')}>
      <div className="tool-head" onClick={() => { setOpen(v => !v); if (onToggle) onToggle(); }}>
        <span className="tool-caret">▶</span>
        {status === 'running' ? <span className="tool-spinner" /> : <span className="tool-done">✓</span>}
        <span className="tool-name">{label}</span>
        {duration && <span className="tool-duration">{duration}</span>}
      </div>
      <div className="tool-body">
        <div className="tool-section">参数</div>
        <pre>{JSON.stringify(event.input, null, 2)}</pre>
        <div className="tool-section">结果</div>
        <pre>{event.result || (status === 'cancelled' ? '已取消' : '等待中…')}</pre>
      </div>
    </div>
  );
}

function Message({ message }) {
  const role = message.role === 'user' ? 'user' : 'assistant';
  return (
    <div className={'message ' + role}>
      <div className="avatar">{role === 'user' ? 'U' : 'A'}</div>
      <div className="bubble">
        {role === 'assistant'
          ? <div className="markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }} />
          : stripDateContext(message.content)}
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
              <button key={a.id}
                className={'choice' + (accent === a.color ? ' active' : '')}
                onClick={() => setAccent(a.color)}
                title={a.label}
              >
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
  const [showSettings, setShowSettings] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [theme, setThemeState] = useState(() => localStorage.getItem('sjtu-agent-theme') || 'dark');
  const [accent, setAccentState] = useState(() => localStorage.getItem('sjtu-agent-accent') || '#3b82f6');
  const abortRef = useRef(null);
  const messagesRef = useRef(null);

  const setTheme = useCallback((next) => {
    setThemeState(next);
    localStorage.setItem('sjtu-agent-theme', next);
  }, []);

  const setAccent = useCallback((next) => {
    setAccentState(next);
    localStorage.setItem('sjtu-agent-accent', next);
  }, []);

  useEffect(() => {
    document.body.dataset.theme = theme;
    document.body.style.setProperty('--accent', accent);
    document.body.style.setProperty('--accent-hover', accent);
  }, [theme, accent]);

  const loadSessions = useCallback(async () => {
    try {
      const data = await api('/api/sessions');
      setSessions(data.sessions || []);
    } catch (err) {
      console.error(err);
    }
  }, []);

  const loadSession = useCallback(async (id) => {
    if (!id) { setSessionId(null); setMessages([]); setStream([]); return; }
    try {
      const data = await api('/api/sessions/' + id + '/messages');
      setSessionId(id);
      setMessages(data.messages || []);
      setStream([]);
    } catch (err) {
      setMessages([]);
    }
  }, []);

  useEffect(() => {
    loadSessions();
    api('/api/config').then(data => setModel(data.model || '')).catch(() => {});
  }, [loadSessions]);

  useEffect(() => {
    if (messagesRef.current) messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
  }, [messages, stream]);

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

  const deleteSession = async (id) => {
    await api('/api/sessions/' + id, { method: 'DELETE' });
    await loadSessions();
    if (sessionId === id) { setSessionId(null); setMessages([]); setStream([]); }
  };

  const clearSession = async (id) => {
    await api('/api/chat/clear', { method: 'POST', body: JSON.stringify({ session_id: id }) });
    await loadSession(id);
    await loadSessions();
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

    let id = sessionId;
    if (!id) {
      try {
        const session = await createSession();
        id = session.id;
        setSessionId(id);
      } catch (err) {
        setSending(false);
        return;
      }
    }

    setMessages(prev => [...prev, { role: 'user', content: message }]);
    setStream([]);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, session_id: id }),
        signal: controller.signal,
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let assistantText = '';
      let hasToken = false;

      const pushAssistant = (next) => {
        setMessages(prev => {
          const copy = prev.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === 'assistant' && last._streaming) {
            copy[copy.length - 1] = { ...last, content: next };
          } else {
            copy.push({ role: 'assistant', content: next, _streaming: true });
          }
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
            hasToken = true;
            pushAssistant(assistantText);
          }
          if (event.tool_start) {
            setStream(prev => [...prev, { kind: 'tool', ...event.tool_start, status: 'running', startedAt: Date.now() }]);
          }
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
          }
        }
      }

      if (!hasToken && !assistantText) {
        setMessages(prev => [...prev, { role: 'assistant', content: '（已完成）' }]);
      }
      await loadSession(id);
      await loadSessions();
    } catch (err) {
      if (err.name !== 'AbortError') {
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
    if (id) {
      try { await api('/api/chat/cancel', { method: 'POST', body: JSON.stringify({ session_id: id }) }); } catch (_) {}
    } else {
      try { await api('/api/chat/cancel', { method: 'POST', body: JSON.stringify({}) }); } catch (_) {}
    }
    setStream(prev => prev.map(t => t.status === 'running' ? { ...t, status: 'cancelled' } : t));
    setMessages(prev => {
      const copy = prev.slice();
      const last = copy[copy.length - 1];
      if (last && last._streaming) copy[copy.length - 1] = { ...last, content: (last.content || '') + '\n\n（已停止）', _streaming: false };
      return copy;
    });
  };

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const activeTitle = useMemo(() => sessions.find(s => s.id === sessionId)?.title || '开始新会话', [sessions, sessionId]);

  return (
    <div className="app">
      <aside className={'sidebar' + (mobileNav ? ' open' : '')}>
        <div className="sidebar-header">
          <div className="brand">SJTU Agent</div>
          <button className="new-session" title="新建会话" onClick={createSession}>+</button>
        </div>
        <div className="session-list">
          {sessions.length === 0 && <div className="empty-sessions">还没有会话，点击 + 开始</div>}
          {sessions.map(s => (
            <div key={s.id} className={'session-item' + (s.id === sessionId ? ' active' : '')} onClick={() => { loadSession(s.id); setMobileNav(false); }}>
              <span className="session-title">{s.title}</span>
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
        <div className="composer-wrap">
          <div className="composer">
            <textarea
              rows="1"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="输入消息，Enter 发送，Shift+Enter 换行"
            />
            {sending
              ? <button className="send-btn stop" onClick={stop}>停止</button>
              : <button className="send-btn" onClick={send}>发送</button>}
          </div>
        </div>
      </main>
      {showSettings && <SettingsModal theme={theme} setTheme={setTheme} accent={accent} setAccent={setAccent} onClose={() => setShowSettings(false)} />}
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
