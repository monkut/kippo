"""Tests for KippoProject MTG calendar-link helpers (kiconiaworks/kippo#13)."""

from urllib.parse import parse_qs, unquote, urlparse

from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase

from projects.models import KippoProject

RESOURCE_CALENDAR_EMAIL = "c_18883p4h9sv0ijl3nucmgmvpf5afs@resource.calendar.google.com"


class MeetingCalendarModelTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.project: KippoProject = created["KippoProject"]

    def test_get_dsearch_tag(self):
        tag = self.project.get_dsearch_tag()
        assert tag == f'[dsearch]{{"project":"{self.project.id}"}}[/dsearch]'

    def test_calendar_template_url_without_calendar_email(self):
        self.organization.calendar_email = ""
        self.organization.save()
        url = self.project.get_meeting_calendar_template_url()
        assert url.startswith("https://calendar.google.com/calendar/render?")
        assert "add=" not in url
        parsed = parse_qs(urlparse(url).query)
        assert parsed["action"][0] == "TEMPLATE"
        assert parsed["text"][0] == self.project.name
        assert parsed["details"][0] == self.project.get_dsearch_tag()

    def test_calendar_template_url_with_calendar_email(self):
        self.organization.calendar_email = RESOURCE_CALENDAR_EMAIL
        self.organization.save()
        # re-fetch so project.organization reflects the saved calendar_email
        project = KippoProject.objects.get(pk=self.project.pk)
        url = project.get_meeting_calendar_template_url()
        parsed = parse_qs(urlparse(url).query)
        assert parsed["text"][0] == project.name
        assert parsed["details"][0] == project.get_dsearch_tag()
        assert parsed["add"][0] == RESOURCE_CALENDAR_EMAIL
        # the '/' in [/dsearch] must be percent-encoded (encodeURIComponent-equivalent)
        assert "%2F" in url

    def test_calendar_template_url_prefills_meeting_title_with_project_name(self):
        # a name with space / '&' / non-ascii must be percent-encoded so it cannot
        # corrupt the query string
        self.project.name = "案件 Alpha & Beta"
        self.project.save()
        project = KippoProject.objects.get(pk=self.project.pk)
        url = project.get_meeting_calendar_template_url()
        parsed = parse_qs(urlparse(url).query)
        assert parsed["text"][0] == "案件 Alpha & Beta"
        text_encoded = url.split("text=", 1)[1].split("&", 1)[0]
        assert " " not in text_encoded
        assert text_encoded == "%E6%A1%88%E4%BB%B6%20Alpha%20%26%20Beta"

    def test_calendar_template_url_details_fully_escaped(self):
        """The details tag must be fully percent-escaped (no raw [ ] { } " : characters)."""
        self.organization.calendar_email = ""
        self.organization.save()
        url = self.project.get_meeting_calendar_template_url()
        details_encoded = url.split("details=", 1)[1]
        assert unquote(details_encoded) == self.project.get_dsearch_tag()
        for raw_char in ("[", "]", "{", "}", '"', ":"):
            assert raw_char not in details_encoded
