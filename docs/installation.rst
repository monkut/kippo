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
4. Obtain the credentials for web application integration
5. Update the `zappa_settings.json` by adding the GOOGLE_OAUTH2_KEY and GOOGLE_OAUTH2_SECRET `environment variables <https://github.com/Miserlou/Zappa#setting-environment-variables>`_.


Prepare static files to be served from an S3 Bucket
========================================================


Prepare Lambda Environment
==============================

This procedure defines how to setup this project with lambda (zappa).
This is a standard django application, so you can also install it to a standard instance or server.

