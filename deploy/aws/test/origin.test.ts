import * as fs from 'node:fs';
import * as path from 'node:path';
import { Match } from 'aws-cdk-lib/assertions';
import { describe, expect, it } from 'vitest';
import { asList, CONTEXT, joined, statements, synth } from './helpers';

const { origin } = synth();

// The committed default-VPC lookup (cdk.context.json).
const lookup = Object.entries(CONTEXT).find(([k]) => k.startsWith('vpc-provider:') && k.includes('filter.isDefault=true'))![1] as any;
const defaultSubnet1a = lookup.subnetGroups
  .flatMap((g: any) => (g.type === 'Public' ? g.subnets : []))
  .find((s: any) => s.availabilityZone === 'us-east-1a');

describe('HarnesswalkOrigin network', () => {
  it('uses the default VPC by default: no VPC of its own, the public subnet in us-east-1a', () => {
    expect(lookup.vpcCidrBlock).toBe('172.31.0.0/16');
    for (const type of ['AWS::EC2::VPC', 'AWS::EC2::Subnet', 'AWS::EC2::InternetGateway', 'AWS::EC2::NatGateway']) {
      origin.resourceCountIs(type, 0);
    }
    origin.hasResourceProperties('AWS::EC2::Instance', { SubnetId: defaultSubnet1a.subnetId });
    origin.hasResourceProperties('AWS::EC2::SecurityGroup', { VpcId: lookup.vpcId });
  });

  it('creates two public subnets in two AZs, no NAT and no private subnets with createVpc=true', () => {
    const { origin: own } = synth({ createVpc: 'true' });
    own.resourceCountIs('AWS::EC2::VPC', 1);
    own.hasResourceProperties('AWS::EC2::VPC', { CidrBlock: '10.79.0.0/24' });
    own.resourceCountIs('AWS::EC2::Subnet', 2);
    own.resourceCountIs('AWS::EC2::NatGateway', 0);
    const subnets = Object.values(own.findResources('AWS::EC2::Subnet')).map((s: any) => s.Properties);
    expect(new Set(subnets.map((s) => s.AvailabilityZone))).toEqual(new Set(['us-east-1a', 'us-east-1b']));
    for (const s of subnets) expect(s.MapPublicIpOnLaunch).toBe(true);
    const inst = Object.values(own.findResources('AWS::EC2::Instance'))[0].Properties;
    const subnet = own.toJSON().Resources[inst.SubnetId.Ref].Properties;
    expect(subnet.AvailabilityZone).toBe('us-east-1a');
    own.resourceCountIs('AWS::EC2::EIP', 1);
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
    expect(inst.SubnetId).toBe(defaultSubnet1a.subnetId);
    expect(vol.AvailabilityZone).toBe('us-east-1a');
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

  it('exports nothing, so no other stack pins the instance or the data volume', () => {
    const outputs = Object.values(origin.toJSON().Outputs ?? {}) as any[];
    expect(outputs.filter((o) => o.Export)).toEqual([]);
  });

  it('boots without echoing secrets and installs a checksummed compose plugin', () => {
    const script = fs.readFileSync(path.join(__dirname, '..', 'lib', 'origin', 'user-data.sh'), 'utf8');
    expect(script).not.toMatch(/^\s*set\s+-[a-z]*x/m);
    expect(script).toMatch(/sha256sum -c -/);
    expect(script).toMatch(/--profile prod up -d/);
    expect(script).toMatch(/systemctl enable harnesswalk\.service/);
  });
});

describe('HarnesswalkOrigin deploy document', () => {
  const docs = Object.values(origin.findResources('AWS::SSM::Document')).map((d: any) => d.Properties);

  it('is one Command document, harnesswalk-deploy, updated as a new version', () => {
    expect(docs).toHaveLength(1);
    expect(docs[0]).toMatchObject({ Name: 'harnesswalk-deploy', DocumentType: 'Command', UpdateMethod: 'NewVersion' });
  });

  it('takes only a git-sha tag and only runs harnesswalk-up and the /readyz wait', () => {
    const content = docs[0].Content;
    expect(Object.keys(content.parameters)).toEqual(['tag']);
    expect(content.parameters.tag).toMatchObject({ type: 'String', allowedPattern: '^[0-9a-f]{7,40}$' });
    const pattern = new RegExp(content.parameters.tag.allowedPattern);
    for (const ok of ['0123abc', 'a29fd6a0c1d2', 'f'.repeat(40)]) expect(pattern.test(ok)).toBe(true);
    for (const bad of ['latest', 'abc', 'a29fd6a-dirty', 'a29fd6a; reboot', 'A29FD6A', 'f'.repeat(41), "$(id)"]) {
      expect(pattern.test(bad)).toBe(false);
    }
    expect(content.mainSteps).toHaveLength(1);
    const run: string[] = content.mainSteps[0].inputs.runCommand;
    expect(content.mainSteps[0].action).toBe('aws:runShellScript');
    expect(run[0]).toBe("/usr/local/sbin/harnesswalk-up '{{ tag }}' || exit 1");
    expect(run.join('\n').match(/\{\{[^}]*\}\}/g)).toEqual(['{{ tag }}']);
    expect(run.join('\n')).toContain('curl -fsS http://127.0.0.1:8000/readyz');
    for (const line of run.slice(1)) expect(line).toMatch(/^(for i in|echo '|exit 1$)/);
  });

  it('matches what harnesswalk-up itself accepts', () => {
    const script = fs.readFileSync(path.join(__dirname, '..', 'lib', 'origin', 'user-data.sh'), 'utf8');
    expect(script).toContain('[[ "$want" =~ ^[0-9a-f]{7,40}$ ]]');
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

  const prodArn = 'arn:aws:ssm:us-east-1:111910761410:parameter/harnesswalk/prod/*';

  it('reads only /harnesswalk/prod/* parameters, decrypting only through SSM', () => {
    expect(JSON.stringify(find('ReadProdParameters').Resource)).toContain(':parameter/harnesswalk/prod/*');
    const decrypt = find('DecryptProdParameters');
    expect(decrypt).toMatchObject({
      Effect: 'Allow',
      Action: 'kms:Decrypt',
      Condition: { StringEquals: { 'kms:ViaService': 'ssm.us-east-1.amazonaws.com' } },
    });
    expect(joined(decrypt.Condition.StringLike['kms:EncryptionContext:PARAMETER_ARN'])).toBe(prodArn);
  });

  it('denies every other parameter in the account, over the SSM core managed policy', () => {
    const deny = find('DenyOtherParameters');
    expect(deny.Effect).toBe('Deny');
    expect(asList(deny.Action).sort()).toEqual(['ssm:GetParameter', 'ssm:GetParameters', 'ssm:GetParametersByPath']);
    expect(deny.Resource).toBeUndefined();
    expect(joined(deny.NotResource)).toBe(prodArn);
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
