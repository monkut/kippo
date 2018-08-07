===============================================
Initial Setup
===============================================

This describes the steps needed for initial setup of a project.


Basic Setup
=============================


1. Create an organization :guilabel:`Projects -> KippoProject`

    - Add Organization Name (For display only)
    - Add Github orginization name (Name used in github)
    - `Add personal access token <https://help.github.com/articles/creating-a-personal-access-token-for-the-command-line/>`_
    - Add valid email domains (All sub-domains used for user emails for validation)

2.



Load Existing Github Projects
===============================

Existing github organizational projects can be loaded into the system with the following command::

    # Locally (for testing)
    python manage.py collect_github_organizational_projects --github-organization-name ${GITHUB_ORGANIZATION_NAME}

    # Deployed with zappa
    zappa manage prod "collect_github_organization_projects --github-organization-name ${GITHUB_ORGANIZATION_NAME}"


Updating