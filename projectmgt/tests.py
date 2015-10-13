from django.test import TestCase
from django.core.urlresolvers import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APIRequestFactory, APIClient, force_authenticate
from rest_framework.authtoken.models import Token
from django.contrib.auth.models import User


class UserTest(APITestCase):

    def setUp(self):
        self.username = "testuser"
        self.password = "password"
        # automatically creates token
        User.objects.create_user(self.username, "testuser@nokia.com", self.password)

    def test_obtain_token(self):
        url = reverse("obtain_auth_token")
        token = Token.objects.get(user__username=self.username)

        # expected JSON response
        # { 'token' : '9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b' }
        data = {'username': self.username,
                'password': self.password}
        response = self.client.post(url, data, format='json')
        self.assertTrue("token" in response.data, "'token' not in response({})".format(response.data))
        msg = "TOKEN: actual('{}') != expected('{}')"
        self.assertEqual(response.data["token"], token.key, msg)
