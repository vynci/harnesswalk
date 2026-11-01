import * as fs from 'node:fs';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sns from 'aws-cdk-lib/aws-sns';
import { Construct } from 'constructs';
import { ECR_REPOSITORY, LOG_GROUP, ORIGIN_HOST, SSM_PARAMS, SSM_PREFIX } from './config';

// The origin: one t4g.small running the API and Caddy with Docker compose, in its own small
// VPC (two public subnets, no NAT, no private subnets). No SSH: shell access is SSM Session
// Manager. The SQLite stores live on a separate EBS volume the instance attaches to itself at
// boot, so a replaced instance (new AMI, new user data) picks up the same data.

export interface OriginStackProps extends cdk.StackProps {
  // Pinned Amazon Linux 2023 arm64 AMI. Pinned so a new AMI never replaces the box by surprise.
  amiId: string;
  // Two AZs for the subnets; the instance and its data volume use the first.
  availabilityZones: [string, string];
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

    const vpc = new ec2.Vpc(this, 'Vpc', {
      ipAddresses: ec2.IpAddresses.cidr('10.79.0.0/24'),
      availabilityZones: props.availabilityZones,
      natGateways: 0,
      subnetConfiguration: [{ name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 26 }],
      // The default-SG lockdown is a custom resource (a Lambda); this stack has no Lambda.
      // Nothing uses the VPC's default security group.
      restrictDefaultSecurityGroup: false,
    });

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

    const subnet = vpc.publicSubnets[0];
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
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ReadProdParameters',
        actions: ['ssm:GetParameter', 'ssm:GetParameters'],
        resources: [
          cdk.Arn.format(
            { service: 'ssm', resource: 'parameter', resourceName: `${SSM_PREFIX.slice(1)}/*` },
            this,
          ),
        ],
      }),
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: 'DecryptProdParameters',
        actions: ['kms:Decrypt'],
        resources: ['*'], // the AWS managed aws/ssm key, only through SSM
        conditions: { StringEquals: { 'kms:ViaService': `ssm.${this.region}.amazonaws.com` } },
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
    cdk.Tags.of(this.instance).add('harnesswalk:role', 'origin');

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
