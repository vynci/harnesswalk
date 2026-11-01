import { describe, expect, it } from 'vitest';
import { asList, joined, statements, synth } from './helpers';

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

  it('makes every tag immutable except latest', () => {
    ci.hasResourceProperties('AWS::ECR::Repository', {
      ImageTagMutability: 'IMMUTABLE_WITH_EXCLUSION',
      ImageTagMutabilityExclusionFilters: [
        { ImageTagMutabilityExclusionFilterType: 'WILDCARD', ImageTagMutabilityExclusionFilterValue: 'latest' },
      ],
    });
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

  it('sends only the harnesswalk-deploy document, only to an instance tagged as the origin', () => {
    const run = find('RunOnOrigin');
    expect(run.Action).toBe('ssm:SendCommand');
    expect(joined(run.Resource)).toBe('arn:aws:ec2:us-east-1:111910761410:instance/*');
    expect(run.Condition).toEqual({ StringEquals: { 'ssm:resourceTag/harnesswalk:role': 'origin' } });
    const doc = find('RunDeployDocument');
    expect(doc.Action).toBe('ssm:SendCommand');
    expect(joined(doc.Resource)).toBe('arn:aws:ssm:us-east-1:111910761410:document/harnesswalk-deploy');
    const sends = st.filter((s) => asList(s.Action).includes('ssm:SendCommand'));
    expect(sends.map((s) => s.Sid).sort()).toEqual(['RunDeployDocument', 'RunOnOrigin']);
    expect(JSON.stringify(ci.toJSON())).not.toContain('AWS-RunShellScript');
  });

  it('does not depend on the Origin stack (the instance can be replaced)', () => {
    expect(JSON.stringify(ci.toJSON())).not.toMatch(/HarnesswalkOrigin|Fn::ImportValue[^}]*Instance/);
  });

  it('never grants a wildcard action, and a wildcard resource only where AWS has no scope', () => {
    const unscoped = new Set([
      'ecr:GetAuthorizationToken',
      'ec2:DescribeInstances',
      'ssm:GetCommandInvocation',
      'ssm:ListCommandInvocations',
    ]);
    for (const s of st) {
      for (const a of asList(s.Action)) expect(a).not.toMatch(/\*/);
      if (asList(s.Resource).includes('*')) for (const a of asList(s.Action)) expect(unscoped).toContain(a);
    }
    ci.hasOutput('DeployRoleArn', {});
    ci.hasOutput('RepositoryUri', {});
  });
});
