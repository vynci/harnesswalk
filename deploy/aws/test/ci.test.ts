import { describe, expect, it } from 'vitest';
import { asList, statements, synth } from './helpers';

const { ci } = synth();
const st = statements(ci);
const find = (sid: string) => st.find((s) => s.Sid === sid)!;
const trust = () =>
  Object.values(ci.findResources('AWS::IAM::Role'))[0].Properties.AssumeRolePolicyDocument.Statement[0];

describe('HarnesswalkCi', () => {
  it('has the harnesswalk/api repository keeping the last 10 images', () => {
    ci.hasResourceProperties('AWS::ECR::Repository', {
      RepositoryName: 'harnesswalk/api',
      ImageScanningConfiguration: { ScanOnPush: true },
    });
    const repo = Object.values(ci.findResources('AWS::ECR::Repository'))[0].Properties;
    const policy = JSON.parse(repo.LifecyclePolicy.LifecyclePolicyText);
    expect(policy.rules[0].selection).toMatchObject({ countType: 'imageCountMoreThan', countNumber: 10 });
  });

  it('creates the GitHub OIDC provider natively (no custom resource) by default', () => {
    ci.resourceCountIs('AWS::IAM::OIDCProvider', 1);
    ci.hasResourceProperties('AWS::IAM::OIDCProvider', {
      Url: 'https://token.actions.githubusercontent.com',
      ClientIdList: ['sts.amazonaws.com'],
    });
    ci.resourceCountIs('AWS::Lambda::Function', 0);
  });

  it('reuses an existing provider when githubOidcProviderArn is set', () => {
    const arn = 'arn:aws:iam::111910761410:oidc-provider/token.actions.githubusercontent.com';
    const { ci: reused } = synth({ githubOidcProviderArn: arn });
    reused.resourceCountIs('AWS::IAM::OIDCProvider', 0);
    const t = Object.values(reused.findResources('AWS::IAM::Role'))[0].Properties.AssumeRolePolicyDocument.Statement[0];
    expect(t.Principal.Federated).toBe(arn);
  });

  it('trusts only the main branch of vynci/harnesswalk', () => {
    expect(trust()).toMatchObject({
      Action: 'sts:AssumeRoleWithWebIdentity',
      Condition: {
        StringEquals: {
          'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
          'token.actions.githubusercontent.com:sub': 'repo:vynci/harnesswalk:ref:refs/heads/main',
        },
      },
    });
    expect(JSON.stringify(trust())).not.toContain('StringLike');
  });

  it('scopes S3 to the web bucket and CloudFront to the distribution', () => {
    expect(JSON.stringify(find('WebBucketList').Resource)).toContain('WebBucket');
    expect(JSON.stringify(find('WebBucketObjects').Resource)).toMatch(/WebBucket.*\/\*/);
    expect(asList(find('Invalidate').Action).sort()).toEqual(['cloudfront:CreateInvalidation', 'cloudfront:GetInvalidation']);
    expect(JSON.stringify(find('Invalidate').Resource)).toContain(':distribution/');
  });

  it('pushes only to its repository and sets only the image tag parameter', () => {
    expect(find('EcrPush').Resource).toEqual({ 'Fn::GetAtt': [expect.stringMatching(/^ApiRepository/), 'Arn'] });
    expect(JSON.stringify(find('ImageTagParameter').Resource)).toContain(':parameter/harnesswalk/prod/IMAGE_TAG');
    expect(JSON.stringify(st)).not.toContain('ANTHROPIC_API_KEY');
  });

  it('runs commands only on the origin instance with AWS-RunShellScript', () => {
    const run = find('RunOnOrigin');
    expect(run.Action).toBe('ssm:SendCommand');
    const resources = JSON.stringify(run.Resource);
    expect(resources).toContain(':instance/');
    expect(resources).toContain(':document/AWS-RunShellScript');
    expect(asList(run.Resource)).toHaveLength(2);
  });

  it('never grants a wildcard action, and a wildcard resource only where AWS has no scope', () => {
    const unscoped = new Set(['ecr:GetAuthorizationToken', 'ssm:GetCommandInvocation', 'ssm:ListCommandInvocations']);
    for (const s of st) {
      for (const a of asList(s.Action)) expect(a).not.toMatch(/\*/);
      if (asList(s.Resource).includes('*')) for (const a of asList(s.Action)) expect(unscoped).toContain(a);
    }
    ci.hasOutput('DeployRoleArn', {});
    ci.hasOutput('RepositoryUri', {});
  });
});
