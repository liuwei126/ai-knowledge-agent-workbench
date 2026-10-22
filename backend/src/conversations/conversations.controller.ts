import { Body, Controller, Delete, Get, Param, Post, Query, Req } from '@nestjs/common';
import { ConversationsService } from './conversations.service';

@Controller('api/conversations')
export class ConversationsController {
  constructor(private readonly conversations: ConversationsService) {}
  @Post() create(@Body('userId') userId: string, @Req() request: any) { return this.conversations.create(userId || request.user?.id); }
  @Get() list(@Query('userId') userId: string, @Req() request: any) { return { items: this.conversations.list(userId || request.user?.id) }; }
  @Get(':id') get(@Param('id') id: string) { return this.conversations.get(id); }
  @Post(':id/messages') add(@Param('id') id: string, @Body() body: any) { return this.conversations.add(id, body); }
  @Delete(':id/messages') clear(@Param('id') id: string) { return this.conversations.clear(id); }
}
