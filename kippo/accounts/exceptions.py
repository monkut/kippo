from social_core.exceptions import AuthException


class OrganizationConfigurationError(Exception):
    pass


class OrganizationInviteExpiredError(AuthException):
    """Raised in the social-auth pipeline when every pending invite for the login email has expired.

    `SocialAuthExceptionMiddleware` turns it into a login-page error message.
    """
