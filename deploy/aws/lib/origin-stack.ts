import * as fs from 'node:fs';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import {
  DEPLOY_DOCUMENT,
  ECR_REPOSITORY,
  IMAGE_TAG_PATTERN,
  LOG_GROUP,
  ORIGIN_HOST,
  ORIGIN_ROLE_TAG,
  SSM_PARAMS,
  SSM_PREFIX,
} from './config';

// The origin: one t4g.small running the API and Caddy with Docker compose, in a public subnet
// of the account's default VPC (context createVpc=true: a small VPC of its own instead, two
// public subnets, no NAT; the account's VPC quota was full when this was written). No SSH:
// shell access is SSM Session Manager. The SQLite stores live on a separate EBS volume the instance attaches to itself at
// boot, so a replaced instance (new AMI, new user data) picks up the same data.

export interface OriginStackProps extends cdk.StackProps {
  // Pinned Amazon Linux 2023 arm64 AMI. Pinned so a new AMI never replaces the box by surprise.
  amiId: string;
  // The instance and its data volume use the first AZ; createVpc puts a subnet in each.
  availabilityZones: [string, string];
  // true: create a VPC of its own (10.79.0.0/24). Default: the account's default VPC, found
  // with a context lookup (cdk.context.json holds the result).
  createVpc?: boolean;
  // com.amazonaws.global.cloudfront.origin-facing in this region.
  cloudFrontPrefixListId: string;
  // For the edge 5xx alarm (the distribution's own metric, us-east-1 only).
  distributionId: string;
  instanceType?: string;
}

export class OriginStack extends cdk.Stack {
  public readonly instance: ec2.Instance;
  public readonly eip: ec2.CfnEIP;
  public readonly alarmTopic: sns.Topic;

  constructor(scope: Construct, id: string, props: OriginStackProps) {
    super(scope, id, props);

    const vpc: ec2.IVpc = props.createVpc
      ? new ec2.Vpc(this, 'Vpc', {
          ipAddresses: ec2.IpAddresses.cidr('10.79.0.0/24'),
          availabilityZones: props.availabilityZones,
          natGateways: 0,
          subnetConfiguration: [{ name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 26 }],
          // The default-SG lockdown is a custom resource (a Lambda); this stack has no Lambda.
          // Nothing uses the VPC's default security group.
          restrictDefaultSecurityGroup: false,
        })
      : ec2.Vpc.fromLookup(this, 'Vpc', { isDefault: true });

    // The public subnet in the first AZ. Before the lookup has run, CDK hands out a dummy VPC
    // (vpc-12345, AZs dummy1a/dummy1b) and synthesizes again once cdk.context.json has the
    // answer, so only a real VPC without such a subnet is an error.
    const az = props.availabilityZones[0];
    const inAz = vpc.selectSubnets({ subnetType: ec2.SubnetType.PUBLIC, availabilityZones: [az] }).subnets;
    if (inAz.length === 0 && vpc.vpcId !== 'vpc-12345') {
      throw new Error(`${vpc.vpcId} has no public subnet in ${az}`);
    }
    const subnet = inAz[0] ?? vpc.publicSubnets[0];

    const sg = new ec2.SecurityGroup(this, 'OriginSg', {
      vpc,
      description: 'harnesswalk origin: 443 from CloudFront only, 80 for ACME HTTP-01, no 22',
      allowAllOutbound: true,
    });
    sg.addIngressRule(
      ec2.Peer.prefixList(props.cloudFrontPrefixListId),
      ec2.Port.tcp(443),
      'HTTPS from CloudFront origin-facing servers only',
    );
    sg.addIngressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(80),
      'ACME HTTP-01 (Caddy redirects everything else to 443)',
    );

    const logGroup = new logs.LogGroup(this, 'LogGroup', {
      logGroupName: LOG_GROUP,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const dataVolume = new ec2.Volume(this, 'DataVolume', {
      availabilityZone: subnet.availabilityZone,
      size: cdk.Size.gibibytes(20),
      volumeType: ec2.EbsDeviceVolumeType.GP3,
      encrypted: true,
      volumeName: 'harnesswalk-data',
      // The data outlives the stack: deleting the stack leaves the volume (runbook).
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    cdk.Tags.of(dataVolume).add('harnesswalk:role', 'data');

    const role = new iam.Role(this, 'InstanceRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      description: 'harnesswalk origin: SSM core, ECR pull, /harnesswalk/prod/* read, logs',
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
      ],
    });
    const repoArn = cdk.Arn.format(
      { service: 'ecr', resource: 'repository', resourceName: ECR_REPOSITORY },
      this,
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: 'EcrAuth',
        actions: ['ecr:GetAuthorizationToken'],
        resources: ['*'], // the action has no resource-level scope
      }),
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: 'EcrPull',
        actions: [
          'ecr:BatchCheckLayerAvailability',
          'ecr:BatchGetImage',
          'ecr:GetDownloadUrlForLayer',
        ],
        resources: [repoArn],
      }),
    );
    const prodParameters = cdk.Arn.format(
      { service: 'ssm', resource: 'parameter', resourceName: `${SSM_PREFIX.slice(1)}/*` },
      this,
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ReadProdParameters',
        actions: ['ssm:GetParameter', 'ssm:GetParameters'],
        resources: [prodParameters],
      }),
    );
    // AmazonSSMManagedInstanceCore allows ssm:GetParameter(s) on every parameter in the
    // account, which other projects share. An explicit Deny outside /harnesswalk/prod/* wins
    // over it (the SSM agent itself reads no parameters).
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: 'DenyOtherParameters',
        effect: iam.Effect.DENY,
        actions: ['ssm:GetParameter', 'ssm:GetParameters', 'ssm:GetParametersByPath'],
        notResources: [prodParameters],
      }),
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: 'DecryptProdParameters',
        actions: ['kms:Decrypt'],
        resources: ['*'], // the AWS managed aws/ssm key, only through SSM
        conditions: {
          StringEquals: { 'kms:ViaService': `ssm.${this.region}.amazonaws.com` },
          // SSM puts the parameter's ARN in the encryption context: only our parameters.
          StringLike: { 'kms:EncryptionContext:PARAMETER_ARN': prodParameters },
        },
      }),
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: 'WriteLogs',
        actions: ['logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams'],
        resources: [logGroup.logGroupArn, `${logGroup.logGroupArn}:*`],
      }),
    );
    // Self-attach of the data volume at boot: only this volume, only to an instance tagged as
    // the harnesswalk origin.
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AttachDataVolume',
        actions: ['ec2:AttachVolume'],
        resources: [
          cdk.Arn.format({ service: 'ec2', resource: 'volume', resourceName: dataVolume.volumeId }, this),
        ],
      }),
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AttachToSelf',
        actions: ['ec2:AttachVolume'],
        resources: [cdk.Arn.format({ service: 'ec2', resource: 'instance', resourceName: '*' }, this)],
        conditions: { StringEquals: { 'aws:ResourceTag/harnesswalk:role': 'origin' } },
      }),
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: 'DescribeVolumes',
        actions: ['ec2:DescribeVolumes'],
        resources: ['*'], // Describe* has no resource-level scope
      }),
    );

    this.eip = new ec2.CfnEIP(this, 'Eip', {
      domain: 'vpc',
      tags: [{ key: 'Name', value: 'harnesswalk-origin' }],
    });

    const script = fs.readFileSync(path.join(__dirname, 'origin', 'user-data.sh'), 'utf8');
    const userData = ec2.UserData.forLinux();
    userData.addCommands(
      `export HW_REGION=${this.region}`,
      `export HW_ACCOUNT=${this.account}`,
      `export HW_DATA_VOLUME_ID=${dataVolume.volumeId}`,
      `export HW_SSM_PREFIX=${SSM_PREFIX}`,
      `export HW_IMAGE_TAG_PARAM=${SSM_PARAMS.imageTag}`,
      `export HW_ECR_REPOSITORY=${ECR_REPOSITORY}`,
      `export HW_LOG_GROUP=${LOG_GROUP}`,
      `export HW_ORIGIN_HOST=${ORIGIN_HOST}`,
      script,
    );

    this.instance = new ec2.Instance(this, 'Instance', {
      vpc,
      vpcSubnets: { subnets: [subnet] },
      instanceType: new ec2.InstanceType(props.instanceType ?? 't4g.small'),
      machineImage: ec2.MachineImage.genericLinux({ [this.region]: props.amiId }),
      securityGroup: sg,
      role,
      userData,
      userDataCausesReplacement: true,
      // IMDSv2 required (the same as requireImdsv2, which cannot be combined with the hop limit).
      httpEndpoint: true,
      httpTokens: ec2.HttpTokens.REQUIRED,
      // 1 hop: dockerd on the host reaches IMDS (awslogs, ECR login), containers on the
      // bridge network do not, so the API process never sees the instance credentials.
      httpPutResponseHopLimit: 1,
      blockDevices: [
        {
          deviceName: '/dev/xvda',
          volume: ec2.BlockDeviceVolume.ebs(20, {
            volumeType: ec2.EbsDeviceVolumeType.GP3,
            encrypted: true,
            deleteOnTermination: true,
          }),
        },
      ],
    });
    cdk.Tags.of(this.instance).add('Name', 'harnesswalk-origin');
    cdk.Tags.of(this.instance).add(ORIGIN_ROLE_TAG.key, ORIGIN_ROLE_TAG.value);

    // The deploy command: harnesswalk-up for one image tag, then wait for /readyz. The tag is
    // the only input and SSM checks it against allowedPattern before anything runs, so whoever
    // may send this document can restart the stack on an image, and nothing else.
    new ssm.CfnDocument(this, 'DeployDocument', {
      name: DEPLOY_DOCUMENT,
      documentType: 'Command',
      documentFormat: 'JSON',
      // A named document cannot be replaced in place; a content change is a new version.
      updateMethod: 'NewVersion',
      content: {
        schemaVersion: '2.2',
        description: 'harnesswalk: run harnesswalk-up on the origin for one image tag, then wait for /readyz',
        parameters: {
          tag: {
            type: 'String',
            description: 'Image tag (a git sha) already set in /harnesswalk/prod/IMAGE_TAG',
            allowedPattern: IMAGE_TAG_PATTERN,
          },
        },
        mainSteps: [
          {
            action: 'aws:runShellScript',
            name: 'deploy',
            precondition: { StringEquals: ['platformType', 'Linux'] },
            inputs: {
              timeoutSeconds: '900',
              runCommand: [
                "/usr/local/sbin/harnesswalk-up '{{ tag }}' || exit 1",
                'for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/readyz && exit 0; sleep 2; done',
                "echo 'harnesswalk-deploy: /readyz did not answer within 60 s' >&2",
                'exit 1',
              ],
            },
          },
        ],
      },
    });

    new ec2.CfnEIPAssociation(this, 'EipAssociation', {
      allocationId: this.eip.attrAllocationId,
      instanceId: this.instance.instanceId,
    });

    // Alarms, all to one topic the owner subscribes an email to (runbook).
    this.alarmTopic = new sns.Topic(this, 'AlarmTopic', { topicName: 'harnesswalk-alarms' });
    const notify = new cwActions.SnsAction(this.alarmTopic);

    // Origin 5xx: the API's JSON access log (HARNESSWALK_LOG_FORMAT=json) has a status field.
    const origin5xx = new logs.MetricFilter(this, 'Origin5xxFilter', {
      logGroup,
      filterPattern: logs.FilterPattern.numberValue('$.status', '>=', 500),
      metricNamespace: 'Harnesswalk',
      metricName: 'Origin5xx',
      metricValue: '1',
      defaultValue: 0,
    });
    const originRequests = new logs.MetricFilter(this, 'OriginRequestsFilter', {
      logGroup,
      filterPattern: logs.FilterPattern.numberValue('$.status', '>=', 100),
      metricNamespace: 'Harnesswalk',
      metricName: 'OriginRequests',
      metricValue: '1',
      defaultValue: 0,
    });
    const period = cdk.Duration.minutes(5);
    const rate = new cloudwatch.MathExpression({
      // Percent of origin responses that were 5xx; 0 when there was no traffic.
      expression: 'IF(requests >= 10, 100 * errors / requests, 0)',
      usingMetrics: {
        errors: origin5xx.metric({ statistic: 'Sum', period }),
        requests: originRequests.metric({ statistic: 'Sum', period }),
      },
      period,
      label: 'Origin 5xx rate (%)',
    });
    rate
      .createAlarm(this, 'Origin5xxRateAlarm', {
        alarmName: 'harnesswalk-origin-5xx-rate',
        alarmDescription: 'More than 5% of API responses were 5xx (at least 10 requests)',
        threshold: 5,
        evaluationPeriods: 2,
        datapointsToAlarm: 2,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      })
      .addAlarmAction(notify);

    // Edge 5xx: also covers an origin CloudFront cannot reach (502/504), which never logs.
    new cloudwatch.Metric({
      namespace: 'AWS/CloudFront',
      metricName: '5xxErrorRate',
      dimensionsMap: { DistributionId: props.distributionId, Region: 'Global' },
      statistic: 'Average',
      period,
    })
      .createAlarm(this, 'Edge5xxRateAlarm', {
        alarmName: 'harnesswalk-edge-5xx-rate',
        alarmDescription: 'CloudFront 5xx error rate above 5% (origin unreachable or failing)',
        threshold: 5,
        evaluationPeriods: 2,
        datapointsToAlarm: 2,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      })
      .addAlarmAction(notify);

    new cloudwatch.Metric({
      namespace: 'AWS/EC2',
      metricName: 'CPUUtilization',
      dimensionsMap: { InstanceId: this.instance.instanceId },
      statistic: 'Average',
      period,
    })
      .createAlarm(this, 'CpuAlarm', {
        alarmName: 'harnesswalk-origin-cpu',
        alarmDescription: 'Origin CPU above 80% for 15 minutes (t4g credits drain)',
        threshold: 80,
        evaluationPeriods: 3,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      })
      .addAlarmAction(notify);

    new cloudwatch.Metric({
      namespace: 'AWS/EC2',
      metricName: 'StatusCheckFailed',
      dimensionsMap: { InstanceId: this.instance.instanceId },
      statistic: 'Maximum',
      period: cdk.Duration.minutes(1),
    })
      .createAlarm(this, 'StatusCheckAlarm', {
        alarmName: 'harnesswalk-origin-status-check',
        alarmDescription: 'Origin instance failed an EC2 status check',
        threshold: 1,
        evaluationPeriods: 3,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.BREACHING,
      })
      .addAlarmAction(notify);

    new cdk.CfnOutput(this, 'ElasticIp', {
      value: this.eip.ref,
      description: `GoDaddy: A record ${ORIGIN_HOST.split('.').slice(0, 2).join('.')} -> this value`,
    });
    new cdk.CfnOutput(this, 'InstanceId', { value: this.instance.instanceId });
    new cdk.CfnOutput(this, 'DataVolumeId', { value: dataVolume.volumeId });
    new cdk.CfnOutput(this, 'AlarmTopicArn', {
      value: this.alarmTopic.topicArn,
      description: 'Subscribe the owner email (runbook)',
    });
    new cdk.CfnOutput(this, 'LogGroupName', { value: logGroup.logGroupName });
  }
}
