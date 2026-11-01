import * as fs from 'node:fs';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { buildApp } from '../lib/app';

export const CERT_ARN = 'arn:aws:acm:us-east-1:111910761410:certificate/11111111-2222-3333-4444-555555555555';

const readJson = (name: string) => JSON.parse(fs.readFileSync(path.join(__dirname, '..', name), 'utf8'));

// The context the CLI sees (cdk.json plus the committed lookups in cdk.context.json, so the
// default VPC is the real one), plus a certificate.
export const CONTEXT = readJson('cdk.context.json');
export function synth(extra: Record<string, string> = {}) {
  const app = new cdk.App({
    context: { ...CONTEXT, ...readJson('cdk.json').context, certArn: CERT_ARN, ...extra },
  });
  const stacks = buildApp(app);
  return {
    edge: Template.fromStack(stacks.edge),
    origin: Template.fromStack(stacks.origin),
    ci: Template.fromStack(stacks.ci),
  };
}

// Every statement of every AWS::IAM::Policy in a template, flattened.
export function statements(t: Template): Array<Record<string, any>> {
  return Object.values(t.findResources('AWS::IAM::Policy')).flatMap(
    (p: any) => p.Properties.PolicyDocument.Statement,
  );
}

export const asList = <T>(v: T | T[]): T[] => (Array.isArray(v) ? v : [v]);

// A Fn::Join of literals and AWS::Partition, as a string (an ARN built with Arn.format).
export function joined(v: any): string {
  if (typeof v === 'string') return v;
  if (v && v.Ref === 'AWS::Partition') return 'aws';
  if (v && v['Fn::Join']) return v['Fn::Join'][1].map(joined).join(v['Fn::Join'][0]);
  throw new Error(`not a literal: ${JSON.stringify(v)}`);
}
