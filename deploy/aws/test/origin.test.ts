import * as fs from 'node:fs';
import * as path from 'node:path';
import { Match } from 'aws-cdk-lib/assertions';
import { describe, expect, it } from 'vitest';
import { asList, statements, synth } from './helpers';

const { origin } = synth();

describe('HarnesswalkOrigin network', () => {
  it('has two public subnets in two AZs, no NAT and no private subnets', () => {
    origin.resourceCountIs('AWS::EC2::Subnet', 2);
    origin.resourceCountIs('AWS::EC2::NatGateway', 0);
    const subnets = Object.values(origin.findResources('AWS::EC2::Subnet')).map((s: any) => s.Properties);
    expect(new Set(subnets.map((s) => s.AvailabilityZone))).toEqual(new Set(['us-east-1a', 'us-east-1b']));
    for (const s of subnets) expect(s.MapPublicIpOnLaunch).toBe(true);
  });

  it('opens 443 to the CloudFront prefix list only, 80 to anyone, and nothing else', () => {
    const sg = Object.values(origin.findResources('AWS::EC2::SecurityGroup'))[0].Properties;
    const inline = sg.SecurityGroupIngress ?? [];
    const separate = Object.values(origin.findResources('AWS::EC2::SecurityGroupIngress')).map(
      (r: any) => r.Properties,
    );
    const rules = [...inline, ...separate];
    expect(rules).toHaveLength(2);
    const https = rules.find((r: any) => r.FromPort === 443);
    expect(https).toMatchObject({ IpProtocol: 'tcp', ToPort: 443, SourcePrefixListId: 'pl-3b927c52' });
    expect(https.CidrIp).toBeUndefined();
    const http = rules.find((r: any) => r.FromPort === 80);
    expect(http).toMatchObject({ IpProtocol: 'tcp', ToPort: 80, CidrIp: '0.0.0.0/0' });
    expect(rules.some((r: any) => r.FromPort <= 22 && r.ToPort >= 22)).toBe(false);
  });
});

describe('HarnesswalkOrigin instance', () => {
  it('is a t4g.small on the pinned AMI with IMDSv2 required and a hop limit of 1', () => {
    origin.hasResourceProperties('AWS::EC2::Instance', {
      InstanceType: 't4g.small',
      ImageId: 'ami-0f11e0e04ccb60d0a',
      MetadataOptions: Match.objectLike({ HttpTokens: 'required', HttpPutResponseHopLimit: 1 }),
      BlockDeviceMappings: [
        { DeviceName: '/dev/xvda', Ebs: { VolumeSize: 20, VolumeType: 'gp3', Encrypted: true, DeleteOnTermination: true } },
      ],
    });
    origin.hasResourceProperties('AWS::EC2::Instance', {
      Tags: Match.arrayWith([{ Key: 'harnesswalk:role', Value: 'origin' }]),
    });
  });

  it('keeps the data on a separate retained, encrypted 20 GB gp3 volume in the instance AZ', () => {
    origin.hasResource('AWS::EC2::Volume', {
      DeletionPolicy: 'Retain',
      Properties: Match.objectLike({ Size: 20, VolumeType: 'gp3', Encrypted: true }),
    });
    const vol = Object.values(origin.findResources('AWS::EC2::Volume'))[0].Properties;
    const inst = Object.values(origin.findResources('AWS::EC2::Instance'))[0].Properties;
    const subnetId = inst.SubnetId.Ref;
    const subnet = origin.toJSON().Resources[subnetId].Properties;
    expect(vol.AvailabilityZone).toBe(subnet.AvailabilityZone);
    // The instance attaches it itself at boot (no VolumeAttachment to fight a replacement).
    origin.resourceCountIs('AWS::EC2::VolumeAttachment', 0);
  });

  it('has an Elastic IP output for the A record', () => {
    origin.resourceCountIs('AWS::EC2::EIP', 1);
    origin.resourceCountIs('AWS::EC2::EIPAssociation', 1);
    origin.hasOutput('ElasticIp', {});
    origin.hasOutput('InstanceId', {});
    origin.hasOutput('AlarmTopicArn', {});
  });

  it('boots without echoing secrets and installs a checksummed compose plugin', () => {
    const script = fs.readFileSync(path.join(__dirname, '..', 'lib', 'origin', 'user-data.sh'), 'utf8');
    expect(script).not.toMatch(/^\s*set\s+-[a-z]*x/m);
    expect(script).toMatch(/sha256sum -c -/);
    expect(script).toMatch(/--profile prod up -d/);
    expect(script).toMatch(/systemctl enable harnesswalk\.service/);
  });
});

describe('HarnesswalkOrigin IAM', () => {
  const st = statements(origin);
  const find = (sid: string) => st.find((s) => s.Sid === sid)!;

  it('has the SSM core managed policy', () => {
    origin.hasResourceProperties('AWS::IAM::Role', {
      ManagedPolicyArns: [
        { 'Fn::Join': ['', ['arn:', { Ref: 'AWS::Partition' }, ':iam::aws:policy/AmazonSSMManagedInstanceCore']] },
      ],
    });
  });

  it('reads only /harnesswalk/prod/* parameters, decrypting only through SSM', () => {
    expect(JSON.stringify(find('ReadProdParameters').Resource)).toContain(':parameter/harnesswalk/prod/*');
    expect(find('DecryptProdParameters')).toMatchObject({
      Action: 'kms:Decrypt',
      Condition: { StringEquals: { 'kms:ViaService': 'ssm.us-east-1.amazonaws.com' } },
    });
  });

  it('pulls only harnesswalk/api and writes only to /harnesswalk/prod logs', () => {
    expect(asList(find('EcrPull').Action)).not.toContain('ecr:PutImage');
    expect(JSON.stringify(find('EcrPull').Resource)).toContain(':repository/harnesswalk/api');
    expect(JSON.stringify(find('WriteLogs').Resource)).toContain('LogGroup');
  });

  it('attaches only its own data volume, only to the tagged origin', () => {
    expect(JSON.stringify(find('AttachDataVolume').Resource)).toContain('DataVolume');
    expect(find('AttachToSelf').Condition).toEqual({
      StringEquals: { 'aws:ResourceTag/harnesswalk:role': 'origin' },
    });
  });

  it('never grants a wildcard action, and a wildcard resource only where AWS has no scope', () => {
    const unscoped = new Set(['ecr:GetAuthorizationToken', 'kms:Decrypt', 'ec2:DescribeVolumes']);
    for (const s of st) {
      for (const a of asList(s.Action)) expect(a).not.toMatch(/\*/);
      if (asList(s.Resource).includes('*')) {
        for (const a of asList(s.Action)) expect(unscoped).toContain(a);
      }
    }
  });
});

describe('HarnesswalkOrigin observability', () => {
  it('keeps /harnesswalk/prod for 30 days', () => {
    origin.hasResourceProperties('AWS::Logs::LogGroup', {
      LogGroupName: '/harnesswalk/prod',
      RetentionInDays: 30,
    });
  });

  it('alarms on origin 5xx rate, edge 5xx rate, CPU and status checks, all to the topic', () => {
    origin.resourceCountIs('AWS::CloudWatch::Alarm', 4);
    const alarms = Object.values(origin.findResources('AWS::CloudWatch::Alarm')).map((a: any) => a.Properties);
    for (const a of alarms) expect(a.AlarmActions).toEqual([{ Ref: expect.stringMatching(/^AlarmTopic/) }]);
    expect(alarms.map((a) => a.AlarmName).sort()).toEqual([
      'harnesswalk-edge-5xx-rate',
      'harnesswalk-origin-5xx-rate',
      'harnesswalk-origin-cpu',
      'harnesswalk-origin-status-check',
    ]);
    origin.hasResourceProperties('AWS::Logs::MetricFilter', { FilterPattern: '{ $.status >= 500 }' });
  });
});
