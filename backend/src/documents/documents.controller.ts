import { Body, Controller, Delete, Get, Param, Post, Query, Req, UploadedFile, UseInterceptors } from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { ApiConsumes, ApiTags } from '@nestjs/swagger';
import { DocumentsService } from './documents.service';

@ApiTags('documents')
@Controller('api/documents')
export class DocumentsController {
  constructor(private readonly documents: DocumentsService) {}
  @Get() list(@Query() query: any, @Req() request: any) { return this.documents.list(query, request.user); }
  @Get(':id') get(@Param('id') id: string) { return this.documents.get(id); }
  @Post('upload') @ApiConsumes('multipart/form-data') @UseInterceptors(FileInterceptor('file')) upload(@UploadedFile() file: any, @Body() body: any) { return this.documents.upload(file, body); }
  @Post(':id/publish') publish(@Param('id') id: string) { return this.documents.action(id, 'published'); }
  @Post(':id/offline') offline(@Param('id') id: string) { return this.documents.action(id, 'offline'); }
  @Post(':id/rebuild-index') rebuild(@Param('id') id: string) { return this.documents.rebuild(id); }
  @Delete(':id') remove(@Param('id') id: string) { return this.documents.remove(id); }
}
