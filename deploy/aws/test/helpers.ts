import * as fs from 'node:fs';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { buildApp } from '../lib/app';

export const CERT_ARN = 'arn:aws:acm:us-east-1:111910761410:certificate/11111111-2222-3333-4444-555555555555';

// The same context cdk.json gives the CLI, plus a certificate.
export function synth(extra: Record<string, string> = {}) {
  const cdkJson = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'cdk.json'), 'utf8'));
  const app = new cdk.App({ context: { ...cdkJson.context, certArn: CERT_ARN, ...extra } });
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
