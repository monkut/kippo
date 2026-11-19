from accounts.models import KippoUser

from projects.models import KippoProject, ProjectParticipant


def make_participant(project: KippoProject, user: KippoUser, percentage: int = 0, role: str = "") -> ProjectParticipant:
    """The ProjectParticipant a new monthly row needs (kippo#75 O2).

    bulk_create skips full_clean(), so fixtures whose project has no period can still add one.
    """
    participant = ProjectParticipant.objects.filter(project=project, user=user).first()
    if participant is not None:
        return participant
    return ProjectParticipant.objects.bulk_create([ProjectParticipant(project=project, user=user, percentage=percentage, role=role)])[0]
