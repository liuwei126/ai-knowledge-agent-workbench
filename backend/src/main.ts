import { NestFactory } from '@nestjs/core';
import { ValidationPipe } from '@nestjs/common';
import { DocumentBuilder, SwaggerModule } from '@nestjs/swagger';
import { AppModule } from './app.module';
import { StorageService } from './storage/storage.service';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);
  app.enableCors({ origin: process.env.CORS_ORIGIN || true });
  app.useGlobalPipes(new ValidationPipe({ transform: true, whitelist: false }));
  const config = new DocumentBuilder().setTitle('AI Knowledge Agent API').setDescription('脱敏银行制度知识库 Demo：全部内容为虚构演示数据').setVersion('2.0').build();
  SwaggerModule.setup('api/docs', app, SwaggerModule.createDocument(app, config));
  const storage = app.get(StorageService);
  app.getHttpAdapter().get('/api/health', (_req, res) => res.json({ status: 'ok', mode: 'demo', providers: { retrieval: 'in-memory-keyword', embedding: 'not-configured', chat: 'local-fallback' } }));
  app.getHttpAdapter().get('/api/stats', (req, res) => res.json(storage.stats(req.user)));
  await app.listen(Number(process.env.PORT) || 3001);
}
bootstrap();
