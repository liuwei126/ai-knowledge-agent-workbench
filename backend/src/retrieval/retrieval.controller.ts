import { Controller, Get, Query, Req } from '@nestjs/common';
import { ApiTags } from '@nestjs/swagger';
import { RetrievalService } from './retrieval.service';

@ApiTags('retrieval')
@Controller('api/search')
export class RetrievalController {
  constructor(private readonly retrieval: RetrievalService) {}
  @Get()
  search(@Query('q') query = '制度', @Query('category') category: string, @Query('limit') limit: string, @Req() request: any) {
    const results = this.retrieval.search(query, request.user, { category, limit: Number(limit) || 5 });
    return { query, provider: 'in-memory-keyword', results, total: results.length };
  }
}
