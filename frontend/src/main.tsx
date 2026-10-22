import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

// 与后端返回字段对齐的 UI 数据类型；可选字段用于兼容文档列表和检索命中两种展示形态。
type Document = { id: string; title: string; category: string; chunks?: number; source?: string; doc_type?: string; preview?: string; created_at?: string };
type Citation = { document_id: string; chunk_id?: string; title: string; content: string; score: number; source: string; vector_score?: number; keyword_score?: number };
type Trace = { name: string; status: string; detail: string; provider: string };
type Message = { role: 'user' | 'assistant'; content: string; citations?: Citation[]; trace?: Trace[]; created_at?: string };
type Conversation = { id: string; created_at: string; messages: Message[] };
type Health = { status: string; vector_ready: boolean; llm_configured: boolean };

// 使用相对地址：开发环境由 Vite 代理到 8000，部署时也可以由网关转发 /api。
const API = '/api';
// 空会话首页的快捷问题，点击后直接复用与输入框相同的问答流程。
const suggestions = ['差旅报销需要哪些材料？', '请假需要提前多久申请？', 'IT 账号如何申请？', '5000 元费用由谁审批？'];
const ACCEPT = '.txt,.md,.pdf,.docx';

/** 统一处理 JSON API，保证非 2xx 响应也能给用户可读的错误。 */
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, options);
  } catch {
    // fetch 只有在网络不通、代理失败或后端未启动时才会直接抛异常。
    throw new Error('无法连接后端服务，请先运行 start-all.bat 或 start-backend.bat');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.message || `请求失败（${response.status}）`);
  return data as T;
}

function App() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState('');
  const [search, setSearch] = useState('');
  const [results, setResults] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [conversationId, setConversationId] = useState<string>();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [feedback, setFeedback] = useState<number | null>(null);
  const [notice, setNotice] = useState('');
  const [health, setHealth] = useState<Health | null>(null);
  // 工单智能运营助手的状态：报告、查询日期和加载状态彼此独立于知识库问答。
  const [ticketDate, setTicketDate] = useState('2026-10-15');
  const [ticketReport, setTicketReport] = useState<any | null>(null);
  const [ticketLoading, setTicketLoading] = useState(false);

  // 页面首次打开时加载文档清单、会话历史与健康状态。
  const loadDocs = async () => {
    try {
      const data = await request<{ items: Document[] }>(`${API}/documents`);
      setDocuments(data.items || []);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '无法加载知识文档');
    }
  };
  const loadConversations = async () => {
    try {
      const data = await request<{ items: Conversation[] }>(`${API}/conversations`);
      setConversations(data.items || []);
    } catch { /* 会话列表加载失败不阻塞主流程 */ }
  };
  useEffect(() => {
    loadDocs();
    loadConversations();
    request<Health>(`${API}/health`).then(setHealth).catch(error => {
      setHealth(null);
      setNotice(error instanceof Error ? error.message : '后端服务不可用');
    });
  }, []);

  const ask = async (text = question) => {
    if (!text.trim() || loading) return;
    setQuestion(''); setLoading(true); setNotice(''); setFeedback(null);
    setMessages(prev => [...prev, { role: 'user', content: text }]);
    try {
      const data = await request<any>(`${API}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, conversation_id: conversationId }),
      });
      setConversationId(data.conversation_id);
      setMessages(prev => [...prev, { role: 'assistant', content: data.answer, citations: data.citations, trace: data.trace }]);
      loadConversations();
    } catch (error) {
      setMessages(prev => [...prev, { role: 'assistant', content: error instanceof Error ? error.message : '问答请求失败，请稍后重试。' }]);
    } finally { setLoading(false); }
  };

  const doSearch = async (value = search) => {
    setSearch(value);
    if (!value.trim()) { setResults([]); return; }
    try {
      const data = await request<{ items: any[] }>(`${API}/search?q=${encodeURIComponent(value)}`);
      setResults(data.items || []);
    } catch (error) { setNotice(error instanceof Error ? error.message : '搜索失败'); }
  };

  const uploadDocument = async (file: File) => {
    if (!/\.(txt|md|pdf|docx)$/i.test(file.name)) { setNotice('仅支持 .txt / .md / .pdf / .docx 文件'); return; }
    if (file.size > 10 * 1024 * 1024) { setNotice('单个文档不能超过 10 MB'); return; }
    setUploading(true); setNotice('');
    try {
      const form = new FormData(); form.append('file', file);
      const data = await request<{ message: string }>(`${API}/documents/upload`, { method: 'POST', body: form });
      setNotice(data.message); await loadDocs();
    } catch (error) { setNotice(error instanceof Error ? error.message : '上传失败'); }
    finally { setUploading(false); }
  };

  const deleteDocument = async (id: string) => {
    try {
      await request(`${API}/documents/${id}`, { method: 'DELETE' });
      setNotice('文档已删除'); await loadDocs();
    } catch (error) { setNotice(error instanceof Error ? error.message : '删除失败'); }
  };

  // 点击历史会话：把消息整体载入主面板并续接 conversation_id，可继续追问。
  const openConversation = (conv: Conversation) => {
    setConversationId(conv.id);
    setMessages(conv.messages.map(m => ({ ...m, citations: (m as any).citations })));
    setFeedback(null);
    setSearch(''); setResults([]);
  };

  // 开启新会话：清空当前上下文，下一条消息将创建新的 conversation。
  const newConversation = () => {
    setConversationId(undefined); setMessages([]); setFeedback(null); setNotice('');
  };

  const generateTicketReport = async () => {
    setTicketLoading(true); setNotice('');
    try {
      const data = await request<any>(`${API}/ticket-report`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: '请总结昨日工单给领导，并列出待跟进事项', date: ticketDate }),
      });
      setTicketReport(data);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '工单报告生成失败');
    } finally { setTicketLoading(false); }
  };

  const sendFeedback = async (rating: 'up' | 'down') => {
    setFeedback(rating === 'up' ? 1 : 0);
    try {
      await request(`${API}/feedback`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: conversationId, rating }),
      });
    } catch (error) { setNotice(error instanceof Error ? error.message : '反馈提交失败'); }
  };
  // 右侧引用和下方 trace 始终展示最近一条助手消息的信息，而非某一轮固定索引。
  const lastAssistant = [...messages].reverse().find(m => m.role === 'assistant');

  // 能力状态徽标：向量检索与 LLM 配置情况来自 /api/health。
  const modeLabel = health
    ? (health.llm_configured ? 'LLM + 混合检索' : health.vector_ready ? '向量+关键词检索 · 模板回答' : '关键词检索 · 模板回答')
    : '连接中…';

  return <div className="app"><header><div className="brand"><div className="logo">知</div><div><b>AI Knowledge Hub</b><span>银行内部知识工作台 · Learning Demo</span></div></div><div className="status"><i /> {modeLabel}</div></header>
    <main><section className="hero"><div><p className="eyebrow">ENTERPRISE · KNOWLEDGE ASSISTANT</p><h1>让制度知识，<em>触手可及</em></h1><p className="sub">上传 PDF / DOCX / Markdown，向量+关键词混合检索，LLM 依据引用生成可解释回答。</p></div><div className="hero-art">✦<span>RAG</span></div></section>
      <section className="panel ticket-panel">
        <div className="panel-head"><div><p className="eyebrow">AGENT OPERATIONS DEMO</p><h2>昨日工单智能简报</h2></div><div className="ticket-controls"><input type="date" value={ticketDate} onChange={e => setTicketDate(e.target.value)} /><button className="primary-btn" onClick={generateTicketReport} disabled={ticketLoading}>{ticketLoading ? '生成中…' : '生成简报 ↗'}</button></div></div>
        <p className="ticket-tip">演示链路：查询工单工具 → SQL 统计 → RAG 检索处理规范 → LLM / 模板生成 → 审计日志</p>
        {ticketReport && <div className="ticket-result"><div className="ticket-metrics"><div><strong>{ticketReport.summary.total}</strong><span>工单总数</span></div><div><strong>{ticketReport.summary.unresolved.length}</strong><span>待跟进</span></div><div><strong>{ticketReport.summary.high_priority.length}</strong><span>高优先级</span></div><div><strong>{ticketReport.provider === 'fallback-template' ? '模板' : 'LLM'}</strong><span>生成方式</span></div></div><div className="ticket-answer">{ticketReport.answer.split('\n').map((line: string, i: number) => <p key={i}>{line}</p>)}</div><div className="ticket-trace">{ticketReport.trace.map((step: any, i: number) => <span key={i}>✓ {step.name}</span>)}</div></div>}
      </section>
      <div className="stats"><div><strong>{documents.length}</strong><span>知识文档</span></div><div><strong>{health?.vector_ready ? '向量混合' : '关键词'}</strong><span>当前检索</span></div><div><strong>{conversations.length}</strong><span>历史会话</span></div><div><strong>{health?.llm_configured ? '已接入' : '可降级'}</strong><span>LLM 状态</span></div></div>
      {notice && <div className="notice" role="status">{notice}</div>}
      <div className="grid">
        <section className="panel chat-panel">
          <div className="panel-head"><div><p className="eyebrow">AI ASSISTANT</p><h2>制度问答</h2></div>
            <div className="panel-tools">
              <button className="pill-btn" onClick={newConversation}>＋ 新会话</button>
              <span className="pill">{health?.llm_configured ? '● LLM' : '● Fallback'}</span>
            </div></div>
          <div className="chat-scroll">{messages.length === 0 && <div className="empty"><div className="empty-icon">⌁</div><h3>你好，我是知识助手</h3><p>试着问我一个关于报销、请假或 IT 账号的问题</p><div className="suggestions">{suggestions.map(s => <button key={s} onClick={() => ask(s)}>{s}<span>→</span></button>)}</div></div>}{messages.map((m, i) => <div className={`message ${m.role}`} key={i}><div className="avatar">{m.role === 'user' ? '我' : '知'}</div><div className="bubble">{m.content.split('\n').map((line, j) => <p key={j}>{line}</p>)}{m.role === 'assistant' && i === messages.length - 1 && <div className="message-actions"><button onClick={() => sendFeedback('up')} className={feedback === 1 ? 'selected' : ''}>有帮助</button><button onClick={() => sendFeedback('down')} className={feedback === 0 ? 'selected' : ''}>需改进</button></div>}</div></div>)}{loading && <div className="message assistant"><div className="avatar">知</div><div className="bubble loading">正在检索知识库并生成回答 <span>•••</span></div></div>}</div>
          <div className="composer"><input value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={e => e.key === 'Enter' && ask()} placeholder="输入你的制度问题..." /><button onClick={() => ask()} disabled={loading}>发送 ↗</button></div></section>
        <aside>
          <section className="panel">
            <div className="panel-head"><h2>知识文档</h2><div className="panel-tools">
              <label className="upload"><input type="file" accept={ACCEPT} disabled={uploading} onChange={e => { const file = e.target.files?.[0]; if (file) uploadDocument(file); e.currentTarget.value = ''; }} />{uploading ? '上传中…' : '上传文档'}</label>
              <span className="count">{documents.length}</span></div></div>
            <div className="search"><span>⌕</span><input value={search} onChange={e => doSearch(e.target.value)} placeholder="混合检索文档内容..." /></div>
            <div className="doc-list">{(results.length ? results : documents).map((doc: any, i: number) => <div className="doc" key={doc.id || doc.chunk_id || i}>
              <div className="doc-icon">{doc.doc_type === 'pdf' ? '◫' : doc.doc_type === 'docx' ? '▤' : '▥'}</div>
              <div className="doc-body"><b>{doc.title}</b><span>{doc.category || '检索结果'} · {doc.chunks ?? 'chunk'}{doc.vector_score !== undefined ? ` · 分 ${doc.score}` : ''}</span></div>
              {doc.id && <button className="doc-del" title="删除文档" onClick={() => deleteDocument(doc.id)}>×</button>}
            </div>)}</div>
            {results.length > 0 && <button className="clear" onClick={() => doSearch('')}>清除搜索</button>}
          </section>
          <section className="panel">
            <div className="panel-head"><h2>会话历史</h2><span className="count">{conversations.length}</span></div>
            <div className="conv-list">{conversations.length === 0 && <p className="conv-empty">暂无会话，提问后自动保存</p>}
              {conversations.map(conv => <button className={`conv ${conv.id === conversationId ? 'active' : ''}`} key={conv.id} onClick={() => openConversation(conv)}>
                <b>{conv.messages.find(m => m.role === 'user')?.content.slice(0, 24) || conv.id}</b>
                <span>{conv.messages.length} 条消息 · {conv.created_at.slice(0, 10)}</span>
              </button>)}</div>
          </section>
          {lastAssistant?.citations && lastAssistant.citations.length > 0 && <section className="panel citations"><div className="panel-head"><h2>引用来源</h2><span className="count">{lastAssistant.citations.length}</span></div>{lastAssistant.citations.map(c => <div className="citation" key={c.chunk_id || c.source}><b>{c.title}</b><p>{c.content}</p><span>相关度 {Math.round(c.score * 100)}%{c.vector_score !== undefined ? `（向量 ${Math.round(c.vector_score * 100)} / 关键词 ${Math.round((c.keyword_score || 0) * 100)}）` : ''}</span></div>)}</section>}
        </aside></div>
      {lastAssistant?.trace && <section className="panel trace"><div className="panel-head"><div><p className="eyebrow">EXPLAINABLE PIPELINE</p><h2>回答链路</h2></div><span className="pill">本次会话</span></div><div className="timeline">{lastAssistant.trace.map((step, i) => <div className="step" key={step.name + i}><div className="dot">{i + 1}</div><div><b>{step.name}</b><span>{step.detail} · {step.provider}</span></div></div>)}</div></section>}
    </main><footer>AI Knowledge Hub · 本项目仅用于学习和面试演示，请勿将 mock 制度当作真实生产规则。</footer></div>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
