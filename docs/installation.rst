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
3. Select your newly created project and click "+ Enable APIs and Services"
4. Search and select the "Google+ API", and click "Enable"
5. From the "Google+ API Dashboard" click, "Create credentials"
6. From the "Add credentials to your project" page fill out the following questions and click, the "What credentials to I need?" button:

    - "Which API are you using?": Google+
    - "Where will you be calling the API from?": Web server (node.js, Tomcat, etc)
    - "What data will you be accessing?": User Data

7. In the "Create an OAuth 2.0 client ID"  *name* section, enter an easy to understand identifier, (Ex: "{my organization}-kippo-credentials"), and click, "Create OAuth client ID".

    .. note::

        "Authorized JavaScript origins" and "Authorized redirect URIs" will be adjusted later.

8. Enter email for contact and "Kippo Project Management" for the *Product name shown to users* section and click, "Continue".

9. Obtain the credentials for web application integration, from the "Download credentials" section click, the "Download" button.

    .. note::

        This downloaded file contains all the necessary OAuth connection information, including the *client-id* and *client-secret*.


10. Update your local ENVIRONMENT VARIABLES with the '' and '' values from the downloaded *client_id.json* file.

11. Update the `zappa_settings.json` by adding the GOOGLE_OAUTH2_KEY and GOOGLE_OAUTH2_SECRET `environment variables <https://github.com/Miserlou/Zappa#setting-environment-variables>`_.



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

    # Apply CORs (Cross-Origin Resoure Sharing)
    # NOTE: this is intended for development only!!!
    aws s3api put-bucket-cors --bucket kippo-zappa-static-abc123 --cors-configuration file://./conf/aws/static-s3-cors.json


4. Obtain the Database endpoint for updating the :file:`zappa_settings.json`::

    aws rds describe-db-instances --query 'DBInstances[?MasterUsername==`django`].Endpoint.Address'

5. Update with stack created VPC security-group::

    python ./conf/update_zappasettings_with_vpcinfo.py --cloudformation-stackname kippo-zappa-cf-stack-prod --stage production --region {REGION}

6. Add the following section to the appropriate STAGE section of the zappa_settings.json for DB ACCESS::

        "environment_variables": {
            "DJANGO_DB_USER": "django",
            "DJANGO_DB_PASSWORD": "{USER DEFINED ON STACK CREATION}",
            "DJANGO_DB_HOST": "{USER DEFINED ON STACK CREATION}",
            "GOOGLE_OAUTH2_KEY": "{CLIENT ID}",
            "GOOGLE_OAUTH2_SECRET": "{CLIENT SECRET}",
            "S3_BUCKET_NAME": "{STATIC BUCKET NAME DEFINED ABOVE}"
        },


7. Create database tables::

    zappa manage production migrate

8. Collect static files to s3 bucket::

    zappa manage production "collectstatic --noinput"

X. After deploy, update the API with the "Authorized Javascript origins" and "Authorized redirect URIs"

