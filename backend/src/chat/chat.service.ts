import { Injectable } from '@nestjs/common';
import { randomUUID } from 'crypto';
import { ConversationsService } from '../conversations/conversations.service';
import { RetrievalService } from '../retrieval/retrieval.service';

@Injectable()
export class ChatService {
  readonly recommendations = ['差旅报销需要哪些材料？', '费用审批应该先申请还是先支出？', '请假需要提前多久提交？', 'IT 账号如何申请和回收？', '采购合同签署前要完成什么？'];
  constructor(private readonly retrieval: RetrievalService, private readonly conversations: ConversationsService) {}

  chat(body: any, user: any) {
    const question = String(body.message || body.query || '').trim();
    if (!question) return { answer: '请输入要查询的制度问题。', citations: [], trace: [], recommendations: this.recommendations };
    const started = Date.now(); const trace: any[] = [];
    const step = (name: string, detail: string, action: () => any) => { const begin = Date.now(); const value = action(); trace.push({ name, status: 'completed', detail, provider: 'local-demo', durationMs: Date.now() - begin }); return value; };
    const cleaned = step('问题预处理', `清洗后的问题：${question}`, () => question.replace(/\s+/g, ' '));
    const hits = step('文档加载与关键词检索', '只检索已发布且当前用户可见的 mock 文档，并按命中词比例排序', () => this.retrieval.search(cleaned, user, { limit: 5 }));
    const context = step('上下文组装', `选取 ${hits.length} 个 chunk，限制为本地 Demo 的可控上下文`, () => hits.map(hit => hit.content).join('\n'));
    const answer = step('fallback 回答', hits.length ? '基于最高分片段生成可追溯模板回答，不调用外部 LLM' : '没有足够依据，返回安全拒答而不是猜测制度', () => hits.length ? `基于已发布的演示资料：${hits[0].content}` : '根据当前可检索的演示资料，未找到足够依据回答该问题。请补充业务场景，或以真实组织制度和人工审批结果为准。');
    const citations = step('引用生成', `${hits.length} 条引用与命中分数`, () => hits.map(hit => ({ documentId: hit.documentId, title: hit.title, section: hit.section, page: hit.page, score: hit.score, source: hit.documentName, snippet: hit.content, highlight: hit.highlight })));
    const conversationId = body.conversationId || body.conversation_id || this.conversations.create(user?.id).id;
    step('会话写入', `保存 conversationId=${conversationId}，便于历史查询和清空`, () => { this.conversations.add(conversationId, { role: 'user', content: question }); this.conversations.add(conversationId, { role: 'assistant', content: answer, citations }); return true; });
    return { answer, citations, context: context.slice(0, 2000), recommendations: this.recommendations, retrieval: { query: cleaned, results: hits, provider: 'in-memory-keyword' }, trace, conversationId, conversation_id: conversationId, feedback: null, latencyMs: Date.now() - started };
  }

  feedback(body: any) { const result = { id: randomUUID(), ...body, createdAt: new Date().toISOString() }; return result; }
}
