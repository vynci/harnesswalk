import * as cdk from 'aws-cdk-lib';
import { CiStack } from './ci-stack';
import { STACKS, TAGS } from './config';
import { EdgeStack } from './edge-stack';
import { OriginStack } from './origin-stack';

// The three stacks, wired together. bin/harnesswalk.ts and the tests both call this.
export function buildApp(app: cdk.App) {
  const ctx = (key: string): string | undefined => {
    const value = app.node.tryGetContext(key);
    return value === undefined || value === '' ? undefined : String(value);
  };
  const required = (key: string, hint: string): string => {
    const value = ctx(key);
    if (!value) throw new Error(`No ${key} in context. ${hint}`);
    return value;
  };

  // One account, one region (CloudFront certificates must live in us-east-1 anyway).
  const env: cdk.Environment = { account: ctx('account') ?? '111910761410', region: 'us-east-1' };

  const edge = new EdgeStack(app, STACKS.edge, {
    env,
    certArn: required(
      'certArn',
      'Run deploy/aws/scripts/request-cert.sh (it writes cdk.context.json), or pass ' +
        '--context certArn=arn:aws:acm:us-east-1:...',
    ),
    description: 'harnesswalk edge: S3 web app + CloudFront (harnesswalk.vynci.dev)',
  });

  const origin = new OriginStack(app, STACKS.origin, {
    env,
    amiId: required('amiId', 'cdk.json pins one (Amazon Linux 2023 arm64).'),
    availabilityZones: (ctx('availabilityZones') ?? 'us-east-1a,us-east-1b').split(',') as [
      string,
      string,
    ],
    cloudFrontPrefixListId: required('cloudFrontPrefixListId', 'cdk.json pins pl-3b927c52.'),
    distributionId: edge.distribution.distributionId,
    instanceType: ctx('instanceType'),
    description: 'harnesswalk origin: one EC2 instance running the API with Docker compose',
  });

  const ci = new CiStack(app, STACKS.ci, {
    env,
    bucketArn: edge.bucket.bucketArn,
    distributionId: edge.distribution.distributionId,
    instanceId: origin.instance.instanceId,
    githubOidcProviderArn: ctx('githubOidcProviderArn'),
    description: 'harnesswalk CI: ECR repository and the GitHub OIDC deploy role',
  });

  for (const [key, value] of Object.entries(TAGS)) cdk.Tags.of(app).add(key, value);
  return { edge, origin, ci };
}
