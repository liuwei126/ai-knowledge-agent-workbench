import { DocumentsService } from '../src/documents/documents.service';
import { ConversationsService } from '../src/conversations/conversations.service';
import { RetrievalService } from '../src/retrieval/retrieval.service';
import { StorageService } from '../src/storage/storage.service';

describe('knowledge demo core', () => {
  const storage = new StorageService();
  const retrieval = new RetrievalService(storage);

  it('returns scored and highlighted keyword results', () => {
    const results = retrieval.search('差旅 报销', { role: 'employee' });
    expect(results[0].category).toBe('差旅报销');
    expect(results[0].score).toBeGreaterThan(0);
    expect(results[0].highlight).toContain('<mark>报销</mark>');
  });

  it('filters document categories', () => {
    const service = new DocumentsService(storage);
    const result = service.list({ category: 'IT账号' }, { role: 'employee' });
    expect(result.total).toBe(1);
    expect(result.items[0].category).toBe('IT账号');
  });

  it('clears a conversation without deleting it', () => {
    const service = new ConversationsService(storage);
    const conversation = service.create('test-user');
    service.add(conversation.id, { role: 'user', content: '测试' });
    expect(service.clear(conversation.id).messages).toHaveLength(0);
    expect(service.get(conversation.id).id).toBe(conversation.id);
  });
});
