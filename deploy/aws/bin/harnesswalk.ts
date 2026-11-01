#!/usr/bin/env node
// harnesswalk.vynci.dev: three stacks, deployed in this order (deploy/README.md):
//   HarnesswalkEdge    S3 + CloudFront (needs the issued ACM certificate: context certArn)
//   HarnesswalkOrigin  VPC, one EC2 instance running Docker compose, data volume, alarms
//   HarnesswalkCi      ECR repository, GitHub OIDC provider, deploy role
import * as cdk from 'aws-cdk-lib';
import { buildApp } from '../lib/app';

const app = new cdk.App();
buildApp(app);
app.synth();
