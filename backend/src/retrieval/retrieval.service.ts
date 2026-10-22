import { Injectable } from '@nestjs/common';
import { StorageService } from '../storage/storage.service';

export type SearchOptions = { category?: string; limit?: number };

@Injectable()
export class RetrievalService {
  constructor(private readonly storage: StorageService) {}

  search(query: string, user: any, options: SearchOptions = {}) {
    const terms = this.tokenize(query);
    if (!terms.length) return [];
    return this.storage.documents.filter(document => (!options.category || document.category === options.category) && this.storage.visible(user, document)).flatMap(document => document.chunks.map(chunk => {
      const haystack = `${chunk.title} ${chunk.category} ${chunk.content}`.toLowerCase();
      const matchedTerms = terms.filter(term => haystack.includes(term));
      return { ...chunk, documentName: document.name, documentStatus: document.status, score: Number((matchedTerms.length / terms.length).toFixed(2)), matchedTerms, highlight: this.highlight(chunk.content, matchedTerms) };
    })).filter(result => result.score > 0).sort((a, b) => b.score - a.score).slice(0, Math.min(options.limit || 5, 20));
  }

  tokenize(query: string) { return query.toLowerCase().split(/[\s，。！？、；：,.!?;:]+/).map(term => term.trim()).filter(term => term.length > 1); }

  private highlight(content: string, terms: string[]) {
    let value = content;
    terms.forEach(term => { value = value.replace(new RegExp(this.escapeRegExp(term), 'gi'), match => `<mark>${match}</mark>`); });
    return value;
  }

  private escapeRegExp(value: string) { return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
}
