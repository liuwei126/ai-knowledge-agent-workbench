import { Injectable } from '@nestjs/common';
import { randomUUID } from 'crypto';

export type DocumentStatus = 'draft' | 'processing' | 'published' | 'offline';
export type Chunk = { id: string; documentId: string; content: string; title: string; section: string; page: number; category: string; permissions: string[] };
export type DocumentRecord = { id: string; name: string; category: string; department: string; status: DocumentStatus; version: number; createdAt: string; updatedAt: string; owner: string; size: number; mimeType: string; summary: string; source: string; demoOnly: true; chunks: Chunk[] };
export type Conversation = { id: string; title: string; userId: string; createdAt: string; updatedAt: string; messages: any[] };

const seed = [
  ['差旅报销办理指引', '差旅报销', '财务共享', '因公出差可报销交通、住宿和城市内交通等合理费用。返程后 10 个工作日内提交申请，需附行程审批、合规发票和行程单；超出示例标准的部分应先走事前说明。'],
  ['费用审批与预算使用说明', '费用审批', '财务共享', '日常费用遵循先申请、后支出的原则。申请单应写明用途、预算科目、金额和受益部门，审批完成后再下单或付款；紧急事项应在事后 2 个工作日内补齐说明。'],
  ['请假与考勤办理说明', '请假考勤', '人力资源', '员工请假通常应提前 1 个工作日在协同平台提交，填写类型、起止时间和交接人。直属负责人审批后生效，连续 3 个工作日以上的示例流程增加部门负责人复核。'],
  ['IT 账号申请与权限规范', 'IT账号', '信息技术', '入职或转岗时由直属负责人发起账号申请，服务台完成身份核验后开通。账号不得共享，密码遗失应立即重置；离职、转岗或权限不再需要时应及时回收。'],
  ['采购与合同流转流程', '采购合同', '采购管理', '采购需求先提交预算和供应商信息，再由采购岗位进行比价或合规性检查。合同签署前需完成法务要点核验和授权审批，未完成流程不得以个人名义承诺付款。'],
  ['发票接收与归档问答', '发票管理', '财务共享', '发票应与真实业务、付款对象和金额一致，提交时选择正确的费用类型并上传清晰附件。发现抬头、税号或金额有误时先联系开票方更正，不要自行修改电子文件。'],
  ['办公用品领用与资产借用', '办公管理', '行政服务', '办公用品按部门需求提交领用申请，资产借用需登记保管人、预计归还日和设备状态。演示环境只记录流程，不代表真实库存、额度或审批权限。'],
  ['员工入转调离办理说明', '人事管理', '人力资源', '入职、转岗和离职均应由负责人提交人员变动申请，相关部门按清单完成账号、资产、资料和权限交接。该文档为虚构学习案例，具体事项应以真实组织制度为准。'],
];

@Injectable()
export class StorageService {
  documents: DocumentRecord[] = [];
  conversations: Conversation[] = [];
  feedback: any[] = [];

  constructor() {
    const now = new Date().toISOString();
    this.documents = seed.map(([name, category, department, content], index) => this.createSeed(name, category, department, content, index, now));
  }

  private createSeed(name: string, category: string, department: string, content: string, index: number, now: string): DocumentRecord {
    const id = `doc-${index + 1}`;
    return { id, name: `${name}.md`, category, department, status: 'published', version: 1, createdAt: now, updatedAt: now, owner: '演示制度小组', size: Buffer.byteLength(content), mimeType: 'text/markdown', summary: content.slice(0, 100), source: `mock://demo/${id}`, demoOnly: true, chunks: [{ id: `chunk-${index + 1}`, documentId: id, content, title: name, section: '第一节 办理说明', page: 1, category, permissions: ['all'] }] };
  }

  nextId(prefix: string) { return `${prefix}-${randomUUID().slice(0, 8)}`; }
  visible(user: any, document: DocumentRecord) { return document.status === 'published' && (user?.role === 'admin' || document.chunks.some(chunk => chunk.permissions.includes('all') || chunk.permissions.includes(user?.department))); }
  stats(user: any) { const docs = this.documents.filter(document => this.visible(user, document)); return { documents: docs.length, published: docs.filter(d => d.status === 'published').length, draft: docs.filter(d => d.status === 'draft').length, processing: docs.filter(d => d.status === 'processing').length, offline: docs.filter(d => d.status === 'offline').length, chunks: docs.reduce((total, d) => total + d.chunks.length, 0) }; }
}
