import * as fs from 'node:fs';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { API_PATH_PATTERNS, DOMAIN, ORIGIN_HOST, webBucketName } from './config';

// The edge: a private S3 bucket with the built web app, and one CloudFront distribution at
// harnesswalk.vynci.dev in front of both the bucket (default behaviour) and the EC2 origin
// (the API paths). No Lambda anywhere: the SPA fallback is a CloudFront Function.

export interface EdgeStackProps extends cdk.StackProps {
  // ACM certificate for DOMAIN in us-east-1 (scripts/request-cert.sh writes it to
  // cdk.context.json as certArn). It must be ISSUED before this stack deploys.
  certArn: string;
}

export class EdgeStack extends cdk.Stack {
  public readonly bucket: s3.Bucket;
  public readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: EdgeStackProps) {
    super(scope, id, props);

    // X-Origin-Verify: Caddy refuses requests without it. CloudFront cannot read an SSM
    // SecureString, so the value comes in as a NoEcho parameter that scripts/deploy-stacks.sh
    // reads from /harnesswalk/prod/ORIGIN_VERIFY; later deploys reuse the previous value.
    const originVerify = new cdk.CfnParameter(this, 'OriginVerify', {
      type: 'String',
      noEcho: true,
      minLength: 32,
      description: 'Value of the X-Origin-Verify header CloudFront sends to the origin',
    });

    this.bucket = new s3.Bucket(this, 'WebBucket', {
      bucketName: webBucketName(this.account),
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      versioned: true,
      lifecycleRules: [
        { noncurrentVersionExpiration: cdk.Duration.days(30) },
        { abortIncompleteMultipartUploadAfter: cdk.Duration.days(1) },
      ],
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const spaRewrite = new cloudfront.Function(this, 'SpaRewrite', {
      functionName: 'harnesswalk-spa-rewrite',
      comment: 'Client-side routes (no file extension) are served /index.html',
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(
        fs.readFileSync(path.join(__dirname, 'edge', 'spa-rewrite.js'), 'utf8'),
      ),
    });

    const viewerIp = new cloudfront.Function(this, 'ViewerIp', {
      functionName: 'harnesswalk-viewer-ip',
      comment: 'Sets X-Harnesswalk-Viewer-Ip from the viewer address (per-IP rate limit)',
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(
        fs.readFileSync(path.join(__dirname, 'edge', 'viewer-ip.js'), 'utf8'),
      ),
    });

    const apiOrigin = new origins.HttpOrigin(ORIGIN_HOST, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
      originSslProtocols: [cloudfront.OriginSslPolicy.TLS_V1_2],
      // 60 s is the most CloudFront allows without a quota increase (up to 180 s; runbook).
      // The API sends an SSE keepalive comment every 15 s, so streams stay well inside it.
      readTimeout: cdk.Duration.seconds(60),
      keepaliveTimeout: cdk.Duration.seconds(60),
      customHeaders: { 'X-Origin-Verify': originVerify.valueAsString },
    });

    const apiBehavior: cloudfront.BehaviorOptions = {
      origin: apiOrigin,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
      cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      // Cookies, query strings and every viewer header (X-Harnesswalk-*, Accept for SSE)
      // reach the origin; Host does not.
      originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
      functionAssociations: [
        { function: viewerIp, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST },
      ],
      responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
      compress: false,
    };

    this.distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: `harnesswalk ${DOMAIN}`,
      domainNames: [DOMAIN],
      certificate: acm.Certificate.fromCertificateArn(this, 'Certificate', props.certArn),
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      // IPv4 only for v1: the API's per-IP rate limit and turn caps key on the address, and an
      // IPv6 viewer can rotate through a /64 at will. Turn it back on (and add the AAAA record)
      // once the API buckets IPv6 by /64 (deploy/README.md, IPv6).
      enableIpv6: false,
      defaultRootObject: 'index.html',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(this.bucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
        // CachingOptimized honours the object's Cache-Control: deploy-web.sh uploads
        // index.html with no-cache and /assets/* (hashed names) as immutable for a year.
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
        compress: true,
        functionAssociations: [
          { function: spaRewrite, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST },
        ],
      },
      additionalBehaviors: Object.fromEntries(API_PATH_PATTERNS.map((p) => [p, apiBehavior])),
      // No errorResponses on purpose: they apply to every behaviour, so the API's 404s and
      // 403s would turn into a 200 index.html. The SPA fallback is the function above.
    });

    new cdk.CfnOutput(this, 'DistributionDomainName', {
      value: this.distribution.distributionDomainName,
      description: `GoDaddy: CNAME ${DOMAIN.split('.')[0]} -> this value`,
    });
    new cdk.CfnOutput(this, 'DistributionId', { value: this.distribution.distributionId });
    new cdk.CfnOutput(this, 'WebBucketName', { value: this.bucket.bucketName });
  }
}
