import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vm from 'node:vm';
import { Match } from 'aws-cdk-lib/assertions';
import { describe, expect, it } from 'vitest';
import { CERT_ARN, synth } from './helpers';

const CACHING_OPTIMIZED = '658327ea-f89d-4fab-a63d-7e88639e58f6';
const CACHING_DISABLED = '4135ea2d-6df8-44a3-9df3-4b5a84be39ad';
const ALL_VIEWER_EXCEPT_HOST = 'b689b0a8-53d0-40ab-baf2-68738e2966ac';

const { edge } = synth();
const dist = () =>
  Object.values(edge.findResources('AWS::CloudFront::Distribution'))[0].Properties.DistributionConfig;

describe('HarnesswalkEdge', () => {
  it('keeps the bucket private, encrypted and TLS-only', () => {
    edge.hasResourceProperties('AWS::S3::Bucket', {
      BucketName: 'harnesswalk-web-111910761410',
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
      BucketEncryption: Match.anyValue(),
      OwnershipControls: { Rules: [{ ObjectOwnership: 'BucketOwnerEnforced' }] },
    });
    edge.hasResource('AWS::S3::Bucket', { DeletionPolicy: 'Retain' });
    edge.hasResourceProperties('AWS::S3::BucketPolicy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Effect: 'Deny',
            Condition: { Bool: { 'aws:SecureTransport': 'false' } },
          }),
        ]),
      },
    });
  });

  it('reads the bucket through Origin Access Control, scoped to this distribution', () => {
    edge.resourceCountIs('AWS::CloudFront::OriginAccessControl', 1);
    edge.resourceCountIs('AWS::CloudFront::CloudFrontOriginAccessIdentity', 0);
    edge.hasResourceProperties('AWS::CloudFront::OriginAccessControl', {
      OriginAccessControlConfig: {
        OriginAccessControlOriginType: 's3',
        SigningBehavior: 'always',
        SigningProtocol: 'sigv4',
      },
    });
    edge.hasResourceProperties('AWS::S3::BucketPolicy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Effect: 'Allow',
            Action: 's3:GetObject',
            Principal: { Service: 'cloudfront.amazonaws.com' },
            Condition: { StringEquals: { 'AWS:SourceArn': Match.anyValue() } },
          }),
        ]),
      },
    });
    const s3Origin = dist().Origins.find((o: any) => o.S3OriginConfig);
    expect(s3Origin.OriginAccessControlId).toBeDefined();
    expect(s3Origin.S3OriginConfig.OriginAccessIdentity).toBe('');
  });

  it('serves the web app from S3 by default with the SPA rewrite and no error responses', () => {
    const d = dist();
    const s3Origin = d.Origins.find((o: any) => o.S3OriginConfig);
    expect(d.DefaultCacheBehavior).toMatchObject({
      TargetOriginId: s3Origin.Id,
      CachePolicyId: CACHING_OPTIMIZED,
      Compress: true,
      ViewerProtocolPolicy: 'redirect-to-https',
      FunctionAssociations: [{ EventType: 'viewer-request', FunctionARN: expect.anything() }],
    });
    expect(d.DefaultRootObject).toBe('index.html');
    // Error responses are distribution-wide: they would rewrite the API's 404s into HTML.
    expect(d.CustomErrorResponses).toBeUndefined();
  });

  it('routes /v1/*, /healthz and /readyz to the EC2 origin uncached', () => {
    const d = dist();
    const api = d.Origins.find((o: any) => o.CustomOriginConfig);
    expect(api.DomainName).toBe('origin.harnesswalk.vynci.dev');
    expect(api.CustomOriginConfig).toMatchObject({
      OriginProtocolPolicy: 'https-only',
      OriginSSLProtocols: ['TLSv1.2'],
      OriginReadTimeout: 60,
    });
    expect(api.OriginCustomHeaders).toEqual([
      { HeaderName: 'X-Origin-Verify', HeaderValue: { Ref: 'OriginVerify' } },
    ]);
    expect(d.CacheBehaviors.map((b: any) => b.PathPattern).sort()).toEqual(
      ['/healthz', '/readyz', '/v1/*'].sort(),
    );
    for (const b of d.CacheBehaviors) {
      expect(b).toMatchObject({
        TargetOriginId: api.Id,
        CachePolicyId: CACHING_DISABLED,
        OriginRequestPolicyId: ALL_VIEWER_EXCEPT_HOST,
        ViewerProtocolPolicy: 'https-only',
        Compress: false,
        FunctionAssociations: [{ EventType: 'viewer-request', FunctionARN: expect.anything() }],
      });
      expect(b.AllowedMethods).toEqual(
        expect.arrayContaining(['GET', 'HEAD', 'OPTIONS', 'PUT', 'PATCH', 'POST', 'DELETE']),
      );
    }
  });

  it('takes the origin secret as a NoEcho parameter, never a literal', () => {
    edge.hasParameter('OriginVerify', { Type: 'String', NoEcho: true });
    expect(JSON.stringify(edge.toJSON())).not.toMatch(/X-Origin-Verify","HeaderValue":"[^{]/);
  });

  it('uses the ACM certificate, the domain alias, TLS 1.2_2021, HTTP/2+3 and price class 100', () => {
    expect(dist()).toMatchObject({
      Aliases: ['harnesswalk.vynci.dev'],
      ViewerCertificate: {
        AcmCertificateArn: CERT_ARN,
        MinimumProtocolVersion: 'TLSv1.2_2021',
        SslSupportMethod: 'sni-only',
      },
      HttpVersion: 'http2and3',
      PriceClass: 'PriceClass_100',
      IPV6Enabled: true,
    });
    edge.hasOutput('DistributionDomainName', {});
    edge.hasOutput('WebBucketName', {});
    edge.hasOutput('DistributionId', {});
  });
});

describe('spa-rewrite CloudFront Function', () => {
  const code = fs.readFileSync(path.join(__dirname, '..', 'lib', 'edge', 'spa-rewrite.js'), 'utf8');
  const sandbox: { handler?: (e: any) => any } = {};
  vm.runInNewContext(`${code}\nthis.handler = handler;`, sandbox);
  const rewrite = (uri: string) => sandbox.handler!({ request: { uri } }).uri;

  it('serves index.html for client-side routes', () => {
    expect(rewrite('/')).toBe('/index.html');
    expect(rewrite('/sessions/abc')).toBe('/index.html');
    expect(rewrite('/lessons/06-loop/')).toBe('/index.html');
    expect(rewrite('/v1.2/thing')).toBe('/index.html'); // a dot in a directory is not a file
  });

  it('leaves files alone', () => {
    expect(rewrite('/assets/index-abc123.js')).toBe('/assets/index-abc123.js');
    expect(rewrite('/favicon.svg')).toBe('/favicon.svg');
    expect(rewrite('/index.html')).toBe('/index.html');
  });
});

describe('viewer-ip CloudFront Function', () => {
  const code = fs.readFileSync(path.join(__dirname, '..', 'lib', 'edge', 'viewer-ip.js'), 'utf8');
  const sandbox: { handler?: (e: any) => any } = {};
  vm.runInNewContext(`${code}\nthis.handler = handler;`, sandbox);

  it('overwrites a forged viewer address with the one CloudFront saw', () => {
    const out = sandbox.handler!({
      viewer: { ip: '2001:db8::1' },
      request: { uri: '/v1/turns', headers: { 'x-harnesswalk-viewer-ip': { value: '6.6.6.6' } } },
    });
    expect(out.headers['x-harnesswalk-viewer-ip']).toEqual({ value: '2001:db8::1' });
  });
});
