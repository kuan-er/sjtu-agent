import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';

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

function ToolCard({ event }) {
  const [open, setOpen] = useState(false);
  const { name, input } = event;
  return (
    <div className={open ? 'tool-card open' : 'tool-card'}>
      <div className="tool-head" onClick={() => setOpen(v => !v)}>
        <span className="tool-caret">▶</span>
        <span>{TOOL_LABELS[name] || name}</span>
      </div>
      <div className="tool-body">
        {'参数：' + JSON.stringify(input, null, 2) + '\n\n结果：等待中…'}
      </div>
    </div>
  );
}

function Message({ message, liveTools }) {
  const role = message.role === 'user' ? 'user' : 'assistant';
  return (
    <div className={'message ' + role}>
      <div className="avatar">{role === 'user' ? 'U' : 'A'}</div>
      <div className="bubble">{stripDateContext(message.content)}</div>
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
  const listRef = useRef(null);

  const loadSessions = useCallback(async () => {
    try {
      const data = await api('/api/sessions');
      setSessions(data.sessions || []);
    } catch (err) {
      console.error(err);
    }
  }, []);

  const loadSession = useCallback(async (id) => {
    if (!id) { setSessionId(null); setMessages([]); return; }
    try {
      const data = await api('/api/sessions/' + id + '/messages');
      setSessionId(id);
      setMessages(data.messages || []);
    } catch (err) {
      setMessages([]);
    }
  }, []);

  useEffect(() => {
    loadSessions();
    api('/api/config').then(data => setModel(data.model || '')).catch(() => {});
  }, [loadSessions]);

  const createSession = async () => {
    const session = await api('/api/sessions', { method: 'POST', body: JSON.stringify({ title: '新会话' }) });
    await loadSessions();
    await loadSession(session.id);
    return session;
  };

  const renameSession = async (id, title) => {
    await api('/api/sessions/' + id, { method: 'PATCH', body: JSON.stringify({ title }) });
    await loadSessions();
  };

  const deleteSession = async (id) => {
    await api('/api/sessions/' + id, { method: 'DELETE' });
    await loadSessions();
    if (sessionId === id) { setSessionId(null); setMessages([]); }
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

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, session_id: id }),
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
          if (event.error) throw new Error(event.error);
          if (event.token) {
            assistantText += event.token;
            hasToken = true;
            pushAssistant(assistantText);
          }
          if (event.tool_start) {
            setStream(prev => [...prev, { kind: 'tool_start', ...event.tool_start }]);
          }
        }
      }

      if (!hasToken && !assistantText) {
        setMessages(prev => [...prev, { role: 'assistant', content: '（已完成）' }]);
      }
      await loadSession(id);
      await loadSessions();
    } catch (err) {
      setMessages(prev => [...prev, { role: 'assistant', content: '❌ ' + (err.message || '请求失败') }]);
    } finally {
      setSending(false);
      if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  };

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const sortedMessages = messages.map(m => ({ ...m }));

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="brand">SJTU Agent</div>
          <button className="new-session" title="新建会话" onClick={createSession}>+</button>
        </div>
        <div className="session-list" ref={listRef}>
          {sessions.length === 0 && <div className="empty-sessions">还没有会话，点击 + 开始</div>}
          {sessions.map(s => (
            <div key={s.id} className={'session-item' + (s.id === sessionId ? ' active' : '')} onClick={() => loadSession(s.id)}>
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

      <main className="main">
        <header className="main-header">
          <div style={{ fontWeight: 600 }}>{sessions.find(s => s.id === sessionId)?.title || '开始新会话'}</div>
          <div className="model-tag">{model ? '模型：' + model : '加载中…'}</div>
        </header>
        <div className="messages">
          {sortedMessages.length === 0 && !sending ? (
            <div className="hero">
              <div>
                <h1>SJTU Agent</h1>
                <p>问课表、查 DDL、搜水源、推荐食堂——直接开始。</p>
              </div>
            </div>
          ) : sortedMessages.map((m, i) => (
            <React.Fragment key={i}>
              {m.role === 'assistant' && m._streaming ? null : <Message message={m} />}
              {m.role === 'assistant' && m._streaming && <div className="message assistant"><div className="avatar">A</div><div className="bubble">{stripDateContext(m.content)}</div></div>}
            </React.Fragment>
          ))}
          {stream.map((t, i) => t.kind === 'tool_start' ? <ToolCard key={i} event={t} /> : null)}
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
            <button className="send-btn" onClick={send} disabled={sending}>{sending ? '生成中…' : '发送'}</button>
          </div>
        </div>
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
