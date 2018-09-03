"""
Update the 'zappa_settings.json'.
"""
import os
import json
import boto3


DEFAULT_AWS_REGION = 'us-west-2'


def find_zappasettings_filepath(root=os.getcwd()):
    filepath = None
    root_directory = os.path.abspath(root)
    for p, dirs, files in os.walk(root_directory):
        if 'zappa_settings.json' in files:
            filepath = os.path.join(p, 'zappa_settings.json')
            break
    if not filepath:
        raise Exception('zappa_settings.json not found in: {}'.format(root_directory))
    return filepath


DEFAULT_ZAPPASETTINGS_FILEPATH = find_zappasettings_filepath()


def get_vpc_privatesubents_and_sgid(stackname, region=DEFAULT_AWS_REGION):
    """
    Query AWS to obtain the related VPC private subnet ids and Security Group ID
    for granting a lambda function access to the VPC
    :returns: (PRIVATE_SUBNET_IDS, SECURITYGROUP_ID)
    """
    cloudformation = boto3.client('cloudformation', region_name=region)
    response = cloudformation.describe_stacks(StackName=stackname)
    # get SubnetIds = subnet-NNNNNNN,subnet-NNNNNNN  # Created 'Private' subnetIds A & B
    assert len(response['Stacks']) == 1
    stack_info = response['Stacks'][0]
    private_subnet_ids = None
    for output_info in stack_info['Outputs']:
        if output_info['OutputKey'] == 'SubnetsPrivate':
            private_subnet_ids = output_info['OutputValue'].split(',')
            break
    # get securitygroup groupId
    # WHERE Name takes the format: (two are created for stack)
    # "GroupName": "kippo-zappa-cf-stack2-LambdaExecSecurityGroup-1U42EEM6F1ZLU",
    startswith_string = '{}-LambdaExecSecurityGroup'.format(stackname)

    ec2 = boto3.client('ec2', region_name=region)
    response = ec2.describe_security_groups()
    securitygroup_group_id = None
    for sg_info in response['SecurityGroups']:
        if sg_info['GroupName'].startswith(startswith_string):
            securitygroup_group_id = sg_info['GroupId']
            break
    return private_subnet_ids, securitygroup_group_id


def update_zappa_settings(stackname, stage, region=DEFAULT_AWS_REGION, filepath=DEFAULT_ZAPPASETTINGS_FILEPATH):
    """
    Update the 'zappa_settings.json' file with required VPC configuration values
    :param stackname: (str) Stackname used
    :param stage: (str) stage name as defined in the zappa_settings.json to apply the update to.
    :param filepath: (str) filepath to the zappa_settings.json file.
    :return: (dict) resulting updated zappa_settings configuration
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError('zappa_settings.json not found at: {}'.format(filepath))
    private_subnet_ids, securitygroup_group_id = get_vpc_privatesubents_and_sgid(stackname, region)

    with open(filepath, 'r', encoding='utf8') as zappa_in:
        zappa_settings = json.load(zappa_in)

    assert stage in zappa_settings

    # add vpc_config
    zappa_settings[stage]['vpc_config'] = {
        "SubnetIds": private_subnet_ids,
        "SecurityGroupIds": [securitygroup_group_id]
    }

    # write output
    with open(filepath, 'w', encoding='utf8') as zappa_out:
        zappa_out.write(json.dumps(zappa_settings, indent=4))
    return zappa_settings


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-p', '--project-id',
                        dest='project_id',
                        default='kippo',
                        help='Project identifier')
    parser.add_argument('-c', '--cloudformation-stackname',
                        dest='stackname',
                        default='kipp-zappa-cf-stack',
                        help='Update the existing zappa_settings.json with the related VPC configuration')
    parser.add_argument('-s', '--stage',
                        default='dev',
                        help='Stage to update VPC configuration for [DEFAULT=dev]')
    parser.add_argument('-r', '--region',
                        default='us-west-1',
                        help='AWS Region stack exists in')
    args = parser.parse_args()

    print('Found: {}'.format(DEFAULT_ZAPPASETTINGS_FILEPATH))
    result = update_zappa_settings(args.stackname,
                                   args.stage,
                                   args.region)
    print('Updated "zappa_settings.json" for ({}): '.format(args.stackname))
    print(json.dumps(result, indent=4))
