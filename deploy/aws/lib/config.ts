// Names shared by the three stacks, the scripts and the tests. One place, so an IAM statement
// in the runbook and a resource in a stack can never disagree.

export const DOMAIN = 'harnesswalk.vynci.dev';
export const ORIGIN_HOST = `origin.${DOMAIN}`;
export const SSM_PREFIX = '/harnesswalk/prod';
export const SSM_PARAMS = {
  anthropicApiKey: `${SSM_PREFIX}/ANTHROPIC_API_KEY`,
  secret: `${SSM_PREFIX}/HARNESSWALK_SECRET`,
  originVerify: `${SSM_PREFIX}/ORIGIN_VERIFY`,
  // Plain String, written by deploy-origin.sh / rollback.sh: the image tag the box runs.
  imageTag: `${SSM_PREFIX}/IMAGE_TAG`,
} as const;
export const ECR_REPOSITORY = 'harnesswalk/api';
export const LOG_GROUP = '/harnesswalk/prod';
export const GITHUB_REPO = 'vynci/harnesswalk';
export const GITHUB_BRANCH = 'main';
export const GITHUB_OIDC_URL = 'https://token.actions.githubusercontent.com';
export const STACKS = {
  edge: 'HarnesswalkEdge',
  origin: 'HarnesswalkOrigin',
  ci: 'HarnesswalkCi',
} as const;
// The API paths CloudFront sends to the origin; everything else is the S3 web app.
export const API_PATH_PATTERNS = ['/v1/*', '/healthz', '/readyz'] as const;

export const webBucketName = (account: string) => `harnesswalk-web-${account}`;

export const TAGS = { project: 'harnesswalk', stage: 'prod' } as const;
