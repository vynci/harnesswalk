import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import {
  DEPLOY_DOCUMENT,
  ECR_REPOSITORY,
  GITHUB_BRANCH,
  GITHUB_OIDC_URL,
  GITHUB_REPO,
  ORIGIN_ROLE_TAG,
  SSM_PARAMS,
} from './config';

// CI: the image repository and the role .github/workflows/deploy.yml assumes with GitHub OIDC.
// The role can do exactly what the deploy scripts do and nothing else: sync the web bucket,
// invalidate the distribution, push the api image, set the image tag parameter and send the
// harnesswalk-deploy document (never AWS-RunShellScript) to the instance tagged as the origin.
// Nothing here refers to the Origin stack, so the instance can be replaced freely.

export interface CiStackProps extends cdk.StackProps {
  bucketArn: string;
  distributionId: string;
  // An existing token.actions.githubusercontent.com provider (an account holds one per URL).
  // Unset: this stack creates it.
  githubOidcProviderArn?: string;
}

export class CiStack extends cdk.Stack {
  public readonly repository: ecr.Repository;
  public readonly role: iam.Role;

  constructor(scope: Construct, id: string, props: CiStackProps) {
    super(scope, id, props);

    this.repository = new ecr.Repository(this, 'ApiRepository', {
      repositoryName: ECR_REPOSITORY,
      imageScanOnPush: true,
      imageTagMutability: ecr.TagMutability.MUTABLE, // `latest` moves; sha tags never do
      encryption: ecr.RepositoryEncryption.AES_256,
      lifecycleRules: [
        { description: 'Keep the last 10 images', maxImageCount: 10, rulePriority: 1 },
      ],
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // OidcProviderNative is AWS::IAM::OIDCProvider itself (no custom-resource Lambda).
    const provider = props.githubOidcProviderArn
      ? iam.OidcProviderNative.fromOidcProviderArn(this, 'GitHubOidc', props.githubOidcProviderArn)
      : new iam.OidcProviderNative(this, 'GitHubOidc', {
          url: GITHUB_OIDC_URL,
          clientIds: ['sts.amazonaws.com'],
        });

    this.role = new iam.Role(this, 'DeployRole', {
      roleName: 'harnesswalk-github-deploy',
      description: `GitHub Actions deploy for ${GITHUB_REPO} (${GITHUB_BRANCH} only)`,
      maxSessionDuration: cdk.Duration.hours(1),
      assumedBy: new iam.WebIdentityPrincipal(provider.oidcProviderArn, {
        StringEquals: {
          'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
          'token.actions.githubusercontent.com:sub': `repo:${GITHUB_REPO}:ref:refs/heads/${GITHUB_BRANCH}`,
        },
      }),
    });

    const add = (sid: string, actions: string[], resources: string[], conditions?: Record<string, unknown>) =>
      this.role.addToPolicy(new iam.PolicyStatement({ sid, actions, resources, conditions }));

    add('WebBucketList', ['s3:ListBucket'], [props.bucketArn]);
    add('WebBucketObjects', ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'], [`${props.bucketArn}/*`]);
    add(
      'Invalidate',
      ['cloudfront:CreateInvalidation', 'cloudfront:GetInvalidation'],
      [cdk.Arn.format({ service: 'cloudfront', region: '', resource: 'distribution', resourceName: props.distributionId }, this)],
    );
    add('EcrAuth', ['ecr:GetAuthorizationToken'], ['*']);
    add(
      'EcrPush',
      [
        'ecr:BatchCheckLayerAvailability',
        'ecr:BatchGetImage',
        'ecr:CompleteLayerUpload',
        'ecr:DescribeImages',
        'ecr:GetDownloadUrlForLayer',
        'ecr:InitiateLayerUpload',
        'ecr:PutImage',
        'ecr:UploadLayerPart',
      ],
      [this.repository.repositoryArn],
    );
    add(
      'ImageTagParameter',
      ['ssm:GetParameter', 'ssm:PutParameter'],
      [cdk.Arn.format({ service: 'ssm', resource: 'parameter', resourceName: SSM_PARAMS.imageTag.slice(1) }, this)],
    );
    // SendCommand is authorised on the instance and on the document separately, so these are
    // two statements: any instance tagged as the origin, and only the deploy document.
    add(
      'RunOnOrigin',
      ['ssm:SendCommand'],
      [cdk.Arn.format({ service: 'ec2', resource: 'instance', resourceName: '*' }, this)],
      { StringEquals: { [`ssm:resourceTag/${ORIGIN_ROLE_TAG.key}`]: ORIGIN_ROLE_TAG.value } },
    );
    add(
      'RunDeployDocument',
      ['ssm:SendCommand'],
      [cdk.Arn.format({ service: 'ssm', resource: 'document', resourceName: DEPLOY_DOCUMENT }, this)],
    );
    // deploy-origin.sh finds the instance by its tag. Describe* and reading a command's
    // result have no resource-level scope.
    add('FindOrigin', ['ec2:DescribeInstances'], ['*']);
    add('ReadCommandResult', ['ssm:GetCommandInvocation', 'ssm:ListCommandInvocations'], ['*']);

    new cdk.CfnOutput(this, 'DeployRoleArn', {
      value: this.role.roleArn,
      description: 'GitHub repository variable HARNESSWALK_DEPLOY_ROLE_ARN',
    });
    new cdk.CfnOutput(this, 'RepositoryUri', {
      value: this.repository.repositoryUri,
      description: 'GitHub repository variable HARNESSWALK_ECR_REPOSITORY',
    });
    new cdk.CfnOutput(this, 'GitHubOidcProviderArn', { value: provider.oidcProviderArn });
  }
}
