import { Injectable, NotFoundException } from '@nestjs/common';
import { StorageService } from '../storage/storage.service';

@Injectable()
export class ConversationsService {
  constructor(private readonly storage: StorageService) {}
  create(userId = 'demo-user') { const now = new Date().toISOString(); const conversation = { id: this.storage.nextId('conv'), title: '新对话', userId, createdAt: now, updatedAt: now, messages: [] }; this.storage.conversations.unshift(conversation); return conversation; }
  list(userId = 'demo-user') { return this.storage.conversations.filter(conversation => conversation.userId === userId).map(({ messages, ...summary }) => ({ ...summary, messageCount: messages.length })); }
  get(id: string) { const conversation = this.storage.conversations.find(item => item.id === id); if (!conversation) throw new NotFoundException('会话不存在'); return conversation; }
  add(id: string, message: any) { const conversation = this.get(id); conversation.messages.push({ ...message, createdAt: new Date().toISOString() }); conversation.updatedAt = new Date().toISOString(); if (conversation.title === '新对话' && message.role === 'user') conversation.title = String(message.content).slice(0, 24); return conversation; }
  clear(id: string) { const conversation = this.get(id); conversation.messages = []; conversation.updatedAt = new Date().toISOString(); return conversation; }
}
