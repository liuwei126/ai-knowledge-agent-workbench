import { BadRequestException, Injectable, NotFoundException } from '@nestjs/common';
import { DocumentRecord, StorageService } from '../storage/storage.service';

@Injectable()
export class DocumentsService {
  constructor(private readonly storage: StorageService) {}

  list(query: any, user: any) {
    let documents = this.storage.documents.filter(document => document.status !== 'offline' || user?.role === 'admin');
    if (query.status) documents = documents.filter(document => document.status === query.status);
    if (query.category) documents = documents.filter(document => document.category === query.category);
    if (query.keyword) { const keyword = String(query.keyword).toLowerCase(); documents = documents.filter(document => `${document.name} ${document.summary}`.toLowerCase().includes(keyword)); }
    const categories = [...new Set(this.storage.documents.map(document => document.category))];
    return { items: documents.map(({ chunks, ...document }) => ({ ...document, chunkCount: chunks.length })), total: documents.length, categories };
  }

  get(id: string) { const document = this.storage.documents.find(item => item.id === id); if (!document) throw new NotFoundException('文档不存在'); return document; }

  upload(file: any, body: any) {
    if (!file) throw new BadRequestException('请上传 txt 或 md 文件');
    const name = file.originalname || 'untitled.txt';
    if (!/\.(txt|md)$/i.test(name)) throw new BadRequestException('Demo 模式仅支持 txt/md');
    const text = file.buffer.toString('utf8').trim();
    if (!text) throw new BadRequestException('文件内容不能为空');
    const id = this.storage.nextId('doc'); const now = new Date().toISOString();
    const category = body.category || '未分类';
    const chunks = this.splitText(text, id, name, category, body.permissions);
    const document: DocumentRecord = { id, name, category, department: body.department || '制度管理', status: 'processing', version: 1, createdAt: now, updatedAt: now, owner: body.owner || '当前用户', size: file.size, mimeType: file.mimetype || 'text/plain', summary: text.slice(0, 160), source: `upload://${name}`, demoOnly: true, chunks };
    // 本地 Demo 不依赖队列或搜索服务，立即完成轻量索引并保留状态字段，方便教学时观察生命周期。
    document.status = 'draft';
    this.storage.documents.unshift(document);
    return { document, index: { status: 'completed', chunks: chunks.length, provider: 'in-memory-keyword' } };
  }

  private splitText(text: string, documentId: string, name: string, category: string, rawPermissions?: string) {
    const paragraphs = text.split(/\n\s*\n|\n(?=第|一、|二、|问：|Q：)/).map(item => item.trim()).filter(Boolean);
    const pieces = paragraphs.length ? paragraphs : [text];
    return pieces.map((content, index) => ({ id: this.storage.nextId('chunk'), documentId, content, title: name.replace(/\.(txt|md)$/i, ''), section: `第${index + 1}节`, page: index + 1, category, permissions: (rawPermissions || 'all').split(',').map(item => item.trim()) }));
  }

  action(id: string, status: 'published' | 'offline') { const document = this.get(id); document.status = status; document.updatedAt = new Date().toISOString(); return document; }
  remove(id: string) { this.get(id); this.storage.documents = this.storage.documents.filter(document => document.id !== id); return { id, deleted: true }; }
  rebuild(id: string) { const document = this.get(id); document.status = 'processing'; document.updatedAt = new Date().toISOString(); document.status = 'draft'; return { documentId: id, status: 'completed', indexedChunks: document.chunks.length, provider: 'in-memory-keyword' }; }
}
