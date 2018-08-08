======================================================
Kippo Installation
======================================================

Prerequisites:

- Python 3.6
- AWS Account (For deployment to AWS)
- awscli (For deployment to AWS)
- postgresql (compatable) database


Configuring Google Logins
=============================

In order to support login via google Oauth2, a project needs to be created in the Google Developer Dashboard, and a key/secret pair obtained:

1. Login to the developer console with the appropriate *google* account ( https://console.developers.google.com/ )
2. Create a *project* for this application
3. Enable the Google+ API for this application
    - Click, "Manage"
    - Click, "Create Credentials"
    - "Which API are you using?, select "Google +"
    - "Where will you be calling the API from?", select, "Web server (..)"
    - "What data will you be accessing?", select "User data"
    - Click the "What credentials do I need?" button

4. Populate step 2, "Create an OAuth 2.0 client ID"
    - For name set, "kippo-{ORGNAME}"



5. Update the `zappa_settings.json` by adding the GOOGLE_OAUTH2_KEY and GOOGLE_OAUTH2_SECRET `environment variables <https://github.com/Miserlou/Zappa#setting-environment-variables>`_.



Prepare static files to be served from an S3 Bucket
========================================================


Prepare Lambda Environment
==============================

This procedure defines how to setup this project with lambda (zappa).
This is a standard django application, so you can also install it to a standard instance or server.

0. Prior to setup/installation set the following environment variables::

    export DB_PASSWORD={PASSWORD TO USE}

1. Create VPC, subnets and Database::

    aws cloudformation create-stack \
        --stack-name kippo-zappa-cf-stack-prod \
        --template-body file://./conf/aws/vpc_db_2azs.yaml \
        --disable-rollback \
        --region ap-northeast-1 \
        --parameters \
            ParameterKey=DBUser,ParameterValue=django \
            ParameterKey=DBPassword,ParameterValue=${DB_PASSWORD} \
        --tags \
            Key=ZappaProject,Value=kippo && \
    aws cloudformation wait stack-create-complete \
        --stack-name kippo-zappa-cf-stack-prod

2. Initialize the zappa settings::

    zappa init

    .. note::

        Results should look similar to::

            {
                "production": {
                    "aws_region": "ap-northeast-1",
                    "django_settings": "kippo.settings.production",
                    "profile_name": "bizlabsstgadmin",
                    "project_name": "kippo",
                    "runtime": "python3.6",
                    "s3_bucket": "zappa-kippo-abc123"
                }
            }

3. Create buckets for serving static and media files::

    aws s3api create-bucket --bucket kippo-zappa-static-abc123 --create-bucket-configuration LocationConstraint=us-west-2
    aws s3api create-bucket --bucket kippo-zappa-media-abc123 --create-bucket-configuration LocationConstraint=us-west-2

    # Apply CORs (Cross-Origin Resoure Sharing)
    # NOTE: this is intended for development only!!!
    aws s3api put-bucket-cors --bucket kippo-zappa-static-abc123 --cors-configuration file://./conf/aws/static-s3-cors.json
    aws s3api put-bucket-cors --bucket kippo-zappa-media-abc123 --cors-configuration file://./conf/aws/static-s3-cors.json


4. Obtain the Database endpoint for updating the :file:`zappa_settings.json`::

    aws rds describe-db-instances --query 'DBInstances[?MasterUsername==`django`].Endpoint.Address'

5. Update with stack created VPC security-group::

    python ./conf/update_zappasettings_with_vpcinfo.py --cloudformation-stackname kippo-zappa-cf-stack-prod --stage production --region {REGION}

6. Add the following section to the appropriate STAGE section of the zappa_settings.json for DB ACCESS::

        "environment_variables": {
            "DJANGO_DB_USER": "django",
            "DJANGO_DB_PASSWORD": "{USER DEFINED ON STACK CREATION}",
            "DJANGO_DB_HOST": "{USER DEFINED ON STACK CREATION}",
        },


X. After deploy, update the API with the "Authorized Javascript origins" and "Authorized redirect URIs"

