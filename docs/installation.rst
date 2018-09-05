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


10. Update your local ENVIRONMENT VARIABLES with the 'GOOGLE_OAUTH2_KEY' and 'GOOGLE_OAUTH2_SECRET' values from the downloaded *client_id.json* file.

11. Update the `zappa_settings.json` by adding the GOOGLE_OAUTH2_KEY and GOOGLE_OAUTH2_SECRET `environment variables <https://github.com/Miserlou/Zappa#setting-environment-variables>`_.



Prepare static files to be served from an S3 Bucket
========================================================


Prepare Lambda Environment
==============================

This procedure defines how to setup this project with lambda (zappa).
This is a standard django application, so you can also install it to a standard instance or server.


Prepare Initial Database
==============================

WHen the system is setup and connected the database needs to be prepared.

1. Run initial migrate::

    zappa manage prod migrate

2. Load initial data fixtures::

    zappa manage prod "loaddata required_bot_users"
    zappa manage prod "loaddata default_labelset"
    zappa manage prod "loaddata default_columnset"


Create the initial superuser
==================================

In order to setup the admin initially, a super user needs to be created.
This super user *may* be deleted after a KippoOrganization has been set  and users added to the system.

.. note::

    You'll want set at least one user as a *superuser* for maintenance.

1. Create initial superuser::

    # Update the password defined here and issue the following command from the directory containing the `zappa_settings.json` file:
    zappa invoke --raw prod "from accounts.models import KippoUser; KippoUser.objects.create_superuser('admin', 'admin@yourdomain.com', 'not-so-secret-password')"



Now your infrastructure is prepared and you are now ready to proceed to :ref:`initial-setup`.

